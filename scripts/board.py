#!/usr/bin/env python3
"""A board for the user: every worker run in progress, one pane per agent.

    worker.py board [--project DIR] [--once WIDTHxHEIGHT] [--window N]

The conductor opens it once, in a tab of the user's Terminal panel, when it starts the first run.
It shows every worker in progress in a pane of its own: in the Codex mode each run of
`scripts/worker.py` under the project's `.orchestra/` and each Codex subagent a run starts, and in
the claude mode each Claude subagent of a session in the project. At most two columns and two rows. More panes
than that go on further windows, which the user switches with 1-9 or the arrow keys. A pane goes
as soon as its agent finishes. q quits.

A run's panes read the same records as `worker.py watch`: the run's event stream, its guard's
log, and the subagents' session files. A Claude subagent's pane reads its transcript under
`~/.claude/projects/`. The conductor does not read the board: its review comes first.

`--once` prints one frame of the given size and stops, for tests and for a look without a terminal.
"""
import argparse
import collections
import glob
import json
import math
import os
import re
import sys
import time
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402
import worker as w  # noqa: E402

short = w.short

MAX_COLS, MAX_ROWS = 2, 2
TWO_COLUMNS_FROM, TWO_ROWS_FROM = 90, 24  # narrower or lower terminals get one column or one row
KEEP = 400


# ---------------------------------------------------------------- text in columns

def cells(ch):
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in "WF" else 1


def fit(text, n, fill=" "):
    """`text` on one line, cut with an ellipsis or padded with `fill` to exactly `n` columns."""
    text = re.sub(r"[\r\n\t]+", " ", str(text))
    if n <= 0:
        return ""
    if sum(cells(ch) for ch in text) > n:
        out, used = [], 0
        for ch in text:
            if used + cells(ch) > n - 1:
                break
            out.append(ch)
            used += cells(ch)
        return "".join(out) + "…" + fill * (n - 1 - used)
    return text + fill * (n - sum(cells(ch) for ch in text))


def wrap(text, n):
    """`text` broken into rows of at most `n` columns, at spaces where it can be."""
    text = re.sub(r"\s+", " ", str(text)).strip()
    rows, row, used = [], "", 0
    for word in text.split(" "):
        wc = sum(cells(ch) for ch in word)
        if row and used + 1 + wc > n:
            rows.append(row)
            row, used = "", 0
        while wc > n:  # a word longer than a row is cut
            head = fit(word, n).rstrip("…").rstrip()
            rows.append(head)
            word = word[len(head):]
            wc = sum(cells(ch) for ch in word)
        row, used = (row + " " + word, used + 1 + wc) if row else (word, wc)
    if row:
        rows.append(row)
    return rows


def tidy(text, project):
    """Paths made short: relative inside the project, the last two parts outside it."""
    text = str(text).replace(project + os.sep, "")

    def short_path(m):
        parts = m.group(0).split("/")
        return "…/" + "/".join(parts[-2:]) if len(parts) > 4 else m.group(0)
    return re.sub(r"(?<![\w.~])/[^\s'\"`;|&<>]+", short_path, text)


def first_sentence(text, limit=200):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    m = re.match(r"^(.+?[.!?])(\s|$)", text)
    return (m.group(1) if m else text)[:limit]


def goal_of(spec):
    """The first sentence of a task spec's `## Goal`, or empty."""
    return first_sentence(c.section_body(c.read_text(spec), "Goal") or "") if spec and os.path.isfile(spec) else ""


