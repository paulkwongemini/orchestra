#!/usr/bin/env python3
"""Run a task on another vendor's coding CLI for a conductor in a CLI mode, and check the run.

    worker.py run    --cli codex --task <task spec> --role <role> [--effort <level>]
    worker.py resume --task <task spec> --message <file>

`run` starts a fresh session on the task spec. `resume` continues the task's last session with
the conductor's review comment, read from a file, with the same settings: a resumed Codex run
does not keep the sandbox options of the first run, so they are passed again.

This is a tool the conductor runs. It builds the command so that the settings are right every
time. The network is open unless the brief's `## Constraints` has the line
`Worker network: blocked`. A PreToolUse hook, `worker_guard.py`, goes with every run and refuses
what the claude mode's hooks refuse a Claude subagent, with the brief's pre-approved keys. The
script keeps the run's record under `files/<task>/cli/`, and after the run prints a few lines:
the status, the time, the tokens, the changed files, what the guard refused, and a warning for
each point that a check after the run can see:

  - a file changed outside the paths under the task spec's `## Scope and limits`
  - a change under `.orchestra/` other than the task's report and its `files/<task>/` folder
  - a change to git state
  - a `DONE` report without a valid verification record, or no report at all

The report's text is never printed, so the conductor can still review before it reads.

Exit codes: 0 the run finished and nothing was flagged, 2 the run finished with warnings,
1 the run did not finish or could not start.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402
import check_report  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROLES_DIR = os.path.join(ROOT, "roles")

# kind: impl writes the project; readonly writes only its report and its files/ folder.
ROLES = {
    "engineer": {"kind": "impl"},
    "implementer": {"kind": "impl"},
    "writer": {"kind": "impl"},
    "analyst": {"kind": "readonly"},
}

# Folders whose changes say nothing about the work: tool caches and the like.
GUARD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "worker_guard.py")
NETWORK_LINE = re.compile(r"^\W*worker network\W*[:：]\s*(blocked|open)\b", re.I | re.M)

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "node_modules", ".venv", "venv"}

TASK_PATH = re.compile(r"^(?P<project>.*)/\.orchestra/(?P<work>[^/]+)/tasks/(?P<file>(?P<id>T\d+)[^/]*\.md)$")
LIMIT_WORDS = re.compile(r"usage limit|rate limit|quota|too many requests|limit reached|upgrade your plan", re.I)

PROMPT = (
    "Read your role at {role} and the shared worker instructions it names, then read your task "
    "spec at {spec}. Follow them and carry out the spec. Write your full report to {report}. "
    "Your last message holds only the status lines that the shared instructions give."
)


class Fail(Exception):
    """A run that cannot start, with the reason for the conductor."""


# ---------------------------------------------------------------- the CLIs

class Codex:
    name = "codex"
    binary = "codex"
    efforts = ("low", "medium", "high", "xhigh", "max", "ultra")
    models = {"engineer": "gpt-6.2-astra", "analyst": "gpt-6.2-astra",
              "implementer": "gpt-6.2-sol", "writer": "gpt-6.2-sol"}
    # The user's choice. `implementer` and `writer` sit one level above the GPT-6.2 guides'
    # starting point, because the user has Codex usage to spare. `analyst` may go up to `max`.
    default_effort = {"engineer": "high", "analyst": "high", "implementer": "medium", "writer": "medium"}

    def home(self):
        return os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")

    def mcp_servers(self):
        """The MCP servers in the user's config. Each is turned off by name: an empty table does not do it."""
        path = os.path.join(self.home(), "config.toml")
        try:
            import tomllib
            with open(path, "rb") as f:
                servers = tomllib.load(f).get("mcp_servers") or {}
        except (OSError, ValueError, ImportError):
            return []
        return sorted(servers) if isinstance(servers, dict) else []

    def settings(self, s):
        """The options both a first run and a resume pass, so that a resume keeps them."""
        args = ["-m", s["model"], "-c", 'model_reasoning_effort="%s"' % s["effort"], "--disable", "plugins"]
        for name in self.mcp_servers():
            key = name if re.match(r"^[A-Za-z0-9_-]+$", name) else json.dumps(name)
            args += ["-c", "mcp_servers.%s.enabled=false" % key]
        args += ["-c", 'sandbox_mode="workspace-write"',
                 "-c", "sandbox_workspace_write.writable_roots=%s" % json.dumps(s["writable_roots"]),
                 # A read-only role loses /tmp and $TMPDIR, which are writable by default: a project
                 # under /tmp would otherwise be writable to it.
                 "-c", "sandbox_workspace_write.exclude_slash_tmp=%s" % ("true" if s["kind"] == "readonly" else "false"),
                 "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=%s" % ("true" if s["kind"] == "readonly" else "false"),
                 "-c", "sandbox_workspace_write.network_access=%s" % ("true" if s["network"] else "false"),
                 "--skip-git-repo-check", "--json"]
        # The guard is passed for this run only, so it needs no trust review and touches no
        # config. The flag also runs the user's other enabled hooks without their review.
        guard = "%s %s %s" % (shlex.quote(sys.executable), shlex.quote(GUARD), shlex.quote(s["guard_context"]))
        args += ["-c", "hooks.PreToolUse=[{matcher=\".*\",hooks=[{type=\"command\",command=%s,timeout=15}]}]" % json.dumps(guard),
                 "--dangerously-bypass-hook-trust"]
        return args

    def run_command(self, s, last_message):
        return [self.binary, "exec"] + self.settings(s) + ["-C", s["cwd"], "-o", last_message, "-"]

    def resume_command(self, s, last_message, message):
        return [self.binary, "exec", "resume", s["session_id"]] + self.settings(s) + ["-o", last_message, message]

    def read_events(self, path):
        """(session id, token usage) from the JSON event stream."""
        session, usage = None, None
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("type") == "thread.started" and event.get("thread_id"):
                        session = str(event["thread_id"])
                    if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
                        usage = event["usage"]
        except OSError:
            pass
        return session, usage


