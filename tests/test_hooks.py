#!/usr/bin/env python3
"""Tests for the hook scripts.

Run: python3 tests/test_hooks.py

Each test feeds a hook script JSON shaped like what Claude Code sends, and checks the verdict.
No session is started, so there are no model calls.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")

FABLE = "claude-fable-5-2"
OPUS = "claude-opus-5-5"
SONNET = "claude-sonnet-5-5"
UNIT = ".orchestra/2026-11-12-login-fix"
OTHER_UNIT = ".orchestra/2026-11-13-export"

BRIEF = """## Goal
Fix the login error.

## Out of scope
The sign-up flow.

## Proposed extras
None.

## Constraints
Do not change the public API.

## Acceptance criteria
The login tests pass.

## Decision rights
The conductor decides implementation details. Public API changes go to the user.
"""

TASK = """## Goal
Send the user back to the login screen when the session expires.

## Scope and limits
Change only src/login.py.

## Context
Session handling lives in src/session.py.

## Acceptance criteria
pytest tests/test_login.py passes.

## If blocked
Report BLOCKED if src/session.py has to change.

## Report
Report path: `.orchestra/2026-11-12-login-fix/reports/T01.md`
"""

REVIEW = """## Scope reviewed
The whole diff of src/login.py.

## Checks run
$ pytest tests/test_login.py
12 passed in 0.8s

## Verdict
Pass.
"""

# The same documents with the body in another language. Only the headings are fixed.
BRIEF_KO = """## Goal
로그인 오류를 고친다.

## Out of scope
회원가입 흐름.

## Proposed extras
없음.

## Constraints
공개 API는 바꾸지 않는다.

## Acceptance criteria
로그인 테스트가 통과한다.

## Decision rights
구현 세부는 지휘자가 정한다. 공개 API 변경은 사용자에게 묻는다.
"""

TASK_KO = """## Goal
세션 만료 시 로그인 화면으로 돌아가게 한다.

## Scope and limits
src/login.py 만 고친다.

## Context
세션 처리는 src/session.py 에 있다.

## Acceptance criteria
pytest tests/test_login.py 가 통과한다.

## If blocked
src/session.py 를 고쳐야 하면 BLOCKED로 보고한다.

## Report
보고서 경로: `.orchestra/2026-11-12-login-fix/reports/T01.md`
"""

REVIEW_KO = """## Scope reviewed
src/login.py 의 diff 전체.

## Checks run
$ pytest tests/test_login.py
12 passed in 0.8s

