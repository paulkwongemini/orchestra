#!/usr/bin/env python3
"""Tests for scripts/guard_agent.py: who may start which subagent, with what prompt, model and brief.

Run: python3 tests/test_guard_agent.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import (BRIEF, BRIEF_KO, FABLE, OTHER_UNIT, SONNET, SPEC_PATH, TASK, TASK_KO, UNIT, HookTest, request,
                     response, spec_prompt)

GUARD = "guard_agent.py"
SCOUT_PROMPT = "FIND: where the session expiry is handled\nIN: src/"
EDITOR_PROMPT = "DO: rename expire_session to end_session\nFILES: src/login.py, src/session.py\nCHECK: pytest tests/test_login.py"


class DelegationRulesTest(HookTest):
    """A delegation waits until the delegation rules are loaded."""

    def setUp(self):
        super().setUp()
        os.remove(self.rules)
        self.write(UNIT + "/brief.md", BRIEF)
        self.write(SPEC_PATH, TASK)

    def test_delegation_is_refused_until_the_rules_are_loaded(self):
        self.assertEqual(self.delegate(), "deny")
        self.skill("orchestra:conductor")
        self.assertEqual(self.delegate(), "deny")
        self.assertEqual(self.skill("orchestra:delegation"), "allow")
        self.assertEqual(self.delegate(), "allow")

    def test_rules_cannot_be_loaded_before_the_conductor_skill(self):
        self.assertEqual(self.skill("orchestra:delegation"), "deny")
        self.assertEqual(self.delegate(), "deny")

    def test_compaction_requires_loading_the_rules_again(self):
        self.set_transcript([request(FABLE), response(FABLE)])
        self.skill("orchestra:conductor")
        self.skill("delegation")
        self.assertEqual(self.delegate(), "allow")
        self.session_start("compact")
        self.assertEqual(self.delegate(), "deny")


class DocumentationRulesTest(HookTest):
    """An implementation delegation waits until the documentation rules are loaded."""

    IMPL = ("sonnet-builder", "opus-engineer", "sonnet-writer")

    def setUp(self):
        super().setUp()
        os.remove(self.doc_rules)
        self.write(UNIT + "/brief.md", BRIEF)
        self.write(SPEC_PATH, TASK)

    def test_implementation_is_refused_until_the_rules_are_loaded(self):
        self.skill("orchestra:conductor")
        self.assert_all("deny", self.delegate, self.IMPL)
        self.assertEqual(self.skill("orchestra:documentation"), "allow")
        self.assert_all("allow", self.delegate, self.IMPL)

    def test_analysis_does_not_wait_for_the_rules(self):
        self.assertEqual(self.delegate("opus-analyst"), "allow")

    def test_rules_cannot_be_loaded_before_the_conductor_skill(self):
        self.assertEqual(self.skill("orchestra:documentation"), "deny")
        self.assertEqual(self.delegate(), "deny")

    def test_compaction_requires_loading_the_rules_again(self):
        self.set_transcript([request(FABLE), response(FABLE)])
        self.skill("orchestra:conductor")
        self.skill("delegation")
        self.skill("documentation")
        self.assertEqual(self.delegate(), "allow")
        self.session_start("compact")
        self.skill("orchestra:conductor")
        self.skill("delegation")
        self.assertEqual(self.delegate(), "deny")
        self.skill("documentation")
        self.assertEqual(self.delegate(), "allow")


class BriefConfirmTest(HookTest):
    """Principle 2: the user must have spoken after the brief last changed."""

    def setUp(self):
        super().setUp()
        self.write(SPEC_PATH, TASK)

    def test_brief_written_after_the_last_prompt_is_denied(self):
        self.spoke(1000)
        self.brief()
        self.assertEqual(self.delegate(), "deny")

    def test_a_prompt_after_the_brief_allows(self):
        self.brief()
        self.spoke(3000)
        self.assertEqual(self.delegate(), "allow")

    def test_an_answer_to_a_question_counts(self):
        self.spoke(1000)
        self.brief()
        self.spoke(3000, event="PostToolUse")
        self.assertEqual(self.delegate(), "allow")

    def test_changing_the_brief_needs_a_new_confirmation(self):
        self.brief()
        self.spoke(3000)
        self.write(UNIT + "/brief.md", BRIEF + "\nMore scope.\n", mtime=4000)
        self.assertEqual(self.delegate(), "deny")

    def test_another_session_speaking_does_not_count(self):
        self.spoke(1000)
        self.brief()
        self.spoke(3000, session="session-2")
        self.assertEqual(self.delegate(), "deny")

    def test_a_subagent_answer_does_not_count(self):
        self.spoke(1000)
        self.brief()
        self.run_hook("session.py", {"hook_event_name": "PostToolUse", "tool_name": "AskUserQuestion",
                                     "agent_id": "agent-1", "agent_type": "orchestra:sonnet-builder"})
        self.assertEqual(self.delegate(), "deny")

    def test_analysis_needs_no_brief_confirmation(self):
        self.spoke(1000)
        self.brief()
        self.assertEqual(self.delegate("opus-analyst"), "allow")

    def test_no_record_of_the_user_speaking_allows(self):
        self.brief()
        self.assertEqual(self.delegate(), "allow")


class GuardAgentTest(HookTest):
    """Principles 2, 4, 5, 6, 9: the conductor's delegation of a task."""

    def ready(self):
        self.write(UNIT + "/brief.md", BRIEF)
        self.write(SPEC_PATH, TASK)

    def test_calls_off_the_roster_are_denied(self):
        self.ready()
        self.assertEqual(self.agent(prompt=spec_prompt()), "deny")  # no type
        off_roster = ("fork", "general-purpose", "Explore", "Plan", "claude", "claude-code-guide", "other:opus-engineer")
        self.assert_all("deny", lambda stype: self.agent(subagent_type=stype, prompt=spec_prompt()), off_roster)

    def test_roster_agent_is_allowed(self):
        self.ready()
        self.assert_all("allow", self.delegate, ("opus-engineer", "sonnet-builder", "sonnet-writer", "opus-analyst"))

    def test_model_of_a_task_delegation(self):
        self.ready()
        with_model = lambda model: self.delegate("sonnet-builder", model=model)
        self.assert_all("deny", with_model, ("fable", "haiku", "inherit"))
        self.assert_all("allow", with_model, ("opus", "sonnet"))

    def test_task_spec_is_required(self):
        self.write(UNIT + "/brief.md", BRIEF)
        self.assertEqual(self.delegate("opus-engineer", "Fix the login error."), "deny")
        self.assertEqual(self.delegate("opus-engineer"), "deny")  # no file
        self.write(SPEC_PATH, TASK.replace("## Acceptance criteria", "## Done"))
        self.assertEqual(self.delegate("opus-engineer"), "deny")  # a section is missing
        self.write(SPEC_PATH, TASK.replace("Change only `src/login.py` and the tests in `tests/login/`.\n", ""))
        self.assertEqual(self.delegate("opus-engineer"), "deny")  # a section is empty
        self.write(SPEC_PATH, TASK)
        self.assertEqual(self.delegate("opus-engineer"), "allow")

    def test_task_spec_outside_a_work_folder_is_not_accepted(self):
        self.write(".orchestra/brief.md", BRIEF)
        self.write(".orchestra/tasks/T01-login-fix.md", TASK)
        prompt = "Read the task spec at `.orchestra/tasks/T01-login-fix.md` and carry it out."
        self.assertEqual(self.delegate("opus-engineer", prompt), "deny")

    def test_brief_is_required_for_implementation_only(self):
        self.write(SPEC_PATH, TASK)
        self.assertEqual(self.delegate("opus-engineer"), "deny")
        self.assertEqual(self.delegate("opus-analyst"), "allow")
        self.write(UNIT + "/brief.md", "## Goal\nFix the login error.\n")
        self.assertEqual(self.delegate("opus-engineer"), "deny")
        self.write(UNIT + "/brief.md", BRIEF)
        self.assertEqual(self.delegate("opus-engineer"), "allow")

    def test_brief_of_another_work_folder_does_not_count(self):
        self.write(OTHER_UNIT + "/brief.md", BRIEF)
        self.write(SPEC_PATH, TASK)
        self.assertEqual(self.delegate("opus-engineer"), "deny")

    def test_document_bodies_can_be_in_any_language(self):
        self.write(UNIT + "/brief.md", BRIEF_KO)
        self.write(UNIT + "/tasks/T01-로그인-수정.md", TASK_KO)
        prompt = "지시서 `%s/tasks/T01-로그인-수정.md`를 읽고 수행하라." % UNIT
        self.assertEqual(self.delegate("opus-engineer", prompt), "allow")

    def test_headings_ignore_case(self):
        self.write(UNIT + "/brief.md", BRIEF.lower())
        self.write(SPEC_PATH, TASK.upper())
        self.assertEqual(self.delegate("opus-engineer"), "allow")

    def test_subagent_cannot_spawn(self):
        self.ready()
        payload = self.sub("opus-engineer", "Agent", {"subagent_type": "orchestra:sonnet-builder", "prompt": spec_prompt()})
        self.assertEqual(self.verdict(GUARD, payload), "deny")

    def test_workflow_is_denied(self):
        self.assertEqual(self.verdict(GUARD, self.main("Workflow", {"name": "audit"})), "deny")

    def test_an_ordinary_session_is_left_alone(self):
        self.set_transcript([request(SONNET)])
        self.assertEqual(self.agent(subagent_type="fork", prompt="anything"), "allow")
        self.assertEqual(self.agent(prompt="anything"), "allow")
        self.assertEqual(self.verdict(GUARD, self.main("Workflow", {"name": "audit"})), "allow")
        self.ready()
        for rules in (self.rules, self.doc_rules):  # a delegation waits for neither set of rules
            with self.subTest(not_loaded=os.path.basename(rules)):
                os.remove(rules)
                self.assertEqual(self.delegate(), "allow")
                open(rules, "w").close()


