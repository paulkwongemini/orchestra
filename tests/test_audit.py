#!/usr/bin/env python3
"""Tests for scripts/audit.py.

Run: python3 tests/test_audit.py

Each test writes a transcript shaped like Claude Code's, with the user's prompts, the conductor's
tool calls and their results, and checks the findings.
"""
import datetime
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import BRIEF, FABLE, REPORT_PATH, REVIEW, REVIEW_PATH, SPEC_PATH, TASK, UNIT, ProjectTest

# The brief with both lines of `## Decision rights` written out
FULL_BRIEF = BRIEF + "\nPre-approved: none\n\nConductor writes: none\n"


class Transcript:
    """Builds transcript records one second apart."""

    START = 1_800_000_000

    def __init__(self):
        self.rows, self.t, self.n = [], self.START, 0

    def stamp(self):
        self.t += 1
        return datetime.datetime.fromtimestamp(self.t, datetime.timezone.utc).isoformat().replace("+00:00", "Z")

    def say(self, text):
        self.rows.append({"type": "user", "timestamp": self.stamp(), "origin": {"kind": "human"},
                          "message": {"role": "user", "content": text}})

    def tool(self, name, model=FABLE, error=None, **ti):
        self.n += 1
        tid = "tu%d" % self.n
        self.rows.append({"type": "assistant", "timestamp": self.stamp(), "isSidechain": False,
                          "message": {"model": model, "content": [{"type": "tool_use", "id": tid, "name": name, "input": ti}]}})
        result = {"type": "tool_result", "tool_use_id": tid, "content": error or "ok"}
        if error:
            result["is_error"] = True
        self.rows.append({"type": "user", "timestamp": self.stamp(), "message": {"role": "user", "content": [result]}})

    def skills(self, *names):
        for name in names:
            self.tool("Skill", skill="orchestra:" + name)