class Agy:
    """The Antigravity CLI, which runs Gemini models. agy mode is specified in docs/prd-conduct-codex-agy.md and not built yet."""
    name = "agy"


CLIS = {"codex": Codex, "agy": Agy}


# ---------------------------------------------------------------- the task

def locate(task_arg):
    """The paths a run needs, worked out from the task spec's path."""
    spec = os.path.realpath(task_arg)
    m = TASK_PATH.match(spec)
    if not m or not os.path.isfile(spec):
        raise Fail("`%s` is not a task spec under `.orchestra/<work>/tasks/` whose name starts with a task ID such as T01." % task_arg)
    project, unit, tid = m.group("project"), os.path.join(m.group("project"), c.WORKDIR, m.group("work")), m.group("id")
    report = None
    body = c.section_body(c.read_text(spec), "Report") or ""
    hit = re.search(r"`([^`]*reports/[^`]+\.md)`", body)
    if hit:
        report = os.path.realpath(os.path.join(project, hit.group(1)))
        if not report.startswith(os.path.join(unit, "reports") + os.sep):
            raise Fail("The report path in the task spec, `%s`, is not under this work folder's `reports/`." % hit.group(1))
    report = report or os.path.join(unit, "reports", tid + ".md")
    files = os.path.join(unit, "files", tid)
    return {"spec": spec, "project": project, "unit": unit, "task": tid, "report": report,
            "files": files, "record": os.path.join(files, "cli")}


def brief_settings(unit):
    """The brief's pre-approved keys, and whether the workers may use the network (open unless blocked)."""
    text = c.read_text(os.path.join(unit, "brief.md"))
    keys = sorted(c.preapproved_keys(text))
    m = NETWORK_LINE.search(c.section_body(text, "Constraints") or "")
    return keys, not (m and m.group(1).lower() == "blocked")


