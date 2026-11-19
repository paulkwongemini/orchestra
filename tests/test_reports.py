#!/usr/bin/env python3
"""Tests for scripts/report_gate.py and scripts/check_report.py: when the conductor may open a
report, and what a subagent's last message and its report have to hold.

Run: python3 tests/test_reports.py
"""
import json
import os
import re
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import (BRIEF, COMMAND_CHECK, MANUAL_CHECK, OTHER_UNIT, REPORT_DONE, REPORT_PATH, REVIEW, REVIEW_KO,
                     REVIEW_PATH, ROOT, SONNET, STATUS_DONE, UNIT, HookTest, request, response, verification,
                     verified_report)

ROSTER = ("opus-engineer", "sonnet-builder", "opus-analyst", "sonnet-writer")
EDITOR_DONE = "STATUS: DONE\nFILES: src/login.py, src/session.py\nCHECK: exit 0"


class ReportGateTest(HookTest):
    """Principle 7: review independently, then read the report"""

    def read(self, rel=REPORT_PATH):
        return self.verdict("report_gate.py", self.main("Read", {"file_path": rel}))

    def test_other_files_are_not_gated(self):
        self.write("src/app.py", "print(1)\n")
        self.assertEqual(self.read("src/app.py"), "allow")
        self.assertEqual(self.read(UNIT + "/reports/missing.md"), "allow")
        self.write(UNIT + "/brief.md", BRIEF)
        self.assertEqual(self.read(UNIT + "/brief.md"), "allow")

    def test_report_opens_only_after_a_complete_newer_review_of_its_work_folder(self):
        no_checks = "## Scope reviewed\ndiff\n\n## Verdict\nPass\n"
        formality = "## Scope reviewed\ndiff\n\n## Checks run\nnone\n\n## Verdict\nPass\n"
        elsewhere = OTHER_UNIT + "/reviews/T01.md"
        cases = [  # what the row shows, the report's mtime, the review's path, text and mtime, the verdict
            ("no review", 1000, None, None, None, "deny"),
            ("no `Checks run`", 1000, REVIEW_PATH, no_checks, 2000, "deny"),
            ("`Checks run` is a formality", 1000, REVIEW_PATH, formality, 2000, "deny"),
            ("empty verdict", 1000, REVIEW_PATH, REVIEW.replace("Pass.\n", ""), 2000, "deny"),
            ("review older than the report", 2000, REVIEW_PATH, REVIEW, 1000, "deny"),
            ("review of another work folder", 1000, elsewhere, REVIEW, 2000, "deny"),
            ("complete review", 1000, REVIEW_PATH, REVIEW, 2000, "allow"),
            ("review body in another language", 1000, REVIEW_PATH, REVIEW_KO, 2000, "allow"),
        ]
        for shows, report_at, review, text, review_at, expected in cases:
            with self.subTest(shows):
                shutil.rmtree(os.path.join(self.proj, ".orchestra"), True)
                self.write(REPORT_PATH, REPORT_DONE, mtime=report_at)
                if review:
                    self.write(review, text, mtime=review_at)
                self.assertEqual(self.read(), expected)

    def test_rework_needs_a_new_review(self):
        self.write(REPORT_PATH, REPORT_DONE, mtime=1000)
        self.write(REVIEW_PATH, REVIEW, mtime=2000)
        self.assertEqual(self.read(), "allow")
        self.write(REPORT_PATH, REPORT_DONE + "Reworked.\n", mtime=3000)
        self.assertEqual(self.read(), "deny")
        self.write(REVIEW_PATH, REVIEW, mtime=4000)
        self.assertEqual(self.read(), "allow")

    def test_blocked_and_failed_reports_open_at_once(self):
        self.write(REPORT_PATH, "STATUS: BLOCKED\n\nThe fix needs a change outside the scope.\n")
        self.assertEqual(self.read(), "allow")
        self.write(REPORT_PATH, "**STATUS:** FAILED\n")
        self.assertEqual(self.read(), "allow")

    def analyst_reports(self):
        """An analyst hands back a DONE report with a manual check."""
        self.write(REPORT_PATH, verified_report([MANUAL_CHECK]))
        self.assertEqual(self.stop("STATUS: DONE\nTASK: T01\nREPORT: %s" % REPORT_PATH, "opus-analyst"), "allow")

    def test_analyst_report_opens_at_once(self):
        self.analyst_reports()
        self.assertEqual(self.read(), "allow")

    def test_analyst_report_in_one_folder_does_not_open_another(self):
        self.analyst_reports()
        self.write(OTHER_UNIT + "/reports/T01.md", REPORT_DONE)
        self.assertEqual(self.read(OTHER_UNIT + "/reports/T01.md"), "deny")

    def test_subagents_are_not_gated(self):
        self.write(REPORT_PATH, REPORT_DONE)
        payload = self.sub("opus-engineer", "Read", {"file_path": REPORT_PATH})
        self.assertEqual(self.verdict("report_gate.py", payload), "allow")

    def test_an_ordinary_session_is_left_alone(self):
        self.write(REPORT_PATH, REPORT_DONE)
        self.set_transcript([request(SONNET), response(SONNET)])
        self.assertEqual(self.read(), "allow")

    def test_task_files_are_not_gated(self):
        self.write(UNIT + "/files/T01/out.txt", "x\n")
        self.assertEqual(self.read(UNIT + "/files/T01/out.txt"), "allow")
        payload = self.main("Grep", {"pattern": "x", "path": UNIT + "/files"})
        self.assertEqual(self.verdict("report_gate.py", payload), "allow")

    def test_grep_in_reports_is_denied(self):
        self.write(REPORT_PATH, REPORT_DONE)
        payload = self.main("Grep", {"pattern": "passed", "path": UNIT + "/reports"})
        self.assertEqual(self.verdict("report_gate.py", payload), "deny")
        payload = self.main("Grep", {"pattern": "passed", "path": "src"})
        self.assertEqual(self.verdict("report_gate.py", payload), "allow")


