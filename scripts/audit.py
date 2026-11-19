#!/usr/bin/env python3
"""List every point in a session where the conductor broke, or tried to break, a rule that the
hooks guard in the claude mode.

    audit.py <transcript.jsonl> [--project <dir>] [--assume claude|codex|agy] [--json]

`--assume` sets the mode from the start, for a session whose mode was turned on before the
transcript begins.

The audit reads the session's transcript in order and judges each of the conductor's calls by the
hooks' own rules, with the brief's confirmation worked out from the transcript at that moment.
It also checks the rules of the CLI modes. Each finding says how the call ended:

  went through   nothing stopped it
  refused        a hook refused it in the session, so the transcript holds the attempt only
  asked          a hook, or the rule, sends it to the user for confirmation

In a CLI mode no hook runs, so a finding there went through unless the user was asked. In the
claude mode the hooks refuse most of these calls, so the findings there are attempts. The same
measure, attempts at a rule's edge, compares the two modes.

What the audit cannot see: whether a review was thorough, whether a task spec was clear, a git
command made while a run was in progress, and shell commands whose effect cannot be read from
the command line. It reads the brief's current text, so a pre-approval that was added or removed
during the session is judged by the brief as it is now.
"""
import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402
import guard_main as gm  # noqa: E402

MODE_COMMAND = re.compile(r"(?:^\s*|<command-name>\s*)/%s:(conduct-codex|conduct-agy|conductor|conduct|enable|disable)(?=\s|<|$)" % c.PLUGIN)
COMMAND_MODES = {"conduct": "claude", "enable": "claude", "conductor": "claude",
                 "conduct-codex": "codex", "conduct-agy": "agy", "disable": None}
UNIT_IN_PATH = re.compile(r"\.orchestra/([^/\s`'\"]+)/")
TASK_FILE = re.compile(r"\.orchestra/([^/\s`'\"]+)/tasks/((T\d+)[^/\s`'\"]*\.md)")
REPORT_FILE = re.compile(r"\.orchestra/([^/\s`'\"]+)/reports/(T\d+)[^/\s`'\"]*\.md")
REVIEW_FILE = re.compile(r"\.orchestra/([^/\s`'\"]+)/reviews/(T\d+)[^/\s`'\"]*\.md")
WORKER_RUN = re.compile(r"worker\.py\s+(run|resume)\b")
DIRECT_CLI = re.compile(r"(?:^|[\s;&|(])(?:codex\s+exec|agy\s+(?:-p|--print|--prompt)|gemini\s)")
WRITE_TOOLS = ("Write", "Edit", "NotebookEdit")
HOOK_REFUSAL = re.compile(r"hook error|\[orchestra\]|denied", re.I)


def epoch(text):
    try:
        return datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def load(path):
    rows = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                rows.append(entry)
    return rows