## Verdict
통과.
"""

COMMAND_CHECK = {
    "criterion": "The login tests pass.",
    "kind": "command",
    "method": "pytest tests/test_login.py",
    "result": "PASS",
    "exit_code": 0,
    "evidence": "12 passed in 0.8s",
}
MANUAL_CHECK = {
    "criterion": "Identify the session-expiry handler.",
    "kind": "manual",
    "method": "Compare the caller and implementation named in the task spec.",
    "result": "PASS",
    "evidence": "src/login.py:18 calls expire_session in src/session.py:42.",
}


def verified_report(checks, unverified=()):
    return ("STATUS: DONE\n\n## What I verified\n```json\n"
            + json.dumps({"checks": checks, "unverified": list(unverified)}, ensure_ascii=False)
            + "\n```\n")


REPORT_DONE = verified_report([COMMAND_CHECK])
STATUS_DONE = "STATUS: DONE\nTASK: T01\nREPORT: %s/reports/T01.md\nFILES: src/login.py" % UNIT


def request(model):
    """A transcript record of a request that has been sent. It exists before the response does."""
    return {"type": "api-request", "params": {"model": model}, "querySource": "sdk"}


def response(model, when="2026-11-12T10:00:00.000Z", side=False):
    """A transcript record of a response."""
    return {"type": "assistant", "isSidechain": side, "timestamp": when,
            "message": {"model": model, "content": [{"type": "text", "text": "ok"}]}}


class HookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="orchestra-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.proj = os.path.join(self.tmp, "proj")
        self.state = os.path.join(self.tmp, "state")
        self.transcript = os.path.join(self.tmp, "session.jsonl")
        os.makedirs(self.proj)
        os.makedirs(self.state)
        # By default a test runs in a new session on Fable that has just sent its first request,
        # and the delegation rules and the documentation rules are already loaded.
        self.set_transcript([request(FABLE)])
        self.rules = os.path.join(self.state, "sessions", "session-1.delegation")
        self.doc_rules = os.path.join(self.state, "sessions", "session-1.documentation")
        os.makedirs(os.path.dirname(self.rules))
        open(self.rules, "w").close()
        open(self.doc_rules, "w").close()

    # ---- helpers

    def set_transcript(self, entries):
        if entries is None:
            if os.path.exists(self.transcript):
                os.remove(self.transcript)
            return
        with open(self.transcript, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, separators=(",", ":")) + "\n")

    def write(self, rel, text, mtime=None):
        path = os.path.join(self.proj, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def run_hook(self, script, payload):
        env = dict(os.environ)
        env.update(CLAUDE_PROJECT_DIR=self.proj, CLAUDE_PLUGIN_ROOT=ROOT, CLAUDE_PLUGIN_DATA=self.state)
        payload.setdefault("cwd", self.proj)
        payload.setdefault("session_id", "session-1")
        payload.setdefault("transcript_path", self.transcript)
        proc = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS, script)],
            input=json.dumps(payload).encode("utf-8"),
            capture_output=True,
            env=env,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        out = proc.stdout.decode("utf-8").strip()
        return json.loads(out) if out else None

    def verdict(self, script, payload):
        out = self.run_hook(script, payload)
        if out is None:
            return "allow"
        if out.get("decision") == "block":
            return "block"
        if "systemMessage" in out:
            return "warn"
        return out.get("hookSpecificOutput", {}).get("permissionDecision", "unknown")

    @staticmethod
    def main(tool, tool_input):
        return {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input}

    @staticmethod
    def sub(role, tool, tool_input):
        return {
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "tool_input": tool_input,
            "agent_id": "agent-1",
            "agent_type": "orchestra:" + role,
        }

    def bash(self, cmd):
        return self.verdict("guard_main.py", self.main("Bash", {"command": cmd}))

    def write_src(self):
        """Whether the main conversation may write a source file. Denied only in a conductor session."""
        return self.verdict("guard_main.py", self.main("Write", {"file_path": "src/app.py", "content": "x"}))

    def switch(self, to_model, from_model=SONNET, source="picker"):
        payload = {"hook_event_name": "PostModelSwitch", "from_model": from_model, "to_model": to_model,
                   "requested_model": to_model, "source": source}
        return self.run_hook("session.py", payload)


class ModeTest(HookTest):
    """A session is a conductor session exactly while its main conversation runs on a Fable model."""

    def test_first_request_of_a_new_session(self):
        self.set_transcript([request(FABLE)])
        self.assertEqual(self.write_src(), "deny")
        self.set_transcript([request(SONNET)])
        self.assertEqual(self.write_src(), "allow")
        self.set_transcript([request(OPUS)])
        self.assertEqual(self.write_src(), "allow")

    def test_mythos_counts_as_fable(self):
        self.set_transcript([request("claude-mythos-5-2")])
        self.assertEqual(self.write_src(), "deny")

    def test_last_response_decides(self):
        self.set_transcript([request(FABLE), response(FABLE)])
        self.assertEqual(self.write_src(), "deny")
        self.set_transcript([request(OPUS), response(OPUS)])
        self.assertEqual(self.write_src(), "allow")

    def test_request_in_flight_turns_the_harness_on(self):
        self.set_transcript([request(OPUS), response(OPUS), request(FABLE)])
        self.assertEqual(self.write_src(), "deny")

    def test_request_in_flight_turns_the_harness_off(self):
        self.set_transcript([request(FABLE), response(FABLE), request(SONNET)])
        self.assertEqual(self.write_src(), "allow")

    def test_recorded_switch_turns_it_off_and_on(self):
        self.set_transcript([request(FABLE), response(FABLE)])
        self.switch(SONNET, from_model=FABLE)
        self.assertEqual(self.write_src(), "allow")
        self.switch(FABLE, from_model=SONNET)
        self.assertEqual(self.write_src(), "deny")

    def test_a_response_newer_than_the_switch_wins(self):
        self.switch(SONNET, from_model=FABLE)
        self.set_transcript([request(FABLE), response(FABLE, when="2100-01-01T00:00:00.000Z")])
        self.assertEqual(self.write_src(), "deny")

    def test_nothing_known_means_an_ordinary_session(self):
        self.set_transcript(None)
        self.assertEqual(self.write_src(), "allow")
        self.set_transcript([{"type": "user", "message": {"content": "hi"}}])
        self.assertEqual(self.write_src(), "allow")

    def test_switch_to_fable_before_any_request(self):
        self.set_transcript(None)
        self.switch(FABLE)
        self.assertEqual(self.write_src(), "deny")

    def test_subagent_and_synthetic_records_are_ignored(self):
        self.set_transcript([request(FABLE), response(FABLE), response(SONNET, side=True), response("<synthetic>")])
        self.assertEqual(self.write_src(), "deny")

    def test_model_is_found_behind_large_records(self):
        filler = {"type": "user", "message": {"content": "x" * 200000}}
        self.set_transcript([request(FABLE), response(FABLE)] + [filler] * 5)
        self.assertEqual(self.write_src(), "deny")

    def test_text_that_looks_like_a_record_is_not_one(self):
        fake = {"type": "user", "message": {"content": '{"type":"assistant","message":{"model":"claude-fable-5-2"}}'}}
        self.set_transcript([request(SONNET), response(SONNET), fake])
        self.assertEqual(self.write_src(), "allow")

    def test_sessions_do_not_share_a_recorded_switch(self):
        self.set_transcript(None)
        self.switch(FABLE)
        payload = self.main("Write", {"file_path": "src/app.py"})
        payload["session_id"] = "session-2"
        self.assertEqual(self.verdict("guard_main.py", payload), "allow")


class SwitchTest(HookTest):
    """The user can turn conductor mode off and on for one session."""

    def say(self, prompt, session="session-1"):
        return self.run_hook("session.py", {"hook_event_name": "UserPromptSubmit", "prompt": prompt, "session_id": session})

    def test_disable_and_enable(self):
        self.assertEqual(self.write_src(), "deny")
        self.say("/orchestra:disable")
        self.assertEqual(self.write_src(), "allow")
        self.assertEqual(self.verdict("guard_agent.py", self.main("Agent", {"subagent_type": "fork"})), "allow")
        self.say("/orchestra:enable")
        self.assertEqual(self.write_src(), "deny")

    def test_explicit_conductor_command_turns_it_back_on(self):
        self.say("/orchestra:disable")
        self.say("/orchestra:conductor fix the login bug")
        self.assertEqual(self.write_src(), "deny")

    def test_expansion_event_works_too(self):
        for name in ("orchestra:disable", "disable"):
            self.run_hook("session.py", {"hook_event_name": "UserPromptExpansion", "command_name": name,
                                         "command_source": "plugin", "prompt": "/orchestra:disable"})
            self.assertEqual(self.write_src(), "allow", name)
            self.say("/orchestra:enable")
        self.run_hook("session.py", {"hook_event_name": "UserPromptExpansion", "command_name": "other:disable",
                                     "command_source": "plugin", "prompt": "/other:disable"})
        self.assertEqual(self.write_src(), "deny")

    def test_only_a_typed_command_counts(self):
        self.say("please run /orchestra:disable for me")
        self.say("/orchestra:disabled")
        self.say("/other:disable")
        self.assertEqual(self.write_src(), "deny")

    def test_disable_is_per_session(self):
        self.say("/orchestra:disable", session="session-2")
        self.assertEqual(self.write_src(), "deny")

    def test_disabled_session_gets_no_notes(self):
        self.say("/orchestra:disable")
        self.assertIsNone(self.run_hook("session.py", {"hook_event_name": "SessionStart", "source": "compact"}))
        self.assertIsNone(self.switch(FABLE, from_model=OPUS))
        self.assertEqual(self.write_src(), "allow")


class RequireSkillTest(HookTest):
    """The conductor loads its instructions before it uses any other tool."""

    def call(self, tool="Read", tool_input=None, **extra):
        payload = self.main(tool, tool_input or {"file_path": "src/app.py"})
        payload.update(extra)
        return self.verdict("require_skill.py", payload)

    def load(self, skill="orchestra:conductor"):
        return self.call("Skill", {"skill": skill})

    def test_tools_are_refused_until_the_skill_is_loaded(self):
        for tool in ("Read", "Grep", "Bash", "Write", "Agent", "AskUserQuestion", "mcp__x__y"):
            self.assertEqual(self.call(tool), "deny", tool)
        self.assertEqual(self.load(), "allow")
        for tool in ("Read", "Grep", "Bash", "Write", "Agent", "AskUserQuestion", "mcp__x__y"):
            self.assertEqual(self.call(tool), "allow", tool)

    def test_short_skill_name_counts(self):
        self.assertEqual(self.load("conductor"), "allow")
        self.assertEqual(self.call(), "allow")

    def test_another_skill_does_not_count(self):
        for skill in ("orchestra:setup", "other:conductor", "superpowers:brainstorming"):
            self.assertEqual(self.load(skill), "deny", skill)
        self.assertEqual(self.call(), "deny")

    def test_tool_search_is_always_allowed(self):
        self.assertEqual(self.call("ToolSearch", {"query": "select:Skill"}), "allow")

    def test_the_user_command_counts(self):
        self.run_hook("session.py", {"hook_event_name": "UserPromptSubmit", "prompt": "/orchestra:conductor fix it"})
        self.assertEqual(self.call(), "allow")

    def test_ordinary_session_is_left_alone(self):
        self.set_transcript([request(SONNET)])
        self.assertEqual(self.call(), "allow")

    def test_disabled_session_is_left_alone(self):
        self.run_hook("session.py", {"hook_event_name": "UserPromptSubmit", "prompt": "/orchestra:disable"})
        self.assertEqual(self.call(), "allow")

    def test_subagents_are_left_alone(self):
        self.assertEqual(self.verdict("require_skill.py", self.sub("sonnet-builder", "Read", {"file_path": "a"})), "allow")

    def test_loading_is_per_session(self):
        self.load()
        self.assertEqual(self.call(session_id="session-2"), "deny")

    def test_compaction_and_clear_require_loading_again(self):
        for source in ("compact", "clear"):
            self.set_transcript([request(FABLE), response(FABLE)])
            self.load()
            out = self.run_hook("session.py", {"hook_event_name": "SessionStart", "source": source})
            self.assertIn("load them again", out["hookSpecificOutput"]["additionalContext"], source)
            self.assertEqual(self.call(), "deny", source)
            self.assertEqual(self.load(), "allow")

    def test_resume_keeps_the_skill_loaded(self):
        self.set_transcript([request(FABLE), response(FABLE)])
        self.load()
        self.run_hook("session.py", {"hook_event_name": "SessionStart", "source": "resume"})
        self.assertEqual(self.call(), "allow")


class DelegationRulesTest(HookTest):
    """A delegation waits until the delegation rules are loaded."""

    PROMPT = "Read the task spec at `%s/tasks/T01-login-fix.md` and carry it out." % UNIT

    def setUp(self):
        super().setUp()
        os.remove(self.rules)
        self.write(UNIT + "/brief.md", BRIEF)
        self.write(UNIT + "/tasks/T01-login-fix.md", TASK)

    def delegate(self):
        return self.verdict("guard_agent.py", self.main("Agent", {"subagent_type": "orchestra:sonnet-builder", "prompt": self.PROMPT}))

    def skill(self, name):
        return self.verdict("require_skill.py", self.main("Skill", {"skill": name}))

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
        self.run_hook("session.py", {"hook_event_name": "SessionStart", "source": "compact"})
        self.assertEqual(self.delegate(), "deny")

    def test_ordinary_session_is_left_alone(self):
        self.set_transcript([request(SONNET)])
        self.assertEqual(self.delegate(), "allow")


class DocumentationRulesTest(HookTest):
    """An implementation delegation waits until the documentation rules are loaded."""

    PROMPT = "Read the task spec at `%s/tasks/T01-login-fix.md` and carry it out." % UNIT

    def setUp(self):
        super().setUp()
        os.remove(self.doc_rules)
        self.write(UNIT + "/brief.md", BRIEF)
        self.write(UNIT + "/tasks/T01-login-fix.md", TASK)

    def delegate(self, name="sonnet-builder"):
        return self.verdict("guard_agent.py", self.main("Agent", {"subagent_type": "orchestra:" + name, "prompt": self.PROMPT}))

    def skill(self, name):
        return self.verdict("require_skill.py", self.main("Skill", {"skill": name}))

    def test_implementation_is_refused_until_the_rules_are_loaded(self):
        self.skill("orchestra:conductor")
        for name in ("sonnet-builder", "opus-engineer", "sonnet-writer"):
            self.assertEqual(self.delegate(name), "deny", name)
        self.assertEqual(self.skill("orchestra:documentation"), "allow")
        for name in ("sonnet-builder", "opus-engineer", "sonnet-writer"):
            self.assertEqual(self.delegate(name), "allow", name)

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
        self.run_hook("session.py", {"hook_event_name": "SessionStart", "source": "compact"})
        self.skill("orchestra:conductor")
        self.skill("delegation")
        self.assertEqual(self.delegate(), "deny")
        self.skill("documentation")
        self.assertEqual(self.delegate(), "allow")

    def test_ordinary_session_is_left_alone(self):
        self.set_transcript([request(SONNET)])
        self.assertEqual(self.delegate(), "allow")


class BriefConfirmTest(HookTest):
    """Principle 2: the user must have spoken after the brief last changed."""

    PROMPT = "Read the task spec at `%s/tasks/T01-login-fix.md` and carry it out." % UNIT

    def delegate(self, name="sonnet-builder"):
        return self.verdict("guard_agent.py", self.main("Agent", {"subagent_type": "orchestra:" + name, "prompt": self.PROMPT}))

    def spoke(self, at, event="UserPromptSubmit", session="session-1"):
        payload = {"hook_event_name": event, "session_id": session}
        if event == "PostToolUse":
            payload.update(tool_name="AskUserQuestion", tool_input={})
        else:
            payload["prompt"] = "ok"
        self.run_hook("session.py", payload)
        turn = os.path.join(self.state, "sessions", session + ".turn")
        os.utime(turn, (at, at))

    def setUp(self):
        super().setUp()
        self.write(UNIT + "/tasks/T01-login-fix.md", TASK)

    def test_brief_written_after_the_last_prompt_is_denied(self):
        self.spoke(1000)
        self.write(UNIT + "/brief.md", BRIEF, mtime=2000)
        self.assertEqual(self.delegate(), "deny")

    def test_a_prompt_after_the_brief_allows(self):
        self.write(UNIT + "/brief.md", BRIEF, mtime=2000)
        self.spoke(3000)
        self.assertEqual(self.delegate(), "allow")

    def test_an_answer_to_a_question_counts(self):
        self.spoke(1000)
        self.write(UNIT + "/brief.md", BRIEF, mtime=2000)
        self.spoke(3000, event="PostToolUse")
        self.assertEqual(self.delegate(), "allow")

    def test_changing_the_brief_needs_a_new_confirmation(self):
        self.write(UNIT + "/brief.md", BRIEF, mtime=2000)
        self.spoke(3000)
        self.write(UNIT + "/brief.md", BRIEF + "\nMore scope.\n", mtime=4000)
        self.assertEqual(self.delegate(), "deny")

    def test_another_session_speaking_does_not_count(self):
        self.spoke(1000)
        self.write(UNIT + "/brief.md", BRIEF, mtime=2000)
        self.spoke(3000, session="session-2")
        self.assertEqual(self.delegate(), "deny")

    def test_a_subagent_answer_does_not_count(self):
        self.spoke(1000)
        self.write(UNIT + "/brief.md", BRIEF, mtime=2000)
        self.run_hook("session.py", {"hook_event_name": "PostToolUse", "tool_name": "AskUserQuestion",
                                     "agent_id": "agent-1", "agent_type": "orchestra:sonnet-builder"})
        self.assertEqual(self.delegate(), "deny")

    def test_analysis_needs_no_brief_confirmation(self):
        self.spoke(1000)
        self.write(UNIT + "/brief.md", BRIEF, mtime=2000)
        self.assertEqual(self.delegate("opus-analyst"), "allow")

    def test_no_record_of_the_user_speaking_allows(self):
        self.write(UNIT + "/brief.md", BRIEF, mtime=2000)
        self.assertEqual(self.delegate(), "allow")


class SessionNoteTest(HookTest):
    """What the model is told about its role."""

    def note(self, payload):
        out = self.run_hook("session.py", payload)
        return out["hookSpecificOutput"]["additionalContext"] if out else None

    def test_new_session_gets_a_note_for_both_cases(self):
        self.set_transcript(None)
        text = self.note({"hook_event_name": "SessionStart", "source": "startup"})
        self.assertIn("If you are running on a Fable model", text)
        self.assertIn("any other model", text)
        self.assertIn("orchestra:conductor", text)

    def test_known_fable_session_gets_the_conductor_note(self):
        self.set_transcript([request(FABLE), response(FABLE)])
        text = self.note({"hook_event_name": "SessionStart", "source": "resume"})
        self.assertIn("This session runs on a Fable model", text)
        self.assertNotIn("any other model", text)

    def test_known_ordinary_session_gets_no_note(self):
        self.set_transcript([request(SONNET), response(SONNET)])
        self.assertIsNone(self.note({"hook_event_name": "SessionStart", "source": "resume"}))

    def test_switch_notes(self):
        self.assertIn("now runs on a Fable model", self.switch(FABLE, from_model=OPUS)["hookSpecificOutput"]["additionalContext"])
        self.assertIn("no longer runs on a Fable model", self.switch(OPUS, from_model=FABLE)["hookSpecificOutput"]["additionalContext"])
        self.assertIsNone(self.switch(OPUS, from_model=SONNET))

    def test_restored_model_is_recorded_without_a_note(self):
        self.set_transcript(None)
        self.assertIsNone(self.switch(FABLE, from_model=SONNET, source="resume"))
        self.assertEqual(self.write_src(), "deny")

    def test_subagents_get_no_note(self):
        payload = {"hook_event_name": "SessionStart", "source": "startup", "agent_id": "a1", "agent_type": "orchestra:opus-engineer"}
        self.set_transcript(None)
        self.assertIsNone(self.run_hook("session.py", payload))


class GuardAgentTest(HookTest):
    """Principles 2, 4, 5, 6, 9"""

    PROMPT = "Read the task spec at `%s/tasks/T01-login-fix.md` and carry it out." % UNIT

    def agent(self, **tool_input):
        return self.verdict("guard_agent.py", self.main("Agent", tool_input))

    def ready(self):
        self.write(UNIT + "/brief.md", BRIEF)
        self.write(UNIT + "/tasks/T01-login-fix.md", TASK)

    def test_missing_type_is_denied(self):
        self.ready()
        self.assertEqual(self.agent(prompt=self.PROMPT), "deny")

    def test_fork_is_denied(self):
        self.ready()
        self.assertEqual(self.agent(subagent_type="fork", prompt=self.PROMPT), "deny")

    def test_builtin_types_are_denied(self):
        self.ready()
        for stype in ("general-purpose", "Explore", "Plan", "claude", "claude-code-guide", "other:opus-engineer"):
            self.assertEqual(self.agent(subagent_type=stype, prompt=self.PROMPT), "deny", stype)

    def test_roster_agent_is_allowed(self):
        self.ready()
        for name in ("opus-engineer", "sonnet-builder", "sonnet-writer", "opus-analyst"):
            self.assertEqual(self.agent(subagent_type="orchestra:" + name, prompt=self.PROMPT), "allow", name)

    def test_model_rules(self):
        self.ready()
        stype = "orchestra:sonnet-builder"
        self.assertEqual(self.agent(subagent_type=stype, prompt=self.PROMPT, model="fable"), "deny")
        self.assertEqual(self.agent(subagent_type=stype, prompt=self.PROMPT, model="haiku"), "deny")
        self.assertEqual(self.agent(subagent_type=stype, prompt=self.PROMPT, model="inherit"), "deny")
        self.assertEqual(self.agent(subagent_type=stype, prompt=self.PROMPT, model="opus"), "allow")
        self.assertEqual(self.agent(subagent_type=stype, prompt=self.PROMPT, model="sonnet"), "allow")

    def test_task_spec_is_required(self):
        self.write(UNIT + "/brief.md", BRIEF)
        stype = "orchestra:opus-engineer"
        self.assertEqual(self.agent(subagent_type=stype, prompt="Fix the login error."), "deny")
        self.assertEqual(self.agent(subagent_type=stype, prompt=self.PROMPT), "deny")  # no file
        self.write(UNIT + "/tasks/T01-login-fix.md", TASK.replace("## Acceptance criteria", "## Done"))
        self.assertEqual(self.agent(subagent_type=stype, prompt=self.PROMPT), "deny")  # a section is missing
        self.write(UNIT + "/tasks/T01-login-fix.md", TASK.replace("Change only src/login.py.\n", ""))
        self.assertEqual(self.agent(subagent_type=stype, prompt=self.PROMPT), "deny")  # a section is empty
        self.write(UNIT + "/tasks/T01-login-fix.md", TASK)
        self.assertEqual(self.agent(subagent_type=stype, prompt=self.PROMPT), "allow")

    def test_task_spec_outside_a_work_folder_is_not_accepted(self):
        self.write(".orchestra/brief.md", BRIEF)
        self.write(".orchestra/tasks/T01-login-fix.md", TASK)
        prompt = "Read the task spec at `.orchestra/tasks/T01-login-fix.md` and carry it out."
        self.assertEqual(self.agent(subagent_type="orchestra:opus-engineer", prompt=prompt), "deny")

    def test_brief_is_required_for_implementation_only(self):
        self.write(UNIT + "/tasks/T01-login-fix.md", TASK)
        self.assertEqual(self.agent(subagent_type="orchestra:opus-engineer", prompt=self.PROMPT), "deny")
        self.assertEqual(self.agent(subagent_type="orchestra:opus-analyst", prompt=self.PROMPT), "allow")
        self.write(UNIT + "/brief.md", "## Goal\nFix the login error.\n")
        self.assertEqual(self.agent(subagent_type="orchestra:opus-engineer", prompt=self.PROMPT), "deny")
        self.write(UNIT + "/brief.md", BRIEF)
        self.assertEqual(self.agent(subagent_type="orchestra:opus-engineer", prompt=self.PROMPT), "allow")

    def test_brief_of_another_work_folder_does_not_count(self):
        self.write(OTHER_UNIT + "/brief.md", BRIEF)
        self.write(UNIT + "/tasks/T01-login-fix.md", TASK)
        self.assertEqual(self.agent(subagent_type="orchestra:opus-engineer", prompt=self.PROMPT), "deny")

    def test_document_bodies_can_be_in_any_language(self):
        self.write(UNIT + "/brief.md", BRIEF_KO)
        self.write(UNIT + "/tasks/T01-로그인-수정.md", TASK_KO)
        prompt = "지시서 `%s/tasks/T01-로그인-수정.md`를 읽고 수행하라." % UNIT
        self.assertEqual(self.agent(subagent_type="orchestra:opus-engineer", prompt=prompt), "allow")

    def test_headings_ignore_case(self):
        self.write(UNIT + "/brief.md", BRIEF.lower())
        self.write(UNIT + "/tasks/T01-login-fix.md", TASK.upper())
        self.assertEqual(self.agent(subagent_type="orchestra:opus-engineer", prompt=self.PROMPT), "allow")

    def test_subagent_cannot_spawn(self):
        self.ready()
        payload = self.sub("opus-engineer", "Agent", {"subagent_type": "orchestra:sonnet-builder", "prompt": self.PROMPT})
        self.assertEqual(self.verdict("guard_agent.py", payload), "deny")

    def test_workflow_is_denied(self):
        self.assertEqual(self.verdict("guard_agent.py", self.main("Workflow", {"name": "audit"})), "deny")

    def test_ordinary_session_is_left_alone(self):
        self.set_transcript([request(OPUS)])
        self.assertEqual(self.agent(subagent_type="fork", prompt="anything"), "allow")
        self.assertEqual(self.agent(prompt="anything"), "allow")
        self.assertEqual(self.verdict("guard_agent.py", self.main("Workflow", {"name": "audit"})), "allow")


class GuardWriteTest(HookTest):
    """Principle 8: Edit and Write"""

    def write_verdict(self, payload):
        return self.verdict("guard_main.py", payload)

    def test_conductor(self):
        cases = [
            ("src/app.py", "deny"),
            (os.path.join(self.proj, "README.md"), "deny"),
            (UNIT + "/brief.md", "allow"),
            (UNIT + "/tasks/T01-login-fix.md", "allow"),
            (UNIT + "/reviews/T01.md", "allow"),
            (UNIT + "/decisions.md", "allow"),
            (UNIT + "/files/T01/notes.txt", "allow"),
            (UNIT + "/reports/T01.md", "deny"),
            (OTHER_UNIT + "/reports/T07.md", "deny"),
            (os.path.join(tempfile.gettempdir(), "orchestra-note.txt"), "allow"),
            (os.path.expanduser("~/.claude/plans/plan.md"), "allow"),
            (os.path.expanduser("~/.claude/projects/some-project/memory/note.md"), "allow"),
            (os.path.expanduser("~/.orchestra-test-missing/file.txt"), "deny"),
        ]
        for path, expected in cases:
            self.assertEqual(self.write_verdict(self.main("Write", {"file_path": path, "content": "x"})), expected, path)
            self.assertEqual(self.write_verdict(self.main("Edit", {"file_path": path})), expected, path)
        payload = self.main("NotebookEdit", {"notebook_path": "analysis.ipynb"})
        self.assertEqual(self.write_verdict(payload), "deny")

    def test_work_folder_is_kept_out_of_git_by_default(self):
        ignore = os.path.join(self.proj, ".orchestra", ".gitignore")
        self.assertEqual(self.write_verdict(self.main("Write", {"file_path": UNIT + "/brief.md"})), "allow")
        with open(ignore, encoding="utf-8") as f:
            self.assertEqual(f.read(), "*\n")
        with open(ignore, "w", encoding="utf-8") as f:
            f.write("")  # the project chose to commit these records
        self.assertEqual(self.write_verdict(self.main("Write", {"file_path": UNIT + "/decisions.md"})), "allow")
        with open(ignore, encoding="utf-8") as f:
            self.assertEqual(f.read(), "")

    def test_implementation_subagent(self):
        for role in ("opus-engineer", "sonnet-builder", "sonnet-writer"):
            self.assertEqual(self.write_verdict(self.sub(role, "Write", {"file_path": "src/app.py"})), "allow", role)
            self.assertEqual(self.write_verdict(self.sub(role, "Write", {"file_path": UNIT + "/reports/T01.md"})), "allow", role)
            self.assertEqual(self.write_verdict(self.sub(role, "Edit", {"file_path": UNIT + "/tasks/T01-login-fix.md"})), "deny", role)
            self.assertEqual(self.write_verdict(self.sub(role, "Write", {"file_path": UNIT + "/reviews/T01.md"})), "deny", role)
            self.assertEqual(self.write_verdict(self.sub(role, "Write", {"file_path": UNIT + "/brief.md"})), "deny", role)
            self.assertEqual(self.write_verdict(self.sub(role, "Write", {"file_path": UNIT + "/files/T01/run.rb"})), "allow", role)
            self.assertEqual(self.write_verdict(self.sub(role, "Edit", {"file_path": UNIT + "/files/T01/run.rb"})), "allow", role)
            self.assertEqual(self.write_verdict(self.sub(role, "Write", {"file_path": UNIT + "/scratch/run.rb"})), "deny", role)
            self.assertEqual(self.write_verdict(self.sub(role, "Write", {"file_path": ".orchestra/files/run.rb"})), "deny", role)

    def test_readonly_subagent(self):
        self.assertEqual(self.write_verdict(self.sub("opus-analyst", "Write", {"file_path": "src/app.py"})), "deny")
        self.assertEqual(self.write_verdict(self.sub("opus-analyst", "Write", {"file_path": UNIT + "/reports/T02.md"})), "allow")
        self.assertEqual(self.write_verdict(self.sub("opus-analyst", "Write", {"file_path": UNIT + "/brief.md"})), "deny")
        self.assertEqual(self.write_verdict(self.sub("opus-analyst", "Write", {"file_path": UNIT + "/files/T02/fetch.rb"})), "allow")
        self.assertEqual(self.write_verdict(self.sub("opus-analyst", "Write", {"file_path": UNIT + "/scratch/fetch.rb"})), "deny")

    def test_sessions_outside_the_harness_are_left_alone(self):
        self.set_transcript([request(SONNET), response(SONNET)])
        self.assertEqual(self.write_verdict(self.main("Write", {"file_path": "src/app.py"})), "allow")
        self.assertEqual(self.write_verdict(self.main("Write", {"file_path": UNIT + "/reports/T01.md"})), "allow")
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Write", "tool_input": {"file_path": "src/app.py"},
                   "agent_id": "a9", "agent_type": "general-purpose"}
        self.assertEqual(self.write_verdict(payload), "allow")


class GuardBashTest(HookTest):
    """Principles 3 and 8: the conductor's shell commands"""

    ALLOW = [
        "git status",
        "git --no-pager diff HEAD~1 --stat",
        "git -C sub log --oneline -5",
        "git branch",
        "git branch --show-current",
        "git stash list",
        "git fetch origin",
        "npm test",
        "npm run build 2>&1 | tail -20",
        "pytest -x -q > /tmp/orchestra-out.txt 2>&1; tail /tmp/orchestra-out.txt",
        "echo hi > /dev/null",
        'echo "decided without asking" >> %s/decisions.md' % UNIT,
        'echo "묻지 않고 정한 것" >> %s/decisions.md' % UNIT,
        'grep -n "a > b" src/app.py',
        'grep ">" src/app.py',
        "cut -d'|' -f2 data.txt | sort | uniq -c",
        "sed -n '1,5p' src/app.py",
        "sed -E 's/a/b/' src/app.py",
        'python3 -c "print(1)"',
        "python3 scripts/check.py --strict",
        "rm /tmp/orchestra-foo.txt",
        "cp src/a.py /tmp/orchestra-a.py",
        'find . -name "*.py" -exec grep -l foo {} \\;',
        "claude plugin validate .",
        "claude --version",
        "echo x | tee /tmp/orchestra-out.txt",
        "echo x | tee -a /dev/null",
        "curl -s https://example.com",
        "curl -sSL https://example.com -o /tmp/orchestra-x.html",
        "echo foo # > src/x.txt",
        "docker compose exec app bin/rails test",
        "bin/rails db:migrate:status",
        "make test",
        "mkdir -p %s/tasks" % UNIT,
        "touch %s/tasks/T02-next.md" % UNIT,
        "ls .orchestra",
        "gh pr view 12",
        "gh api repos/a/b/pulls",
        "pip list",
        "uv run pytest",
        "bundle exec rspec spec/login_spec.rb",
        "yarn test",
        "pacman -Ss ripgrep",
        "awk '{print $1}' data.txt",
        "perl -ne 'print if /x/' data.txt",
        "wget -qO- https://example.com",
        "ls -la && wc -l src/*.py",
        "timeout 60 npm test",
        "env FOO=1 pytest",
        "cd src && npm test",
    ]
    DENY = [
        "echo hi > src/x.txt",
        "echo hi >> README.md",
        "sed -i 's/a/b/' src/app.py",
        "sed -i.bak -e 's/a/b/' src/app.py",
        "cat > src/a.py <<'EOF'\nprint(1)\nEOF",
        "python3 -c \"open('a.txt','w').write('x')\"",
        "python3 - <<'EOF'\nimport os\nos.remove('x')\nEOF",
        "node -e \"require('fs').writeFileSync('a','b')\"",
        "rm -rf build",
        "cp /tmp/orchestra-a.py src/a.py",
        "mv a b",
        "find . -name '*.pyc' -delete",
        "find . -name '*.py' -exec sed -i s/a/b/ {} +",
        "find . -name '*.py' | xargs sed -i 's/a/b/'",
        "find . -type f | xargs rm",
        'bash -c "rm -rf src"',
        "echo $(rm -rf src)",
        'echo "$(sed -i s/a/b/ f)"',
        "echo `rm -rf src`",
        'claude -p "fix it" --model fable',
        "claude",
        "cat %s/reports/T01.md" % UNIT,
        "rm %s/reports/T01.md" % UNIT,
        "echo x | tee out.txt",
        "curl -o x.zip https://example.com/x.zip",
        "curl -O https://example.com/x.zip",
        "echo foo#bar; rm -rf src",
        "cd src\nrm x.py",
        "if [ -f x ]; then rm x; fi",
        'for f in *.py; do sed -i s/a/b/ "$f"; done',
        "VAR=1 sudo rm -rf /",
        "awk '{print > \"out.txt\"}' data.txt",
        "perl -pi -e 's/a/b/' data.txt",
        "wget https://example.com/a.zip",
        "vim src/app.py",
        "patch -p1 < fix.diff",
        "truncate -s 0 app.log",
    ]
    ASK = [
        'git commit -m "fix"',
        "git add -A && git commit -m x",
        "git push origin main",
        "git checkout -- src/a.py",
        "git branch -D feature",
        "git branch new-feature",
        "git stash",
        "git reset --hard HEAD~1",
        "npm install lodash",
        "pip install requests",
        "bundle install",
        "yarn",
        "pacman -S ripgrep",
        "go mod tidy",
        "curl -X POST https://api.example.com -d '{\"a\":1}'",
        "bin/rails db:migrate",
        "ssh host ls",
        "gh pr create --title x",
        "kubectl apply -f deploy.yaml",
        'echo "unterminated',
    ]

    def test_allowed_commands(self):
        for cmd in self.ALLOW:
            self.assertEqual(self.bash(cmd), "allow", cmd)

    def test_denied_commands(self):
        for cmd in self.DENY:
            self.assertEqual(self.bash(cmd), "deny", cmd)

    def test_commands_that_need_the_user(self):
        for cmd in self.ASK:
            self.assertEqual(self.bash(cmd), "ask", cmd)

    def test_readonly_subagent(self):
        def run(cmd):
            return self.verdict("guard_main.py", self.sub("opus-analyst", "Bash", {"command": cmd}))
        self.assertEqual(run("git status"), "allow")
        self.assertEqual(run("rg -n TODO src"), "allow")
        self.assertEqual(run("sed -i 's/a/b/' src/app.py"), "deny")
        self.assertEqual(run("echo x > src/x.txt"), "deny")
        self.assertEqual(run("ruby fetch.rb > %s/files/T02/out.jsonl" % UNIT), "allow")
        self.assertEqual(run("cp src/app.py %s/files/T02/app.py" % UNIT), "allow")
        self.assertEqual(run("ruby fetch.rb > %s/brief.md" % UNIT), "deny")
        self.assertEqual(run('git commit -m "x"'), "ask")
        self.assertEqual(run("claude -p x"), "deny")

    def test_implementation_subagent(self):
        def run(cmd):
            return self.verdict("guard_main.py", self.sub("opus-engineer", "Bash", {"command": cmd}))
        self.assertEqual(run("sed -i 's/a/b/' src/app.py"), "allow")
        self.assertEqual(run("rm -rf build && npm install"), "allow")
        self.assertEqual(run('git commit -m "x"'), "allow")
        self.assertEqual(run("claude -p x"), "deny")
        self.assertEqual(run('bash -c "claude -p x --model fable"'), "deny")
        self.assertEqual(run("claude --version"), "allow")

    def test_ordinary_session_is_left_alone(self):
        self.set_transcript([request(SONNET)])
        for cmd in ("rm -rf build", "sed -i 's/a/b/' src/app.py", 'git commit -m "x"', "echo hi > src/x.txt"):
            self.assertEqual(self.bash(cmd), "allow", cmd)