class CheckReportTest(HookTest):
    """Principle 7: handback format and verification records for DONE."""

    def test_message_off_the_format_is_blocked(self):
        self.write(REPORT_PATH, REPORT_DONE)
        self.assert_all("block", self.stop, (
            "I added session expiry handling and all tests pass.",
            STATUS_DONE + "\n\nAll tests passed.",
            STATUS_DONE.replace("DONE", "DONE (all tests pass)"),
            STATUS_DONE.replace("TASK: T01", "TASK: T01 fixed everything"),
            STATUS_DONE + "\n\n모든 테스트가 통과했습니다.",
            STATUS_DONE + "\nQUESTION: What should I do next?",  # a question only when blocked
            "STATUS: DONE\nTASK: T01\nREPORT: %s\nFILES: fixed the bug and every test passes now" % REPORT_PATH,
        ))

    def test_valid_message_passes(self):
        self.write(REPORT_PATH, REPORT_DONE)
        self.assert_all("allow", self.stop, (
            STATUS_DONE,
            "```\n" + STATUS_DONE + "\n```",
            "**STATUS:** DONE\n**TASK:** T01\n**REPORT:** `%s`\n**FILES:**\n- src/login.py\n- tests/test_login.py" % REPORT_PATH,
            "STATUS: DONE\nTASK: T01\nREPORT: %s\nFILES: none" % REPORT_PATH,
        ))

    def test_done_requires_verification_for_every_roster_agent(self):
        self.write(REPORT_PATH, "STATUS: DONE\n\n## What I did\nChanged the file.\n")
        self.assert_all("block", lambda role: self.stop(STATUS_DONE, role), ROSTER)

    def test_done_needs_a_verification_record_that_passes(self):
        without_exit = {key: value for key, value in COMMAND_CHECK.items() if key != "exit_code"}
        reports = [("an unverified requirement", verified_report([COMMAND_CHECK], ["The required smoke test could not start."])),
                   ("no exit code", verified_report([without_exit]))]
        reports += [("a %s check" % result, verified_report([COMMAND_CHECK, dict(COMMAND_CHECK, result=result)]))
                    for result in ("FAIL", "NOT_RUN", "SKIPPED")]
        reports += [("exit code %r" % (code,), verified_report([dict(COMMAND_CHECK, exit_code=code)]))
                    for code in (1, -1, None, "0", False)]
        reports += [("checks %r" % (checks,), verified_report(checks))
                    for checks in ([], ["all passed"], [dict(COMMAND_CHECK, kind="syntax")])]
        reports += [("%s %r" % (key, value), verified_report([dict(COMMAND_CHECK, **{key: value})]))
                    for key in ("criterion", "method", "evidence") for value in ("", "  ", None, [])]
        reports += [("record %s" % record, verification(record))
                    for record in ("not json", "[]", "null", "{}", json.dumps({"checks": [COMMAND_CHECK]}),
                                   json.dumps({"checks": [COMMAND_CHECK], "unverified": "none"}))]
        for what, report in reports:
            with self.subTest(what):
                self.write(REPORT_PATH, report)
                self.assertEqual(self.stop(STATUS_DONE), "block")

    def test_manual_verification_is_allowed(self):
        check = dict(MANUAL_CHECK, criterion="세션 만료 처리 위치를 확인한다.", evidence="src/session.py:42에서 처리한다.")
        self.write(REPORT_PATH, verified_report([check]))
        self.assert_all("allow", lambda role: self.stop(STATUS_DONE, role), ROSTER)

    def test_invalid_done_is_let_through_after_the_retry_limit(self):
        self.write(REPORT_PATH, "STATUS: DONE\n")
        self.assertEqual([self.stop(STATUS_DONE, agent_id="agent-1") for _ in range(3)], ["block"] * 3)
        self.assertNotEqual(self.stop(STATUS_DONE, agent_id="agent-1"), "block")  # no endless retries

    def test_fixed_done_passes_after_refusals(self):
        self.write(REPORT_PATH, "STATUS: DONE\n")
        self.assertEqual(self.stop(STATUS_DONE, agent_id="agent-1"), "block")
        self.write(REPORT_PATH, REPORT_DONE)
        self.assertEqual(self.stop(STATUS_DONE, agent_id="agent-1"), "allow")

    def test_text_around_the_verification_block_is_allowed(self):
        report = REPORT_DONE.replace("```json", "I ran the checks below.\n\n```json", 1)
        self.assertIn("I ran the checks below.", report)
        self.write(REPORT_PATH, report)
        self.assertEqual(self.stop(STATUS_DONE), "allow")

    def test_report_done_cannot_hide_behind_failed_message(self):
        self.write(REPORT_PATH, "STATUS: DONE\n")
        message = STATUS_DONE.replace("STATUS: DONE", "STATUS: FAILED")
        self.assertEqual([self.stop(message, agent_id="agent-1") for _ in range(3)], ["block"] * 3)

    def test_incomplete_work_can_return_blocked_or_failed(self):
        for status in ("BLOCKED", "FAILED"):
            with self.subTest(status=status):
                self.write(REPORT_PATH, "STATUS: %s\n\nThe required check did not pass.\n" % status)
                message = STATUS_DONE.replace("STATUS: DONE", "STATUS: %s" % status)
                if status == "BLOCKED":
                    message += "\nQUESTION: May I install the declared test dependencies?"
                self.assertEqual(self.stop(message), "allow")

    def test_report_file_is_required(self):
        self.assertEqual(self.stop(STATUS_DONE), "block")  # no file
        self.write(REPORT_PATH, "")
        self.assertEqual(self.stop(STATUS_DONE), "block")  # empty file
        self.write(REPORT_PATH, "## What I did\nNo STATUS on the first line.\n")
        self.assertEqual(self.stop(STATUS_DONE), "block")
        self.write(REPORT_PATH, "STATUS: FAILED\n")
        self.assertEqual(self.stop(STATUS_DONE), "block")  # status mismatch
        self.write(UNIT + "/reports/T02.md", REPORT_DONE)
        self.assertEqual(self.stop(STATUS_DONE.replace("reports/T01", "reports/T02")), "block")  # task mismatch
        self.write("notes/T01.md", REPORT_DONE)
        self.assertEqual(self.stop("STATUS: DONE\nTASK: T01\nREPORT: notes/T01.md"), "block")  # outside reports
        self.write(".orchestra/reports/T01.md", REPORT_DONE)
        self.assertEqual(self.stop("STATUS: DONE\nTASK: T01\nREPORT: .orchestra/reports/T01.md"), "block")  # not in a work folder

    def test_blocked_needs_a_question(self):
        self.write(REPORT_PATH, "STATUS: BLOCKED\n")
        base = "STATUS: BLOCKED\nTASK: T01\nREPORT: %s" % REPORT_PATH
        self.assertEqual(self.stop(base, agent_id="agent-1"), "block")
        question = "\nQUESTION: May I change src/session.py?\nWithout it the acceptance criteria cannot be met."
        self.assertEqual(self.stop(base + question, agent_id="agent-1"), "allow")

    def test_agents_off_the_roster_are_left_alone(self):
        payload = {"hook_event_name": "SubagentStop", "agent_id": "x", "agent_type": "general-purpose",
                   "last_assistant_message": "anything"}
        self.assertEqual(self.verdict("check_report.py", payload), "allow")
        payload["agent_type"] = ""
        self.assertEqual(self.verdict("check_report.py", payload), "allow")

    def test_gives_up_after_three_attempts(self):
        results = [self.stop("a summary that ignores the format", agent_id="agent-9") for _ in range(4)]
        self.assertEqual(results, ["block", "block", "block", "warn"])

    def test_report_through_the_handback_tool(self):
        self.write(REPORT_PATH, REPORT_DONE)
        self.assertEqual(self.handback("All fixed."), "deny")
        self.assertEqual(self.handback(STATUS_DONE), "allow")
        self.assertEqual(self.stop("", agent_id="agent-1"), "allow")  # already reported through the tool

    def test_handback_rejects_unverified_done(self):
        self.write(REPORT_PATH, "STATUS: DONE\n")
        self.assertEqual([self.handback(STATUS_DONE) for _ in range(5)], ["deny"] * 5)

    def test_stop_rechecks_report_after_handback(self):
        self.write(REPORT_PATH, REPORT_DONE)
        self.assertEqual(self.handback(STATUS_DONE), "allow")
        self.write(REPORT_PATH, verified_report([dict(COMMAND_CHECK, result="NOT_RUN")]))
        self.assertEqual(self.stop("", agent_id="agent-1"), "block")

    def test_rejected_handback_clears_previous_approval(self):
        self.write(REPORT_PATH, REPORT_DONE)
        self.assertEqual(self.handback(STATUS_DONE), "allow")
        self.assertEqual(self.handback("not a valid handback"), "deny")
        self.assertEqual(self.stop("", agent_id="agent-1"), "block")