def guard_refusals(path, since):
    """What the guard refused since a moment, as (tool, what, reason) tuples."""
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("at", 0) >= since and not r.get("fault"):
                    out.append((r.get("tool"), r.get("command") or ", ".join(r.get("paths") or []), r.get("reason", "")))
    except OSError:
        pass
    return out


def scope_paths(spec, project):
    """Paths named under `## Scope and limits`, relative to the project. A heuristic: it reads backticked text."""
    body = c.section_body(c.read_text(spec), "Scope and limits") or ""
    found = []
    for token in re.findall(r"`([^`\n]+)`", body):
        token = token.strip().rstrip("/")
        if not token or " " in token or token.startswith("-"):
            continue
        path = os.path.normpath(token if not os.path.isabs(token) else os.path.relpath(token, project))
        if not path.startswith(".."):
            found.append(path)
    return found


def in_scope(rel, scope):
    return any(rel == p or rel.startswith(p + os.sep) for p in scope)


# ---------------------------------------------------------------- what changed

def snapshot(project):
    """Every file of the project with its size and modification time, tool caches left out."""
    files = {}
    for top, dirs, names in os.walk(project):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            path = os.path.join(top, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            files[os.path.relpath(path, project)] = (st.st_size, st.st_mtime_ns)
    return files


def git_state(project):
    """HEAD, the branch and the staged changes, or None outside a repository."""
    if not os.path.isdir(os.path.join(project, ".git")):
        return None
    out = []
    for cmd in (["rev-parse", "HEAD"], ["symbolic-ref", "-q", "HEAD"], ["diff", "--cached", "--name-only"], ["stash", "list"]):
        try:
            proc = subprocess.run(["git", "-C", project] + cmd, capture_output=True, timeout=30)
            out.append(proc.stdout.decode("utf-8", "replace").strip())
        except (OSError, subprocess.SubprocessError):
            out.append("?")
    return out


def changed(before, after):
    return sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))


# ---------------------------------------------------------------- one run

def execute(cli, cmd, cwd, stdin, paths, round_no):
    record = paths["record"]
    events = os.path.join(record, "events-%d.jsonl" % round_no)
    errors = os.path.join(record, "stderr-%d.txt" % round_no)
    before, git_before = snapshot(paths["project"]), git_state(paths["project"])
    started = time.time()
    try:
        with open(events, "wb") as out, open(errors, "wb") as err:
            proc = subprocess.run(cmd, cwd=cwd, input=stdin, stdout=out, stderr=err)
        code = proc.returncode
    except OSError as e:
        raise Fail("`%s` could not be started: %s" % (cmd[0], e))
    elapsed = time.time() - started
    after, git_after = snapshot(paths["project"]), git_state(paths["project"])
    paths["started"] = started
    return code, elapsed, events, errors, changed(before, after), git_before != git_after