def duration(seconds):
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        return "%dh%02dm" % (seconds // 3600, seconds % 3600 // 60)
    return "%dm%02ds" % (seconds // 60, seconds % 60) if seconds >= 60 else "%ds" % seconds


def tokens(n):
    return "%dk tok" % round(n / 1000) if n >= 1000 else "%d tok" % n


def model_name(model):
    """`gpt-6.2-astra` as `astra`, `claude-opus-5-5` as `opus 5.5`."""
    if not model:
        return None
    m = re.match(r"^claude-([a-z]+)-(\d+)-(\d+)", str(model))
    return "%s %s.%s" % m.groups() if m else str(model).split("-")[-1]


UNKNOWN = -1  # the time of a line that was already there when the board first read the run


def clock(when):
    if when == UNKNOWN:
        return "--:--"
    return time.strftime("%H:%M", time.localtime(when)) if when else time.strftime("%H:%M")


# ---------------------------------------------------------------- what the panes hold

class Pane:
    """One agent: a run's worker or one of its subagents, kept as entries a person can read.

    An entry is what the agent said, an action it took with whether it worked, or a note. On the
    board its messages lead, in full, and its actions sit under them, a line each.
    """

    SYMBOL = {"ok": "✓", "fail": "✗", "run": "…", "guard": "!"}

    def __init__(self, title, started, role=None):
        self.title, self.started, self.role = title, started, role
        self.model = self.effort = self.tokens = None
        self.goal = ""
        self.refused = 0
        self.subagents = 0
        self.entries = collections.deque(maxlen=KEEP)
        self.open = {}  # the actions still running, by the id their result will carry

    def say(self, when, text):
        if str(text).strip():
            self.entries.append({"kind": "say", "when": when, "text": text})

    def act(self, when, text, key=None, status="ok", detail=""):
        entry = {"kind": "act", "when": when, "text": text, "status": status, "detail": detail}
        self.entries.append(entry)
        if key and status == "run":
            self.open[key] = entry

    def finish(self, key, ok, detail=""):
        entry = self.open.pop(key, None)
        if entry:
            entry["status"], entry["detail"] = ("ok", "") if ok else ("fail", detail)
        return entry is not None

    def refuse(self, when, text, key=None):
        self.refused += 1
        entry = self.open.pop(key, None) if key else None
        if entry:
            entry["status"], entry["detail"] = "guard", "refused: " + text
        else:
            self.entries.append({"kind": "act", "when": when, "text": "refused: " + text, "status": "guard", "detail": ""})

    def note(self, when, text):
        self.entries.append({"kind": "note", "when": when, "text": text})

    def rows(self, width, now):
        """The entries as (text, bold) rows of at most `width` columns, oldest first."""
        out = []
        for e in self.entries:
            if e["kind"] == "say":
                lines = wrap(e["text"], max(8, width - 6))
                if len(lines) > 4:
                    lines = lines[:4]
                    lines[-1] = fit(lines[-1] + " …", width - 6).rstrip()
                out += [("%-5s %s" % (clock(e["when"]) if i == 0 else "", line), True) for i, line in enumerate(lines)]
            elif e["kind"] == "act":
                text = "  %s %s" % (self.SYMBOL[e["status"]], e["text"])
                if e["detail"]:
                    text += " — " + e["detail"]
                if e["status"] == "run" and e["when"] not in (None, UNKNOWN):
                    text += " (%s)" % duration(now - e["when"])
                out.append((text, False))
            else:
                out.append(("  · " + e["text"], False))
        return out

    def meta(self, now):
        parts = [model_name(self.model), self.effort, duration(now - self.started)]
        if self.tokens:
            parts.append(tokens(self.tokens))
        return " · ".join(p for p in parts if p)

    def footer(self):
        parts = ["guard %d" % self.refused]
        if self.role is not None:
            parts.append("subagents %d" % self.subagents)
        return " · ".join(parts)


def plan_note(items, done_key, text_key):
    """`plan 2/5 · the step in hand` from a todo list."""
    items = [i for i in items or [] if isinstance(i, dict)]
    if not items:
        return None
    done = sum(1 for i in items if i.get(done_key) in (True, "completed"))
    current = next((i for i in items if i.get(done_key) not in (True, "completed")), items[-1])
    return "plan %d/%d · %s" % (done, len(items), current.get(text_key) or "")


def codex_command(item):
    cmd = item.get("command")
    cmd = cmd[-1] if isinstance(cmd, list) and cmd else str(cmd or "")
    m = w.BASH_WRAP.match(cmd)
    return re.sub(r"^cd \S+ && ", "", m.group(2) if m else cmd)


class RunView:
    """A run in progress, read the way `worker.py watch` reads it."""

    def __init__(self, record, live, project, home):
        self.record, self.project = record, project
        self.round, self.started = int(live.get("round", 1)), float(live.get("started") or time.time())
        self.pid = live.get("pid")
        task = os.path.basename(os.path.dirname(record))
        title = "%s · %s" % (task, live.get("role") or "worker") + (" · round %d" % self.round if self.round > 1 else "")
        self.task, self.main = task, Pane(title, self.started, role=live.get("role") or "worker")
        self.main.model, self.main.effort = live.get("model"), live.get("effort")
        self.main.goal = goal_of(live.get("spec"))
        self.events = w.Tail(os.path.join(record, "events-%d.jsonl" % self.round))
        self.guard = w.Tail(os.path.join(record, "guard.jsonl"))
        self.agents, self.subs, self.root_file = w.Subagents(home, self.started), {}, None
        # The event stream has no times. Lines read as they come get the time they are read; the
        # lines a run had before the board found it get none.
        self.backlog = time.time() - self.started > 3

    def ended(self):
        if w.run_ended(self.record, self.round, self.started):
            return True
        if self.pid:  # a worker.py that died writes no run.json
            try:
                os.kill(int(self.pid), 0)
            except ProcessLookupError:
                return True
            except (OSError, ValueError):
                pass
        return False

    def local(self, text):
        return tidy(text, self.project)

    def event(self, pane, e, now):
        """One line of the run's event stream into its pane."""
        t, item = e.get("type"), e.get("item") if isinstance(e.get("item"), dict) else {}
        it, key = item.get("type"), item.get("id")
        if t in ("turn.failed", "error"):
            pane.note(now, "error: " + short(json.dumps(e.get("error") or e.get("message") or e, ensure_ascii=False), 200))
        elif t == "item.started" and it == "command_execution":
            pane.act(now, "run " + self.local(codex_command(item)), key=key, status="run")
        elif t != "item.completed":
            return
        elif it == "command_execution":
            code = item.get("exit_code")
            if not pane.finish(key, code in (0, None), "exit %s" % code):
                pane.act(now, "run " + self.local(codex_command(item)), status="ok" if code in (0, None) else "fail",
                         detail="" if code in (0, None) else "exit %s" % code)
        elif it == "agent_message":
            pane.say(now, self.local(item.get("text") or ""))
        elif it == "file_change":
            pane.act(now, "edit " + ", ".join(self.local(ch.get("path", "")) for ch in item.get("changes") or []))
        elif it == "todo_list":
            note = plan_note(item.get("items"), "completed", "text")
            if note:
                pane.note(now, note)
        elif it == "web_search":
            pane.act(now, "search the web: " + str(item.get("query") or ""))
        elif it == "mcp_tool_call":
            pane.act(now, "use %s" % (item.get("tool") or "a tool"))

    def sub_item(self, pane, when, item):
        """One finished item of a Codex subagent's session file into its pane."""
        kind = item.get("type")
        if kind == "AgentMessage":
            text = self.local(" ".join(str(x.get("text", "")) for x in item.get("content") or [] if isinstance(x, dict)))
            if getattr(pane, "goal_pending", False) and text.strip():
                pane.goal, pane.goal_pending = first_sentence(text), False
            pane.say(when, text)
        elif kind == "CommandExecution":
            code = item.get("exit_code")
            pane.act(when, "run " + self.local(codex_command(item)), status="ok" if code in (0, None) else "fail",
                     detail="" if code in (0, None) else "exit %s" % code)
        elif kind == "FileChange":
            changes = item.get("changes") if isinstance(item.get("changes"), dict) else {}
            pane.act(when, "edit " + ", ".join(self.local(k) for k in changes))
        elif kind == "Extension":
            pane.act(when, "search the web: " + str(item.get("query") or ""))
        elif kind == "McpToolCall":
            pane.act(when, "use %s" % (item.get("tool") or "a tool"))

    def update(self):
        now = UNKNOWN if self.backlog else time.time()
        self.backlog = False
        for e in self.events.read():
            if not isinstance(e, dict):
                continue
            if e.get("type") == "thread.started":
                self.agents.set_root(e.get("thread_id"))
            self.event(self.main, e, now)
        for tid, when, kind, value in self.agents.items():
            name = self.agents.name(tid)
            if kind == "start":
                # Codex keeps the message that sets a subagent's task only in encrypted form, so its
                # goal line waits for what it first says, which as a rule restates the task.
                job = str((self.agents.meta.get(tid) or {}).get("agent_path") or "").rsplit("/", 1)[-1]
                self.subs[tid] = Pane("%s › %s" % (self.task, name) + (" (%s)" % job if job else ""), when or time.time())
                self.subs[tid].goal, self.subs[tid].goal_pending = "started by %s" % self.task, True
                self.main.note(when, "%s started" % name)
                continue
            pane = self.subs.get(tid)
            if pane is None:
                continue
            if kind == "item":
                self.sub_item(pane, when, value)
            elif kind == "tokens":
                pane.tokens = value
            elif kind == "model":
                pane.model, pane.effort = value
            elif kind == "done":
                del self.subs[tid]  # a finished agent's pane goes at once
                self.main.note(when, "%s finished" % name)
        self.main.subagents = len(self.subs)
        root = self.agents.meta.get(self.agents.root or "")
        if root and self.root_file is None:
            self.root_file = w.Tail(root["path"])
        for e in self.root_file.read() if self.root_file else ():
            for kind, value in w.rollout_items(e) if isinstance(e, dict) else ():
                if kind == "tokens":
                    self.main.tokens = value
        for r in self.guard.read():
            if r.get("fault") or r.get("at", 0) < self.started:
                continue
            pane = self.subs.get(r.get("thread")) or self.main
            pane.refuse(r.get("at"), "%s — %s" % (self.local(short(w.called(r), 120)), first_sentence(r.get("reason"))))

    def panes(self):
        return [self.main] + sorted(self.subs.values(), key=lambda p: (p.started, p.title))


TASK_IN_PROMPT = re.compile(r"((?:/[^\s`'\"]*/)?\.orchestra/[^/\s`'\"]+/tasks/(T\d+)[^\s`'\"]*?\.md)")
REFUSED = re.compile(r"hook error|\[orchestra\]|PreToolUse|denied", re.I)
STALE = 30 * 60  # an unfinished subagent whose transcript has not moved for this long is taken as gone


def claude_projects():
    return os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "projects")


def project_slug(project):
    """The name Claude Code gives a project's folder under `~/.claude/projects/`."""
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(project))