class HaikuReportTest(HookTest):
    """The last message of a Haiku subagent."""

    SCOUT_GOOD = [
        "STATUS: FOUND\nsrc/session.py:42 def expire_session(user):\nsrc/login.py:18 calls it.",
        "STATUS: NOT FOUND\nNo file under src/ names a session expiry.",
        "STATUS: NOT FOUND",
        "\n**STATUS:** FOUND\nsrc/a.py:1",
    ]
    SCOUT_BAD = [
        "I found it in src/session.py:42.",
        "src/session.py:42\nSTATUS: FOUND",
        "STATUS: DONE\nsrc/session.py:42",
        "STATUS: FOUND\n" + "src/a.py:1 x\n" * 400,
    ]
    EDITOR_GOOD = [
        EDITOR_DONE,
        "STATUS: FAILED\nFILES: none\nCHECK: exit 1",
        "```\n" + EDITOR_DONE + "\n```",
        "**STATUS:** DONE\n**FILES:**\n- src/login.py\n- src/session.py\n**CHECK:** `exit 0`",
    ]
    EDITOR_BAD = [
        "STATUS: DONE\nFILES: src/login.py\nCHECK: exit 1",
        EDITOR_DONE + "\nI renamed the function and the tests pass.",
        "STATUS: DONE\nFILES: src/login.py",
        "FILES: src/login.py\nCHECK: exit 0",
        "STATUS: DONE\nCHECK: exit 0",
        "STATUS: BLOCKED\nFILES: none\nCHECK: exit 0",
        "STATUS: DONE\nFILES: renamed it in both files\nCHECK: exit 0",
        "STATUS: DONE\nFILES: src/login.py\nCHECK: passed",
        "STATUS: DONE\nFILES: " + ", ".join("src/f%d.py" % i for i in range(300)) + "\nCHECK: exit 0",
    ]
    FORMATS = (("haiku-scout", SCOUT_GOOD, SCOUT_BAD), ("haiku-editor", EDITOR_GOOD, EDITOR_BAD))

    def test_last_messages(self):
        for role, good, bad in self.FORMATS:
            self.assert_all("allow", lambda message: self.stop(message, role), good)
            self.assert_all("block", lambda message: self.stop(message, role), bad)
            self.assertFalse(os.path.exists(os.path.join(self.proj, ".orchestra")), role)  # no report file anywhere

    def test_refusal_gives_the_format(self):
        payload = {"hook_event_name": "SubagentStop", "agent_id": "f1", "agent_type": "orchestra:haiku-editor",
                   "last_assistant_message": "All done."}
        reason = self.reason("check_report.py", payload)
        self.assertIn("CHECK: exit", reason)
        self.assertNotIn("REPORT:", reason)
        payload.update(agent_id="f2", agent_type="orchestra:haiku-scout")
        self.assertIn("STATUS: NOT FOUND", self.reason("check_report.py", payload))

    def test_retry_limit(self):
        for role in ("haiku-editor", "haiku-scout"):
            with self.subTest(role=role):
                results = [self.stop("a summary that ignores the format", role, agent_id=role) for _ in range(4)]
                self.assertEqual(results, ["block", "block", "block", "warn"])

    def test_no_metadata_is_recorded(self):
        self.assertEqual(self.handback(EDITOR_DONE, "haiku-editor"), "allow")
        self.assertEqual(self.stop("", "haiku-editor", agent_id="agent-1"), "allow")
        self.assertEqual(self.stop("STATUS: FOUND\nsrc/a.py:1", "haiku-scout"), "allow")
        self.assertFalse(os.path.exists(os.path.join(self.state, "meta")))

    def test_haiku_report_through_the_handback_tool(self):
        for role, good, bad in self.FORMATS:
            for n, message in enumerate(good):
                with self.subTest(role=role, message=message):
                    agent = "%s-g%d" % (role, n)
                    self.assertEqual(self.handback(message, role, agent), "allow")
                    self.assertEqual(self.stop("", role, agent), "allow")  # already handed back through the tool
            for n, message in enumerate(bad):
                with self.subTest(role=role, message=message[:80]):
                    self.assertEqual(self.handback(message, role, "%s-b%d" % (role, n)), "deny")

    def test_haiku_formats_are_refused_from_other_roster_subagents(self):
        self.assertEqual(self.stop(EDITOR_DONE, "sonnet-builder"), "block")
        self.assertEqual(self.stop("STATUS: FOUND\nsrc/a.py:1", "opus-analyst"), "block")


class HooksFileTest(unittest.TestCase):
    def test_subagent_stop_matcher(self):
        with open(os.path.join(ROOT, "hooks", "hooks.json"), encoding="utf-8") as f:
            hooks = json.load(f)["hooks"]
        matchers = [entry["matcher"] for entry in hooks["SubagentStop"]]
        self.assertEqual(len(matchers), 1)
        pattern = re.compile(matchers[0])
        for name in ("opus-engineer", "opus-analyst", "sonnet-builder", "sonnet-writer", "haiku-scout", "haiku-editor"):
            self.assertTrue(pattern.search("orchestra:" + name), name)
        for name in ("orchestra:general-purpose", "haiku-scout", "other:haiku-scout", "orchestra:haiku-scout-x"):
            self.assertFalse(pattern.search(name), name)


if __name__ == "__main__":
    unittest.main(verbosity=1)