def report_lines(cli, s, paths, code, elapsed, events, errors, changes, git_changed):
    """The lines the conductor reads, and whether any of them is a warning."""
    session, usage = cli.read_events(events)
    if session:
        s["session_id"] = session
    rel = lambda p: os.path.relpath(p, paths["project"])  # noqa: E731
    own = (paths["files"] + os.sep, )
    lines, warnings = [], []
    tokens = ""
    if usage:
        tokens = " | tokens in %s (cached %s), out %s" % (
            format(usage.get("input_tokens", 0), ","), format(usage.get("cached_input_tokens", 0), ","),
            format(usage.get("output_tokens", 0), ","))
    lines.append("TASK %s | %s %s %s | %ds%s" % (paths["task"], cli.name, s["model"], s["effort"], round(elapsed), tokens))
    lines.append("SESSION: %s" % (session or "unknown"))

    fields, _stray = check_report.parse(c.read_text(paths["last_message"]))
    status = (fields.get("STATUS") or "").split()[0].upper() if fields.get("STATUS") else ""
    lines.append("STATUS: %s" % (status or "none in the last message"))
    if status == "BLOCKED" and fields.get("QUESTION"):
        lines.append("QUESTION: %s" % fields["QUESTION"])

    if code != 0:
        warnings.append("The CLI exited with %d. Its error output is in `%s`." % (code, rel(errors)))
        tail = c.read_text(errors)[-2000:]
        if LIMIT_WORDS.search(tail) or LIMIT_WORDS.search(c.read_text(events)[-4000:]):
            warnings.append("The CLI's output mentions a usage limit. Stop this task and tell the user.")

    work = [p for p in changes if not os.path.join(paths["project"], p).startswith(own)
            and os.path.join(paths["project"], p) != paths["report"]]
    refused = guard_refusals(os.path.join(paths["record"], "guard.jsonl"), paths.get("started", 0))
    for tool, what, reason in refused[:10]:
        lines.append("GUARD: refused %s `%s`: %s" % (tool, what[:120], reason.split(". ")[0]))
    if len(refused) > 10:
        lines.append("GUARD: and %d more refusals, in `%s`" % (len(refused) - 10, rel(os.path.join(paths["record"], "guard.jsonl"))))
    lines.append("CHANGED: %s" % (", ".join(work[:30]) + (" and %d more" % (len(work) - 30) if len(work) > 30 else "") if work else "nothing"))

    scope = scope_paths(paths["spec"], paths["project"])
    orchestra = [p for p in work if p.split(os.sep)[0] == c.WORKDIR]
    project_files = [p for p in work if p.split(os.sep)[0] != c.WORKDIR]
    if orchestra:
        warnings.append("Changed under `.orchestra/` outside the task's report and `files/%s/`: %s" % (paths["task"], ", ".join(orchestra)))
    if not scope:
        if project_files:
            warnings.append("No paths found under `## Scope and limits`, so the changed files were not compared with the scope. Compare them by hand.")
    else:
        outside = [p for p in project_files if not in_scope(p, scope)]
        if outside:
            warnings.append("Changed outside the paths under `## Scope and limits`: %s" % ", ".join(outside))
    if s["kind"] == "readonly" and project_files:
        warnings.append("A read-only role changed project files: %s" % ", ".join(project_files))
    if git_changed:
        warnings.append("Git state changed during the run: HEAD, the branch, the index or the stash.")

    if not os.path.isfile(paths["report"]):
        warnings.append("No report at `%s`." % rel(paths["report"]))
    elif c.read_status(paths["report"]) == "DONE":
        problems = check_report.verification_problems(paths["report"])
        if problems:
            warnings.append("The report says DONE, but its verification record does not pass: %s" % " ".join(problems))
    lines.append("RECORD: %s" % rel(paths["record"]))
    lines += ["WARN: " + w for w in warnings]
    return lines, bool(warnings)


def save(path, s):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)


