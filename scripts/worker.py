#!/usr/bin/env python3
"""Run a task on the Codex CLI for a conductor in a CLI mode, and check the run.

    worker.py run    --cli codex --task <task spec> --role <role> [--effort <level>]
    worker.py resume --task <task spec> --message <file>
    worker.py watch  --task <task spec> [--round N] [--no-follow] [--calls]
    worker.py board  [--project DIR] [--once WIDTHxHEIGHT] [--window N]

`run` starts a fresh session on the task spec. `resume` continues the task's last session with
the conductor's review comment, read from a file, with the same settings: a resumed Codex run
does not keep the sandbox options of the first run, so they are passed again.

This is a tool the conductor runs. It builds the command so that the settings are right every
time. The network is always open. A PreToolUse hook, `worker_guard.py`, goes with every run and refuses
what the claude mode's hooks refuse a Claude subagent, with the brief's pre-approved keys. The
script keeps the run's record under `files/<task>/cli/`, and after the run prints a few lines:
the status, the time, the tokens, the changed files, what the guard refused, and a warning for
each point that a check after the run can see:

  - a file changed outside the paths under the task spec's `## Scope and limits`
  - a change under `.orchestra/` other than the task's report and its `files/<task>/` folder
  - a line naming `.orchestra/` added to a project file, which then depends on a git-ignored folder
  - a change to git state
  - a `DONE` report without a valid verification record, or no report at all

Runs in one project share its files. When others went at the same time, their records and
reports are left out, and a changed file goes to the run whose guard saw it edited, or else to
the run whose scope holds it; the `ALONGSIDE:` line names them and what was put down to them.

The report's text is never printed, so the conductor can still review before it reads.

`watch` is for the user, who follows a run in a terminal of their own while it goes: the
worker's messages, its commands and edits from the CLI's event stream, and the guard's refusals
as they happen. The Codex subagents the worker starts are not in that stream: their lines come
from their own session files, as each call ends, marked with the subagent's name. With
`--calls`, the commands and edits come from the guard's log of every call instead, with their
times, and a subagent's call is marked with its name. The conductor does not run it.

Exit codes: 0 the run finished and nothing was flagged, 2 the run finished with warnings,
1 the run did not finish or could not start.
"""
import argparse
import collections
import glob
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

# Folders whose changes say nothing about the work: tool caches and the like.
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "node_modules", ".venv", "venv"}

# A project file that names this depends on a folder that git ignores and only this machine has.
ORCHESTRA_REF = b".orchestra/"
# Larger files are not read for it: they are data or build output, not code a worker writes.
REF_MAX_BYTES = 1 << 20

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
        # Codex's own tools a worker has no use for: the ChatGPT app connectors, which act on the
        # user's account, the goals, which persist in the user's Codex state, and image generation.
        for feature in ("apps", "goals", "image_generation"):
            args += ["--disable", feature]
        for name in self.mcp_servers():
            key = name if re.match(r"^[A-Za-z0-9_-]+$", name) else json.dumps(name)
            args += ["-c", "mcp_servers.%s.enabled=false" % key]
        # A read-only role loses /tmp and $TMPDIR, which are writable by default: a project under
        # /tmp would otherwise be writable to it. The network is always open, set here so that the
        # user's config does not decide it.
        no_tmp = toml_bool(s["kind"] == "readonly")
        args += ["-c", 'sandbox_mode="workspace-write"',
                 "-c", "sandbox_workspace_write.writable_roots=%s" % json.dumps(s["writable_roots"]),
                 "-c", "sandbox_workspace_write.exclude_slash_tmp=%s" % no_tmp,
                 "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=%s" % no_tmp,
                 "-c", "sandbox_workspace_write.network_access=true",
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


def brief_keys(work_folder):
    """The brief's pre-approved keys."""
    return sorted(c.preapproved_keys(c.read_text(os.path.join(work_folder, "brief.md"))))


def guard_refusals(path, since):
    """The guard's records of what it refused since a moment."""
    return [r for r in guard_rows(path) if r.get("at", 0) >= since and not r.get("fault")]


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


def edited_paths(record, since, project):
    """The project paths a run edited with apply_patch since a moment, its subagents included, from its guard's log."""
    out = set()
    for r in guard_rows(os.path.join(record, "activity.jsonl")):
        if r.get("at", 0) >= since:
            out.update(os.path.relpath(p, project) for p in r.get("paths") or [] if c.inside(p, project))
    return out


def guard_rows(path):
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict):
                    yield r
    except OSError:
        return


