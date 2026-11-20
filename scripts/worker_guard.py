#!/usr/bin/env python3
"""PreToolUse hook for a worker run that scripts/worker.py starts on the Codex CLI.

    worker_guard.py <context.json>

`worker.py` passes this hook to the CLI for one run, and the context file says who the worker
is: its role's kind, the project, the work folder, the task, the brief's pre-approved keys, and
where to log. The hook judges each shell command and each file edit by the same rules that the
claude mode's hooks apply to a Claude subagent:

  - a command of a pre-approval kind (data, dependencies, git, pushes, GitHub, writing requests
    to other hosts, ssh, deployments) runs only when the brief pre-approves its kind, and a data
    command that names production never runs
  - a worker writes inside `.orchestra/` only to `reports/` and `files/`; a read-only role writes
    nothing else in the project
  - no worker starts a Claude session with the `claude` CLI

It reads the Codex input format:

  {"tool_name": "Bash" | "apply_patch" | ..., "tool_input": {"command": ...}, "cwd": ...}

Each refusal is logged for `worker.py` to report, and every call, let through or not, goes to the
context's `activity` log for `worker.py watch`. A fault in the hook lets the call through, as for a
Claude subagent, and is logged.

The shell check reads the command line. It is a tripwire, not a sandbox: a request made from
inside a script it starts is not seen.
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402
import guard_main as gm  # noqa: E402

PATCH_PATH = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+?)\s*$|^\*\*\* Move to: (.+?)\s*$", re.M)
CODEX_SHELL = ("Bash", "shell", "exec_command", "local_shell")


def read_call(data):
    """(tool name, shell command or None, paths to be written)."""
    name = str(data.get("tool_name") or "")
    ti = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    text = ti.get("command") if isinstance(ti.get("command"), str) else None
    if name == "apply_patch":
        paths = [a or b for a, b in PATCH_PATH.findall(text or "")]
        return name, None, paths
    return name, text if name in CODEX_SHELL else None, []


def judge(ctx, data, call):
    """None to let the call through, or the reason to refuse it. `call` is what read_call returned."""
    _name, command, paths = call
    actor = "readonly" if ctx.get("kind") == "readonly" else "impl"
    check = {"cwd": ctx["project"]}
    base = data.get("cwd") or ctx["project"]
    for text in paths:
        path = os.path.realpath(text if os.path.isabs(text) else os.path.join(base, text))
        allowed = gm.readonly_may_write(path, check) if actor == "readonly" else gm.impl_may_write(path, check)
        if not allowed:
            where = os.path.relpath(path, ctx["project"]) if c.inside(path, ctx["project"]) else path
            if actor == "readonly":
                return ("Your role is read-only: write only your report and your files/ folder. `%s` "
                        "is neither. Put the change in your report as a recommendation." % where)
            return ("Inside .orchestra/, write only your report and your files/ folder. `%s` belongs to "
                    "the conductor. Do not look for another way to change it." % where)
    if command:
        approval = c.Approval(ctx.get("work_folder"), set(ctx.get("keys") or []), "confirmed")
        result = gm.judge_command(command, gm.Ctx(check, actor, command, approval))
        if result is not None:
            return result[1]  # an `ask` has nobody to answer it, so it is refused too
    return None


def log(ctx, record, key="log"):
    path = ctx.get(key)
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def answer(reason):
    if reason:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                            "permissionDecision": "deny",
                                                            "permissionDecisionReason": reason}}))
    sys.stdout.flush()


def main():
    ctx = {}
    try:
        with open(sys.argv[1], encoding="utf-8") as f:
            ctx = json.load(f)
        os.environ["CLAUDE_PROJECT_DIR"] = ctx["project"]
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        call = read_call(data)
        name, command, paths = call
        reason = judge(ctx, data, call)
        now = time.time()
        # Every call, let through or not, for the user who follows the run with `worker.py watch`.
        log(ctx, {"at": now, "tool": name, "command": (command or "")[:300], "paths": paths,
                  "refused": bool(reason)}, "activity")
        if reason:
            log(ctx, {"at": now, "tool": name, "command": (command or "")[:300], "paths": paths, "reason": reason[:300]})
        answer(reason)
    except Exception as exc:  # a fault lets the call through, as for a Claude subagent
        log(ctx, {"at": time.time(), "fault": "%s: %s" % (type(exc).__name__, exc)})
        answer(None)
    sys.exit(0)


if __name__ == "__main__":
    main()
