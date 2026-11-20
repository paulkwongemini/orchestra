#!/usr/bin/env python3
"""Run a task on the Codex CLI for a conductor in a CLI mode, and check the run.

    worker.py run    --cli codex --task <task spec> --role <role> [--effort <level>]
    worker.py resume --task <task spec> --message <file>
    worker.py watch  --task <task spec> [--round N] [--no-follow] [--calls]

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

`watch` is for the user, who follows a run in a terminal of their own while it goes: the
worker's messages, its commands and edits from the CLI's event stream, and the guard's refusals
as they happen. With `--calls`, the commands and edits come from the guard's log of every call
instead, with their times. The conductor does not run it.

Exit codes: 0 the run finished and nothing was flagged, 2 the run finished with warnings,
1 the run did not finish or could not start.
"""
import argparse
import collections
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

# `engineer` and `analyst` start at `high`, `implementer` and `writer` at `medium`. For GPT-6.2 that
# is one level above the guides' starting point for the latter two.
DEFAULT_EFFORT = {"engineer": "high", "analyst": "high", "implementer": "medium", "writer": "medium"}

GUARD = os.path.join(ROOT, "scripts", "worker_guard.py")
NETWORK_LINE = re.compile(r"^\W*worker network\W*[:：]\s*(blocked|open)\b", re.I | re.M)

# Folders whose changes say nothing about the work: tool caches and the like.
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


def guard_command(s):
    """The shell command with which a CLI runs the guard for this run."""
    return " ".join(shlex.quote(part) for part in (sys.executable, GUARD, s["guard_context"]))


def toml_bool(value):
    return "true" if value else "false"


# ---------------------------------------------------------------- the CLIs

class Codex:
    name = "codex"
    binary = "codex"
    efforts = ("low", "medium", "high", "xhigh", "max", "ultra")
    models = {"engineer": "gpt-6.2-astra", "analyst": "gpt-6.2-astra",
              "implementer": "gpt-6.2-sol", "writer": "gpt-6.2-sol"}
    default_effort = DEFAULT_EFFORT

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
        # A read-only role loses /tmp and $TMPDIR, which are writable by default: a project under
        # /tmp would otherwise be writable to it.
        no_tmp = toml_bool(s["kind"] == "readonly")
        args += ["-c", 'sandbox_mode="workspace-write"',
                 "-c", "sandbox_workspace_write.writable_roots=%s" % json.dumps(s["writable_roots"]),
                 "-c", "sandbox_workspace_write.exclude_slash_tmp=%s" % no_tmp,
                 "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=%s" % no_tmp,
                 "-c", "sandbox_workspace_write.network_access=%s" % toml_bool(s["network"]),
                 "--skip-git-repo-check", "--json"]
        # The guard is passed for this run only, so it needs no trust review and touches no
        # config. The flag also runs the user's other enabled hooks without their review.
        args += ["-c", "hooks.PreToolUse=[{matcher=\".*\",hooks=[{type=\"command\",command=%s,timeout=15}]}]" % json.dumps(guard_command(s)),
                 "--dangerously-bypass-hook-trust"]
        return args

    def prepare(self, s, paths):
        """Where the run works and what it may write, by the role's kind."""
        if s["kind"] == "impl":
            s["cwd"], s["writable_roots"] = paths["project"], []
        else:
            s["cwd"], s["writable_roots"] = paths["files"], [os.path.dirname(paths["report"])]

    def run_command(self, s, last_message, prompt):
        """The command and its stdin. The prompt goes in on stdin, so the CLI does not wait for more."""
        return [self.binary, "exec"] + self.settings(s) + ["-C", s["cwd"], "-o", last_message, "-"], prompt.encode("utf-8")

    def resume_command(self, s, last_message, message):
        return [self.binary, "exec", "resume", s["session_id"]] + self.settings(s) + ["-o", last_message, message], b""

    def after_run(self, events, last_message):
        """Codex writes the last message itself, with -o."""

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


CLIS = {"codex": Codex}


# ---------------------------------------------------------------- the task