def alongside(paths, start, end):
    """The project's other runs that were in progress at some time between `start` and `end`.

    Runs in one project share its files, so the files that changed during a run may be another
    run's work. Each comes with what tells its changes apart: its own record folder and report,
    the paths it edited, and the paths of its scope.
    """
    out = []
    for live_path in glob.glob(os.path.join(paths["project"], c.ORCHESTRA_DIR, "*", "files", "*", "cli", "live.json")):
        record = os.path.dirname(live_path)
        if os.path.realpath(record) == os.path.realpath(paths["record"]):
            continue
        try:
            with open(live_path, encoding="utf-8") as f:
                live = json.load(f)
            started = float(live.get("started") or 0)
        except (OSError, ValueError, TypeError):
            continue
        if not started or started >= end:
            continue
        try:  # run.json is written when a round ends
            ended = os.path.getmtime(os.path.join(record, "run.json"))
            with open(os.path.join(record, "run.json"), encoding="utf-8") as f:
                if ended < started or int(json.load(f).get("rounds", 1)) < int(live.get("round", 1)):
                    ended = None
        except (OSError, ValueError, TypeError):
            ended = None
        if ended is not None and ended <= start:
            continue
        files = os.path.dirname(record)
        task, work_folder = os.path.basename(files), os.path.dirname(os.path.dirname(files))
        spec = live.get("spec") or next(iter(sorted(glob.glob(os.path.join(work_folder, "tasks", task + "*.md")))), None)
        out.append({"task": task, "files": files, "report": live.get("report") or os.path.join(work_folder, "reports", task + ".md"),
                    "scope": scope_paths(spec, paths["project"]) if spec and os.path.isfile(spec) else [],
                    "edits": edited_paths(record, started, paths["project"])})
    return sorted(out, key=lambda r: r["task"])


def put_down(changes, mine, scope, others):
    """The changes that are this run's, and those put down to the runs alongside it, by task.

    A path this run edited is its own. Otherwise a path another run edited is that run's, then a
    path in this run's scope is this run's, then a path in another run's scope is that run's.
    Whatever is left is this run's, so a change in nobody's scope is still flagged here.
    """
    own, theirs = [], collections.OrderedDict()

    def owner(p):
        if p in mine:
            return None
        for o in others:
            if p in o["edits"]:
                return o["task"]
        if scope and in_scope(p, scope):
            return None
        for o in others:
            if o["scope"] and in_scope(p, o["scope"]):
                return o["task"]
        return None

    for p in changes:
        task = owner(p)
        if task:
            theirs.setdefault(task, []).append(p)
        else:
            own.append(p)
    return own, theirs


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


def reference_lines(path):
    """The lines of a text file that name `.orchestra/`, as (line number, stripped line)."""
    try:
        if os.path.getsize(path) > REF_MAX_BYTES:
            return []
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return []
    if ORCHESTRA_REF not in data or b"\0" in data:
        return []
    return [(n, line.strip()) for n, line in enumerate(data.splitlines(), 1) if ORCHESTRA_REF in line]


def references(project):
    """Each project file outside `.orchestra/` with the lines in it that name `.orchestra/`."""
    refs = {}
    for top, dirs, names in os.walk(project):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not (top == project and d == c.ORCHESTRA_DIR)]
        for name in names:
            path = os.path.join(top, name)
            found = reference_lines(path)
            if found:
                refs[os.path.relpath(path, project)] = collections.Counter(line for _n, line in found)
    return refs


