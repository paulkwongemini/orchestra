"""Shared helpers for the orchestra hook scripts."""
import datetime
import hashlib
import json
import os
import re
import sys
import tempfile
import time

PLUGIN = "orchestra"
WORKDIR = ".orchestra"
TAG = "[orchestra] "
CONDUCTOR_SKILL = PLUGIN + ":conductor"
DELEGATION_SKILL = PLUGIN + ":delegation"

# The subagent roster.
#   impl     = produces a result. The conductor reviews the result independently
#              before it reads the report.
#   readonly = analyzes only. The report is the result.
ROSTER = {
    "opus-engineer": {"model": "opus", "kind": "impl"},
    "opus-analyst": {"model": "opus", "kind": "readonly"},
    "sonnet-builder": {"model": "sonnet", "kind": "impl"},
    "sonnet-writer": {"model": "sonnet", "kind": "impl"},
}

# Required headings. Keep these in step with "Document formats" in skills/conductor/SKILL.md.
BRIEF_HEADINGS = ["Goal", "Out of scope", "Proposed extras", "Constraints", "Acceptance criteria", "Decision rights"]
TASK_HEADINGS = ["Goal", "Scope and limits", "Context", "Acceptance criteria", "If blocked", "Report"]
REVIEW_HEADINGS = ["Scope reviewed", "Checks run", "Verdict"]
REVIEW_EVIDENCE = "Checks run"
REVIEW_VERDICT = "Verdict"
STATUSES = ("DONE", "BLOCKED", "FAILED")


# ---------------------------------------------------------------- input and output

def read_input():
    """Read the hook input (JSON) from stdin. Returns an empty dict if it cannot be read."""
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
        data = json.loads(raw) if raw.strip() else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _emit(obj):
    # ASCII-only output, so the hook works under any locale.
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()
    sys.exit(0)


def allow():
    """Make no decision. The normal permission flow applies."""
    sys.exit(0)


def _pre_tool(decision, reason):
    _emit({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": TAG + reason,
        }
    })


def deny(reason):
    _pre_tool("deny", reason)


def ask(reason):
    _pre_tool("ask", reason)


def block(reason):
    """Stop and SubagentStop: keep the agent running and hand it `reason` as its next instruction."""
    _emit({"decision": "block", "reason": TAG + reason})


def warn_user(message):
    _emit({"systemMessage": TAG + message})


def add_context(event, text):
    """SessionStart and PostModelSwitch: add text to the model's context."""
    _emit({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}})


# ---------------------------------------------------------------- who is calling

def bare(agent_type):
    """`orchestra:opus-engineer` -> `opus-engineer`."""
    t = (agent_type or "").strip()
    prefix = PLUGIN + ":"
    return t[len(prefix):] if t.startswith(prefix) else t


def is_subagent(data):
    return bool(data.get("agent_id"))


def is_fable(model):
    """Fable and Mythos are the same model tier. Both make a session a conductor session."""
    m = str(model or "").lower()
    return "fable" in m or "mythos" in m


def roster_text():
    return ", ".join("`%s:%s`" % (PLUGIN, name) for name in ROSTER)


# ---------------------------------------------------------------- which model the main conversation runs on
#
# The harness applies only while the main conversation runs on a Fable model. A hook is not
# told the model directly, so it is worked out from two places:
#   - the session transcript: each response records its model, and each request records its
#     model when it is sent, which is before the response and its tool calls exist
#   - the PostModelSwitch hook, which records every model switch for the session

_ASSISTANT = re.compile(r'"type":\s*"assistant"')
_REQUEST = re.compile(r'"type":\s*"api-request"\s*[,}]')


def _epoch(text):
    try:
        return datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def transcript_signals(path, max_bytes=16 * 1024 * 1024):
    """Read the end of the transcript.

    Returns (model of the last response in the main conversation, when it was written,
    model of the newest request sent after that response). Any of them can be None.
    """
    try:
        size = os.path.getsize(path)
    except (OSError, TypeError):
        return None, None, None
    window = 256 * 1024
    while True:
        start = max(0, size - window)
        try:
            with open(path, "rb") as f:
                f.seek(start)
                lines = f.read().decode("utf-8", "replace").split("\n")
        except OSError:
            return None, None, None
        if start > 0:
            lines = lines[1:]  # the first line may be cut in half
        in_flight = None
        for line in reversed(lines):
            is_request = _REQUEST.search(line)
            if not is_request and not _ASSISTANT.search(line):
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            if entry.get("type") == "api-request":
                params = entry.get("params")
                if in_flight is None and isinstance(params, dict) and params.get("model"):
                    in_flight = str(params["model"])
            elif entry.get("type") == "assistant" and not entry.get("isSidechain"):
                message = entry.get("message")
                model = message.get("model") if isinstance(message, dict) else None
                if model and not str(model).startswith("<"):
                    return str(model), _epoch(entry.get("timestamp")), in_flight
        if start == 0 or window >= max_bytes:
            return None, None, in_flight
        window *= 4


