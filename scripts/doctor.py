#!/usr/bin/env python3
"""Environment check for orchestra. Run by `/orchestra:setup`. Changes nothing in the project."""
import json
import os
import subprocess
import sys
import tempfile

OK, WARN = "[ok]", "[warn]"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def fork_verdict(model, tmp):
    """What the delegation hook says to a fork call in a session that runs on `model`."""
    transcript = os.path.join(tmp, model + ".jsonl")
    with open(transcript, "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "api-request", "params": {"model": model}}) + "\n")
    session = "doctor-" + model
    # Conductor mode is on in the test session, as after `/orchestra:conduct`.
    os.makedirs(os.path.join(tmp, "sessions"), exist_ok=True)
    open(os.path.join(tmp, "sessions", session + ".on"), "w").close()
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Agent",
        "tool_input": {"subagent_type": "fork", "prompt": "check"},
        "session_id": session,
        "transcript_path": transcript,
    }
    env = dict(os.environ, CLAUDE_PLUGIN_DATA=tmp)
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "guard_agent.py")],
        input=json.dumps(payload).encode("utf-8"),
        capture_output=True,
        env=env,
        timeout=20,
    )
    out = proc.stdout.decode("utf-8").strip()
    if not out:
        return "allow"
    return json.loads(out).get("hookSpecificOutput", {}).get("permissionDecision", "?")


def hook_selftest():
    """In conductor mode, a fork must be refused on Fable and Opus and left alone on another model."""
    try:
        with tempfile.TemporaryDirectory(prefix="orchestra-doctor-") as tmp:
            return (fork_verdict("claude-fable-5-2", tmp) == "deny"
                    and fork_verdict("claude-opus-5-5", tmp) == "deny"
                    and fork_verdict("claude-sonnet-5-5", tmp) == "allow")
    except Exception:
        return False


def count(folder):
    try:
        return len([n for n in os.listdir(folder) if n.endswith(".md")])
    except OSError:
        return 0


def main():
    project = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    layers = [
        ("user settings", load_json(os.path.expanduser("~/.claude/settings.json"))),
        ("project settings", load_json(os.path.join(project, ".claude", "settings.json"))),
        ("project local settings", load_json(os.path.join(project, ".claude", "settings.local.json"))),
    ]
    lines = ["orchestra check", ""]

    manifest = load_json(os.path.join(ROOT, ".claude-plugin", "plugin.json"))
    lines.append("%s Plugin %s %s" % (OK, manifest.get("name", "?"), manifest.get("version", "?")))
    lines.append("%s Python %s" % (OK, sys.version.split()[0]))

    if hook_selftest():
        lines.append(
            "%s Hook scripts work. In conductor mode, a fork call was refused in a Fable and an "
            "Opus session and left alone in a Sonnet session." % OK
        )
    else:
        lines.append(
            "%s Hook scripts did not behave as expected. Run `python3 %s/tests/test_hooks.py`." % (WARN, ROOT)
        )

    lines.append(
        "%s Mode: conductor mode is off when a session starts. `/orchestra:conduct` turns it on "
        "for the session, and it applies while the session runs on a Fable or an Opus model. "
        "`/orchestra:disable` turns it off." % OK
    )

    if any(s.get("disableAllHooks") for _name, s in layers):
        lines.append("%s disableAllHooks is on. Hooks do not run, so the harness does nothing." % WARN)

    # Work folders
    root = os.path.join(project, ".orchestra")
    units = []
    try:
        units = sorted(e.name for e in os.scandir(root) if e.is_dir())
    except OSError:
        pass
    if not units:
        lines.append("%s No work folders yet under %s. One is created with the first brief." % (OK, root))
    else:
        lines.append("%s Work folders under %s:" % (OK, root))
        for name in units:
            unit = os.path.join(root, name)
            brief = "brief" if os.path.isfile(os.path.join(unit, "brief.md")) else "no brief"
            lines.append(
                "     %s: %s, %d task specs, %d reports, %d review records"
                % (name, brief, count(os.path.join(unit, "tasks")), count(os.path.join(unit, "reports")),
                   count(os.path.join(unit, "reviews")))
            )

    sys.stdout.buffer.write(("\n".join(lines) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
