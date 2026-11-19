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
ORCHESTRA_DIR = ".orchestra"
TAG = "[orchestra] "
CONDUCTOR_SKILL = PLUGIN + ":conductor"
DELEGATION_SKILL = PLUGIN + ":delegation"
DOCUMENTATION_SKILL = PLUGIN + ":documentation"
# Skills the conductor loads on top of its instructions. A hook waits for each one.
SUB_SKILLS = (DELEGATION_SKILL, DOCUMENTATION_SKILL)

# The subagent roster.
#   impl     = produces a result. The conductor reviews the result independently
#              before it reads the report.
#   readonly = analyzes only. The report is the result.
# The Haiku subagents do work that needs no judgment and file no report: `haiku-scout` finds
# and reads (readonly), and `haiku-editor` makes a mechanical edit (impl). Whoever started one
# checks its result itself.
ROSTER = {
    "opus-engineer": {"model": "opus", "kind": "impl"},
    "opus-analyst": {"model": "opus", "kind": "readonly"},
    "sonnet-builder": {"model": "sonnet", "kind": "impl"},
    "sonnet-writer": {"model": "sonnet", "kind": "impl"},
    "haiku-scout": {"model": "haiku", "kind": "readonly"},
    "haiku-editor": {"model": "haiku", "kind": "impl"},
}
HAIKU = tuple(name for name, entry in ROSTER.items() if entry["model"] == "haiku")

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
    """Fable and Mythos are the same model tier."""
    m = str(model or "").lower()
    return "fable" in m or "mythos" in m


def is_opus(model):
    return "opus" in str(model or "").lower()


def is_conductor_model(model):
    """A model that can conduct: Fable (or Mythos) and Opus."""
    return is_fable(model) or is_opus(model)


# Effort levels at which an Opus conductor is advised to run higher, and asked once.
LOW_EFFORTS = ("low", "medium")


def roster_text():
    return ", ".join("`%s:%s`" % (PLUGIN, name) for name in ROSTER)


# ---------------------------------------------------------------- per-session records
#
# Each record is a small file of its own, `<state>/sessions/<session id><suffix>`, so that two
# hooks running at once never rewrite each other's record.

def _state_file(data, suffix):
    folder = os.path.join(state_dir(), "sessions")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, safe_name(data.get("session_id") or "unknown") + suffix)