def cmd_run(args):
    if args.cli not in CLIS:
        raise Fail("Unknown CLI `%s`. Known: %s" % (args.cli, ", ".join(CLIS)))
    cli = CLIS[args.cli]()
    if not isinstance(cli, Codex):
        raise Fail("The %s CLI is not supported yet. See docs/prd-conduct-codex-agy.md." % args.cli)
    if args.role not in ROLES:
        raise Fail("Unknown role `%s`. Roles: %s" % (args.role, ", ".join(ROLES)))
    effort = args.effort or cli.default_effort[args.role]
    if effort not in cli.efforts:
        raise Fail("Unknown effort `%s` for %s. Levels: %s" % (effort, cli.name, ", ".join(cli.efforts)))
    if not shutil.which(cli.binary):
        raise Fail("`%s` is not on the PATH." % cli.binary)
    paths = locate(args.task)
    os.makedirs(paths["record"], exist_ok=True)
    os.makedirs(os.path.dirname(paths["report"]), exist_ok=True)
    kind = ROLES[args.role]["kind"]
    if kind == "impl":
        cwd, roots = paths["project"], []
    else:
        cwd, roots = paths["files"], [os.path.dirname(paths["report"])]
    keys, network = brief_settings(paths["unit"])
    context = os.path.join(paths["record"], "guard.json")
    with open(context, "w", encoding="utf-8") as f:
        json.dump({"kind": kind, "project": paths["project"], "unit": paths["unit"], "task": paths["task"],
                   "keys": keys, "log": os.path.join(paths["record"], "guard.jsonl")}, f, indent=2)
    s = {"cli": cli.name, "role": args.role, "kind": kind, "model": cli.models[args.role], "effort": effort,
         "network": network, "keys": keys, "cwd": cwd, "writable_roots": roots, "guard_context": context,
         "session_id": None, "rounds": 1}
    paths["last_message"] = os.path.join(paths["record"], "last-message-1.md")
    prompt = PROMPT.format(role=os.path.join(ROLES_DIR, args.role + ".md"), spec=paths["spec"], report=paths["report"])
    cmd = cli.run_command(s, paths["last_message"])
    code, elapsed, events, errors, changes, git_changed = execute(cli, cmd, cwd, prompt.encode("utf-8"), paths, 1)
    lines, warned = report_lines(cli, s, paths, code, elapsed, events, errors, changes, git_changed)
    save(os.path.join(paths["record"], "run.json"), s)
    print("\n".join(lines))
    return 1 if code != 0 else (2 if warned else 0)


def cmd_resume(args):
    paths = locate(args.task)
    try:
        with open(os.path.join(paths["record"], "run.json"), encoding="utf-8") as f:
            s = json.load(f)
    except (OSError, ValueError):
        raise Fail("No earlier run of %s to resume. Start one with `run`." % paths["task"])
    if not s.get("session_id"):
        raise Fail("The earlier run of %s recorded no session id, so it cannot be resumed. Start a new run." % paths["task"])
    message = c.read_text(args.message).strip()
    if not message:
        raise Fail("The message file `%s` is empty or cannot be read." % args.message)
    cli = CLIS[s["cli"]]()
    # The brief may have changed since the first run, for example with a key the user approved.
    s["keys"], s["network"] = brief_settings(paths["unit"])
    context = s.get("guard_context") or os.path.join(paths["record"], "guard.json")
    s["guard_context"] = context
    with open(context, "w", encoding="utf-8") as f:
        json.dump({"kind": s["kind"], "project": paths["project"], "unit": paths["unit"], "task": paths["task"],
                   "keys": s["keys"], "log": os.path.join(paths["record"], "guard.jsonl")}, f, indent=2)
    s["rounds"] = int(s.get("rounds", 1)) + 1
    paths["last_message"] = os.path.join(paths["record"], "last-message-%d.md" % s["rounds"])
    cmd = cli.resume_command(s, paths["last_message"], message)
    code, elapsed, events, errors, changes, git_changed = execute(cli, cmd, s["cwd"], b"", paths, s["rounds"])
    lines, warned = report_lines(cli, s, paths, code, elapsed, events, errors, changes, git_changed)
    save(os.path.join(paths["record"], "run.json"), s)
    print("\n".join(lines))
    return 1 if code != 0 else (2 if warned else 0)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run a task on a coding CLI for the conductor, and check the run.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="start a fresh session on a task spec")
    run.add_argument("--cli", required=True)
    run.add_argument("--task", required=True)
    run.add_argument("--role", required=True)
    run.add_argument("--effort")
    resume = sub.add_parser("resume", help="continue the task's last session with a review comment")
    resume.add_argument("--task", required=True)
    resume.add_argument("--message", required=True, help="a file with the review comment")
    args = parser.parse_args(argv)
    try:
        return cmd_run(args) if args.command == "run" else cmd_resume(args)
    except Fail as e:
        print("ERROR: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