def human_text(entry):
    """The text of a prompt the user typed, or None."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain"):
        return None
    content = (entry.get("message") or {}).get("content")
    origin = entry.get("origin") or {}
    if isinstance(content, str) and origin.get("kind") == "human":
        return content
    return None


class Audit:
    def __init__(self, project, mode=None):
        self.project = os.path.realpath(project)
        self.data = {"cwd": self.project, "session_id": "audit"}
        self.mode = mode
        self.last_turn = None
        self.brief_written = {}     # unit -> when the conductor last wrote its brief
        self.reviews = {}           # (unit, task) -> when the review record was first written
        self.active = None          # the work folder the conductor last touched
        self.loaded = set()
        self.analysts = set()       # (unit, task) delegated to an analyst, whose report is read at once
        self.findings = []
        self.calls = 0

    # ---- state

    def unit_path(self, name):
        return os.path.join(self.project, c.WORKDIR, name)

    def approval(self):
        """The brief's approval as the hooks would have seen it at this moment."""
        if self.active is None:
            return gm.Approval(None, set(), "no-work")
        brief = os.path.join(self.unit_path(self.active), "brief.md")
        if not os.path.isfile(brief):
            return gm.Approval(self.unit_path(self.active), set(), "no-brief")
        text = c.read_text(brief)
        listed = c.preapproved_keys(text)
        files = frozenset(c.conductor_write_files(text, self.data))
        written = self.brief_written.get(self.active)
        if self.last_turn is None:
            state = "no-turn"
        elif written is not None and written > self.last_turn:
            state = "newer"
        else:
            state = "confirmed"
        return gm.Approval(self.unit_path(self.active), listed, state, files)

    def touch(self, text):
        m = UNIT_IN_PATH.search(text or "")
        if m and os.path.isdir(self.unit_path(m.group(1))):
            self.active = m.group(1)

    def add(self, when, rule, detail, outcome):
        self.findings.append({"time": when, "mode": self.mode or "off", "rule": rule, "detail": detail, "outcome": outcome})

    # ---- one call

    def call(self, when, name, ti, result, conducting_model=True):
        conducting = self.mode is not None and conducting_model
        refused = bool(result and result.get("is_error") and HOOK_REFUSAL.search(str(result.get("content"))))
        outcome = "refused" if refused else "went through"
        text = json.dumps(ti, ensure_ascii=False)
        self.touch(text)
        if name == "Skill":
            self.loaded.add(str(ti.get("skill") or "").split(":")[-1])
        if not conducting:
            return
        self.calls += 1
        cli = self.mode in c.CLI_MODES

        if name in WRITE_TOOLS:
            path = c.resolve(str(ti.get("file_path") or ti.get("notebook_path") or ""), self.data)
            if not gm.conductor_may_write(path, self.data, self.approval()):
                self.add(when, "The conductor implements", "%s %s" % (name, os.path.relpath(path, self.project)), outcome)
            rel = os.path.relpath(path, self.project)
            m = re.match(r"\.orchestra/([^/]+)/brief\.md$", rel)
            if m and not refused:
                self.brief_written[m.group(1)] = when
            m = REVIEW_FILE.search(rel)
            if m and not refused:
                self.reviews.setdefault((m.group(1), m.group(2)), when)
            if TASK_FILE.search(rel) and "documentation" not in self.loaded:
                self.add(when, "A rule skill was not loaded", "a task spec was written before `orchestra:documentation` was loaded", outcome)

        if name in ("Read", "Grep", "Glob"):
            target = str(ti.get("file_path") or ti.get("path") or "")
            self.report_read(when, target, outcome)

        if name == "AskUserQuestion" and "interview" not in self.loaded:
            self.add(when, "A rule skill was not loaded", "a question was asked before `orchestra:interview` was loaded", outcome)

        if name == "Bash":
            cmd = str(ti.get("command") or "")
            self.bash(when, cmd, outcome, cli)

        if name in ("Agent", "Workflow") and cli:
            stype = c.bare(str(ti.get("subagent_type") or ""))
            if stype != "haiku-scout":
                self.add(when, "A Claude subagent did a task in a CLI mode", "%s %s" % (name, stype or "without a type"), outcome)
        if name == "Agent" and c.bare(str(ti.get("subagent_type") or "")) == "opus-analyst":
            m = TASK_FILE.search(str(ti.get("prompt") or ""))
            if m:
                self.analysts.add((m.group(1), m.group(3)))

    def report_read(self, when, target, outcome):
        m = REPORT_FILE.search(target)
        if not m:
            return
        unit, task = m.group(1), m.group(2)
        report = os.path.join(self.unit_path(unit), "reports", task + ".md")
        if c.read_status(report) in ("BLOCKED", "FAILED") or (unit, task) in self.analysts:
            return
        if (unit, task) in self.reviews and self.reviews[(unit, task)] <= when:
            return
        review = os.path.join(self.unit_path(unit), "reviews", task + ".md")
        if (unit, task) not in self.reviews and os.path.isfile(review) and self.started and os.path.getmtime(review) < self.started:
            return  # reviewed before this session began
        self.add(when, "A report was read before its review record", os.path.relpath(report, self.project), outcome)

    def bash(self, when, cmd, outcome, cli):
        if gm._REPORTS_IN_COMMAND.search(cmd):
            m = REPORT_FILE.search(cmd)
            if m:
                self.report_read(when, cmd, outcome)
        worker = WORKER_RUN.search(cmd)
        if worker:
            self.worker_run(when, cmd, worker.group(1), outcome)
        elif cli and DIRECT_CLI.search(cmd):
            self.add(when, "The CLI was run directly, not through worker.py", cmd[:160], outcome)
        if worker:
            return  # the run itself writes files through the CLI, which the shell rules do not judge

        approval = self.approval()

        class AuditCtx(gm.Ctx):
            @property
            def approval(self_inner):
                return approval

        try:
            result = gm.judge_command(cmd, AuditCtx(self.data, "conductor", cmd))
        except Exception as e:  # a fault in the guard is not a finding
            result = None
            sys.stderr.write("audit: could not judge %r: %s\n" % (cmd[:80], e))
        if result is None:
            return
        kind, reason = result
        first = reason.split(". ")[0]
        if kind == "ask":
            self.add(when, "A command that needs the user's confirmation", "%s | %s" % (cmd[:160], first), "asked" if outcome == "went through" and not cli else outcome)
        else:
            self.add(when, "A command the hooks refuse", "%s | %s" % (cmd[:160], first), outcome)

    def worker_run(self, when, cmd, kind, outcome):
        m = TASK_FILE.search(cmd)
        if not m:
            self.add(when, "A run without a task spec", cmd[:160], outcome)
            return
        unit, spec_name, task = m.group(1), m.group(2), m.group(3)
        self.active = unit
        spec = os.path.join(self.unit_path(unit), "tasks", spec_name)
        if not os.path.isfile(spec):
            self.add(when, "A run without a task spec", spec_name, outcome)
        else:
            missing = c.missing_headings(c.read_text(spec), c.TASK_HEADINGS)
            if missing:
                self.add(when, "A run without a task spec", "%s lacks %s" % (spec_name, ", ".join(missing)), outcome)
        if kind == "run":
            state = self.approval().state
            if state in ("newer", "no-turn"):
                self.add(when, "A run before the brief was confirmed", "%s (brief %s)" % (task, state), outcome)
            for skill in ("conductor", "cli-workers", "codex"):
                if skill not in self.loaded:
                    self.add(when, "A rule skill was not loaded", "a run started before `orchestra:%s` was loaded" % skill, outcome)
        record = os.path.join(self.unit_path(unit), "files", task, "cli", "run.json")
        try:
            with open(record, encoding="utf-8") as f:
                if json.load(f).get("kind") == "readonly":
                    self.analysts.add((unit, task))
        except (OSError, ValueError):
            pass

    # ---- the whole transcript

    def run(self, rows):
        times = [epoch(r.get("timestamp")) for r in rows if r.get("timestamp")]
        self.started = min(t for t in times if t) if any(times) else None
        results = {}
        for r in rows:
            content = (r.get("message") or {}).get("content")
            if r.get("type") == "user" and isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "tool_result":
                        results[item.get("tool_use_id")] = item
        asked = set()
        for r in rows:
            when = epoch(r.get("timestamp")) or 0
            text = human_text(r)
            if text is not None:
                self.last_turn = when
                m = MODE_COMMAND.search(text)
                if m:
                    self.mode = COMMAND_MODES[m.group(1)]
                continue
            if r.get("type") == "user":
                for item in (r.get("message") or {}).get("content") or []:
                    if isinstance(item, dict) and item.get("type") == "tool_result" and item.get("tool_use_id") in asked:
                        self.last_turn = when  # the user answered a question
                continue
            if r.get("type") != "assistant" or r.get("isSidechain"):
                continue
            model = (r.get("message") or {}).get("model")
            conducting_model = c.is_conductor_model(model)
            for item in (r.get("message") or {}).get("content") or []:
                if not isinstance(item, dict) or item.get("type") != "tool_use":
                    continue
                ti = item.get("input") if isinstance(item.get("input"), dict) else {}
                if item.get("name") == "AskUserQuestion":
                    asked.add(item.get("id"))
                self.call(when, item.get("name") or "", ti, results.get(item.get("id")), conducting_model)
        return self.findings


