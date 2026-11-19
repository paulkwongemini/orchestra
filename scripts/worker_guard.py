#!/usr/bin/env python3
"""PreToolUse hook for a worker run that scripts/worker.py starts on the Codex CLI or the Antigravity CLI.

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
  - an agy worker does not ask to leave its sandbox

It reads both input formats:

  Codex  {"tool_name": "Bash" | "apply_patch" | ..., "tool_input": {"command": ...}, "cwd": ...}
  agy    {"toolCall": {"name": "run_command" | "write_file" | ..., "args": {...}}, "cwd": ...}

and answers in the format of the CLI that called it. Each refusal is logged for `worker.py` to
report, and every call, let through or not, goes to the context's `activity` log for
`worker.py watch`. A fault in the hook lets the call through, as for a Claude subagent, and is logged.

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
AGY_PATH_KEYS = ("Path", "TargetFile", "AbsolutePath", "FilePath", "file_path", "path")
# What a call is aimed at, for the activity log: a path, or the address or query of a lookup.
AGY_TARGET_KEYS = AGY_PATH_KEYS + ("Url", "Query")
CODEX_SHELL = ("Bash", "shell", "exec_command", "local_shell")
AGY_SHELL = ("run_command",)
# agy's file-writing tools. `write_file` was seen in a run; the others are matched by name.
WRITE_TOOL = re.compile(r"write|edit|replace|create|delete|remove|move|rename|patch|insert", re.I)


def read_call(data):
    """(cli, tool name, shell command or None, paths to be written, wants to leave the sandbox)."""
    if isinstance(data.get("toolCall"), dict):
        call = data["toolCall"]
        name = str(call.get("name") or "")
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        command = str(args.get("CommandLine") or "") if name in AGY_SHELL else None
        paths = []
        if WRITE_TOOL.search(name):  # a tool that reads a file names it the same way, so only writers count
            paths = [str(args[k]) for k in AGY_PATH_KEYS if isinstance(args.get(k), str) and args.get(k)]
        bypass = bool(args.get("BypassSandbox"))
        return "agy", name, command, paths, bypass
    name = str(data.get("tool_name") or "")
    ti = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    text = ti.get("command") if isinstance(ti.get("command"), str) else None
    if name == "apply_patch":
        paths = [a or b for a, b in PATCH_PATH.findall(text or "")]
        return "codex", name, None, paths, False
    return "codex", name, text if name in CODEX_SHELL else None, [], False


def judge(ctx, data, call):
    """None to let the call through, or the reason to refuse it. `call` is what read_call returned."""
    _cli, _name, command, paths, bypass = call
    actor = "readonly" if ctx.get("kind") == "readonly" else "impl"
    check = {"cwd": ctx["project"]}
    if bypass:
        return ("This run stays in its sandbox, and nobody is present to approve leaving it. Do not "
                "set BypassSandbox. If the task needs the command outside the sandbox, stop and "
                "report BLOCKED with the command and why.")
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


def answer(cli, reason):
    if cli == "agy":
        out = {"decision": "deny", "reason": reason} if reason else {"decision": "allow"}
        sys.stdout.write(json.dumps(out))
    elif reason:
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                            "permissionDecision": "deny",
                                                            "permissionDecisionReason": reason}}))
    sys.stdout.flush()


def main():
    ctx, cli = {}, "codex"
    try:
        with open(sys.argv[1], encoding="utf-8") as f:
            ctx = json.load(f)
        os.environ["CLAUDE_PROJECT_DIR"] = ctx["project"]
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        call = read_call(data)
        cli, name, command, paths, _bypass = call
        reason = judge(ctx, data, call)
        now = time.time()
        # Every call, let through or not, for the user who follows the run with `worker.py watch`.
        args = (data.get("toolCall") or {}).get("args") if isinstance(data.get("toolCall"), dict) else data.get("tool_input")
        target = next((str(args[k]) for k in AGY_TARGET_KEYS if isinstance(args, dict) and args.get(k)), "")
        log(ctx, {"at": now, "tool": name, "command": (command or "")[:300], "paths": paths, "target": target[:300],
                  "refused": bool(reason)}, "activity")
        if reason:
            log(ctx, {"at": now, "tool": name, "command": (command or "")[:300], "paths": paths, "reason": reason[:300]})
        answer(cli, reason)
    except Exception as exc:  # a fault lets the call through, as for a Claude subagent
        log(ctx, {"at": time.time(), "fault": "%s: %s" % (type(exc).__name__, exc)})
        answer(cli, None)
    sys.exit(0)


if __name__ == "__main__":
    main()