def locate(task_arg):
    """The paths a run needs, worked out from the task spec's path."""
    spec = os.path.realpath(task_arg)
    m = TASK_PATH.match(spec)
    if not m or not os.path.isfile(spec):
        raise Fail("`%s` is not a task spec under `.orchestra/<work>/tasks/` whose name starts with a task ID such as T01." % task_arg)
    project, work_folder, tid = m.group("project"), os.path.join(m.group("project"), c.ORCHESTRA_DIR, m.group("work")), m.group("id")
    report = None
    body = c.section_body(c.read_text(spec), "Report") or ""
    hit = re.search(r"`([^`]*reports/[^`]+\.md)`", body)
    if hit:
        report = os.path.realpath(os.path.join(project, hit.group(1)))
        if not report.startswith(os.path.join(work_folder, "reports") + os.sep):
            raise Fail("The report path in the task spec, `%s`, is not under this work folder's `reports/`." % hit.group(1))
    report = report or os.path.join(work_folder, "reports", tid + ".md")
    files = os.path.join(work_folder, "files", tid)
    return {"spec": spec, "project": project, "work_folder": work_folder, "task": tid, "report": report,
            "files": files, "record": os.path.join(files, "cli")}


def brief_settings(work_folder):
    """The brief's pre-approved keys, and whether the workers may use the network (open unless blocked)."""
    text = c.read_text(os.path.join(work_folder, "brief.md"))
    keys = sorted(c.preapproved_keys(text))
    m = NETWORK_LINE.search(c.section_body(text, "Constraints") or "")
    return keys, not (m and m.group(1).lower() == "blocked")


def guard_refusals(path, since):
    """The guard's records of what it refused since a moment."""
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("at", 0) >= since and not r.get("fault"):
                    out.append(r)
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


def short(text, width=160):
    text = " ".join(str(text or "").split())
    return text if len(text) <= width else text[:width - 1] + "…"


def called(r):
    """The command or the paths of a call in the guard's log."""
    return r.get("command") or ", ".join(r.get("paths") or [])


def refusal(r, width):
    """One refusal from the guard's log: the call and the first sentence of the reason."""
    return "refused %s `%s`: %s" % (r.get("tool"), short(called(r), width), (r.get("reason") or "").split(". ")[0])


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

Outcome = collections.namedtuple("Outcome", "code elapsed started events errors changes git_changed")


def execute(cli, cmd, cwd, stdin, paths, round_no):
    """Run the CLI once, and see what the run changed."""
    record = paths["record"]
    events = os.path.join(record, "events-%d.jsonl" % round_no)
    errors = os.path.join(record, "stderr-%d.txt" % round_no)
    before, git_before = snapshot(paths["project"]), git_state(paths["project"])
    started = time.time()
    save(os.path.join(record, "live.json"), {"round": round_no, "started": started, "cli": cli.name})  # for `watch`
    try:
        with open(events, "wb") as out, open(errors, "wb") as err:
            proc = subprocess.run(cmd, cwd=cwd, input=stdin, stdout=out, stderr=err)
        code = proc.returncode
    except OSError as e:
        raise Fail("`%s` could not be started: %s" % (cmd[0], e))
    elapsed = time.time() - started
    after, git_after = snapshot(paths["project"]), git_state(paths["project"])
    return Outcome(code, elapsed, started, events, errors, changed(before, after), git_before != git_after)


def report_lines(cli, s, paths, outcome):
    """The lines the conductor reads, and whether any of them is a warning."""
    code, elapsed, events, errors = outcome.code, outcome.elapsed, outcome.events, outcome.errors
    session, usage = cli.read_events(events)
    if session:
        s["session_id"] = session

    def rel(path):
        return os.path.relpath(path, paths["project"])

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

    work = [p for p in outcome.changes if not os.path.join(paths["project"], p).startswith(own)
            and os.path.join(paths["project"], p) != paths["report"]]
    refused = guard_refusals(os.path.join(paths["record"], "guard.jsonl"), outcome.started)
    for r in refused[:10]:
        lines.append("GUARD: " + refusal(r, 120))
    if len(refused) > 10:
        lines.append("GUARD: and %d more refusals, in `%s`" % (len(refused) - 10, rel(os.path.join(paths["record"], "guard.jsonl"))))
    lines.append("CHANGED: %s" % (", ".join(work[:30]) + (" and %d more" % (len(work) - 30) if len(work) > 30 else "") if work else "nothing"))

    scope = scope_paths(paths["spec"], paths["project"])
    orchestra = [p for p in work if p.split(os.sep)[0] == c.ORCHESTRA_DIR]
    project_files = [p for p in work if p.split(os.sep)[0] != c.ORCHESTRA_DIR]
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
    if outcome.git_changed:
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