class ReportGateTest(HookTest):
    """Principle 7: review independently, then read the report"""

    REPORT = UNIT + "/reports/T01.md"
    REVIEW = UNIT + "/reviews/T01.md"

    def read(self, rel=None):
        return self.verdict("report_gate.py", self.main("Read", {"file_path": rel or self.REPORT}))

    def test_other_files_are_not_gated(self):
        self.write("src/app.py", "print(1)\n")
        self.assertEqual(self.read("src/app.py"), "allow")
        self.assertEqual(self.read(UNIT + "/reports/missing.md"), "allow")
        self.write(UNIT + "/brief.md", BRIEF)
        self.assertEqual(self.read(UNIT + "/brief.md"), "allow")

    def test_report_needs_a_review_first(self):
        self.write(self.REPORT, REPORT_DONE, mtime=1000)
        self.assertEqual(self.read(), "deny")

    def test_review_must_be_complete(self):
        self.write(self.REPORT, REPORT_DONE, mtime=1000)
        self.write(self.REVIEW, "## Scope reviewed\ndiff\n\n## Verdict\nPass\n", mtime=2000)
        self.assertEqual(self.read(), "deny")  # no "Checks run"
        self.write(self.REVIEW, "## Scope reviewed\ndiff\n\n## Checks run\nnone\n\n## Verdict\nPass\n", mtime=2000)
        self.assertEqual(self.read(), "deny")  # "Checks run" is a formality
        self.write(self.REVIEW, REVIEW.replace("Pass.\n", ""), mtime=2000)
        self.assertEqual(self.read(), "deny")  # empty verdict

    def test_review_must_be_newer_than_report(self):
        self.write(self.REPORT, REPORT_DONE, mtime=2000)
        self.write(self.REVIEW, REVIEW, mtime=1000)
        self.assertEqual(self.read(), "deny")

    def test_report_opens_after_review(self):
        self.write(self.REPORT, REPORT_DONE, mtime=1000)
        self.write(self.REVIEW, REVIEW, mtime=2000)
        self.assertEqual(self.read(), "allow")

    def test_review_of_another_work_folder_does_not_count(self):
        self.write(self.REPORT, REPORT_DONE, mtime=1000)
        self.write(OTHER_UNIT + "/reviews/T01.md", REVIEW, mtime=2000)
        self.assertEqual(self.read(), "deny")

    def test_review_body_can_be_in_any_language(self):
        self.write(self.REPORT, REPORT_DONE, mtime=1000)
        self.write(self.REVIEW, REVIEW_KO, mtime=2000)
        self.assertEqual(self.read(), "allow")

    def test_rework_needs_a_new_review(self):
        self.write(self.REPORT, REPORT_DONE, mtime=1000)
        self.write(self.REVIEW, REVIEW, mtime=2000)
        self.assertEqual(self.read(), "allow")
        self.write(self.REPORT, REPORT_DONE + "Reworked.\n", mtime=3000)
        self.assertEqual(self.read(), "deny")
        self.write(self.REVIEW, REVIEW, mtime=4000)
        self.assertEqual(self.read(), "allow")

    def test_blocked_and_failed_reports_open_at_once(self):
        self.write(self.REPORT, "STATUS: BLOCKED\n\nThe fix needs a change outside the scope.\n")
        self.assertEqual(self.read(), "allow")
        self.write(self.REPORT, "**STATUS:** FAILED\n")
        self.assertEqual(self.read(), "allow")

    def test_analyst_report_opens_at_once(self):
        self.write(self.REPORT, verified_report([MANUAL_CHECK]))
        stop = {"hook_event_name": "SubagentStop", "agent_id": "agent-7", "agent_type": "orchestra:opus-analyst",
                "last_assistant_message": "STATUS: DONE\nTASK: T01\nREPORT: %s" % self.REPORT}
        self.assertEqual(self.verdict("check_report.py", stop), "allow")
        self.assertEqual(self.read(), "allow")

    def test_analyst_report_in_one_folder_does_not_open_another(self):
        self.write(self.REPORT, verified_report([MANUAL_CHECK]))
        stop = {"hook_event_name": "SubagentStop", "agent_id": "agent-7", "agent_type": "orchestra:opus-analyst",
                "last_assistant_message": "STATUS: DONE\nTASK: T01\nREPORT: %s" % self.REPORT}
        self.assertEqual(self.verdict("check_report.py", stop), "allow")
        self.write(OTHER_UNIT + "/reports/T01.md", REPORT_DONE)
        self.assertEqual(self.read(OTHER_UNIT + "/reports/T01.md"), "deny")

    def test_subagents_are_not_gated(self):
        self.write(self.REPORT, REPORT_DONE)
        payload = self.sub("opus-engineer", "Read", {"file_path": self.REPORT})
        self.assertEqual(self.verdict("report_gate.py", payload), "allow")

    def test_ordinary_session_is_not_gated(self):
        self.write(self.REPORT, REPORT_DONE)
        self.set_transcript([request(OPUS), response(OPUS)])
        self.assertEqual(self.read(), "allow")

    def test_task_files_are_not_gated(self):
        self.write(UNIT + "/files/T01/out.txt", "x\n")
        self.assertEqual(self.read(UNIT + "/files/T01/out.txt"), "allow")
        payload = self.main("Grep", {"pattern": "x", "path": UNIT + "/files"})
        self.assertEqual(self.verdict("report_gate.py", payload), "allow")

    def test_grep_in_reports_is_denied(self):
        self.write(self.REPORT, REPORT_DONE)
        payload = self.main("Grep", {"pattern": "passed", "path": UNIT + "/reports"})
        self.assertEqual(self.verdict("report_gate.py", payload), "deny")
        payload = self.main("Grep", {"pattern": "passed", "path": "src"})
        self.assertEqual(self.verdict("report_gate.py", payload), "allow")