class HaikuAgentTest(HookTest):
    """The Haiku subagents: who may start them, and with what prompt and model."""

    IMPL = ("opus-engineer", "sonnet-builder", "sonnet-writer")

    def inside(self, caller, name, prompt, **extra):
        """The verdict on an Agent call that a roster subagent makes."""
        stype = name if ":" in name or name in ("general-purpose", "Explore", "fork") else "orchestra:" + name
        return self.verdict(GUARD, self.sub(caller, "Agent", dict({"subagent_type": stype, "prompt": prompt}, **extra)))

    def work_record(self):
        path = self.marker("work")
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return f.read()

    def no_skills(self):
        os.remove(self.rules)
        os.remove(self.doc_rules)

    # ---- the conductor

    def test_conductor_to_scout(self):
        self.no_skills()
        scout = lambda prompt: self.delegate("haiku-scout", prompt)
        self.assert_all("allow", scout, (SCOUT_PROMPT, "  FIND: the login handler"))
        self.assertIsNone(self.work_record())
        self.assert_all("deny", scout, (
            "Find where the session expiry is handled.",
            "find: the login handler",  # keys are uppercase
            "FIND:   \nwhere the session expiry is handled",
            "FIND: x\n" + "y" * 1500,
        ))
        self.assertEqual(scout("FIND: x\n" + "y" * (1500 - 8)), "allow")

    def test_scout_refusals_name_the_problem_and_the_format(self):
        payload = self.main("Agent", {"subagent_type": "orchestra:haiku-scout", "prompt": "Find it."})
        why = self.reason(GUARD, payload)
        self.assertIn("FIND:", why)
        self.assertIn("Missing", why)
        payload["tool_input"]["prompt"] = "FIND:\n"
        self.assertIn("nothing after the key", self.reason(GUARD, payload))
        payload["tool_input"]["prompt"] = "FIND: x\n" + "y" * 2000
        why = self.reason(GUARD, payload)
        self.assertIn("not a mechanical one", why)
        self.assertIn("delegate it as a task", why)

    def test_conductor_to_editor(self):
        self.no_skills()
        self.confirmed("")
        before = self.work_record()
        self.assertEqual(self.delegate("haiku-editor", EDITOR_PROMPT), "allow")
        self.assertEqual(self.work_record(), before)
        lines = EDITOR_PROMPT.splitlines()
        for key in ("DO", "FILES", "CHECK"):
            with self.subTest(key=key):
                dropped = "\n".join(l for l in lines if not l.startswith(key + ":"))
                self.assertEqual(self.delegate("haiku-editor", dropped), "deny")
                emptied = "\n".join(key + ":" if l.startswith(key + ":") else l for l in lines)
                self.assertEqual(self.delegate("haiku-editor", emptied), "deny")
        self.assertEqual(self.delegate("haiku-editor", EDITOR_PROMPT + "\n" + "y" * 1500), "deny")

    def test_editor_needs_a_confirmed_brief(self):
        payload = self.main("Agent", {"subagent_type": "orchestra:haiku-editor", "prompt": EDITOR_PROMPT})
        self.assert_refused(GUARD, payload, "no active work folder")
        self.brief()
        self.activate()
        self.assert_refused(GUARD, payload, "no user turn is recorded")
        self.spoke(1000)
        self.assert_refused(GUARD, payload, "after the user last spoke")
        self.spoke(3000)
        self.assertEqual(self.verdict(GUARD, payload), "allow")
        os.remove(os.path.join(self.proj, UNIT, "brief.md"))
        self.assert_refused(GUARD, payload, "has no brief")

    def test_editor_brief_of_another_folder_does_not_count(self):
        self.brief()
        self.write(OTHER_UNIT + "/tasks/T01-login-fix.md", TASK)
        self.spoke(3000)
        self.assertEqual(self.write_to(OTHER_UNIT + "/decisions.md"), "allow")
        self.activate()
        self.assertEqual(self.delegate("haiku-editor", EDITOR_PROMPT), "allow")
        self.assertEqual(self.delegate("opus-analyst", spec_prompt(OTHER_UNIT)), "allow")  # makes the other folder active
        self.assertEqual(self.delegate("haiku-editor", EDITOR_PROMPT), "deny")

    def test_only_haiku_requests_skip_the_skills_and_a_scout_takes_no_task_spec(self):
        self.no_skills()
        self.write(UNIT + "/brief.md", BRIEF)
        self.write(SPEC_PATH, TASK)
        self.assertEqual(self.delegate("sonnet-builder"), "deny")  # the skills are not loaded
        self.assertEqual(self.delegate("haiku-scout"), "deny")  # a task spec is no Haiku request

    # ---- inside subagents

    def test_implementation_subagents_may_start_haiku(self):
        for caller in self.IMPL:
            with self.subTest(caller=caller):
                self.assertEqual(self.inside(caller, "haiku-scout", SCOUT_PROMPT), "allow")
                self.assertEqual(self.inside(caller, "haiku-editor", EDITOR_PROMPT), "allow")
                self.assertEqual(self.inside(caller, "haiku-scout", "Find the handler."), "deny")
                self.assertEqual(self.inside(caller, "haiku-editor", "DO: rename it\nFILES: src/a.py"), "deny")
                self.assertEqual(self.inside(caller, "haiku-editor", EDITOR_PROMPT + "\n" + "y" * 1500), "deny")
                others = ("sonnet-builder", "opus-analyst", "opus-engineer", "general-purpose", "Explore", "fork",
                          "other:haiku-scout")
                self.assert_all("deny", lambda other: self.inside(caller, other, SCOUT_PROMPT), others)
                self.assertEqual(self.verdict(GUARD, self.sub(caller, "Agent", {"prompt": SCOUT_PROMPT})), "deny")
                self.assertEqual(self.verdict(GUARD, self.sub(caller, "Workflow", {"name": "audit"})), "deny")

    def test_subagent_length_refusal_says_do_it_yourself(self):
        payload = self.sub("sonnet-builder", "Agent", {"subagent_type": "orchestra:haiku-scout",
                                                       "prompt": "FIND: x\n" + "y" * 2000})
        self.assertIn("do the work yourself", self.reason(GUARD, payload))

    def test_analyst_may_start_only_the_scout(self):
        self.assertEqual(self.inside("opus-analyst", "haiku-scout", SCOUT_PROMPT), "allow")
        self.assertEqual(self.inside("opus-analyst", "haiku-editor", EDITOR_PROMPT), "deny")
        self.assertEqual(self.inside("opus-analyst", "haiku-scout", "FIND:"), "deny")
        self.assertEqual(self.inside("opus-analyst", "sonnet-builder", SCOUT_PROMPT), "deny")

    def test_haiku_subagents_start_nothing(self):
        for caller in ("haiku-scout", "haiku-editor"):
            with self.subTest(caller=caller):
                self.assertEqual(self.inside(caller, "haiku-scout", SCOUT_PROMPT), "deny")
                self.assertEqual(self.inside(caller, "haiku-editor", EDITOR_PROMPT), "deny")
                self.assertEqual(self.inside(caller, "sonnet-builder", SCOUT_PROMPT), "deny")
                self.assertEqual(self.verdict(GUARD, self.sub(caller, "Workflow", {"name": "audit"})), "deny")

    def test_subagents_off_the_roster_are_left_alone(self):
        payload = self.sub("general-purpose", "Agent", {"subagent_type": "orchestra:haiku-editor", "prompt": "x"})
        self.assertEqual(self.verdict(GUARD, payload), "allow")

    # ---- the model argument

    def test_model_of_a_haiku_request(self):
        self.confirmed("")
        for name, prompt in (("haiku-scout", SCOUT_PROMPT), ("haiku-editor", EDITOR_PROMPT)):
            for model in ("sonnet", "opus", "fable", "inherit"):
                with self.subTest(name=name, model=model):
                    self.assertEqual(self.delegate(name, prompt, model=model), "deny")
                    self.assertEqual(self.inside("opus-engineer", name, prompt, model=model), "deny")
            for extra in ({"model": "haiku"}, {}):
                with self.subTest(name=name, extra=extra):
                    self.assertEqual(self.delegate(name, prompt, **extra), "allow")
                    self.assertEqual(self.inside("opus-engineer", name, prompt, **extra), "allow")
        self.write(SPEC_PATH, TASK)
        self.assertEqual(self.delegate("sonnet-builder"), "allow")
        self.assertEqual(self.delegate("sonnet-builder", model="haiku"), "deny")
        self.assertEqual(self.delegate("opus-analyst", model="haiku"), "deny")


if __name__ == "__main__":
    unittest.main(verbosity=1)