def claude_action(name, ti, local):
    """What a Claude subagent's tool call does, for a person, or None for one that is a note."""
    ti = ti if isinstance(ti, dict) else {}
    if name == "Bash":
        cmd = re.sub(r"^cd \S+ && ", "", str(ti.get("command") or ""))
        desc = str(ti.get("description") or "").strip()
        return "%s · %s" % (desc, local(cmd)) if desc else "run " + local(cmd)
    if name in ("Edit", "MultiEdit", "NotebookEdit"):
        return "edit " + local(str(ti.get("file_path") or ti.get("notebook_path") or ""))
    if name == "Write":
        return "write " + local(str(ti.get("file_path") or ""))
    if name == "Read":
        return "read " + local(str(ti.get("file_path") or ""))
    if name == "Grep":
        return "search for '%s'%s" % (ti.get("pattern") or "", " in " + local(str(ti["path"])) if ti.get("path") else "")
    if name == "Glob":
        return "find files " + str(ti.get("pattern") or "")
    if name in ("Agent", "Task"):
        return "start a subagent: " + str(ti.get("description") or ti.get("subagent_type") or "")
    if name == "SubagentHandback":
        return "send the report"
    if name == "WebFetch":
        return "read " + str(ti.get("url") or "a web page")
    if name == "WebSearch":
        return "search the web: " + str(ti.get("query") or "")
    if name == "Skill":
        return "load the skill " + str(ti.get("skill") or "")
    return "use " + str(name)