def _session_file(data):
    folder = os.path.join(state_dir(), "sessions")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, safe_name(data.get("session_id") or "unknown") + ".json")


def record_switch(data, model):
    """PostModelSwitch: remember the model the session switched to."""
    try:
        with open(_session_file(data), "w", encoding="utf-8") as f:
            json.dump({"model": str(model or ""), "at": time.time()}, f)
    except OSError:
        pass


def _load_switch(data):
    try:
        with open(_session_file(data), encoding="utf-8") as f:
            switch = json.load(f)
        return switch if isinstance(switch, dict) else None
    except (OSError, ValueError):
        return None


def main_model_is_fable(data):
    """True, False, or None when nothing is known yet (a session that has sent no request)."""
    last_model, last_time, in_flight = transcript_signals(data.get("transcript_path"))
    switch = _load_switch(data)
    settled = last_model
    if switch and (last_time is None or switch.get("at", 0) > last_time):
        settled = switch.get("model")  # a switch that is newer than the last response
    if settled is None and in_flight is None:
        return None
    # A request sent after the last response is the newest fact there is, so it decides.
    # A session continued on another model sends no switch event, and this is how it shows.
    if in_flight is not None:
        return is_fable(in_flight)
    return is_fable(settled)


def _off_file(data):
    folder = os.path.join(state_dir(), "sessions")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, safe_name(data.get("session_id") or "unknown") + ".off")


def set_disabled(data, disabled):
    """Record that the user turned conductor mode off, or back on, for this session."""
    try:
        if disabled:
            open(_off_file(data), "w").close()
        elif os.path.exists(_off_file(data)):
            os.remove(_off_file(data))
    except OSError:
        pass


def is_disabled(data):
    return os.path.exists(_off_file(data))


def _loaded_file(data, skill):
    folder = os.path.join(state_dir(), "sessions")
    os.makedirs(folder, exist_ok=True)
    suffix = ".loaded" if skill == CONDUCTOR_SKILL else "." + bare(skill)
    return os.path.join(folder, safe_name(data.get("session_id") or "unknown") + suffix)


def mark_loaded(data, loaded, skill=CONDUCTOR_SKILL):
    """Record that a skill's text is in the context of this session, or no longer is."""
    try:
        if loaded:
            with open(_loaded_file(data, skill), "w"):
                pass
        elif os.path.exists(_loaded_file(data, skill)):
            os.remove(_loaded_file(data, skill))
    except OSError:
        pass


def is_loaded(data, skill=CONDUCTOR_SKILL):
    return os.path.exists(_loaded_file(data, skill))


def is_skill(name, skill):
    """Whether a Skill tool call names this plugin skill, with or without the plugin prefix."""
    name = str(name or "").strip().lstrip("/")
    return name in (skill, bare(skill))


def _turn_file(data):
    folder = os.path.join(state_dir(), "sessions")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, safe_name(data.get("session_id") or "unknown") + ".turn")


def mark_user_turn(data):
    """Record that the user just said something: a prompt, or an answer to a question."""
    try:
        with open(_turn_file(data), "w"):
            pass
    except OSError:
        pass


def last_user_turn(data):
    """When the user last said something in this session, or None when that was not recorded."""
    try:
        return os.path.getmtime(_turn_file(data))
    except OSError:
        return None


def main_is_conductor(data):
    """True for the main conversation of a session that runs on a Fable model,
    unless the user turned conductor mode off for the session."""
    return not is_subagent(data) and not is_disabled(data) and bool(main_model_is_fable(data))


# ---------------------------------------------------------------- paths

def project_dir(data):
    return os.path.realpath(os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd())


def work_dir(data):
    return os.path.join(project_dir(data), WORKDIR)


def resolve(path, data):
    """Turn a path from a tool input into a real absolute path."""
    p = os.path.expandvars(os.path.expanduser(str(path)))
    if not os.path.isabs(p):
        p = os.path.join(data.get("cwd") or project_dir(data), p)
    return os.path.realpath(p)