def _read_state(data, suffix):
    """The text of a record, or None when there is no record or it is empty."""
    try:
        with open(_state_file(data, suffix), encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _write_state(data, suffix, text):
    """Write a record, or remove it with None. An empty text leaves a file whose time is the record."""
    path = _state_file(data, suffix)
    try:
        if text is not None:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        elif os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------- which model the main conversation runs on
#
# The harness applies only while the user has turned conductor mode on and the main
# conversation runs on a Fable or an Opus model. A hook is not told the model directly, so it
# is worked out from two places:
#   - the session transcript: each response records its model, and each request records its
#     model when it is sent, which is before the response and its tool calls exist
#   - the PostModelSwitch hook, which records every model switch for the session

_ASSISTANT = re.compile(r'"type":\s*"assistant"')
_REQUEST = re.compile(r'"type":\s*"api-request"\s*[,}]')


def epoch(text):
    """An ISO timestamp as seconds, or None."""
    try:
        return datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _transcript_entries(path, max_bytes=16 * 1024 * 1024):
    """The requests and the main conversation's responses in a transcript, newest first.

    Reads a window at the end of the file and widens it for as long as the caller keeps asking,
    up to `max_bytes`.
    """
    try:
        size = os.path.getsize(path)
    except (OSError, TypeError):
        return
    window, end = 256 * 1024, size
    while True:
        start = max(0, size - window)
        try:
            with open(path, "rb") as f:
                f.seek(start)
                lines = f.read(end - start).split(b"\n")
        except OSError:
            return
        if start > 0:
            end = start + len(lines[0])  # the first line may be cut in half: the next window reads it whole
            lines = lines[1:]
        for raw in reversed(lines):
            line = raw.decode("utf-8", "replace")
            if not _REQUEST.search(line) and not _ASSISTANT.search(line):
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            if entry.get("type") == "api-request" or (entry.get("type") == "assistant" and not entry.get("isSidechain")):
                yield entry
        if start == 0 or window >= max_bytes:
            return
        window *= 4


def transcript_signals(path):
    """Read the end of the transcript.

    Returns (model of the last response in the main conversation, when it was written,
    model of the newest request sent after that response). Any of them can be None.
    """
    in_flight = None
    for entry in _transcript_entries(path):
        if entry["type"] == "api-request":
            params = entry.get("params")
            if in_flight is None and isinstance(params, dict) and params.get("model"):
                in_flight = str(params["model"])
            continue
        message = entry.get("message")
        model = message.get("model") if isinstance(message, dict) else None
        if model and not str(model).startswith("<"):
            return str(model), epoch(entry.get("timestamp")), in_flight
    return None, None, in_flight


def record_switch(data, model):
    """PostModelSwitch: remember the model the session switched to."""
    _write_state(data, ".json", json.dumps({"model": str(model or ""), "at": time.time()}))


def _load_switch(data):
    try:
        switch = json.loads(_read_state(data, ".json") or "")
    except ValueError:
        return None
    return switch if isinstance(switch, dict) else None


def main_model(data):
    """The model the main conversation runs on, or None when nothing is known yet (a session that has sent no request)."""
    last_model, last_time, in_flight = transcript_signals(data.get("transcript_path"))
    # A request sent after the last response is the newest fact there is, so it decides.
    # A session continued on another model sends no switch event, and this is how it shows.
    if in_flight is not None:
        return in_flight
    switch = _load_switch(data)
    if switch and (last_time is None or switch.get("at", 0) > last_time):
        return switch.get("model") or None  # a switch that is newer than the last response
    return last_model


def main_model_can_conduct(data):
    """True, False, or None when nothing is known yet."""
    model = main_model(data)
    return None if model is None else is_conductor_model(model)


def main_effort(data):
    """The effort level of the main conversation's newest request or response, lower case, or None.

    Each request records the effort it was sent with, before its response and tool calls exist,
    so a hook sees the effort that is in force for the call it is judging.
    """
    for entry in _transcript_entries(data.get("transcript_path")):
        if entry["type"] == "api-request":
            params = entry.get("params")
            config = params.get("output_config") if isinstance(params, dict) else None
            effort = config.get("effort") if isinstance(config, dict) else None
        else:
            effort = entry.get("effort")
        if effort:
            return str(effort).strip().lower()
    return None


# ---------------------------------------------------------------- the session's mode and what it has done
#
# Conductor mode is on when the user has turned on one of three worker modes. In `claude` the
# workers are Claude subagents and the hooks guard the work. In the CLI modes the workers are
# runs of another vendor's CLI, and the hooks step aside: the conductor keeps the rules by its
# instructions alone.
MODES = ("claude", "codex", "agy")
CLI_MODES = ("codex", "agy")
# The mode each command turns on. `disable` turns conductor mode off.
COMMAND_MODES = {"conduct": "claude", "conductor": "claude", "conduct-codex": "codex", "conduct-agy": "agy"}
OFF_COMMAND = "disable"


def set_mode(data, mode):
    """Record the worker mode the user turned on for this session, or None for off. It is off until turned on."""
    _write_state(data, ".mode", mode if mode in MODES else None)


def session_mode(data):
    """The session's worker mode, or None when conductor mode is off."""
    mode = _read_state(data, ".mode")
    return mode if mode in MODES else None


def in_cli_mode(data):
    """Whether a CLI mode is on. Every guard then steps aside."""
    return session_mode(data) in CLI_MODES


def effort_asked(data):
    """The low effort level the user was last asked about in this session, or None."""
    return _read_state(data, ".effort")


def mark_effort_asked(data, effort):
    """Record the effort level the user was asked about, or clear the record with None."""
    _write_state(data, ".effort", effort or None)


def _loaded_suffix(skill):
    return ".loaded" if skill == CONDUCTOR_SKILL else "." + bare(skill)


def mark_loaded(data, loaded, skill=CONDUCTOR_SKILL):
    """Record that a skill's text is in the context of this session, or no longer is."""
    _write_state(data, _loaded_suffix(skill), "" if loaded else None)


def is_loaded(data, skill=CONDUCTOR_SKILL):
    return os.path.exists(_state_file(data, _loaded_suffix(skill)))


def is_skill(name, skill):
    """Whether a Skill tool call names this plugin skill, with or without the plugin prefix."""
    name = str(name or "").strip().lstrip("/")
    return name in (skill, bare(skill))


def mark_user_turn(data):
    """Record that the user just said something: a prompt, or an answer to a question."""
    _write_state(data, ".turn", "")


def last_user_turn(data):
    """When the user last said something in this session, or None when that was not recorded."""
    try:
        return os.path.getmtime(_state_file(data, ".turn"))
    except OSError:
        return None


def set_active_work(data, work_folder):
    """Record the work folder the conductor of this session is working in. The latest record wins."""
    _write_state(data, ".work", os.path.realpath(work_folder))


def active_work(data):
    """The session's active work folder, or None when none is recorded or it is not a work folder of this project."""
    work_folder = _read_state(data, ".work")
    if not work_folder:
        return None
    found, rest = split_work_folder(work_folder, data)
    return work_folder if found == work_folder and not rest else None


def main_conducts(data):
    """True for the main conversation of a session in any conductor mode, while it runs on a Fable or an Opus model."""
    return not is_subagent(data) and session_mode(data) is not None and bool(main_model_can_conduct(data))


def main_is_conductor(data):
    """True for the main conversation of a session in the `claude` conductor mode, while it runs
    on a Fable or an Opus model. This is the session the guards act in."""
    return main_conducts(data) and session_mode(data) == "claude"


# ---------------------------------------------------------------- paths

def project_dir(data):
    return os.path.realpath(os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd())


def orchestra_dir(data):
    return os.path.join(project_dir(data), ORCHESTRA_DIR)


def resolve(path, data):
    """Turn a path from a tool input into a real absolute path."""
    p = os.path.expandvars(os.path.expanduser(str(path)))
    if not os.path.isabs(p):
        p = os.path.join(data.get("cwd") or project_dir(data), p)
    return os.path.realpath(p)


def inside(path, base):
    base = os.path.realpath(base)
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


def split_work_folder(path, data):
    """`<project>/.orchestra/<work>/reports/T01.md` -> (`<project>/.orchestra/<work>`, ["reports", "T01.md"]).

    Each piece of work has its own folder under `.orchestra/`. Returns (None, []) for a path
    that is not inside one.
    """
    root = os.path.realpath(orchestra_dir(data))
    if not inside(path, root) or path == root:
        return None, []
    parts = os.path.relpath(path, root).split(os.sep)
    return os.path.join(root, parts[0]), parts[1:]


def in_reports(path, data):
    """True for anything inside a work folder's `reports/` folder."""
    work_folder, rest = split_work_folder(path, data)
    return work_folder is not None and bool(rest) and rest[0] == "reports"


def in_files(path, data):
    """True for anything inside a work folder's `files/` folder, where tasks keep working files."""
    work_folder, rest = split_work_folder(path, data)
    return work_folder is not None and bool(rest) and rest[0] == "files"


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
    root = orchestra_dir(data)
    target = os.path.join(root, ".gitignore")
    try:
        os.makedirs(root, exist_ok=True)
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


def brief_work_folder(path, data):
    """The work folder for `<project>/.orchestra/<work>/brief.md`, or None for any other path."""
    work_folder, rest = split_work_folder(path, data)
    return work_folder if rest == ["brief.md"] else None


PREAPPROVAL_KEYS = ("data", "deps", "git", "push", "github", "http", "ssh", "deploy")
DECISION_RIGHTS = "Decision rights"
_PREAPPROVED = re.compile(
    r"^[ \t]*(?:[-*+][ \t]+|\d+[.)][ \t]+)?[*_`]*pre[- ]?approved[*_`]*[ \t]*[:：][*_`]*(.*)$",
    re.I | re.M,
)


def preapproved_keys(text):
    """The keys on the `Pre-approved:` line under `## Decision rights`. Other words are ignored."""
    body = section_body(text, DECISION_RIGHTS)
    keys = set()
    for m in _PREAPPROVED.finditer(body or ""):
        for word in re.split(r"[\s,，、]+", m.group(1)):
            word = word.strip("`*_~()[]{}.;:：'\"").lower()
            if word in PREAPPROVAL_KEYS:
                keys.add(word)
    return keys


_CONDUCTOR_WRITES = re.compile(
    # After the colon, `_` counts as markup only when whitespace follows it, so the underscore
    # that begins an entry such as `_config.yml` stays part of the entry.
    r"^[ \t]*(?:[-*+][ \t]+|\d+[.)][ \t]+)?[*_`]*conductor[ \t]+writes[*_`]*[ \t]*[:：]"
    r"(?:[*_`]*(?=[ \t]|$)|[*`]*)(.*)$",
    re.I | re.M,
)
_GLOB = re.compile(r"[*?\[]")


def conductor_write_entries(text):
    """The entries on the `Conductor writes:` lines under `## Decision rights`, as written."""
    body = section_body(text, DECISION_RIGHTS)
    entries = []
    for m in _CONDUCTOR_WRITES.finditer(body or ""):
        # Only commas separate entries, because a path may contain a space.
        for entry in re.split(r"[,，、]", m.group(1)):
            # Never `_`: it can begin or end a file name, as in `_config.yml` or `__init__.py`.
            entry = entry.strip(" \t\r`'\"*")
            if entry and entry.lower() != "none":
                entries.append(entry)
    return entries


def conductor_write_file(entry, data):
    """The real path of the one project file an entry names, or None when it names no such file.

    An entry with a glob character, a directory, a path outside the project, and a path inside
    `.orchestra/` name nothing.
    """
    if _GLOB.search(entry) or entry.endswith(("/", os.sep)):
        return None
    root = project_dir(data)
    try:
        p = os.path.expanduser(entry)
        if not os.path.isabs(p):
            p = os.path.join(root, p)
        p = os.path.realpath(p)
        if os.path.isdir(p):
            return None
    except (OSError, ValueError):
        return None
    if p == root or not inside(p, root) or inside(p, orchestra_dir(data)):
        return None
    return p


def conductor_write_files(text, data):
    """The real paths of the project files the `Conductor writes:` lines of a brief name."""
    files = set()
    for entry in conductor_write_entries(text):
        p = conductor_write_file(entry, data)
        if p:
            files.add(p)
    return files


class Approval:
    """What a work folder's brief pre-approves, and which project files it lets the conductor write.

    state: `no-work` (no work folder), `no-brief`, `no-turn` (no user turn recorded), `newer`
    (the brief changed after the user last spoke), or `confirmed`. `listed` and `listed_files`
    are what the brief says. `keys` and `files` are what counts, and they are empty unless the
    state is `confirmed`.
    """

    def __init__(self, work_folder, listed, state, listed_files=frozenset()):
        self.work_folder = work_folder
        self.listed = listed
        self.state = state
        self.keys = listed if state == "confirmed" else set()
        self.listed_files = listed_files
        self.files = listed_files if state == "confirmed" else frozenset()

    @property
    def shown(self):
        return "`.orchestra/%s/brief.md`" % os.path.basename(self.work_folder) if self.work_folder else "`.orchestra/<work>/brief.md`"

    @property
    def unconfirmed(self):
        """Why the user has not confirmed this version of the brief, in the `newer` and `no-turn` states."""
        because = ("it was written or changed after the user last spoke" if self.state == "newer"
                   else "no user turn is recorded for this session")
        return because + ", so the user has not confirmed this version of the brief"


def read_approval(work_folder, data, turn, changed=None):
    """The approval that a work folder's brief gives, for a user who last spoke at `turn`.

    `changed` is when the brief last changed. By default it is the file's own time.
    """
    if work_folder is None:
        return Approval(None, set(), "no-work")
    brief = os.path.join(work_folder, "brief.md")
    if not os.path.isfile(brief):
        return Approval(work_folder, set(), "no-brief")
    text = read_text(brief)
    listed = preapproved_keys(text)
    files = frozenset(conductor_write_files(text, data))
    if changed is None:
        # Read the time after the text, so a change in between counts as unconfirmed.
        try:
            changed = os.path.getmtime(brief)
        except OSError:
            return Approval(work_folder, set(), "no-brief")
    # Stricter than check_brief in guard_agent.py, which lets a brief through when no user turn
    # is recorded. A pre-approved action runs with nobody present to confirm it, so it must not
    # be approved because a record is missing.
    if turn is None:
        state = "no-turn"
    elif changed > turn:
        state = "newer"
    else:
        state = "confirmed"
    return Approval(work_folder, listed, state, files)


def brief_approval(data):
    """The approval of the brief in the session's active work folder."""
    return read_approval(active_work(data), data, last_user_turn(data))


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
    """Drop records that are older than a month: the per-session ones, and the marks that
    check_report.py leaves for a subagent that never stopped cleanly.

    What is recorded about a report (`meta/`) is kept, because the report it describes stays in
    the project and may be read at any later time.
    """
    cutoff = time.time() - max_age_days * 86400
    base = state_dir()
    for folder, prefixes in ((os.path.join(base, "sessions"), ("",)), (base, ("handback-", "attempts-"))):
        try:
            for entry in os.scandir(folder):
                if entry.is_file() and entry.name.startswith(prefixes) and entry.stat().st_mtime < cutoff:
                    os.remove(entry.path)
        except OSError:
            pass


def _meta_path(work_folder, tid):
    digest = hashlib.sha1(os.path.realpath(work_folder).encode("utf-8")).hexdigest()[:12]
    folder = os.path.join(state_dir(), "meta", digest)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, safe_name(tid) + ".json")


def save_meta(data, work_folder, tid, meta):
    """Record which kind of subagent filed a report. Kept outside the project."""
    try:
        with open(_meta_path(work_folder, tid), "w", encoding="utf-8") as f:
            json.dump(meta, f)
    except OSError:
        pass


def load_meta(data, work_folder, tid):
    try:
        with open(_meta_path(work_folder, tid), encoding="utf-8") as f:
            meta = json.load(f)
        return meta if isinstance(meta, dict) else {}
    except (OSError, ValueError):
        return {}