class ClaudeView:
    """A Claude subagent of a session in this project, read from its transcript as it is written.

    Claude Code keeps each subagent's transcript at
    `~/.claude/projects/<project>/<session>/subagents/agent-<id>.jsonl`, a line per message as
    it comes, and beside it `agent-<id>.meta.json`, which says `"finished": true` once it ends.
    """

    def __init__(self, meta_path, meta, project):
        self.meta_path, self.project = meta_path, project
        self.base = meta_path[:-len(".meta.json")]
        self.started = os.path.getmtime(meta_path)
        kind = str(meta.get("agentType") or "subagent").split(":")[-1]
        self.kind, self.description = kind, meta.get("description") or ""
        self.task = None
        self.main = Pane(self.title(), self.started)
        self.main.role = None
        try:
            with open(self.base + ".prefix.json", encoding="utf-8") as f:
                self.main.model = (json.load(f).get("state") or {}).get("model")
        except (OSError, ValueError, AttributeError):
            pass
        self.tail, self.usage, self.first = w.Tail(self.base + ".jsonl"), {}, True

    def title(self):
        name = self.kind + (" · " + self.description if not self.task and self.description else "")
        return "%s · %s" % (self.task, name) if self.task else name

    def local(self, text):
        return tidy(text, self.project)

    def ended(self):
        try:
            with open(self.meta_path, encoding="utf-8") as f:
                if json.load(f).get("finished"):
                    return True
        except (OSError, ValueError):
            return True
        try:
            return time.time() - os.path.getmtime(self.base + ".jsonl") > STALE
        except OSError:
            return time.time() - self.started > STALE

    def update(self):
        pane = self.main
        for e in self.tail.read():
            if not isinstance(e, dict) or e.get("isMeta"):
                continue
            message = e.get("message") if isinstance(e.get("message"), dict) else {}
            content = message.get("content")
            when = c.epoch(e.get("timestamp"))
            if e.get("type") == "user" and self.first:  # the prompt: the task spec's path names the task
                self.first = False
                text = content if isinstance(content, str) else " ".join(
                    str(x.get("text", "")) for x in content or [] if isinstance(x, dict))
                m = TASK_IN_PROMPT.search(text)
                if m:
                    self.task = m.group(2)
                    pane.title = self.title()
                    spec = m.group(1) if os.path.isabs(m.group(1)) else os.path.join(self.project, m.group(1))
                    pane.goal = goal_of(spec)
                if not pane.goal:
                    pane.goal = first_sentence(self.description or text)
                continue
            if not isinstance(content, list):
                continue
            if e.get("type") == "assistant":
                if str(message.get("model") or "").startswith("claude-"):
                    pane.model = message["model"]
                usage = message.get("usage") or {}
                if message.get("id") and usage:
                    self.usage[message["id"]] = sum(int(usage.get(k) or 0) for k in (
                        "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"))
                    pane.tokens = sum(self.usage.values())
                for x in content:
                    if not isinstance(x, dict):
                        continue
                    if x.get("type") == "text":
                        pane.say(when, self.local(x.get("text", "")))
                    elif x.get("type") == "tool_use" and x.get("name") == "TodoWrite":
                        note = plan_note((x.get("input") or {}).get("todos"), "status", "content")
                        if note:
                            pane.note(when, note)
                    elif x.get("type") == "tool_use":
                        pane.act(when, claude_action(x.get("name"), x.get("input"), self.local), key=x.get("id"), status="run")
            elif e.get("type") == "user":
                for x in content:
                    if not (isinstance(x, dict) and x.get("type") == "tool_result"):
                        continue
                    body = x.get("content")
                    text = body if isinstance(body, str) else " ".join(
                        str(y.get("text", "")) for y in body or [] if isinstance(y, dict))
                    text = re.sub(r"</?tool_use_error>", "", text)
                    if x.get("is_error") and REFUSED.search(text):
                        pane.refuse(when, first_sentence(self.local(re.sub(r"^.*?\[orchestra\]\s*", "", text))), key=x.get("tool_use_id"))
                    else:
                        pane.finish(x.get("tool_use_id"), not x.get("is_error"), first_sentence(self.local(text), 160))

    def panes(self):
        return [self.main]