def write_guard_context(s, paths):
    """The guard's view of the run. Written before each run, so a key the user approved meanwhile counts."""
    with open(s["guard_context"], "w", encoding="utf-8") as f:
        json.dump({"kind": s["kind"], "project": paths["project"], "work_folder": paths["work_folder"], "task": paths["task"],
                   "keys": s["keys"], "log": os.path.join(paths["record"], "guard.jsonl"),
                   "activity": os.path.join(paths["record"], "activity.jsonl")}, f, indent=2)


def launch(cli, s, paths, command):
    """Run round `s["rounds"]` of a task and report on it.

    `command` builds the CLI's command and its stdin from the path the last message is written to.
    """
    write_guard_context(s, paths)
    cli.prepare(s, paths)
    paths["last_message"] = os.path.join(paths["record"], "last-message-%d.md" % s["rounds"])
    cmd, stdin = command(paths["last_message"])
    outcome = execute(cli, cmd, s["cwd"], stdin, paths, s["rounds"])
    cli.after_run(outcome.events, paths["last_message"])
    lines, warned = report_lines(cli, s, paths, outcome)
    save(os.path.join(paths["record"], "run.json"), s)
    print("\n".join(lines))
    return 1 if outcome.code != 0 else (2 if warned else 0)


def cmd_run(args):
    if args.cli not in CLIS:
        raise Fail("Unknown CLI `%s`. Known: %s" % (args.cli, ", ".join(CLIS)))
    cli = CLIS[args.cli]()
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
    keys, network = brief_settings(paths["work_folder"])
    s = {"cli": cli.name, "role": args.role, "kind": ROLES[args.role]["kind"], "model": cli.models[args.role],
         "effort": effort, "network": network, "keys": keys,
         "guard_context": os.path.join(paths["record"], "guard.json"), "session_id": None, "rounds": 1}
    prompt = PROMPT.format(role=os.path.join(ROLES_DIR, args.role + ".md"), spec=paths["spec"], report=paths["report"])
    return launch(cli, s, paths, lambda last_message: cli.run_command(s, last_message, prompt))


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
    s["keys"], s["network"] = brief_settings(paths["work_folder"])
    s["guard_context"] = s.get("guard_context") or os.path.join(paths["record"], "guard.json")
    s["rounds"] = int(s.get("rounds", 1)) + 1
    return launch(cli, s, paths, lambda last_message: cli.resume_command(s, last_message, message))


# ---------------------------------------------------------------- following a run

BASH_WRAP = re.compile(r"^\S*\b(?:ba|z)?sh -l?c (['\"])(.*)\1$", re.S)


class EventReader:
    """Turns a run's event stream into lines for a person to follow."""

    def lines(self, event):
        if not isinstance(event, dict):
            return []
        return self.codex(event)

    def codex(self, e):
        kind, item = e.get("type"), e.get("item") if isinstance(e.get("item"), dict) else {}
        it = item.get("type")
        if kind == "thread.started":
            return ["start  session %s" % e.get("thread_id")]
        if kind == "turn.completed":
            u = e.get("usage") or {}
            return ["end    tokens in %s, out %s" % (format(u.get("input_tokens", 0), ","), format(u.get("output_tokens", 0), ","))]
        if kind in ("turn.failed", "error"):
            return ["ERROR  %s" % short(json.dumps(e.get("error") or e.get("message") or e, ensure_ascii=False))]
        if kind == "item.started" and it == "command_execution":
            m = BASH_WRAP.match(item.get("command") or "")
            return ["$      %s" % short(m.group(2) if m else item.get("command"))]
        if kind != "item.completed":
            return []
        if it == "agent_message":
            return ["say    %s" % short(item.get("text"), 400)]
        if it == "reasoning":
            return ["think  %s" % short(item.get("text"), 200)]
        if it == "command_execution" and item.get("exit_code") not in (0, None):
            return ["       exit %s" % item.get("exit_code")]
        if it == "file_change":
            return ["edit   %s" % ", ".join("%s %s" % (ch.get("kind", ""), ch.get("path", "")) for ch in item.get("changes") or [])]
        if it == "todo_list":
            return ["plan   %s" % "; ".join(("[x] " if t.get("completed") else "[ ] ") + short(t.get("text"), 60) for t in item.get("items") or [])]
        if it in ("mcp_tool_call", "web_search"):
            return ["tool   %s %s" % (it, short(item.get("query") or item.get("tool") or "", 120))]
        return []