class CheckReportTest(HookTest):
    """Principle 7: handback format and verification records for DONE."""

    REPORT = UNIT + "/reports/T01.md"

    def stop(self, message, role="opus-engineer", agent_id="agent-1"):
        payload = {"hook_event_name": "SubagentStop", "agent_id": agent_id, "agent_type": "orchestra:" + role,
                   "last_assistant_message": message}
        return self.verdict("check_report.py", payload)

    def test_narrative_summary_is_blocked(self):
        self.write(self.REPORT, REPORT_DONE)
        self.assertEqual(self.stop("I added session expiry handling and all tests pass.", agent_id="n1"), "block")
        self.assertEqual(self.stop(STATUS_DONE + "\n\nAll tests passed.", agent_id="n2"), "block")
        self.assertEqual(self.stop(STATUS_DONE.replace("DONE", "DONE (all tests pass)"), agent_id="n3"), "block")
        self.assertEqual(self.stop(STATUS_DONE.replace("TASK: T01", "TASK: T01 fixed everything"), agent_id="n4"), "block")
        self.assertEqual(self.stop(STATUS_DONE + "\n\n모든 테스트가 통과했습니다.", agent_id="n5"), "block")

    def test_valid_message_passes(self):
        self.write(self.REPORT, REPORT_DONE)
        self.assertEqual(self.stop(STATUS_DONE), "allow")
        self.assertEqual(self.stop("```\n" + STATUS_DONE + "\n```"), "allow")
        fancy = "**STATUS:** DONE\n**TASK:** T01\n**REPORT:** `%s`\n**FILES:**\n- src/login.py\n- tests/test_login.py" % self.REPORT
        self.assertEqual(self.stop(fancy), "allow")
        self.assertEqual(self.stop("STATUS: DONE\nTASK: T01\nREPORT: %s\nFILES: none" % self.REPORT), "allow")

    def test_done_requires_verification_for_every_roster_agent(self):
        self.write(self.REPORT, "STATUS: DONE\n\n## What I did\nChanged the file.\n")
        for role in ("opus-engineer", "sonnet-builder", "opus-analyst", "sonnet-writer"):
            with self.subTest(role=role):
                self.assertEqual(self.stop(STATUS_DONE, role=role, agent_id=role), "block")

    def test_done_rejects_failed_or_unrun_checks(self):
        for result in ("FAIL", "NOT_RUN", "SKIPPED"):
            with self.subTest(result=result):
                self.write(self.REPORT, verified_report([COMMAND_CHECK, dict(COMMAND_CHECK, result=result)]))
                self.assertEqual(self.stop(STATUS_DONE, agent_id=result), "block")

    def test_done_rejects_unverified_requirements(self):
        self.write(self.REPORT, verified_report([COMMAND_CHECK], ["The required smoke test could not start."]))
        self.assertEqual(self.stop(STATUS_DONE), "block")

    def test_command_check_requires_successful_exit(self):
        for exit_code in (1, -1, None, "0", False):
            with self.subTest(exit_code=exit_code):
                self.write(self.REPORT, verified_report([dict(COMMAND_CHECK, exit_code=exit_code)]))
                self.assertEqual(self.stop(STATUS_DONE, agent_id=str(exit_code)), "block")
        check = dict(COMMAND_CHECK)
        del check["exit_code"]
        self.write(self.REPORT, verified_report([check]))
        self.assertEqual(self.stop(STATUS_DONE, agent_id="missing-exit"), "block")

    def test_manual_verification_is_allowed(self):
        check = dict(MANUAL_CHECK, criterion="세션 만료 처리 위치를 확인한다.", evidence="src/session.py:42에서 처리한다.")
        self.write(self.REPORT, verified_report([check]))
        for role in ("opus-engineer", "sonnet-builder", "opus-analyst", "sonnet-writer"):
            with self.subTest(role=role):
                self.assertEqual(self.stop(STATUS_DONE, role=role, agent_id=role), "allow")

    def test_verification_requires_usable_records(self):
        bad_records = [[], ["all passed"], [dict(COMMAND_CHECK, kind="syntax")]]
        for key in ("criterion", "method", "evidence"):
            for value in ("", "  ", None, []):
                bad_records.append([dict(COMMAND_CHECK, **{key: value})])
        for number, checks in enumerate(bad_records):
            with self.subTest(checks=checks):
                self.write(self.REPORT, verified_report(checks))
                self.assertEqual(self.stop(STATUS_DONE, agent_id=f"record-{number}"), "block")

    def test_verification_requires_valid_json_and_unverified_list(self):
        bad_payloads = ["not json", "[]", "null", "{}", json.dumps({"checks": [COMMAND_CHECK]}),
                        json.dumps({"checks": [COMMAND_CHECK], "unverified": "none"})]
        for number, payload in enumerate(bad_payloads):
            with self.subTest(payload=payload):
                self.write(self.REPORT, "STATUS: DONE\n\n## What I verified\n```json\n" + payload + "\n```\n")
                self.assertEqual(self.stop(STATUS_DONE, agent_id=f"json-{number}"), "block")

    def test_invalid_done_is_let_through_after_the_retry_limit(self):
        self.write(self.REPORT, "STATUS: DONE\n")
        self.assertEqual([self.stop(STATUS_DONE) for _ in range(3)], ["block"] * 3)
        self.assertNotEqual(self.stop(STATUS_DONE), "block")  # no endless retries

    def test_fixed_done_passes_after_refusals(self):
        self.write(self.REPORT, "STATUS: DONE\n")
        self.assertEqual(self.stop(STATUS_DONE), "block")
        self.write(self.REPORT, REPORT_DONE)
        self.assertEqual(self.stop(STATUS_DONE), "allow")

    def test_text_around_the_verification_block_is_allowed(self):
        report = REPORT_DONE.replace("```json", "I ran the checks below.\n\n```json", 1)
        self.assertIn("I ran the checks below.", report)
        self.write(self.REPORT, report)
        self.assertEqual(self.stop(STATUS_DONE), "allow")

    def test_report_done_cannot_hide_behind_failed_message(self):
        self.write(self.REPORT, "STATUS: DONE\n")
        message = STATUS_DONE.replace("STATUS: DONE", "STATUS: FAILED")
        self.assertEqual([self.stop(message) for _ in range(3)], ["block"] * 3)

    def test_incomplete_work_can_return_blocked_or_failed(self):
        for status in ("BLOCKED", "FAILED"):
            with self.subTest(status=status):
                self.write(self.REPORT, f"STATUS: {status}\n\nThe required check did not pass.\n")
                message = STATUS_DONE.replace("STATUS: DONE", f"STATUS: {status}")
                if status == "BLOCKED":
                    message += "\nQUESTION: May I install the declared test dependencies?"
                self.assertEqual(self.stop(message), "allow")

    def test_report_file_is_required(self):
        self.assertEqual(self.stop(STATUS_DONE, agent_id="r1"), "block")  # no file
        self.write(self.REPORT, "")
        self.assertEqual(self.stop(STATUS_DONE, agent_id="r2"), "block")  # empty file
        self.write(self.REPORT, "## What I did\nNo STATUS on the first line.\n")
        self.assertEqual(self.stop(STATUS_DONE, agent_id="r3"), "block")
        self.write(self.REPORT, "STATUS: FAILED\n")
        self.assertEqual(self.stop(STATUS_DONE, agent_id="r4"), "block")  # status mismatch
        self.write(UNIT + "/reports/T02.md", REPORT_DONE)
        self.assertEqual(self.stop(STATUS_DONE.replace("reports/T01", "reports/T02"), agent_id="r5"), "block")  # task mismatch
        self.write("notes/T01.md", REPORT_DONE)
        self.assertEqual(self.stop("STATUS: DONE\nTASK: T01\nREPORT: notes/T01.md", agent_id="r6"), "block")  # outside reports
        self.write(".orchestra/reports/T01.md", REPORT_DONE)
        self.assertEqual(self.stop("STATUS: DONE\nTASK: T01\nREPORT: .orchestra/reports/T01.md", agent_id="r7"), "block")  # not in a work folder

    def test_blocked_needs_a_question(self):
        self.write(self.REPORT, "STATUS: BLOCKED\n")
        base = "STATUS: BLOCKED\nTASK: T01\nREPORT: %s" % self.REPORT
        self.assertEqual(self.stop(base), "block")
        self.assertEqual(self.stop(base + "\nQUESTION: May I change src/session.py?\nWithout it the acceptance criteria cannot be met."), "allow")

    def test_question_only_when_blocked(self):
        self.write(self.REPORT, REPORT_DONE)
        self.assertEqual(self.stop(STATUS_DONE + "\nQUESTION: What should I do next?"), "block")

    def test_files_must_be_paths(self):
        self.write(self.REPORT, REPORT_DONE)
        message = "STATUS: DONE\nTASK: T01\nREPORT: %s\nFILES: fixed the bug and every test passes now" % self.REPORT
        self.assertEqual(self.stop(message), "block")

    def test_agents_off_the_roster_are_left_alone(self):
        payload = {"hook_event_name": "SubagentStop", "agent_id": "x", "agent_type": "general-purpose",
                   "last_assistant_message": "anything"}
        self.assertEqual(self.verdict("check_report.py", payload), "allow")
        payload["agent_type"] = ""
        self.assertEqual(self.verdict("check_report.py", payload), "allow")

    def test_gives_up_after_three_attempts(self):
        results = [self.stop("a summary that ignores the format", agent_id="agent-9") for _ in range(4)]
        self.assertEqual(results, ["block", "block", "block", "warn"])

    def test_handback_path(self):
        self.write(self.REPORT, REPORT_DONE)
        bad = self.sub("opus-engineer", "SubagentHandback", {"message": "All fixed."})
        self.assertEqual(self.verdict("check_report.py", bad), "deny")
        good = self.sub("opus-engineer", "SubagentHandback", {"message": STATUS_DONE})
        self.assertEqual(self.verdict("check_report.py", good), "allow")
        self.assertEqual(self.stop("", agent_id="agent-1"), "allow")  # already reported through the tool

    def test_handback_rejects_unverified_done(self):
        self.write(self.REPORT, "STATUS: DONE\n")
        handback = self.sub("opus-engineer", "SubagentHandback", {"message": STATUS_DONE})
        self.assertEqual([self.verdict("check_report.py", handback) for _ in range(5)], ["deny"] * 5)

    def test_stop_rechecks_report_after_handback(self):
        self.write(self.REPORT, REPORT_DONE)
        handback = self.sub("opus-engineer", "SubagentHandback", {"message": STATUS_DONE})
        self.assertEqual(self.verdict("check_report.py", handback), "allow")
        self.write(self.REPORT, verified_report([dict(COMMAND_CHECK, result="NOT_RUN")]))
        self.assertEqual(self.stop(""), "block")

    def test_rejected_handback_clears_previous_approval(self):
        self.write(self.REPORT, REPORT_DONE)
        good = self.sub("opus-engineer", "SubagentHandback", {"message": STATUS_DONE})
        self.assertEqual(self.verdict("check_report.py", good), "allow")
        bad = self.sub("opus-engineer", "SubagentHandback", {"message": "not a valid handback"})
        self.assertEqual(self.verdict("check_report.py", bad), "deny")
        self.assertEqual(self.stop(""), "block")


class DoctorTest(HookTest):
    def test_doctor_runs(self):
        self.write(UNIT + "/brief.md", BRIEF)
        self.write(UNIT + "/tasks/T01-login-fix.md", TASK)
        env = dict(os.environ, CLAUDE_PROJECT_DIR=self.proj)
        proc = subprocess.run([sys.executable, os.path.join(SCRIPTS, "doctor.py")], capture_output=True, env=env, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        out = proc.stdout.decode("utf-8")
        self.assertIn("orchestra check", out)
        self.assertIn("Hook scripts work", out)
        self.assertIn("2026-11-12-login-fix: brief, 1 task specs, 0 reports, 0 review records", out)


if __name__ == "__main__":
    unittest.main(verbosity=1)