class Board:
    def __init__(self, project, home):
        self.project, self.home, self.runs, self.window = project, home, {}, 0
        self.claude = {}  # meta path -> ClaudeView

    def refresh_claude(self):
        """The Claude subagents of every session in this project that have not finished."""
        pattern = os.path.join(claude_projects(), project_slug(self.project), "*", "subagents", "agent-*.meta.json")
        for meta_path in glob.glob(pattern):
            view = self.claude.get(meta_path)
            if view is None:
                try:
                    if time.time() - os.path.getmtime(meta_path[:-len(".meta.json")] + ".jsonl") > STALE:
                        continue
                    with open(meta_path, encoding="utf-8") as f:
                        meta = json.load(f)
                except (OSError, ValueError):
                    continue
                if meta.get("finished"):
                    continue
                view = self.claude[meta_path] = ClaudeView(meta_path, meta, self.project)
            view.update()
            if view.ended():
                del self.claude[meta_path]  # a finished subagent's pane goes at once

    def refresh(self):
        for live_path in glob.glob(os.path.join(self.project, c.ORCHESTRA_DIR, "*", "files", "*", "cli", "live.json")):
            record = os.path.dirname(live_path)
            try:
                with open(live_path, encoding="utf-8") as f:
                    live = json.load(f)
            except (OSError, ValueError):
                continue
            view = self.runs.get(record)
            if view and view.round != int(live.get("round", 1)):
                view = None  # a resume started a new round
            if view is None:
                view = RunView(record, live, self.project, self.home)
                if view.ended():
                    continue
                self.runs[record] = view
            view.update()
            if view.ended():
                del self.runs[record]  # a finished run's panes go at once
        self.refresh_claude()

    def panes(self):
        views = list(self.runs.values()) + list(self.claude.values())
        return [p for v in sorted(views, key=lambda v: (v.started, v.main.title)) for p in v.panes()]