def activity_line(r):
    return "%-6s %s %s" % ("GUARD" if r.get("refused") else "call", r.get("tool"), short(called(r)))


class Tail:
    """New complete lines of a file, as JSON, each time it is read."""

    def __init__(self, path):
        self.path, self.pos, self.rest = path, 0, ""

    def read(self):
        try:
            with open(self.path, encoding="utf-8", errors="replace") as f:
                f.seek(self.pos)
                chunk = f.read()
                self.pos = f.tell()
        except OSError:
            return []
        parts = (self.rest + chunk).split("\n")
        self.rest = parts.pop()
        out = []
        for line in parts:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out


def latest_round(record):
    """The number of the newest round that has an event stream, or None."""
    try:
        names = os.listdir(record)
    except OSError:
        return None
    rounds = [int(m.group(1)) for m in (re.match(r"events-(\d+)\.jsonl$", name) for name in names) if m]
    return max(rounds) if rounds else None


def run_ended(record, round_no, since):
    """The script writes run.json when a run is over."""
    try:
        if os.path.getmtime(os.path.join(record, "run.json")) < since:
            return False
        with open(os.path.join(record, "run.json"), encoding="utf-8") as f:
            return int(json.load(f).get("rounds", 1)) >= round_no
    except (OSError, ValueError):
        return False


def cmd_watch(args):
    """Follow a run for the user. The conductor does not run this: it waits for the run's report."""
    paths = locate(args.task)
    record, out = paths["record"], sys.stdout
    round_no = args.round
    while round_no is None:
        round_no = latest_round(record)
        if round_no is None:
            if not args.follow:
                raise Fail("No run of %s has started yet." % paths["task"])
            time.sleep(1)
    events_path = os.path.join(record, "events-%d.jsonl" % round_no)
    try:
        with open(os.path.join(record, "live.json"), encoding="utf-8") as f:
            live = json.load(f)
        round_start = float(live["started"]) if int(live.get("round", 0)) == round_no else 0
    except (OSError, ValueError, KeyError, TypeError):
        round_start = 0
    events, guard = Tail(events_path), Tail(os.path.join(record, "activity.jsonl" if args.calls else "guard.jsonl"))
    reader = EventReader()
    out.write("%s %s, round %d.%s\n" % ("Following" if args.follow else "Showing", paths["task"], round_no, " Ctrl-C to stop." if args.follow else ""))

    def local(line):
        return line.replace(paths["project"] + os.sep, "")

    while True:
        now = time.strftime("%H:%M:%S")
        for r in guard.read():
            if r.get("fault") or r.get("at", 0) < round_start:
                continue
            stamp = time.strftime("%H:%M:%S", time.localtime(r["at"]))
            out.write("%s  %s\n" % (stamp, local(activity_line(r) if args.calls else "GUARD  " + refusal(r, 100))))
        for e in events.read():
            for line in reader.lines(e):
                if args.calls and line.startswith(("$", "edit ", "tool ")):
                    continue  # the guard's log already shows the calls
                out.write("%s  %s\n" % (now if args.follow else "--:--:--", local(line)))
        out.flush()
        if not args.follow:
            return 0
        if run_ended(record, round_no, round_start):
            out.write("The run is over. The conductor reviews it before anyone reads the report.\n")
            return 0
        time.sleep(1)


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
    watch = sub.add_parser("watch", help="follow a run as it goes, for the user")
    watch.add_argument("--task", required=True)
    watch.add_argument("--round", type=int, help="the round to show; the latest by default")
    watch.add_argument("--no-follow", dest="follow", action="store_false", help="print what is there and stop")
    watch.add_argument("--calls", action="store_true", help="show every call as the guard saw it, with its time")
    args = parser.parse_args(argv)
    try:
        if args.command == "watch":
            try:
                return cmd_watch(args)
            except KeyboardInterrupt:
                return 0
        return cmd_run(args) if args.command == "run" else cmd_resume(args)
    except Fail as e:
        print("ERROR: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