def added_references(project, files, before):
    """`path:line` for each line naming `.orchestra/` that a file has more of than it had before the run."""
    added = []
    for rel in files:
        left = collections.Counter(before.get(rel, {}))
        for n, line in reference_lines(os.path.join(project, rel)):
            if left[line] > 0:
                left[line] -= 1
            else:
                added.append("%s:%d" % (rel, n))
    return added


def changed(before, after):
    return sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))


# ---------------------------------------------------------------- one run

Outcome = collections.namedtuple("Outcome", "code elapsed started events errors changes git_changed references")


def execute(cli, cmd, cwd, stdin, paths, round_no, s=None):
    """Run the CLI once, and see what the run changed."""
    record = paths["record"]
    events = os.path.join(record, "events-%d.jsonl" % round_no)
    errors = os.path.join(record, "stderr-%d.txt" % round_no)
    before, git_before = snapshot(paths["project"]), git_state(paths["project"])
    refs_before = references(paths["project"])
    started = time.time()
    live = {"round": round_no, "started": started, "cli": cli.name, "pid": os.getpid()}  # for `watch` and `board`
    live.update({k: (s or {}).get(k) for k in ("role", "kind", "model", "effort")})
    live.update(spec=paths["spec"], report=paths["report"])  # for the check of a run alongside
    save(os.path.join(record, "live.json"), live)
    try:
        with open(events, "wb") as out, open(errors, "wb") as err:
            proc = subprocess.run(cmd, cwd=cwd, input=stdin, stdout=out, stderr=err)
        code = proc.returncode
    except OSError as e:
        raise Fail("`%s` could not be started: %s" % (cmd[0], e))
    elapsed = time.time() - started
    after, git_after = snapshot(paths["project"]), git_state(paths["project"])
    return Outcome(code, elapsed, started, events, errors, changed(before, after), git_before != git_after, refs_before)


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

    others = alongside(paths, outcome.started, outcome.started + outcome.elapsed)
    own += tuple(o["files"] + os.sep for o in others)  # their records and working files
    reports = {paths["report"]} | {o["report"] for o in others}
    work = [p for p in outcome.changes if not os.path.join(paths["project"], p).startswith(own)
            and os.path.join(paths["project"], p) not in reports]
    scope = scope_paths(paths["spec"], paths["project"])
    work, theirs = put_down(work, edited_paths(paths["record"], outcome.started, paths["project"]), scope, others)
    refused = guard_refusals(os.path.join(paths["record"], "guard.jsonl"), outcome.started)
    for r in refused[:10]:
        lines.append("GUARD: " + refusal(r, 120))
    if len(refused) > 10:
        lines.append("GUARD: and %d more refusals, in `%s`" % (len(refused) - 10, rel(os.path.join(paths["record"], "guard.jsonl"))))
    lines.append("CHANGED: %s" % (", ".join(work[:30]) + (" and %d more" % (len(work) - 30) if len(work) > 30 else "") if work else "nothing"))
    if others:
        line = "ALONGSIDE: %s ran at the same time." % ", ".join(o["task"] for o in others)
        if theirs:
            line += " Changes put down to them: %s" % "; ".join("%s %s" % (t, ", ".join(ps)) for t, ps in theirs.items())
        lines.append(line)
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
    added = added_references(paths["project"], project_files, outcome.references)
    if added:
        warnings.append("Lines naming `.orchestra/` were added to project files: %s. A project file must not depend on "
                        "the work folder, and capture for a review belongs in a script under `files/<task>/`."
                        % ", ".join(added[:20] + (["and %d more" % (len(added) - 20)] if len(added) > 20 else [])))
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
    outcome = execute(cli, cmd, s["cwd"], stdin, paths, s["rounds"], s)
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
    s = {"cli": cli.name, "role": args.role, "kind": ROLES[args.role]["kind"], "model": cli.models[args.role],
         "effort": effort, "keys": brief_keys(paths["work_folder"]),
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
    s["keys"] = brief_keys(paths["work_folder"])
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
        if it == "collab_tool_call":
            return ["agent  %s" % item.get("tool")]
        return []


class Subagents:
    """The Codex subagents a run starts, and what each of them does.

    The run's event stream says nothing of its subagents. Each subagent has a session file of its
    own under `$CODEX_HOME/sessions/`, written as each of its calls ends. Its first line names the
    thread that started it, and the file begins with a copy of that thread's history, which ends
    at the line `subagent_history_start_ordinal` names.
    """

    def __init__(self, home, since):
        self.folder, self.since = os.path.join(home, "sessions"), since
        self.root = None      # the run's own thread, from its event stream
        self.threads = set()  # the run's thread and the subagents found so far
        self.meta = {}        # thread id -> the first line of its session file, for every file read
        self.tails = {}       # subagent thread id -> [Tail, lines read]
        self.read_paths = set()

    def set_root(self, thread):
        if thread and not self.root:
            self.root = str(thread)
            self.threads.add(self.root)

    def name(self, thread):
        """The subagent's nickname, or None for the run's own thread and for an unknown one."""
        if not thread or thread not in self.meta or thread not in self.tails:
            return None
        return self.meta[thread].get("agent_nickname") or self.meta[thread].get("agent_path") or "subagent"

    def tag(self, thread):
        """`[Name] ` before a subagent's line; nothing for the run's own thread."""
        if not thread or not self.root or thread == self.root:
            return ""
        return "[%s] " % (self.name(thread) or "subagent")

    def files(self):
        """Session files written since the round started, in the folders of the days it spans."""
        days, t = set(), self.since - 86400
        while t <= time.time() + 86400:
            days.add(time.strftime("%Y/%m/%d", time.localtime(t)))
            t += 43200
        for day in sorted(days):
            folder = os.path.join(self.folder, day)
            try:
                names = os.listdir(folder)
            except OSError:
                continue
            for name in names:
                path = os.path.join(folder, name)
                if name.startswith("rollout-") and name.endswith(".jsonl") and path not in self.read_paths:
                    try:
                        if os.path.getmtime(path) >= self.since - 5:
                            yield path
                    except OSError:
                        pass

    def find(self):
        """Read the first line of each new session file, and take in every subagent of a known thread."""
        for path in self.files():
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    first = json.loads(f.readline())
            except (OSError, ValueError):
                continue  # not written yet: tried again next time
            self.read_paths.add(path)
            meta = first.get("payload") if isinstance(first, dict) and first.get("type") == "session_meta" else None
            if isinstance(meta, dict) and meta.get("id"):
                self.meta[str(meta["id"])] = dict(meta, path=path)
        found = []
        changed = True
        while changed:
            changed = False
            for tid, meta in self.meta.items():
                if tid not in self.threads and meta.get("parent_thread_id") in self.threads:
                    self.threads.add(tid)
                    self.tails[tid] = [Tail(meta["path"]), 0]
                    found.append((tid, c.epoch(meta.get("timestamp")), "start", None))
                    changed = True
        return found

    def items(self):
        """(thread, time, kind, value) for what each subagent did since the last call. The kind is
        `start`, `line` (a line for a person), `item` (the finished item itself), `tokens`, `model`
        or `done`."""
        out = self.find()
        for tid, entry in self.tails.items():
            tail, start = entry[0], int(self.meta[tid].get("subagent_history_start_ordinal") or 0)
            for e in tail.read():
                index = e.get("ordinal", entry[1]) if isinstance(e, dict) else entry[1]
                entry[1] += 1
                if not isinstance(e, dict) or index < start:
                    continue
                when = c.epoch(e.get("timestamp"))
                out += [(tid, when, kind, value) for kind, value in rollout_items(e)]
        return out

    def lines(self, calls=False):
        """(time, line) for what each subagent did since the last call, marked with its name. With
        `calls`, leave out the commands, edits and tool calls, which the guard's log shows."""
        out = []
        for tid, when, kind, value in self.items():
            if kind in ("start", "done"):
                out.append((when, "agent  %s %s" % (self.name(tid), "started" if kind == "start" else "finished")))
            elif kind == "line" and not (calls and value.startswith(("$", "edit ", "tool "))):
                out.append((when, "[%s] %s" % (self.name(tid), value)))
        return out


def rollout_items(e):
    """(kind, value) pairs from one line of a subagent's session file: see `Subagents.items`."""
    p = e.get("payload") if e.get("type") == "event_msg" else None
    if not isinstance(p, dict):
        return []
    if p.get("type") == "task_complete":
        return [("done", None)]
    if p.get("type") == "token_count":
        usage = (p.get("info") or {}).get("total_token_usage") or {}
        total = usage.get("total_tokens") or (usage.get("input_tokens", 0) + usage.get("output_tokens", 0))
        return [("tokens", total)] if total else []
    if p.get("type") == "thread_settings_applied":
        ts = p.get("thread_settings") or {}
        return [("model", (ts.get("model"), ts.get("reasoning_effort")))]
    out = [("line", line) for line in rollout_lines(p)]
    if p.get("type") == "item_completed" and isinstance(p.get("item"), dict):
        out.append(("item", p["item"]))  # the item itself, for the board
    return out


def rollout_lines(p):
    """Lines for a person from one event of a subagent's session file."""
    item = p.get("item") if p.get("type") == "item_completed" and isinstance(p.get("item"), dict) else {}
    kind = item.get("type")
    if kind == "AgentMessage":
        return ["say    %s" % short(" ".join(str(x.get("text", "")) for x in item.get("content") or [] if isinstance(x, dict)), 400)]
    if kind == "CommandExecution":
        cmd = item.get("command")
        out = ["$      %s" % short(cmd[-1] if isinstance(cmd, list) and cmd else cmd)]
        if item.get("exit_code") not in (0, None):
            out.append("       exit %s" % item.get("exit_code"))
        return out
    if kind == "FileChange":
        changes = item.get("changes") if isinstance(item.get("changes"), dict) else {}
        return ["edit   %s" % ", ".join("%s %s" % ((v or {}).get("type", ""), k) for k, v in changes.items())]
    if kind == "Extension":
        return ["tool   %s %s" % (item.get("kind"), short(item.get("query") or "", 120))]
    if kind == "McpToolCall":
        return ["tool   mcp %s.%s" % (item.get("server"), item.get("tool"))]
    return []


def activity_line(r, tag=""):
    return "%-6s %s%s %s" % ("GUARD" if r.get("refused") else "call", tag, r.get("tool"), short(called(r)))


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
    try:  # without a start time, an old run's subagents are looked for over the day before its end
        since = round_start or os.path.getmtime(events_path) - 86400
    except OSError:
        since = time.time()
    agents = Subagents(Codex().home(), since)
    out.write("%s %s, round %d.%s\n" % ("Following" if args.follow else "Showing", paths["task"], round_no, " Ctrl-C to stop." if args.follow else ""))

    def local(line):
        return line.replace(paths["project"] + os.sep, "")

    def clock(when):
        return time.strftime("%H:%M:%S", time.localtime(when)) if when else "--:--:--"

    while True:
        ended = args.follow and run_ended(record, round_no, round_start)  # one more pass after the end
        now = time.strftime("%H:%M:%S") if args.follow else "--:--:--"
        shown = []
        for e in events.read():
            if isinstance(e, dict) and e.get("type") == "thread.started":
                agents.set_root(e.get("thread_id"))
            for line in reader.lines(e):
                if args.calls and line.startswith(("$", "edit ", "tool ")):
                    continue  # the guard's log already shows the calls
                shown.append((now, line))
        sub = [(clock(when), line) for when, line in agents.lines(calls=args.calls)]
        rows = []
        for r in guard.read():
            if r.get("fault") or r.get("at", 0) < round_start:
                continue
            tag = agents.tag(r.get("thread"))
            rows.append((clock(r["at"]), activity_line(r, tag) if args.calls else "GUARD  " + tag + refusal(r, 100)))
        for stamp, line in rows + shown + sub:
            out.write("%s  %s\n" % (stamp, local(line)))
        out.flush()
        if not args.follow:
            return 0
        if ended:
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
    sub.add_parser("board", help="every run in progress, one pane per agent, for the user", add_help=False)
    if argv is None:
        argv = sys.argv[1:]
    if argv[:1] == ["board"]:
        import board
        return board.main(argv[1:])
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