# ---------------------------------------------------------------- drawing

def layout(n, width, height, cols, rows):
    """(x, y, w, h) for `n` panes, at most `cols` across and `rows` down."""
    nrows = max(1, min(rows, math.ceil(n / cols)))
    out, placed = [], 0
    for r in range(nrows):
        y0, y1 = height * r // nrows, height * (r + 1) // nrows
        across = n - placed if r == nrows - 1 else min(cols, n - placed)
        for i in range(across):
            x0, x1 = width * i // across, width * (i + 1) // across
            out.append((x0, y0, x1 - x0, y1 - y0))
        placed += across
    return out


def draw_pane(pane, w_, h, now):
    """The pane's rows, each exactly `w_` columns, and the spans in it to show in bold."""
    inner = w_ - 2
    title = " %s " % pane.title
    rows = ["┌─" + fit(title, inner - 1, "─") + "┐"]
    bold = [(0, 2, min(sum(cells(ch) for ch in title), inner - 1))]
    if h >= 3:
        rows.append("│" + fit(" " + pane.meta(now), inner) + "│")
    if h >= 5 and pane.goal:
        rows.append("│" + fit(" goal: " + pane.goal, inner) + "│")
        rows.append("├" + "─" * inner + "┤")
    body = max(0, h - len(rows) - 1)
    entries = pane.rows(inner - 2, now)[-body:] if body else []
    for text, strong in entries:
        if strong:
            bold.append((len(rows), 2, min(sum(cells(ch) for ch in text), inner - 1)))
        rows.append("│" + fit(" " + text, inner) + "│")
    rows += ["│" + " " * inner + "│"] * (body - len(entries))
    if h >= 2:
        rows.append("└─" + fit(" %s " % pane.footer(), inner - 1, "─") + "┘")
    return rows[:h], [b for b in bold if b[0] < h]