def inside(path, base):
    base = os.path.realpath(base)
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


def split_unit(path, data):
    """`<project>/.orchestra/<work>/reports/T01.md` -> (`<project>/.orchestra/<work>`, ["reports", "T01.md"]).

    Each piece of work has its own folder under `.orchestra/`. Returns (None, []) for a path
    that is not inside one.
    """
    root = os.path.realpath(work_dir(data))
    if not inside(path, root) or path == root:
        return None, []
    parts = os.path.relpath(path, root).split(os.sep)
    return os.path.join(root, parts[0]), parts[1:]


def in_reports(path, data):
    """True for anything inside a work folder's `reports/` folder."""
    unit, rest = split_unit(path, data)
    return unit is not None and bool(rest) and rest[0] == "reports"


def temp_roots(data):
    roots = {"/tmp", "/var/tmp", os.path.realpath(tempfile.gettempdir())}
    scratch = data.get("scratchpad_dir")
    if scratch:
        roots.add(os.path.realpath(scratch))
    return roots


def task_id(filename):
    """`T01-login-fix.md` -> `T01`, `T01.md` -> `T01`."""
    stem = os.path.splitext(os.path.basename(str(filename)))[0]
    return stem.split("-", 1)[0]


def ensure_gitignore(data):
    """Keep `.orchestra/` out of git by default.

    Create-only: a project that wants to commit these records replaces the file's content,
    and that choice is left alone.
    """
    root = work_dir(data)
    target = os.path.join(root, ".gitignore")
    try:
        os.makedirs(root, exist_ok=True)  # the write that follows creates it anyway
        if not os.path.exists(target):
            with open(target, "w", encoding="utf-8") as f:
                f.write("*\n")
    except OSError:
        pass


# ---------------------------------------------------------------- document checks

def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _heading_re(heading):
    return re.compile(
        r"^#{1,6}[ \t]*(?:\d+[.)][ \t]*)?" + re.escape(heading) + r"(?=[ \t]*$|[ \t]*[(:：—-])",
        re.M | re.I,
    )


def section_body(text, heading):
    """The text under a heading, or None when the heading is absent."""
    m = _heading_re(heading).search(text)
    if not m:
        return None
    rest = text[m.end():]
    rest = rest.split("\n", 1)[1] if "\n" in rest else ""
    nxt = re.search(r"^#{1,6}[ \t]", rest, re.M)
    return (rest[:nxt.start()] if nxt else rest).strip()


def missing_headings(text, headings):
    return [h for h in headings if section_body(text, h) is None]


def empty_sections(text, headings):
    return [h for h in headings if section_body(text, h) == ""]


_STATUS_LINE = re.compile(r"^\W*STATUS\W*[:：]\s*\W*([A-Za-z]+)", re.I)


def read_status(path):
    """The STATUS value on the first line of a report, or an empty string."""
    for line in read_text(path).splitlines():
        if line.strip():
            m = _STATUS_LINE.match(line.strip())
            return m.group(1).upper() if m else ""
    return ""


# ---------------------------------------------------------------- state shared between hooks

def state_dir():
    base = os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.join(tempfile.gettempdir(), "orchestra-state")
    os.makedirs(base, exist_ok=True)
    return base


def safe_name(text):
    return re.sub(r"[^\w.\-]", "_", str(text))[:120]


def prune_state(max_age_days=30):
    """Drop per-session records that are older than a month."""
    cutoff = time.time() - max_age_days * 86400
    folder = os.path.join(state_dir(), "sessions")
    try:
        for entry in os.scandir(folder):
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                os.remove(entry.path)
    except OSError:
        pass


def _meta_path(data, unit, tid):
    digest = hashlib.sha1(os.path.realpath(unit).encode("utf-8")).hexdigest()[:12]
    folder = os.path.join(state_dir(), "meta", digest)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, safe_name(tid) + ".json")


def save_meta(data, unit, tid, meta):
    """Record which kind of subagent filed a report. Kept outside the project."""
    try:
        with open(_meta_path(data, unit, tid), "w", encoding="utf-8") as f:
            json.dump(meta, f)
    except OSError:
        pass


def load_meta(data, unit, tid):
    try:
        with open(_meta_path(data, unit, tid), encoding="utf-8") as f:
            meta = json.load(f)
        return meta if isinstance(meta, dict) else {}
    except (OSError, ValueError):
        return {}