class AuditTest(ProjectTest):
    def setUp(self):
        super().setUp()
        self.write(UNIT + "/brief.md", FULL_BRIEF)
        self.write(SPEC_PATH, TASK)
        self.write(REPORT_PATH, "STATUS: DONE\n")
        self.tr = Transcript()

    def p(self, rel):
        return os.path.join(self.proj, rel)

    def audit(self, *extra):
        path = os.path.join(self.tmp, "t.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for row in self.tr.rows:
                f.write(json.dumps(row) + "\n")
        return self.run_json("audit.py", path, "--project", self.proj, "--json", *extra)

    def rules(self, out):
        return [(f["rule"], f["outcome"]) for f in out["findings"]]

    def clean_codex_session(self):
        """A Codex-mode session that keeps every rule, up to the first run."""
        tr = self.tr
        tr.say("/orchestra:conduct-codex fix the login bug")
        tr.skills("conductor", "cli-workers", "codex", "interview")
        tr.tool("AskUserQuestion", questions=[])
        tr.tool("Write", file_path=self.p(UNIT + "/brief.md"), content=FULL_BRIEF)
        tr.say("start now")
        tr.skills("documentation")
        tr.tool("Write", file_path=self.p(SPEC_PATH), content=TASK)
        tr.tool("Bash", command="python3 /x/scripts/worker.py run --cli codex --task %s --role implementer" % SPEC_PATH)

    def test_a_session_that_keeps_the_rules_has_no_findings(self):
        self.clean_codex_session()
        self.tr.tool("Bash", command="python3 -m unittest discover -s tests")
        self.tr.tool("Write", file_path=self.p(REVIEW_PATH), content="## Verdict\nPass\n")
        self.tr.tool("Read", file_path=self.p(REPORT_PATH))
        out = self.audit()
        self.assertEqual(out["findings"], [])
        self.assertGreater(out["calls"], 5)

    def test_breaking_the_rules_in_codex_mode(self):
        self.clean_codex_session()
        self.tr.tool("Read", file_path=self.p(REPORT_PATH))
        self.tr.tool("Edit", file_path=self.p("src/login.py"), old_string="a", new_string="b")
        self.tr.tool("Bash", command="codex exec -m gpt-6.2-sol 'fix it'")
        self.tr.tool("Agent", subagent_type="orchestra:sonnet-builder", prompt="x")
        self.tr.tool("Agent", subagent_type="orchestra:haiku-scout", prompt="FIND: x")
        self.tr.tool("Bash", command="git commit -m wip")
        found = self.rules(self.audit())
        for rule in ("A report was read before its review record", "The conductor implements",
                     "The CLI was run directly, not through worker.py", "A Claude subagent did a task in a CLI mode",
                     "A command the hooks refuse"):
            self.assertIn((rule, "went through"), found, rule)
        self.assertEqual(sum(1 for r, _ in found if r == "A Claude subagent did a task in a CLI mode"), 1)

    def test_a_run_before_the_brief_is_confirmed(self):
        tr = self.tr
        tr.say("/orchestra:conduct-codex")
        tr.skills("conductor", "cli-workers", "codex", "documentation")
        tr.tool("Write", file_path=self.p(UNIT + "/brief.md"), content=FULL_BRIEF)
        tr.tool("Bash", command="python3 worker.py run --cli codex --task %s --role engineer" % SPEC_PATH)
        self.assertIn(("A run before the brief was confirmed", "went through"), self.rules(self.audit()))

    def test_missing_skills_and_a_missing_spec(self):
        tr = self.tr
        tr.say("/orchestra:conduct-codex")
        tr.tool("AskUserQuestion", questions=[])
        tr.tool("Write", file_path=self.p(UNIT + "/tasks/T02-x.md"), content="## Goal\nx\n")
        tr.tool("Bash", command="python3 worker.py run --cli codex --task %s/tasks/T02-x.md --role engineer" % UNIT)
        tr.tool("Bash", command="python3 worker.py run --cli codex --task %s/tasks/T09-none.md --role engineer" % UNIT)
        found = self.rules(self.audit())
        # One skill for the question, one for the task spec, and three for each of the two runs.
        self.assertEqual(sum(1 for r, _ in found if r == "A rule skill was not loaded"), 1 + 1 + 3 + 3)
        self.assertEqual(sum(1 for r, _ in found if r == "A run without a task spec"), 2)

    def test_an_attempt_the_hooks_refused_in_the_claude_mode(self):
        tr = self.tr
        tr.say("/orchestra:conduct")
        tr.skills("conductor")
        tr.tool("Write", file_path=self.p("src/login.py"), content="x",
                error="PreToolUse:Write hook error: [orchestra] Conductor mode is on in this session")
        self.assertEqual(self.rules(self.audit()), [("The conductor implements", "refused")])

    def test_a_pre_approved_command_is_no_finding(self):
        self.write(UNIT + "/brief.md", FULL_BRIEF.replace("Pre-approved: none", "Pre-approved: git"))
        tr = self.tr
        tr.say("/orchestra:conduct-codex")
        tr.tool("Read", file_path=self.p(UNIT + "/brief.md"))
        tr.say("ok")
        tr.tool("Bash", command="git commit -m done")
        self.assertEqual(self.audit()["findings"], [])

    def test_a_blocked_report_is_read_without_a_review(self):
        self.write(REPORT_PATH, "STATUS: BLOCKED\n")
        self.tr.say("/orchestra:conduct-codex")
        self.tr.tool("Read", file_path=self.p(REPORT_PATH))
        self.assertEqual(self.audit()["findings"], [])

    def test_a_review_from_before_the_session_counts(self):
        self.tr.say("/orchestra:conduct-codex")
        self.tr.tool("Read", file_path=self.p(REPORT_PATH))
        unreviewed = [("A report was read before its review record", "went through")]
        self.assertEqual(self.rules(self.audit()), unreviewed)
        review = self.write(REVIEW_PATH, REVIEW, mtime=Transcript.START - 60)
        self.assertEqual(self.audit()["findings"], [])
        # A record that appeared during the session, without the conductor writing it, does not count.
        os.utime(review, (Transcript.START + 60, Transcript.START + 60))
        self.assertEqual(self.rules(self.audit()), unreviewed)

    def test_no_mode_and_other_models_are_not_judged(self):
        self.tr.tool("Edit", file_path=self.p("src/login.py"))
        self.assertEqual(self.audit()["calls"], 0)
        self.tr.say("/orchestra:conduct-codex")
        self.tr.tool("Edit", model="claude-sonnet-5-5", file_path=self.p("src/login.py"))
        self.assertEqual(self.audit()["findings"], [])
        self.tr.say("/orchestra:disable")
        self.tr.tool("Edit", file_path=self.p("src/login.py"))
        self.assertEqual(self.audit()["findings"], [])
        out = self.audit("--assume", "codex")
        self.assertEqual(len(out["findings"]), 1)  # the first edit, before any command, under the assumed mode


if __name__ == "__main__":
    unittest.main(verbosity=1)