def render(board, width, height, now=None):
    """The screen's rows, each `width` columns, and the spans to show in bold: (row, col, length)."""
    now = now or time.time()
    panes = board.panes()
    cols = MAX_COLS if width >= TWO_COLUMNS_FROM else 1
    rows = MAX_ROWS if height >= TWO_ROWS_FROM else 1
    per = cols * rows
    windows = max(1, math.ceil(len(panes) / per))
    board.window = min(max(board.window, 0), windows - 1)
    tabs = " ".join(("[%d]" if i == board.window else " %d ") % (i + 1) for i in range(windows)) if windows > 1 else ""
    head = " orchestra board · %s · %d running" % (os.path.basename(board.project), len(panes))
    keys = "1-9 window · ←→ · q quit " if windows > 1 else "q quit "
    gap = width - sum(cells(ch) for ch in head) - sum(cells(ch) for ch in keys) - len(tabs) - 2
    screen = [fit(head + " " * max(1, gap // 2) + tabs + " " * max(1, gap - gap // 2) + keys, width)]
    bold = [(0, 0, min(width, sum(cells(ch) for ch in head)))]
    area = height - 1
    shown = panes[board.window * per:(board.window + 1) * per]
    if not shown:
        blank = [" " * width] * area
        if area:
            blank[area // 2] = fit(" " * max(0, (width - 60) // 2) + "Nothing in progress. Each worker shows here as it starts.", width)
        return screen + blank, bold
    grid = [[] for _ in range(area)]
    for pane, (x, y, w_, h) in zip(shown, layout(len(shown), width, area, cols, rows)):
        drawn, spans = draw_pane(pane, w_, h, now)
        for i, row in enumerate(drawn):
            grid[y + i].append((x, row))
        bold += [(1 + y + r, x + col, n) for r, col, n in spans]
    for row in grid:
        screen.append("".join(text for _x, text in sorted(row)) if row else " " * width)
    return screen, bold


# ---------------------------------------------------------------- the terminal

def run_curses(board):
    import curses
    import locale
    locale.setlocale(locale.LC_ALL, "")

    def loop(scr):
        curses.curs_set(0)
        scr.timeout(1000)
        last = 0
        while True:
            if time.time() - last >= 1:
                board.refresh()
                last = time.time()
            height, width = scr.getmaxyx()
            screen, bold = render(board, width, height)
            scr.erase()
            for y, row in enumerate(screen[:height]):
                try:
                    scr.addstr(y, 0, row)
                except curses.error:
                    pass  # writing the screen's last cell raises after the cell is drawn
            for y, x, n in bold:
                try:
                    scr.chgat(y, x, n, curses.A_BOLD)
                except curses.error:
                    pass
            scr.refresh()
            key = scr.getch()
            if key in (ord("q"), ord("Q")):
                return
            if ord("1") <= key <= ord("9"):
                board.window = key - ord("1")
            elif key in (curses.KEY_RIGHT, ord("\t"), ord("l")):
                board.window += 1
            elif key in (curses.KEY_LEFT, curses.KEY_BTAB, ord("h")):
                board.window -= 1

    try:
        curses.wrapper(loop)
    except KeyboardInterrupt:
        pass
    return 0


def find_project(start):
    """The nearest folder at or above `start` that holds `.orchestra/`, or `start` itself."""
    here = os.path.realpath(start)
    while True:
        if os.path.isdir(os.path.join(here, c.ORCHESTRA_DIR)):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return os.path.realpath(start)
        here = parent


def main(argv=None):
    parser = argparse.ArgumentParser(prog="worker.py board", description="Every worker run in progress, one pane per agent.")
    parser.add_argument("--project", default=".", help="the project, or a folder inside it")
    parser.add_argument("--once", metavar="WIDTHxHEIGHT", help="print one frame of this size and stop")
    parser.add_argument("--window", type=int, default=1, help="with --once, the window to print")
    args = parser.parse_args(argv)
    board = Board(find_project(args.project), w.Codex().home())
    if args.once:
        m = re.match(r"^(\d+)x(\d+)$", args.once)
        if not m:
            parser.error("--once takes a size such as 120x40")
        board.refresh()
        board.window = args.window - 1
        screen, _bold = render(board, int(m.group(1)), int(m.group(2)))
        print("\n".join(row.rstrip() for row in screen))
        return 0
    if not sys.stdout.isatty():
        print("ERROR: the board needs a terminal. Use --once for one frame.")
        return 1
    return run_curses(board)


if __name__ == "__main__":
    sys.exit(main())
