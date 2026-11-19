#!/usr/bin/env python3
"""Tests for scripts/require_skill.py: the conductor skill before any other tool, and the Opus effort question.

Run: python3 tests/test_require_skill.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import FABLE, OPUS, SONNET, HookTest, at_effort, request, response


class RequireSkillTest(HookTest):
    """The conductor loads its instructions before it uses any other tool."""

    TOOLS = ("Read", "Grep", "Bash", "Write", "Agent", "AskUserQuestion", "mcp__x__y")

    def test_tools_are_refused_until_the_skill_is_loaded(self):
        self.assert_all("deny", self.use, self.TOOLS)
        self.assertEqual(self.skill(), "allow")
        self.assert_all("allow", self.use, self.TOOLS)

    def test_short_skill_name_counts(self):
        self.assertEqual(self.skill("conductor"), "allow")
        self.assertEqual(self.use(), "allow")

    def test_another_skill_does_not_count(self):
        self.assert_all("deny", self.skill, ("orchestra:setup", "other:conductor", "superpowers:brainstorming"))
        self.assertEqual(self.use(), "deny")

    def test_tool_search_is_always_allowed(self):
        self.assertEqual(self.use("ToolSearch", {"query": "select:Skill"}), "allow")

    def test_the_user_command_counts(self):
        self.say("/orchestra:conductor fix it")
        self.assertEqual(self.use(), "allow")

    def test_an_ordinary_session_is_left_alone(self):
        self.set_transcript([request(SONNET)])
        self.assertEqual(self.use(), "allow")

    def test_disabled_session_is_left_alone(self):
        self.say("/orchestra:disable")
        self.assertEqual(self.use(), "allow")

    def test_subagents_are_left_alone(self):
        self.assertEqual(self.verdict("require_skill.py", self.sub("sonnet-builder", "Read", {"file_path": "a"})), "allow")

    def test_loading_is_per_session(self):
        self.set_mode("claude", "session-2")
        self.skill()
        self.assertEqual(self.use(session_id="session-2"), "deny")

    def test_compaction_and_clear_require_loading_again(self):
        for source in ("compact", "clear"):
            with self.subTest(source=source):
                self.set_transcript([request(FABLE), response(FABLE)])
                self.skill()
                self.assertIn("load them again", self.note(self.session_start(source)))
                self.assertEqual(self.use(), "deny")
                self.assertEqual(self.skill(), "allow")

    def test_resume_keeps_the_skill_loaded(self):
        self.set_transcript([request(FABLE), response(FABLE)])
        self.skill()
        self.session_start("resume")
        self.assertEqual(self.use(), "allow")


class EffortTest(HookTest):
    """An Opus conductor at a low effort level is told once to recommend High or above."""

    def test_low_and_medium_are_asked_about_once(self):
        for effort in ("low", "medium", "LOW"):
            with self.subTest(effort=effort):
                self.say("/orchestra:conduct")
                self.set_transcript([at_effort(OPUS, effort)])
                verdict, reason = self.refusal("require_skill.py", self.main("Skill", {"skill": "orchestra:conductor"}))
                self.assertEqual(verdict, "deny")
                self.assertIn("High effort or above", reason)
                self.assertIn("`%s`" % effort.lower(), reason)
                # The user answered. Whatever they said, the work goes on at this level.
                self.assertEqual(self.skill(), "allow")
                self.assertEqual(self.use(), "allow")
                self.assertEqual(self.use(), "allow")

    def test_high_and_above_are_not_asked_about(self):
        for effort in ("high", "xhigh", "max"):
            with self.subTest(effort=effort):
                self.set_transcript([at_effort(OPUS, effort)])
                self.assertEqual(self.skill(), "allow")

    def test_unknown_effort_is_not_asked_about(self):
        self.set_transcript([request(OPUS)])
        self.assertEqual(self.skill(), "allow")

    def test_fable_is_not_asked_about(self):
        self.set_transcript([at_effort(FABLE, "low")])
        self.assertEqual(self.skill(), "allow")

    def test_ordinary_and_disabled_sessions_are_not_asked_about(self):
        self.set_transcript([at_effort(SONNET, "low")])
        self.assertEqual(self.use(), "allow")
        self.set_transcript([at_effort(OPUS, "low")])
        self.say("/orchestra:disable")
        self.assertEqual(self.use(), "allow")

    def test_the_newest_request_decides(self):
        self.set_transcript([at_effort(OPUS, "high"), response(OPUS), at_effort(OPUS, "low")])
        self.assertEqual(self.skill(), "deny")

    def test_a_raised_effort_needs_no_answer(self):
        self.set_transcript([at_effort(OPUS, "low")])
        self.assertEqual(self.skill(), "deny")
        self.set_transcript([at_effort(OPUS, "low"), response(OPUS), at_effort(OPUS, "high")])
        self.assertEqual(self.skill(), "allow")
        self.assertEqual(self.use(), "allow")

    def test_another_low_level_or_turning_it_on_again_asks_again(self):
        self.set_transcript([at_effort(OPUS, "medium")])
        self.assertEqual(self.skill(), "deny")
        self.assertEqual(self.skill(), "allow")
        self.set_transcript([at_effort(OPUS, "low")])
        self.assertEqual(self.use(), "deny")
        self.assertEqual(self.use(), "allow")
        self.say("/orchestra:conduct")
        self.assertEqual(self.use(), "deny")

    def test_compaction_does_not_ask_again(self):
        self.set_transcript([at_effort(OPUS, "low")])
        self.skill()
        self.skill()
        self.session_start("compact")
        self.assertEqual(self.skill(), "allow")

    def test_tool_search_is_never_stopped(self):
        self.set_transcript([at_effort(OPUS, "low")])
        self.assertEqual(self.use("ToolSearch", {"query": "select:Skill"}), "allow")
        self.assertEqual(self.skill(), "deny")


if __name__ == "__main__":
    unittest.main(verbosity=1)