def main(argv=None):
    parser = argparse.ArgumentParser(description="List where a conductor broke, or tried to break, the rules the hooks guard.")
    parser.add_argument("transcript")
    parser.add_argument("--project", default=os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    parser.add_argument("--json", action="store_true", help="print the findings as JSON")
    parser.add_argument("--assume", choices=c.MODES, help="the mode at the start of the transcript")
    args = parser.parse_args(argv)
    os.environ["CLAUDE_PROJECT_DIR"] = os.path.realpath(args.project)
    audit = Audit(args.project, args.assume)
    findings = audit.run(load(args.transcript))
    if args.json:
        print(json.dumps({"calls": audit.calls, "findings": findings}, indent=2, ensure_ascii=False))
        return 0
    print("Conductor calls judged: %d. Findings: %d." % (audit.calls, len(findings)))
    counts = {}
    for f in findings:
        key = (f["mode"], f["rule"], f["outcome"])
        counts[key] = counts.get(key, 0) + 1
    for (mode, rule, outcome), n in sorted(counts.items()):
        print("  %-6s %-55s %-13s %d" % (mode, rule, outcome, n))
    for f in findings:
        stamp = datetime.datetime.fromtimestamp(f["time"]).strftime("%H:%M:%S") if f["time"] else "?"
        print("%s %s | %s | %s | %s" % (stamp, f["mode"], f["rule"], f["outcome"], f["detail"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
