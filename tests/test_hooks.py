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
import unittest.mock

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
        # the user has turned conductor mode on, and the delegation rules and the documentation
        # rules are already loaded.
        self.set_transcript([request(FABLE)])
        self.on = os.path.join(self.state, "sessions", "session-1.mode")
        self.rules = os.path.join(self.state, "sessions", "session-1.delegation")
        self.doc_rules = os.path.join(self.state, "sessions", "session-1.documentation")
        os.makedirs(os.path.dirname(self.rules))
        self.set_mode("claude")
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

    def set_mode(self, mode, session="session-1"):
        """Record a conductor mode for a session, as the user's command does."""
        with open(os.path.join(self.state, "sessions", session + ".mode"), "w", encoding="utf-8") as f:
            f.write(mode)

    def turn_on(self, session):
        """Turn the `claude` conductor mode on for another session, as `/orchestra:conduct` does."""
        self.set_mode("claude", session)

    def switch(self, to_model, from_model=SONNET, source="picker"):
        payload = {"hook_event_name": "PostModelSwitch", "from_model": from_model, "to_model": to_model,
                   "requested_model": to_model, "source": source}
        return self.run_hook("session.py", payload)


class ModeTest(HookTest):
    """With conductor mode on, a session is a conductor session exactly while its main
    conversation runs on a Fable or an Opus model."""

    def test_first_request_of_a_new_session(self):
        self.set_transcript([request(FABLE)])
        self.assertEqual(self.write_src(), "deny")
        self.set_transcript([request(SONNET)])
        self.assertEqual(self.write_src(), "allow")
        self.set_transcript([request(OPUS)])
        self.assertEqual(self.write_src(), "deny")
        self.set_transcript([request("claude-haiku-5-5")])
        self.assertEqual(self.write_src(), "allow")

    def test_mythos_counts_as_fable(self):
        self.set_transcript([request("claude-mythos-5-2")])
        self.assertEqual(self.write_src(), "deny")

    def test_opus_conducts_with_the_same_roster(self):
        self.set_transcript([request(OPUS), response(OPUS)])
        self.assertEqual(self.verdict("guard_agent.py", self.main("Agent", {"subagent_type": "fork"})), "deny")
        self.assertEqual(self.verdict("guard_agent.py", self.main("Agent", {"prompt": "x"})), "deny")
        self.assertEqual(self.verdict("guard_agent.py", self.main("Agent", {"subagent_type": "general-purpose"})), "deny")

    def test_last_response_decides(self):
        self.set_transcript([request(FABLE), response(FABLE)])
        self.assertEqual(self.write_src(), "deny")
        self.set_transcript([request(SONNET), response(SONNET)])
        self.assertEqual(self.write_src(), "allow")

    def test_request_in_flight_turns_the_harness_on(self):
        self.set_transcript([request(SONNET), response(SONNET), request(FABLE)])
        self.assertEqual(self.write_src(), "deny")
        self.set_transcript([request(SONNET), response(SONNET), request(OPUS)])
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
        self.turn_on("session-2")
        self.set_transcript(None)
        self.switch(FABLE)
        payload = self.main("Write", {"file_path": "src/app.py"})
        payload["session_id"] = "session-2"
        self.assertEqual(self.verdict("guard_main.py", payload), "allow")


class SwitchTest(HookTest):
    """Conductor mode is off when a session starts. The user turns it on and off for one session."""

    def say(self, prompt, session="session-1"):
        return self.run_hook("session.py", {"hook_event_name": "UserPromptSubmit", "prompt": prompt, "session_id": session})

    def test_off_when_a_session_starts(self):
        os.remove(self.on)
        for model in (FABLE, OPUS):
            self.set_transcript([request(model)])
            self.assertEqual(self.write_src(), "allow", model)
            self.assertEqual(self.verdict("guard_agent.py", self.main("Agent", {"subagent_type": "fork"})), "allow", model)
            self.assertEqual(self.verdict("require_skill.py", self.main("Read", {"file_path": "a"})), "allow", model)

    def test_each_command_turns_it_on(self):
        for command in ("/orchestra:conduct", "/orchestra:enable", "/orchestra:conductor fix the login bug"):
            os.remove(self.on)
            self.assertEqual(self.write_src(), "allow", command)
            self.say(command)
            self.assertEqual(self.write_src(), "deny", command)

    def test_disable_and_enable(self):
        self.assertEqual(self.write_src(), "deny")
        self.say("/orchestra:disable")
        self.assertEqual(self.write_src(), "allow")
        self.assertEqual(self.verdict("guard_agent.py", self.main("Agent", {"subagent_type": "fork"})), "allow")
        self.say("/orchestra:enable")
        self.assertEqual(self.write_src(), "deny")
        self.say("/orchestra:disable")
        self.say("/orchestra:conduct")
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
            self.run_hook("session.py", {"hook_event_name": "UserPromptExpansion", "command_name": "orchestra:conduct",
                                         "command_source": "plugin", "prompt": "/orchestra:conduct"})
            self.assertEqual(self.write_src(), "deny", name)
        self.run_hook("session.py", {"hook_event_name": "UserPromptExpansion", "command_name": "other:disable",
                                     "command_source": "plugin", "prompt": "/other:disable"})
        self.assertEqual(self.write_src(), "deny")

    def test_only_a_typed_command_counts(self):
        self.say("please run /orchestra:disable for me")
        self.say("/orchestra:disabled")
        self.say("/other:disable")
        self.assertEqual(self.write_src(), "deny")
        os.remove(self.on)
        self.say("please run /orchestra:conduct for me")
        self.say("/orchestra:conductor-x")
        self.say("/other:conduct")
        self.assertEqual(self.write_src(), "allow")

    def test_mode_is_per_session(self):
        self.say("/orchestra:disable", session="session-2")
        self.assertEqual(self.write_src(), "deny")
        os.remove(self.on)
        self.say("/orchestra:conduct", session="session-2")
        self.assertEqual(self.write_src(), "allow")

    def test_disabled_session_gets_no_notes(self):
        self.say("/orchestra:disable")
        self.assertIsNone(self.run_hook("session.py", {"hook_event_name": "SessionStart", "source": "compact"}))
        self.assertIsNone(self.switch(FABLE, from_model=SONNET))
        self.assertEqual(self.write_src(), "allow")


class CliModeTest(HookTest):
    """In a CLI mode (`codex`, `agy`) the hooks step aside, except the Opus effort question."""

    def say(self, prompt):
        self.run_hook("session.py", {"hook_event_name": "UserPromptSubmit", "prompt": prompt})

    def guards(self):
        """The verdicts of each guard on calls it refuses in the `claude` mode."""
        self.write(UNIT + "/reports/T01.md", "STATUS: DONE\nno verification record\n")
        return {
            "write": self.write_src(),
            "fork": self.verdict("guard_agent.py", self.main("Agent", {"subagent_type": "fork"})),
            "untyped": self.verdict("guard_agent.py", self.main("Agent", {"prompt": "x"})),
            "migrate": self.bash("bin/rails db:migrate"),
            "load": self.verdict("require_skill.py", self.main("Read", {"file_path": "a"})),
            "report": self.verdict("report_gate.py", self.main("Read", {"file_path": UNIT + "/reports/T01.md"})),
            "subagent": self.verdict("guard_agent.py", self.sub("sonnet-builder", "Agent", {"subagent_type": "orchestra:opus-engineer", "prompt": "x"})),
            "handback": self.verdict("check_report.py", {"hook_event_name": "SubagentStop", "agent_id": "agent-1",
                                                         "agent_type": "orchestra:opus-engineer",
                                                         "last_assistant_message": "I did it."}),
        }

    def test_the_claude_mode_guards(self):
        verdicts = self.guards()
        self.assertEqual(verdicts.pop("handback"), "block")
        for name, verdict in verdicts.items():
            self.assertEqual(verdict, "deny", name)

    def test_every_guard_steps_aside_in_a_cli_mode(self):
        for command in ("/orchestra:conduct-codex", "/orchestra:conduct-agy"):
            self.say(command)
            for name, verdict in self.guards().items():
                self.assertEqual(verdict, "allow", "%s %s" % (command, name))

    def test_commands_switch_between_modes(self):
        self.say("/orchestra:conduct-codex fix the login bug")
        self.assertEqual(self.write_src(), "allow")
        self.say("/orchestra:conduct")
        self.assertEqual(self.write_src(), "deny")
        self.say("/orchestra:conduct-agy")
        self.assertEqual(self.write_src(), "allow")
        self.say("/orchestra:conductor")
        self.assertEqual(self.write_src(), "deny")
        self.say("/orchestra:conduct-codex")
        self.say("/orchestra:disable")
        self.assertEqual(self.write_src(), "allow")
        self.assertEqual(self.verdict("guard_agent.py", self.main("Agent", {"subagent_type": "fork"})), "allow")

    def test_only_the_exact_command_counts(self):
        for prompt in ("/orchestra:conduct-codexx", "/orchestra:conduct-cod", "please run /orchestra:conduct-codex"):
            self.say(prompt)
            self.assertEqual(self.write_src(), "deny", prompt)
        self.run_hook("session.py", {"hook_event_name": "UserPromptExpansion", "command_name": "orchestra:conduct-codex",
                                     "command_source": "plugin", "prompt": "/orchestra:conduct-codex"})
        self.assertEqual(self.write_src(), "allow")
        self.run_hook("session.py", {"hook_event_name": "UserPromptExpansion", "command_name": "other:conduct",
                                     "command_source": "plugin", "prompt": "/other:conduct"})
        self.assertEqual(self.write_src(), "allow")

    def test_a_cli_mode_gets_no_notes(self):
        self.say("/orchestra:conduct-codex")
        self.set_transcript([request(FABLE), response(FABLE)])
        for source in ("startup", "resume", "compact", "clear"):
            self.assertIsNone(self.run_hook("session.py", {"hook_event_name": "SessionStart", "source": source}), source)
        self.assertIsNone(self.switch(FABLE, from_model=SONNET))
        self.assertIsNone(self.switch(SONNET, from_model=OPUS))

    def test_the_effort_question_still_asks_once(self):
        self.say("/orchestra:conduct-codex")
        self.set_transcript([at_effort(OPUS, "low")])
        read = self.main("Read", {"file_path": "a"})
        self.assertEqual(self.verdict("require_skill.py", read), "deny")
        self.assertEqual(self.verdict("require_skill.py", read), "allow")
        self.say("/orchestra:conduct-agy")
        self.assertEqual(self.verdict("require_skill.py", read), "deny")
        self.set_transcript([at_effort(OPUS, "high")])
        self.say("/orchestra:conduct-codex")
        self.assertEqual(self.verdict("require_skill.py", read), "allow")
        self.set_transcript([at_effort(SONNET, "low")])
        self.assertEqual(self.verdict("require_skill.py", read), "allow")

    def test_the_mode_is_per_session(self):
        self.set_mode("codex", "session-2")
        self.assertEqual(self.write_src(), "deny")


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
        self.turn_on("session-2")
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


def at_effort(model, effort):
    """A request sent at an effort level, as Claude Code records it."""
    entry = request(model)
    entry["params"]["output_config"] = {"effort": effort}
    return entry


class EffortTest(HookTest):
    """An Opus conductor at a low effort level is told once to recommend High or above."""

    def call(self, tool="Read", tool_input=None):
        return self.run_hook("require_skill.py", self.main(tool, tool_input or {"file_path": "src/app.py"}))

    def decision(self, out):
        return out["hookSpecificOutput"]["permissionDecision"] if out else "allow"

    def say(self, prompt):
        self.run_hook("session.py", {"hook_event_name": "UserPromptSubmit", "prompt": prompt})

    def test_low_and_medium_are_asked_about_once(self):
        for effort in ("low", "medium", "LOW"):
            with self.subTest(effort=effort):
                self.say("/orchestra:conduct")
                self.set_transcript([at_effort(OPUS, effort)])
                out = self.call("Skill", {"skill": "orchestra:conductor"})
                self.assertEqual(self.decision(out), "deny")
                reason = out["hookSpecificOutput"]["permissionDecisionReason"]
                self.assertIn("High effort or above", reason)
                self.assertIn("`%s`" % effort.lower(), reason)
                # The user answered. Whatever they said, the work goes on at this level.
                self.assertEqual(self.decision(self.call("Skill", {"skill": "orchestra:conductor"})), "allow")
                self.assertEqual(self.decision(self.call()), "allow")
                self.assertEqual(self.decision(self.call()), "allow")

    def test_high_and_above_are_not_asked_about(self):
        for effort in ("high", "xhigh", "max"):
            with self.subTest(effort=effort):
                self.set_transcript([at_effort(OPUS, effort)])
                self.assertEqual(self.decision(self.call("Skill", {"skill": "orchestra:conductor"})), "allow")

    def test_unknown_effort_is_not_asked_about(self):
        self.set_transcript([request(OPUS)])
        self.assertEqual(self.decision(self.call("Skill", {"skill": "orchestra:conductor"})), "allow")

    def test_fable_is_not_asked_about(self):
        self.set_transcript([at_effort(FABLE, "low")])
        self.assertEqual(self.decision(self.call("Skill", {"skill": "orchestra:conductor"})), "allow")

    def test_ordinary_and_disabled_sessions_are_not_asked_about(self):
        self.set_transcript([at_effort(SONNET, "low")])
        self.assertEqual(self.decision(self.call()), "allow")
        self.set_transcript([at_effort(OPUS, "low")])
        self.say("/orchestra:disable")
        self.assertEqual(self.decision(self.call()), "allow")

    def test_the_newest_request_decides(self):
        self.set_transcript([at_effort(OPUS, "high"), response(OPUS), at_effort(OPUS, "low")])
        self.assertEqual(self.decision(self.call("Skill", {"skill": "orchestra:conductor"})), "deny")

    def test_a_raised_effort_needs_no_answer(self):
        self.set_transcript([at_effort(OPUS, "low")])
        self.assertEqual(self.decision(self.call("Skill", {"skill": "orchestra:conductor"})), "deny")
        self.set_transcript([at_effort(OPUS, "low"), response(OPUS), at_effort(OPUS, "high")])
        self.assertEqual(self.decision(self.call("Skill", {"skill": "orchestra:conductor"})), "allow")
        self.assertEqual(self.decision(self.call()), "allow")

    def test_another_low_level_or_turning_it_on_again_asks_again(self):
        self.set_transcript([at_effort(OPUS, "medium")])
        self.assertEqual(self.decision(self.call("Skill", {"skill": "orchestra:conductor"})), "deny")
        self.assertEqual(self.decision(self.call("Skill", {"skill": "orchestra:conductor"})), "allow")
        self.set_transcript([at_effort(OPUS, "low")])
        self.assertEqual(self.decision(self.call()), "deny")
        self.assertEqual(self.decision(self.call()), "allow")
        self.say("/orchestra:conduct")
        self.assertEqual(self.decision(self.call()), "deny")

    def test_compaction_does_not_ask_again(self):
        self.set_transcript([at_effort(OPUS, "low")])
        self.call("Skill", {"skill": "orchestra:conductor"})
        self.call("Skill", {"skill": "orchestra:conductor"})
        self.run_hook("session.py", {"hook_event_name": "SessionStart", "source": "compact"})
        self.assertEqual(self.decision(self.call("Skill", {"skill": "orchestra:conductor"})), "allow")

    def test_tool_search_is_never_stopped(self):
        self.set_transcript([at_effort(OPUS, "low")])
        self.assertEqual(self.decision(self.call("ToolSearch", {"query": "select:Skill"})), "allow")
        self.assertEqual(self.decision(self.call("Skill", {"skill": "orchestra:conductor"})), "deny")


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

    def test_new_session_gets_no_note(self):
        os.remove(self.on)
        for transcript in (None, [request(FABLE), response(FABLE)], [request(OPUS), response(OPUS)]):
            self.set_transcript(transcript)
            self.assertIsNone(self.note({"hook_event_name": "SessionStart", "source": "startup"}))

    def test_unknown_model_with_the_mode_on_gets_a_note_for_both_cases(self):
        self.set_transcript(None)
        text = self.note({"hook_event_name": "SessionStart", "source": "resume"})
        self.assertIn("If you are running on a Fable or an Opus model", text)
        self.assertIn("any other model", text)
        self.assertIn("orchestra:conductor", text)

    def test_known_conductor_session_gets_the_conductor_note(self):
        for model in (FABLE, OPUS):
            self.set_transcript([request(model), response(model)])
            text = self.note({"hook_event_name": "SessionStart", "source": "resume"})
            self.assertIn("turned conductor mode on", text, model)
            self.assertNotIn("any other model", text, model)

    def test_known_ordinary_session_gets_no_note(self):
        self.set_transcript([request(SONNET), response(SONNET)])
        self.assertIsNone(self.note({"hook_event_name": "SessionStart", "source": "resume"}))

    def test_switch_notes(self):
        self.assertIn("now runs on a Fable or an Opus model", self.switch(FABLE, from_model=SONNET)["hookSpecificOutput"]["additionalContext"])
        self.assertIn("now runs on a Fable or an Opus model", self.switch(OPUS, from_model=SONNET)["hookSpecificOutput"]["additionalContext"])
        self.assertIn("no longer runs on a Fable or an Opus model", self.switch(SONNET, from_model=OPUS)["hookSpecificOutput"]["additionalContext"])
        self.assertIsNone(self.switch(OPUS, from_model=FABLE))
        self.assertIsNone(self.switch(FABLE, from_model=OPUS))
        self.assertIsNone(self.switch("claude-haiku-5-5", from_model=SONNET))

    def test_switch_with_the_mode_off_gets_no_note(self):
        os.remove(self.on)
        self.assertIsNone(self.switch(FABLE, from_model=SONNET))
        self.assertIsNone(self.switch(SONNET, from_model=OPUS))

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
        self.set_transcript([request(SONNET)])
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
        "git push origin main",
        "curl -X POST https://api.example.com -d '{\"a\":1}'",
        "ssh host ls",
        "gh pr create --title x",
        "kubectl apply -f deploy.yaml",
    ]
    NOT_PREAPPROVED = [
        'git commit -m "fix"',
        "git add -A && git commit -m x",
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
        "bin/rails db:migrate",
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

    def test_commands_refused_without_preapproval(self):
        for cmd in self.NOT_PREAPPROVED:
            self.assertEqual(self.bash(cmd), "deny", cmd)

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
        self.assertEqual(run('git commit -m "x"'), "deny")
        self.assertEqual(run("claude -p x"), "deny")

    def test_implementation_subagent(self):
        def run(cmd):
            return self.verdict("guard_main.py", self.sub("opus-engineer", "Bash", {"command": cmd}))
        self.assertEqual(run("sed -i 's/a/b/' src/app.py"), "allow")
        self.assertEqual(run("rm -rf build && npm install"), "deny")  # nothing is pre-approved
        self.assertEqual(run('git commit -m "x"'), "deny")
        self.assertEqual(run("claude -p x"), "deny")
        self.assertEqual(run('bash -c "claude -p x --model fable"'), "deny")
        self.assertEqual(run("claude --version"), "allow")

    def test_ordinary_session_is_left_alone(self):
        self.set_transcript([request(SONNET)])
        for cmd in ("rm -rf build", "sed -i 's/a/b/' src/app.py", 'git commit -m "x"', "echo hi > src/x.txt"):
            self.assertEqual(self.bash(cmd), "allow", cmd)


# Shortened copies of commands that froze a real overnight session
FROZEN_A = ('for f in components browse; do printf "%-14s var()=%s\\n" $f '
            '"$(grep -o \'var(--\' $f.css | wc -l)"; done')
FROZEN_B = ("ls -R .orchestra/2026-11-12-login-fix/files/T02 | head -50; docker exec -w /app app bash -lc "
            "'bin/rails db:prepare db:seed >/dev/null 2>&1; bin/rails runner \"puts Book.count\"'")
FROZEN_C = """docker exec -w /app app bash -lc '
bin/rails db:migrate:status 2>&1 | tail -3; bin/rails test 2>&1 | tail -2
PW=$(head -c 18 /dev/urandom | base64 | tr -dc "A-Za-z0-9" | head -c 16)
OPERATOR_EMAIL=op.review@example.com OPERATOR_PASSWORD=$PW bin/rails db:seed 2>&1 | tail -1
curl -s -o /tmp/out.html -X POST http://127.0.0.1:3141/session --data-urlencode "email_address=op.review@example.com"
bin/rails db:seed:replant 2>&1 | tail -1'"""
FROZEN_D = """docker exec -w /app app bash -lc '
set -e
rm -rf tmp/final; mkdir -p tmp/final/shots
DATABASE_URL=sqlite3:tmp/final/full.sqlite3 bin/rails db:prepare >/dev/null 2>&1
DATABASE_URL=sqlite3:tmp/final/full.sqlite3 bin/rails db:seed 2>&1 | tail -1'
cd tmp/final/shots && for s in home shelf; do for w in 390 1280; do printf "%s-%s: AE=%s\\n" $s $w "$(compare -metric AE $s-$w-a.png $s-$w-b.png null: 2>&1 | head -1 | cut -d' ' -f1)"; done; done"""

KEY_COMMANDS = {
    "data": "bin/rails db:migrate",
    "deps": "npm install lodash",
    "git": 'git commit -m "x"',
    "push": "git push origin main",
    "github": "gh pr create --title x",
    "http": "curl -X POST https://api.example.com -d a=1",
    "ssh": "ssh host ls",
    "deploy": "kubectl apply -f deploy.yaml",
}


class PreapprovalTest(HookTest):
    """Principle 3: what the brief in the active work folder pre-approves."""

    PROMPT = "Read the task spec at `%s/tasks/T01-login-fix.md` and carry it out." % UNIT

    # ---- helpers

    def brief(self, line="", unit=UNIT, at=2000, text=BRIEF):
        self.write(unit + "/brief.md", text + ("\n" + line + "\n" if line else ""), mtime=at)

    def spoke(self, at):
        self.run_hook("session.py", {"hook_event_name": "UserPromptSubmit", "prompt": "ok"})
        turn = os.path.join(self.state, "sessions", "session-1.turn")
        os.utime(turn, (at, at))

    def activate(self, unit=UNIT, how="read"):
        brief = unit + "/brief.md"
        if how == "read":
            self.assertEqual(self.verdict("report_gate.py", self.main("Read", {"file_path": brief})), "allow")
        elif how == "write":
            self.assertEqual(self.verdict("guard_main.py", self.main("Write", {"file_path": brief, "content": "x"})), "allow")
        else:
            self.write(unit + "/tasks/T01-login-fix.md", TASK)
            prompt = "Read the task spec at `%s/tasks/T01-login-fix.md` and carry it out." % unit
            payload = self.main("Agent", {"subagent_type": "orchestra:opus-analyst", "prompt": prompt})
            self.assertEqual(self.verdict("guard_agent.py", payload), "allow")

    def confirmed(self, line, unit=UNIT, text=BRIEF):
        """A brief with this line, confirmed by the user, in the active work folder."""
        self.brief(line, unit=unit, text=text)
        self.spoke(3000)
        self.activate(unit)

    def reason(self, payload):
        out = self.run_hook("guard_main.py", payload)
        return out["hookSpecificOutput"]["permissionDecisionReason"] if out else ""

    def impl(self, cmd):
        return self.verdict("guard_main.py", self.sub("opus-engineer", "Bash", {"command": cmd}))

    def analyst(self, cmd):
        return self.verdict("guard_main.py", self.sub("opus-analyst", "Bash", {"command": cmd}))

    # ---- the commands that froze the real session

    def test_unreadable_command_passes_with_nothing_pre_approved(self):
        self.assertEqual(self.bash(FROZEN_A), "allow")

    def test_frozen_commands_pass_with_data(self):
        self.confirmed("Pre-approved: data")
        for cmd in (FROZEN_A, FROZEN_B, FROZEN_C, FROZEN_D):
            self.assertEqual(self.bash(cmd), "allow", cmd)

    def test_frozen_commands_are_refused_without_the_line(self):
        self.confirmed("")
        for cmd in (FROZEN_B, FROZEN_C, FROZEN_D):
            payload = self.main("Bash", {"command": cmd})
            self.assertEqual(self.verdict("guard_main.py", payload), "deny", cmd)
            why = self.reason(payload)
            self.assertIn("`data`", why)
            self.assertIn("`Pre-approved:`", why)
            self.assertIn("## Decision rights", why)
            self.assertIn("does not pre-approve", why)

    # ---- when the line counts

    def test_each_recording_moment_makes_the_folder_active(self):
        for how in ("read", "write", "delegate"):
            with self.subTest(how=how):
                self.brief("Pre-approved: data", unit=OTHER_UNIT)
                self.brief("Pre-approved: data")
                self.spoke(3000)
                self.activate(OTHER_UNIT, "read")
                os.remove(os.path.join(self.proj, OTHER_UNIT, "brief.md"))
                self.assertEqual(self.bash("bin/rails db:migrate"), "deny")
                self.activate(UNIT, how)
                self.assertEqual(self.bash("bin/rails db:migrate"), "allow")

    def test_no_active_work_folder(self):
        self.brief("Pre-approved: data")
        self.spoke(3000)
        payload = self.main("Bash", {"command": "bin/rails db:migrate"})
        self.assertEqual(self.verdict("guard_main.py", payload), "deny")
        self.assertIn("no active work folder", self.reason(payload))

    def test_brief_newer_than_the_last_turn(self):
        self.spoke(1000)
        self.brief("Pre-approved: data", at=2000)
        self.activate()
        payload = self.main("Bash", {"command": "bin/rails db:migrate"})
        self.assertEqual(self.verdict("guard_main.py", payload), "deny")
        self.assertIn("has not confirmed this version of the brief", self.reason(payload))
        self.spoke(3000)
        self.assertEqual(self.verdict("guard_main.py", payload), "allow")

    def test_no_user_turn_recorded(self):
        self.brief("Pre-approved: data")
        self.activate()
        payload = self.main("Bash", {"command": "bin/rails db:migrate"})
        self.assertEqual(self.verdict("guard_main.py", payload), "deny")
        self.assertIn("no user turn is recorded", self.reason(payload))
        for cmd in ("git push origin main", "ssh host ls"):
            self.assertEqual(self.bash(cmd), "ask", cmd)

    def test_brief_of_another_work_folder_does_not_count(self):
        self.brief("Pre-approved: data")
        self.brief("Pre-approved: git", unit=OTHER_UNIT)
        self.spoke(3000)
        self.activate(OTHER_UNIT)
        self.assertEqual(self.bash("bin/rails db:migrate"), "deny")
        self.assertEqual(self.bash('git commit -m "x"'), "allow")
        self.activate(UNIT)  # the latest record wins
        self.assertEqual(self.bash("bin/rails db:migrate"), "allow")
        self.assertEqual(self.bash('git commit -m "x"'), "deny")

    def test_a_subagent_does_not_change_the_active_folder(self):
        self.brief("Pre-approved: data")
        self.brief("", unit=OTHER_UNIT)
        self.spoke(3000)
        self.activate(UNIT)
        read = self.sub("opus-analyst", "Read", {"file_path": OTHER_UNIT + "/brief.md"})
        self.assertEqual(self.verdict("report_gate.py", read), "allow")
        write = self.sub("opus-engineer", "Write", {"file_path": OTHER_UNIT + "/brief.md", "content": "x"})
        self.assertEqual(self.verdict("guard_main.py", write), "deny")
        self.assertEqual(self.bash("bin/rails db:migrate"), "allow")

    def test_sessions_do_not_share_the_active_folder(self):
        self.confirmed("Pre-approved: data")
        self.turn_on("session-2")
        payload = self.main("Bash", {"command": "bin/rails db:migrate"})
        payload["session_id"] = "session-2"
        self.assertEqual(self.verdict("guard_main.py", payload), "deny")

    # ---- the keys

    def test_each_key_covers_its_own_category_only(self):
        for key, own in KEY_COMMANDS.items():
            with self.subTest(key=key):
                self.confirmed("Pre-approved: " + key)
                self.assertEqual(self.bash(own), "allow", own)
                for other, cmd in KEY_COMMANDS.items():
                    if other != key:
                        self.assertNotEqual(self.bash(cmd), "allow", "%s passed %s" % (key, cmd))
        self.confirmed("Pre-approved: deploy")
        self.assertEqual(self.bash("docker push registry/app:1"), "allow")

    def test_a_command_in_two_categories_needs_both_keys(self):
        cmd = 'git commit -m "x" && git push origin main'
        self.confirmed("Pre-approved: git")
        self.assertEqual(self.bash(cmd), "ask")
        self.confirmed("Pre-approved: push")
        self.assertEqual(self.bash(cmd), "deny")
        self.confirmed("Pre-approved: git, push")
        self.assertEqual(self.bash(cmd), "allow")
        self.confirmed("Pre-approved: data")
        self.assertEqual(self.bash("bin/rails db:migrate && npm install"), "deny")

    def test_prompts_name_the_key(self):
        for key in ("push", "github", "http", "ssh", "deploy"):
            payload = self.main("Bash", {"command": KEY_COMMANDS[key]})
            self.assertEqual(self.verdict("guard_main.py", payload), "ask", key)
            self.assertIn("`%s`" % key, self.reason(payload), key)

    def test_refusals_say_the_way_forward(self):
        self.confirmed("")
        for key in ("data", "deps", "git"):
            why = self.reason(self.main("Bash", {"command": KEY_COMMANDS[key]}))
            self.assertIn("`%s`" % key, why)
            self.assertIn("ask them", why)
            self.assertIn("does not depend on the answer", why)
        why = self.reason(self.sub("opus-engineer", "Bash", {"command": "npm install"}))
        self.assertIn("`deps`", why)
        self.assertIn("BLOCKED", why)
        self.assertIn("another way", why)
        why = self.reason(self.sub("opus-analyst", "Bash", {"command": "npm install"}))
        self.assertIn("read-only", why)
        self.assertIn("recommendation", why)

    def test_refusals_name_the_way_that_needs_no_approval(self):
        self.confirmed("")
        mentions = ('echo "run bin/rails db:seed first"', "sed -n '/db:migrate/p' README.md")
        for cmd in mentions:
            for payload in (self.main("Bash", {"command": cmd}),
                            self.sub("opus-engineer", "Bash", {"command": cmd}),
                            self.sub("opus-analyst", "Bash", {"command": cmd})):
                self.assertEqual(self.verdict("guard_main.py", payload), "deny", cmd)
                why = self.reason(payload)
                self.assertIn("Grep, Read or Write tool", why, cmd)
                self.assertIn("needs no pre-approval", why, cmd)
        why = self.reason(self.sub("opus-engineer", "Bash", {"command": mentions[0]}))
        self.assertLess(why.index("Grep, Read or Write tool"), why.index("Do not look for another way"))
        self.assertIn("run the data command itself", why)
        self.confirmed("Pre-approved: data")
        why = self.reason(self.sub("opus-engineer", "Bash", {"command": "RAILS_ENV=production bin/rails db:migrate"}))
        self.assertIn("Grep, Read or Write tool", why)
        self.confirmed("")
        for cmd in ("git checkout -- src/a.py", "git mv a b", "git rm x", "git add -A"):
            payload = self.sub("opus-engineer", "Bash", {"command": cmd})
            self.assertEqual(self.verdict("guard_main.py", payload), "deny", cmd)
            why = self.reason(payload)
            self.assertIn("change the file directly, without git", why, cmd)
            self.assertIn("needs the git command itself", why, cmd)
            self.assertNotIn("Grep, Read or Write tool", why, cmd)
        self.assertNotIn("without git", self.reason(self.main("Bash", {"command": "git checkout -- src/a.py"})))
        for payload in (self.main("Bash", {"command": "npm install"}),
                        self.sub("opus-engineer", "Bash", {"command": "npm install"}),
                        self.sub("opus-analyst", "Bash", {"command": "npm install"})):
            why = self.reason(payload)
            self.assertIn("`deps`" if "read-only" not in why else "read-only", why)
            self.assertNotIn("Grep, Read or Write tool", why)
            self.assertNotIn("without git", why)

    # ---- production

    def test_production_data_commands_go_to_the_user(self):
        self.confirmed("Pre-approved: data")
        for cmd in (
            "RAILS_ENV=production bin/rails db:migrate",
            "bin/rails db:migrate -e production",
            "RACK_ENV='prod' bundle exec rake db:migrate",
            'NODE_ENV="production" npx prisma migrate deploy',
            "APP_ENV=prod alembic upgrade head",
            "DJANGO_ENV=production python manage.py migrate",
            "bin/rails db:seed --env production",
            "bin/rails db:seed --env=production",
            "bin/rails db:migrate --environment production",
            "bin/rails db:migrate --environment=production",
            "docker exec -e RAILS_ENV=production app bin/rails db:migrate",
        ):
            self.assertEqual(self.bash(cmd), "ask", cmd)
            self.assertEqual(self.impl(cmd), "deny", cmd)
        self.assertEqual(self.bash("RAILS_ENV=staging bin/rails db:migrate"), "allow")

    def test_a_test_target_does_not_pass_a_data_command(self):
        self.confirmed("")
        for cmd in ("RAILS_ENV=test bin/rails db:schema:load", "cmd/rake RAILS_ENV=test db:migrate",
                    "DATABASE_URL=sqlite3:tmp/x.sqlite3 bin/rails db:seed"):
            self.assertEqual(self.bash(cmd), "deny", cmd)

    # ---- searches

    SEARCHES = [
        'grep -rn "db:seed" README.md',
        'rg "db:migrate" bin/',
        'git log --grep "db:migrate"',
        'git --no-pager log --grep "db:migrate"',
        'git -C sub diff -G"db:seed"',
        'echo "$(grep -c \'db:seed\' README.md)"',
        'grep -rn "drop table" db/ | head -5',
        "sudo grep db:seed /etc/motd",
        'timeout 5 rg "manage.py migrate" .',
        "find . -name '*.md' -exec grep -l \"db:seed\" {} +",
        'grep -rn "RAILS_ENV=production bin/rails db:migrate" docs/',
    ]
    NOT_SEARCHES = [
        "bin/rails db:migrate",
        "docker exec app bash -lc 'bin/rails db:seed'",
        'echo "bin/rails db:migrate" | sh',
        'psql -c "DROP TABLE users"',
        "docker exec -i app bash <<'EOF'\nbin/rails db:migrate\nEOF",
        "grep -q x f && bin/rails db:migrate",
        'grep "db:seed" f; alembic upgrade head',
        'bash -c "grep x f; bin/rails db:migrate"',
        "rg --pre 'bin/rails db:migrate' x",
        "rg --pre='bin/rails db:migrate' x",
        "git -c core.pager='bin/rails db:migrate' log",
        "GIT_PAGER='bin/rails db:migrate' git log",
        "git grep -O'bin/rails db:migrate' x",
        "env RAILS_ENV=test bin/rails db:schema:load",
        "echo x | xargs -I{} bin/rails db:migrate",
        "find . -name '*.rb' -exec bin/rails db:migrate \\;",
        'echo "run bin/rails db:seed first"',
        "sed -n '/db:migrate/p' README.md",
    ]
    OTHER_RUNS = [
        'rg --hostname-bin="bin/rails db:migrate" x',
        'git grep -nO"bin/rails db:migrate" x',
        'git grep --open-files-in-pager="bin/rails db:migrate" x',
        'git grep --open-f="bin/rails db:migrate" x',
        'git diff --ext-diff "db:seed"',
        'git --git-dir=.git log --grep "db:seed"',
        "env --unset grep bin/rails db:migrate",
        "sudo -D grep bin/rails db:migrate",
        "./grep db:migrate",
        "find . -exec grep x {} ';' -exec bin/rails db:migrate ';'",
        'grep x f; bin/rails db:migrate"VERSION=1"',
        'grep x f # bin/rails db:migrate',
        'grep x f; <<< "bin/rails db:migrate"',
        "grep -f - f <<'EOF'\ndb:seed\nEOF",
    ]

    def test_searches_pass_with_nothing_pre_approved(self):
        self.confirmed("Pre-approved: none")
        for cmd in self.SEARCHES:
            self.assertEqual(self.bash(cmd), "allow", cmd)
            self.assertEqual(self.impl(cmd), "allow", cmd)
            self.assertEqual(self.analyst(cmd), "allow", cmd)

    def test_other_commands_naming_data_commands_are_still_refused(self):
        self.confirmed("Pre-approved: none")
        for cmd in self.NOT_SEARCHES + self.OTHER_RUNS:
            for payload in (self.main("Bash", {"command": cmd}), self.sub("opus-engineer", "Bash", {"command": cmd})):
                self.assertEqual(self.verdict("guard_main.py", payload), "deny", cmd)
                self.assertIn("`data`", self.reason(payload), cmd)

    def test_unreadable_line_keeps_the_data_rule_for_a_subagent(self):
        self.confirmed("Pre-approved: none")
        cmd = 'echo "unterminated; bin/rails db:migrate'
        self.assertEqual(self.impl(cmd), "deny")
        self.assertIn("`data`", self.reason(self.sub("opus-engineer", "Bash", {"command": cmd})))
        self.assertEqual(self.impl('echo "unterminated'), "allow")

    def test_data_key_covers_the_commands_that_are_not_searches(self):
        self.confirmed("Pre-approved: data")
        for cmd in self.NOT_SEARCHES:
            self.assertEqual(self.bash(cmd), "allow", cmd)
            self.assertEqual(self.impl(cmd), "allow", cmd)
        self.assertEqual(self.bash("RAILS_ENV=production bin/rails db:migrate"), "ask")
        self.assertEqual(self.bash("RAILS_ENV=production echo $(bin/rails db:migrate)"), "ask")

    # ---- the parser

    def test_quoted_parentheses_inside_substitutions(self):
        self.assertEqual(self.bash('echo "$(grep -o \'var(--\' f.css | wc -l)"'), "allow")
        self.assertEqual(self.bash("echo $(echo ')')"), "allow")
        self.assertEqual(self.bash('echo "$(printf \')\' ; rm -rf src)"'), "deny")
        self.assertEqual(self.bash("echo $(echo ')'; rm -rf src)"), "deny")
        self.assertEqual(self.bash('echo "$(echo "$(printf \')\'; rm -rf src)")"'), "deny")
        self.assertEqual(self.bash('echo "$(echo "$(printf \')\')")"'), "allow")
        self.assertEqual(self.bash("echo `printf ')'; rm -rf src`"), "deny")
        self.assertEqual(self.bash("echo $(echo `echo ')'`)"), "allow")
        self.assertEqual(self.bash("echo $(echo `rm -rf src`)"), "deny")
        self.assertEqual(self.bash("echo `echo hi`;rm -rf src"), "deny")
        self.assertEqual(self.bash("echo $(echo hi);rm -rf src"), "deny")
        self.assertEqual(self.bash('echo "`echo \')\'`";rm -rf src'), "deny")

    def test_unreadable_commands_are_refused(self):
        for cmd in ('echo "unterminated', "echo $(ls", "eval " * 8 + "ls"):
            payload = self.main("Bash", {"command": cmd})
            self.assertEqual(self.verdict("guard_main.py", payload), "deny", cmd)
            self.assertIn("could not read", self.reason(payload))
            self.assertIn("simpler, separate commands", self.reason(payload))
            self.assertEqual(self.analyst(cmd), "deny", cmd)
            self.assertEqual(self.impl(cmd), "allow", cmd)

    # ---- the subagents

    def test_implementation_subagent(self):
        cmds = {"git": 'git commit -m x', "push": "git push origin main", "deps": "npm install",
                "data": "bin/rails db:migrate", "ssh": "ssh host ls"}
        for cmd in cmds.values():
            self.assertEqual(self.impl(cmd), "deny", cmd)
        for cmd in ("sed -i 's/a/b/' src/app.py", "rm -rf build", "echo x > src/x.txt",
                    "curl -o x.zip https://example.com/x.zip", "curl -X POST http://localhost:3000/a -d a=1"):
            self.assertEqual(self.impl(cmd), "allow", cmd)
        for key, cmd in cmds.items():
            with self.subTest(key=key):
                self.confirmed("Pre-approved: " + key)
                self.assertEqual(self.impl(cmd), "allow", cmd)

    def test_readonly_subagent(self):
        for key, cmd in (("git", "git commit -m x"), ("data", "bin/rails db:migrate")):
            self.confirmed("")
            self.assertEqual(self.analyst(cmd), "deny", cmd)
            self.confirmed("Pre-approved: " + key)
            self.assertEqual(self.analyst(cmd), "deny", cmd)
        self.assertEqual(self.analyst("git status"), "allow")

    # ---- curl

    def test_loopback_curl(self):
        for cmd in (
            "curl -X POST http://127.0.0.1:3000/session -d a=1",
            "curl -X POST http://localhost:3000/ -d a=1",
            "curl -X POST http://[::1]:3000/ -d a=1",
            "curl -s -XPOST localhost:3000/x --data-urlencode 'a=b c'",
            "curl -sS -H 'Content-Type: application/json' --json '{}' http://127.1.2.3/api",
        ):
            self.assertEqual(self.bash(cmd), "allow", cmd)
        for cmd in (
            "curl -X POST https://api.example.com -d a=1",
            'curl -X POST "$B/session" -d a=1',
            "curl -X POST http://127.0.0.1.nip.io/x -d a=1",
            "curl -X POST http://localhost.example.com/x -d a=1",
            "curl -X POST http://localhost@example.com/x -d a=1",
            "curl -X POST http://localhost:3000/x http://example.com/y -d a=1",
            "curl -X POST --resolve localhost:80:203.0.113.5 http://localhost/x -d a=1",
            "curl -x http://proxy.example.com:8080 -X POST http://localhost/x -d a=1",
            "curl -sL -X POST http://localhost/x -d a=1",
            "curl -X POST -d a=1",
        ):
            self.assertEqual(self.bash(cmd), "ask", cmd)
        self.confirmed("Pre-approved: http")
        self.assertEqual(self.bash("curl -X POST https://api.example.com -d a=1"), "allow")
        self.assertEqual(self.bash('curl -X POST "$B/session" -d a=1'), "allow")

    # ---- the line in the brief

    def test_line_forms(self):
        approved = [
            "- Pre-approved: data",
            "* **Pre-approved:** data, deps",
            "**Pre-approved**: `data`, `deps`",
            "`Pre-approved`: `data` `deps`",
            "pre-approved： data",
            "PRE-APPROVED: data deps",
            "1. Pre-approved: deps,data",
        ]
        for line in approved:
            with self.subTest(line=line):
                self.confirmed(line)
                self.assertEqual(self.bash("bin/rails db:migrate"), "allow", line)
        for line in ("Pre-approved: none", "Pre-approved:", "- Pre-approved: `none`", "Pre-approved: database",
                     "Approved: data", "Not pre-approved: data"):
            with self.subTest(line=line):
                self.confirmed(line)
                self.assertEqual(self.bash("bin/rails db:migrate"), "deny", line)

    def test_line_outside_decision_rights_does_not_count(self):
        self.confirmed("", text=BRIEF.replace("Do not change the public API.", "Pre-approved: data"))
        self.assertEqual(self.bash("bin/rails db:migrate"), "deny")

    def test_brief_body_in_another_language(self):
        self.confirmed("- `Pre-approved`: `data`", text=BRIEF_KO)
        self.assertEqual(self.bash("bin/rails db:migrate"), "allow")
        self.assertEqual(self.bash("npm install"), "deny")

    def test_ordinary_session_is_left_alone(self):
        self.set_transcript([request(SONNET)])
        for cmd in (FROZEN_B, FROZEN_C, FROZEN_D, 'echo "unterminated', "git push origin main", "npm install"):
            self.assertEqual(self.bash(cmd), "allow", cmd)


class ConductorWritesTest(HookTest):
    """Principle 8: the project files the confirmed brief lets the conductor write itself."""

    brief = PreapprovalTest.brief
    spoke = PreapprovalTest.spoke
    activate = PreapprovalTest.activate
    confirmed = PreapprovalTest.confirmed
    reason = PreapprovalTest.reason

    LINE = "Conductor writes: src/app.py"

    def write_to(self, path, tool="Write"):
        key = "notebook_path" if tool == "NotebookEdit" else "file_path"
        return self.verdict("guard_main.py", self.main(tool, {key: path, "content": "x"}))

    def writes(self, path):
        """Whether the conductor's Write and Edit to the path are allowed, as one verdict."""
        write, edit = self.write_to(path), self.write_to(path, "Edit")
        self.assertEqual(write, edit, path)
        return write

    def test_listed_file_is_allowed(self):
        self.confirmed(self.LINE)
        self.assertEqual(self.writes("src/app.py"), "allow")
        self.assertEqual(self.writes(os.path.join(self.proj, "src", "app.py")), "allow")
        self.write("src/app.py", "x")
        self.assertEqual(self.writes("src/app.py"), "allow")
        self.assertEqual(self.writes("src/../src/app.py"), "allow")

    def test_relative_entry_is_resolved_against_the_project(self):
        self.write("src/app.py", "x")
        self.confirmed(self.LINE)
        payload = self.main("Write", {"file_path": "app.py", "content": "x"})
        payload["cwd"] = os.path.join(self.proj, "src")
        self.assertEqual(self.verdict("guard_main.py", payload), "allow")
        payload["tool_input"]["file_path"] = "src/app.py"
        self.assertEqual(self.verdict("guard_main.py", payload), "deny")

    def test_absolute_and_home_entries(self):
        self.confirmed("Conductor writes: %s" % os.path.join(self.proj, "docs", "guide.md"))
        self.assertEqual(self.writes("docs/guide.md"), "allow")
        with unittest.mock.patch.dict(os.environ, {"HOME": self.tmp}):
            self.confirmed("Conductor writes: ~/proj/docs/a.md")
            self.assertEqual(self.writes("docs/a.md"), "allow")
            self.assertEqual(self.writes("docs/guide.md"), "deny")

    def test_unlisted_file_is_denied_with_the_way(self):
        self.confirmed(self.LINE)
        for path in ("src/other.py", "README.md"):
            payload = self.main("Write", {"file_path": path, "content": "x"})
            self.assertEqual(self.verdict("guard_main.py", payload), "deny", path)
            why = self.reason(payload)
            self.assertIn("You cannot edit", why)
            self.assertIn("delegate this change", why)
            self.assertIn("`Conductor writes:`", why)
            self.assertIn("## Decision rights", why)
            self.assertIn("does not list this file", why)
            self.assertIn("Code, configuration and tests are delegated", why)

    def test_brief_newer_than_the_last_turn(self):
        self.spoke(1000)
        self.brief(self.LINE, at=2000)
        self.activate()
        payload = self.main("Write", {"file_path": "src/app.py", "content": "x"})
        self.assertEqual(self.verdict("guard_main.py", payload), "deny")
        why = self.reason(payload)
        self.assertIn("lists this file", why)
        self.assertIn("has not confirmed this version of the brief", why)
        self.assertIn("ask them", why)
        why = self.reason(self.main("Write", {"file_path": "src/other.py", "content": "x"}))
        self.assertIn("does not list this file", why)
        self.spoke(3000)
        self.assertEqual(self.verdict("guard_main.py", payload), "allow")

    def test_no_user_turn_recorded(self):
        self.brief(self.LINE)
        self.activate()
        payload = self.main("Write", {"file_path": "src/app.py", "content": "x"})
        self.assertEqual(self.verdict("guard_main.py", payload), "deny")
        why = self.reason(payload)
        self.assertIn("no user turn is recorded", why)
        self.assertIn("has not confirmed this version of the brief", why)

    def test_no_active_work_folder(self):
        self.brief(self.LINE)
        self.spoke(3000)
        payload = self.main("Write", {"file_path": "src/app.py", "content": "x"})
        self.assertEqual(self.verdict("guard_main.py", payload), "deny")
        why = self.reason(payload)
        self.assertIn("no active work folder", why)
        self.assertIn("`Conductor writes:`", why)
        self.assertEqual(self.bash("echo x > src/app.py"), "deny")

    def test_active_work_folder_without_a_brief(self):
        self.confirmed(self.LINE)
        os.remove(os.path.join(self.proj, UNIT, "brief.md"))
        payload = self.main("Write", {"file_path": "src/app.py", "content": "x"})
        self.assertEqual(self.verdict("guard_main.py", payload), "deny")
        self.assertIn("has no brief", self.reason(payload))

    def test_brief_of_another_work_folder_does_not_count(self):
        self.brief(self.LINE, unit=OTHER_UNIT)
        self.brief("")
        self.spoke(3000)
        self.activate(UNIT)
        self.assertEqual(self.writes("src/app.py"), "deny")
        self.activate(OTHER_UNIT)
        self.assertEqual(self.writes("src/app.py"), "allow")
        self.activate(UNIT)  # the latest record wins
        self.assertEqual(self.writes("src/app.py"), "deny")

    def test_unreadable_brief_lists_nothing(self):
        if os.geteuid() == 0:
            self.skipTest("root reads any file")
        self.confirmed(self.LINE)
        brief = os.path.join(self.proj, UNIT, "brief.md")
        os.chmod(brief, 0)
        self.addCleanup(os.chmod, brief, 0o644)
        self.assertEqual(self.writes("src/app.py"), "deny")

    def test_lines_that_list_nothing(self):
        for line in ("Conductor writes: none", "Conductor writes: `None`", "Conductor writes:",
                     "Conductor writes: , ,", "", "Not conductor writes: src/app.py",
                     "Conductor may write: src/app.py"):
            with self.subTest(line=line):
                self.confirmed(line)
                self.assertEqual(self.writes("src/app.py"), "deny", line)
                self.assertEqual(self.writes("none"), "deny", line)

    def test_line_outside_decision_rights_does_not_count(self):
        self.confirmed("", text=BRIEF.replace("Do not change the public API.", self.LINE))
        self.assertEqual(self.writes("src/app.py"), "deny")

    def test_entries_that_name_no_single_project_file(self):
        self.write("src/app.py", "x")
        outside = "/etc/orchestra-test-missing.txt"
        cases = [
            ("src/*.py", ["src/*.py"], []),
            ("src/app.p?", ["src/app.p?"], []),
            ("src/[a]pp.py", ["src/[a]pp.py"], []),
            ("src", [], ["rm -rf src", "cp /tmp/orchestra-a.py src", "echo x | tee src"]),
            ("src/", [], ["rm -rf src/", "cp /tmp/orchestra-a.py src/"]),
            ("src/new/", ["src/new"], ["touch src/new"]),
            (".", ["."], ["rm -rf ."]),
            (outside, [outside], []),
            (os.path.relpath(outside, os.path.realpath(self.proj)), [outside], []),
            ("~/.orchestra-test-missing/file.txt", [os.path.expanduser("~/.orchestra-test-missing/file.txt")], []),
            (UNIT + "/reports/T01.md", [UNIT + "/reports/T01.md"], []),
            (UNIT + "/reports/", [UNIT + "/reports/T01.md"], []),
        ]
        for entry, targets, cmds in cases:
            with self.subTest(entry=entry):
                self.confirmed("Conductor writes: " + entry)
                for target in targets + ["src/app.py"]:
                    self.assertEqual(self.writes(target), "deny", target)
                for cmd in cmds:
                    self.assertEqual(self.bash(cmd), "deny", cmd)
        self.confirmed("Conductor writes: src/*.py, src/app.py")
        self.assertEqual(self.writes("src/app.py"), "allow")
        self.assertEqual(self.writes("src/*.py"), "deny")

    def test_orchestra_rules_stay_as_they_are(self):
        self.confirmed("Conductor writes: %s/reports/T01.md, %s/decisions.md" % (UNIT, UNIT))
        self.assertEqual(self.writes(UNIT + "/reports/T01.md"), "deny")
        why = self.reason(self.main("Write", {"file_path": UNIT + "/reports/T01.md", "content": "x"}))
        self.assertIn("Reports are written by subagents", why)
        self.assertEqual(self.writes(UNIT + "/decisions.md"), "allow")
        self.assertEqual(self.writes(UNIT + "/tasks/T02-next.md"), "allow")
        self.assertEqual(self.bash("echo x > %s/reports/T01.md" % UNIT), "deny")

    def test_line_forms(self):
        listed = [
            ("- Conductor writes: src/app.py", ["src/app.py"]),
            ("* **Conductor writes:** src/app.py, docs/guide.md", ["src/app.py", "docs/guide.md"]),
            ("**Conductor writes**: `src/app.py`, `docs/guide.md`", ["src/app.py", "docs/guide.md"]),
            ("`Conductor writes`: `src/app.py`", ["src/app.py"]),
            ("_Conductor writes_: src/app.py", ["src/app.py"]),
            ("conductor writes： src/app.py", ["src/app.py"]),
            ("1. CONDUCTOR WRITES: docs/guide.md，src/app.py", ["src/app.py", "docs/guide.md"]),
            ("Conductor writes: docs/guide.md、 'src/app.py'", ["src/app.py", "docs/guide.md"]),
            ('Conductor writes: "src/app.py" , *docs/guide.md*', ["src/app.py", "docs/guide.md"]),
            ("Conductor writes: none, src/app.py", ["src/app.py"]),
            ("Conductor writes: src/app.py\nConductor writes: docs/guide.md", ["src/app.py", "docs/guide.md"]),
            ("Conductor writes: docs/user guide.md, src/app.py", ["docs/user guide.md", "src/app.py"]),
            ("- Conductor writes: `docs/user guide.md`", ["docs/user guide.md"]),
        ]
        for line, paths in listed:
            with self.subTest(line=line):
                self.confirmed(line)
                for path in paths:
                    self.assertEqual(self.writes(path), "allow", path)
                for path in ({"src/app.py", "docs/guide.md", "docs/user guide.md"} - set(paths)) | {"docs/user", "guide.md"}:
                    self.assertEqual(self.writes(path), "deny", path)

    def test_entries_keep_their_underscores(self):
        for line in ("Conductor writes: _config.yml, __init__.py, notes_",
                     "- **Conductor writes:** `_config.yml`, `__init__.py`, `notes_`",
                     "Conductor writes:_config.yml\nConductor writes: __init__.py\nConductor writes: notes_"):
            with self.subTest(line=line):
                self.confirmed(line)
                for path in ("_config.yml", "__init__.py", "notes_"):
                    self.assertEqual(self.write_to(path), "allow", path)
                for path in ("config.yml", "init__.py", "__init__", "notes"):
                    self.assertEqual(self.write_to(path), "deny", path)

    def test_brief_body_in_another_language(self):
        self.confirmed("- `Conductor writes`: `src/app.py`", text=BRIEF_KO)
        self.assertEqual(self.writes("src/app.py"), "allow")
        self.assertEqual(self.writes("src/other.py"), "deny")

    def test_notebook(self):
        self.confirmed("Conductor writes: analysis.ipynb")
        self.assertEqual(self.write_to("analysis.ipynb", "NotebookEdit"), "allow")
        self.assertEqual(self.write_to("other.ipynb", "NotebookEdit"), "deny")

    def test_shell(self):
        self.write("src/app.py", "x")
        self.confirmed(self.LINE)
        for cmd in ("echo x > src/app.py", "echo x >> src/app.py", "echo x | tee src/app.py",
                    "cp /tmp/orchestra-a.py src/app.py"):
            self.assertEqual(self.bash(cmd), "allow", cmd)
        for cmd in ("echo x > src/other.py", "echo x | tee src/app.py src/other.py",
                    "cp src/app.py src/other.py", "sed -i 's/a/b/' src/app.py", "perl -pi -e 's/a/b/' src/app.py",
                    "vim src/app.py", "patch src/app.py < fix.diff",
                    "python3 -c \"open('src/app.py','w').write('x')\"", "find src -name app.py -delete"):
            self.assertEqual(self.bash(cmd), "deny", cmd)

    def test_readonly_subagent(self):
        self.confirmed(self.LINE)
        for tool in ("Write", "Edit"):
            payload = self.sub("opus-analyst", tool, {"file_path": "src/app.py", "content": "x"})
            self.assertEqual(self.verdict("guard_main.py", payload), "deny", tool)
        payload = self.sub("opus-analyst", "Bash", {"command": "echo x > src/app.py"})
        self.assertEqual(self.verdict("guard_main.py", payload), "deny")

    def test_implementation_subagent(self):
        self.confirmed("Conductor writes: src/app.py, %s/brief.md, %s/reviews/T01.md" % (UNIT, UNIT))
        for role in ("sonnet-builder", "opus-engineer"):
            def run(tool, path):
                return self.verdict("guard_main.py", self.sub(role, tool, {"file_path": path}))
            self.assertEqual(run("Write", "src/app.py"), "allow", role)
            self.assertEqual(run("Write", "src/other.py"), "allow", role)
            self.assertEqual(run("Write", UNIT + "/reports/T01.md"), "allow", role)
            self.assertEqual(run("Edit", UNIT + "/tasks/T01-login-fix.md"), "deny", role)
            self.assertEqual(run("Write", UNIT + "/reviews/T01.md"), "deny", role)
            self.assertEqual(run("Write", UNIT + "/brief.md"), "deny", role)
            self.assertEqual(run("Write", UNIT + "/files/T01/run.rb"), "allow", role)
            self.assertEqual(run("Edit", UNIT + "/files/T01/run.rb"), "allow", role)
            self.assertEqual(run("Write", UNIT + "/scratch/run.rb"), "deny", role)
            self.assertEqual(run("Write", ".orchestra/files/run.rb"), "deny", role)

    def test_sessions_outside_the_harness_are_left_alone(self):
        self.confirmed(self.LINE)
        self.set_transcript([request(SONNET), response(SONNET)])
        for path in ("src/app.py", "src/other.py", UNIT + "/reports/T01.md"):
            self.assertEqual(self.writes(path), "allow", path)
        self.assertEqual(self.bash("sed -i 's/a/b/' src/app.py"), "allow")


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
        self.set_transcript([request(SONNET), response(SONNET)])
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


SCOUT_PROMPT = "FIND: where the session expiry is handled\nIN: src/"
EDITOR_PROMPT = "DO: rename expire_session to end_session\nFILES: src/login.py, src/session.py\nCHECK: pytest tests/test_login.py"
EDITOR_DONE = "STATUS: DONE\nFILES: src/login.py, src/session.py\nCHECK: exit 0"


class HaikuAgentTest(HookTest):
    """The Haiku subagents: who may start them, and with what prompt and model."""

    IMPL = ("opus-engineer", "sonnet-builder", "sonnet-writer")

    def agent(self, payload):
        return self.verdict("guard_agent.py", payload)

    def call(self, name, prompt, **extra):
        return self.agent(self.main("Agent", dict({"subagent_type": "orchestra:" + name, "prompt": prompt}, **extra)))

    def inside(self, caller, name, prompt, **extra):
        stype = name if ":" in name or name in ("general-purpose", "Explore", "fork") else "orchestra:" + name
        return self.agent(self.sub(caller, "Agent", dict({"subagent_type": stype, "prompt": prompt}, **extra)))

    def reason(self, payload):
        out = self.run_hook("guard_agent.py", payload)
        return out["hookSpecificOutput"]["permissionDecisionReason"] if out else ""

    def spoke(self, at):
        self.run_hook("session.py", {"hook_event_name": "UserPromptSubmit", "prompt": "ok"})
        turn = os.path.join(self.state, "sessions", "session-1.turn")
        os.utime(turn, (at, at))

    def activate(self, unit=UNIT):
        self.assertEqual(self.verdict("report_gate.py", self.main("Read", {"file_path": unit + "/brief.md"})), "allow")

    def work_record(self):
        path = os.path.join(self.state, "sessions", "session-1.work")
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
        self.assertEqual(self.call("haiku-scout", SCOUT_PROMPT), "allow")
        self.assertEqual(self.call("haiku-scout", "  FIND: the login handler"), "allow")
        self.assertIsNone(self.work_record())
        self.assertEqual(self.call("haiku-scout", "Find where the session expiry is handled."), "deny")
        self.assertEqual(self.call("haiku-scout", "find: the login handler"), "deny")  # keys are uppercase
        self.assertEqual(self.call("haiku-scout", "FIND:   \nwhere the session expiry is handled"), "deny")
        self.assertEqual(self.call("haiku-scout", "FIND: x\n" + "y" * 1500), "deny")
        self.assertEqual(self.call("haiku-scout", "FIND: x\n" + "y" * (1500 - 8)), "allow")

    def test_scout_refusals_name_the_problem_and_the_format(self):
        payload = self.main("Agent", {"subagent_type": "orchestra:haiku-scout", "prompt": "Find it."})
        why = self.reason(payload)
        self.assertIn("FIND:", why)
        self.assertIn("Missing", why)
        payload["tool_input"]["prompt"] = "FIND:\n"
        self.assertIn("nothing after the key", self.reason(payload))
        payload["tool_input"]["prompt"] = "FIND: x\n" + "y" * 2000
        why = self.reason(payload)
        self.assertIn("not a mechanical one", why)
        self.assertIn("delegate it as a task", why)

    def test_conductor_to_editor(self):
        self.no_skills()
        self.write(UNIT + "/brief.md", BRIEF, mtime=2000)
        self.spoke(3000)
        self.activate()
        before = self.work_record()
        self.assertEqual(self.call("haiku-editor", EDITOR_PROMPT), "allow")
        self.assertEqual(self.work_record(), before)
        for key in ("DO", "FILES", "CHECK"):
            lines = EDITOR_PROMPT.splitlines()
            dropped = "\n".join(l for l in lines if not l.startswith(key + ":"))
            self.assertEqual(self.call("haiku-editor", dropped), "deny", key)
            emptied = "\n".join(key + ":" if l.startswith(key + ":") else l for l in lines)
            self.assertEqual(self.call("haiku-editor", emptied), "deny", key)
        self.assertEqual(self.call("haiku-editor", EDITOR_PROMPT + "\n" + "y" * 1500), "deny")

    def test_editor_needs_a_confirmed_brief(self):
        payload = self.main("Agent", {"subagent_type": "orchestra:haiku-editor", "prompt": EDITOR_PROMPT})
        self.assertEqual(self.agent(payload), "deny")
        self.assertIn("no active work folder", self.reason(payload))
        self.write(UNIT + "/brief.md", BRIEF, mtime=2000)
        self.activate()
        self.assertEqual(self.agent(payload), "deny")
        self.assertIn("No user turn is recorded", self.reason(payload))
        self.spoke(1000)
        self.assertEqual(self.agent(payload), "deny")
        self.assertIn("after the user last spoke", self.reason(payload))
        self.spoke(3000)
        self.assertEqual(self.agent(payload), "allow")
        os.remove(os.path.join(self.proj, UNIT, "brief.md"))
        self.assertEqual(self.agent(payload), "deny")
        self.assertIn("has no brief", self.reason(payload))

    def test_editor_brief_of_another_folder_does_not_count(self):
        self.write(UNIT + "/brief.md", BRIEF, mtime=2000)
        self.write(OTHER_UNIT + "/tasks/T01-login-fix.md", TASK)
        self.spoke(3000)
        self.assertEqual(self.verdict("guard_main.py", self.main("Write", {"file_path": OTHER_UNIT + "/decisions.md"})), "allow")
        self.activate()
        self.assertEqual(self.call("haiku-editor", EDITOR_PROMPT), "allow")
        prompt = "Read the task spec at `%s/tasks/T01-login-fix.md` and carry it out." % OTHER_UNIT
        self.assertEqual(self.call("opus-analyst", prompt), "allow")  # makes the other folder active
        self.assertEqual(self.call("haiku-editor", EDITOR_PROMPT), "deny")

    def test_existing_rules_for_the_conductor_stay(self):
        self.no_skills()
        prompt = "Read the task spec at `%s/tasks/T01-login-fix.md` and carry it out." % UNIT
        self.write(UNIT + "/brief.md", BRIEF)
        self.write(UNIT + "/tasks/T01-login-fix.md", TASK)
        self.assertEqual(self.call("sonnet-builder", prompt), "deny")  # the skills are not loaded
        self.assertEqual(self.call("haiku-scout", prompt), "deny")  # a task spec is no Haiku request

    # ---- inside subagents

    def test_implementation_subagents_may_start_haiku(self):
        for caller in self.IMPL:
            with self.subTest(caller=caller):
                self.assertEqual(self.inside(caller, "haiku-scout", SCOUT_PROMPT), "allow")
                self.assertEqual(self.inside(caller, "haiku-editor", EDITOR_PROMPT), "allow")
                self.assertEqual(self.inside(caller, "haiku-scout", "Find the handler."), "deny")
                self.assertEqual(self.inside(caller, "haiku-editor", "DO: rename it\nFILES: src/a.py"), "deny")
                self.assertEqual(self.inside(caller, "haiku-editor", EDITOR_PROMPT + "\n" + "y" * 1500), "deny")
                for other in ("sonnet-builder", "opus-analyst", "opus-engineer", "general-purpose",
                              "Explore", "fork", "other:haiku-scout"):
                    self.assertEqual(self.inside(caller, other, SCOUT_PROMPT), "deny", other)
                self.assertEqual(self.agent(self.sub(caller, "Agent", {"prompt": SCOUT_PROMPT})), "deny")
                self.assertEqual(self.agent(self.sub(caller, "Workflow", {"name": "audit"})), "deny")

    def test_subagent_length_refusal_says_do_it_yourself(self):
        payload = self.sub("sonnet-builder", "Agent", {"subagent_type": "orchestra:haiku-scout",
                                                       "prompt": "FIND: x\n" + "y" * 2000})
        self.assertIn("do the work yourself", self.reason(payload))

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
                self.assertEqual(self.agent(self.sub(caller, "Workflow", {"name": "audit"})), "deny")

    def test_subagents_off_the_roster_are_left_alone(self):
        payload = self.sub("general-purpose", "Agent", {"subagent_type": "orchestra:haiku-editor", "prompt": "x"})
        self.assertEqual(self.agent(payload), "allow")

    # ---- the model argument

    def test_model_argument(self):
        self.write(UNIT + "/brief.md", BRIEF, mtime=2000)
        self.spoke(3000)
        self.activate()
        for name, prompt in (("haiku-scout", SCOUT_PROMPT), ("haiku-editor", EDITOR_PROMPT)):
            for model in ("sonnet", "opus", "fable", "inherit"):
                self.assertEqual(self.call(name, prompt, model=model), "deny", (name, model))
                self.assertEqual(self.inside("opus-engineer", name, prompt, model=model), "deny", (name, model))
            for extra in ({"model": "haiku"}, {}):
                self.assertEqual(self.call(name, prompt, **extra), "allow", (name, extra))
                self.assertEqual(self.inside("opus-engineer", name, prompt, **extra), "allow", (name, extra))
        prompt = "Read the task spec at `%s/tasks/T01-login-fix.md` and carry it out." % UNIT
        self.write(UNIT + "/tasks/T01-login-fix.md", TASK)
        self.assertEqual(self.call("sonnet-builder", prompt), "allow")
        self.assertEqual(self.call("sonnet-builder", prompt, model="haiku"), "deny")
        self.assertEqual(self.call("opus-analyst", prompt, model="haiku"), "deny")


class HaikuGuardTest(HookTest):
    """What the Haiku subagents may write and run."""

    def run_as(self, role, tool, tool_input):
        return self.verdict("guard_main.py", self.sub(role, tool, tool_input))

    def sh(self, role, cmd):
        return self.run_as(role, "Bash", {"command": cmd})

    def reason(self, role, cmd):
        out = self.run_hook("guard_main.py", self.sub(role, "Bash", {"command": cmd}))
        return out["hookSpecificOutput"]["permissionDecisionReason"] if out else ""

    def confirmed(self, line):
        self.write(UNIT + "/brief.md", BRIEF + "\n" + line + "\n", mtime=2000)
        self.run_hook("session.py", {"hook_event_name": "UserPromptSubmit", "prompt": "ok"})
        os.utime(os.path.join(self.state, "sessions", "session-1.turn"), (3000, 3000))
        self.assertEqual(self.verdict("report_gate.py", self.main("Read", {"file_path": UNIT + "/brief.md"})), "allow")

    def test_scout_is_read_only(self):
        self.assertEqual(self.run_as("haiku-scout", "Edit", {"file_path": "src/app.py"}), "deny")
        self.assertEqual(self.run_as("haiku-scout", "Write", {"file_path": "src/app.py"}), "deny")
        self.assertEqual(self.sh("haiku-scout", "echo x > src/x.txt"), "deny")
        self.assertEqual(self.sh("haiku-scout", "rg -n TODO src"), "allow")
        self.confirmed("Pre-approved: git")
        self.assertEqual(self.sh("opus-engineer", 'git commit -m "x"'), "allow")  # the key is in force
        self.assertEqual(self.sh("haiku-scout", 'git commit -m "x"'), "deny")

    def test_editor_writes(self):
        for tool in ("Edit", "Write"):
            self.assertEqual(self.run_as("haiku-editor", tool, {"file_path": "src/app.py"}), "allow", tool)
            readme = os.path.join(self.proj, "README.md")
            self.assertEqual(self.run_as("haiku-editor", tool, {"file_path": readme}), "allow", tool)
            for path in (UNIT + "/reports/T01.md", UNIT + "/files/T01/run.rb", UNIT + "/brief.md",
                         UNIT + "/tasks/T01-login-fix.md", ".orchestra/notes.md"):
                self.assertEqual(self.run_as("haiku-editor", tool, {"file_path": path}), "deny", (tool, path))
        self.assertEqual(self.run_as("haiku-editor", "NotebookEdit", {"notebook_path": "analysis.ipynb"}), "allow")
        self.assertEqual(self.run_as("haiku-editor", "NotebookEdit", {"notebook_path": UNIT + "/files/a.ipynb"}), "deny")

    def test_editor_shell_writes(self):
        for path in (UNIT + "/reports/T01.md", UNIT + "/files/T01/out.txt", UNIT + "/brief.md"):
            for cmd in ("echo x > " + path, "echo x >> " + path, "echo x | tee " + path,
                        "cp src/a.py " + path, "rm " + path, "sed -i s/a/b/ " + path):
                self.assertEqual(self.sh("haiku-editor", cmd), "deny", cmd)
        for cmd in ("echo x > src/x.txt", "sed -i 's/a/b/' src/app.py", "cp %s/brief.md /tmp/orchestra-b.md" % UNIT,
                    "cat %s/brief.md" % UNIT, "python3 tests/x.py", "pytest -q > /tmp/orchestra-out.txt 2>&1"):
            self.assertEqual(self.sh("haiku-editor", cmd), "allow", cmd)

    def test_editor_runs_no_category_command(self):
        for key in ("git", "deps", "data", "push", "ssh"):
            with self.subTest(key=key):
                cmd = KEY_COMMANDS[key]
                self.assertEqual(self.sh("haiku-editor", cmd), "deny", cmd)
                self.confirmed("Pre-approved: " + key)
                self.assertEqual(self.sh("opus-engineer", cmd), "allow", cmd)  # the key is in force
                self.assertEqual(self.sh("haiku-editor", cmd), "deny", cmd)
                why = self.reason("haiku-editor", cmd)
                self.assertIn("FAILED", why)
                self.assertIn(cmd, why)
        self.assertEqual(self.sh("haiku-editor", "RAILS_ENV=" + "production " + KEY_COMMANDS["data"]), "deny")
        self.assertEqual(self.sh("haiku-editor", "claude -p x"), "deny")
        self.assertEqual(self.sh("haiku-editor", "git status"), "allow")


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

    def stop(self, message, role, agent_id="agent-1"):
        payload = {"hook_event_name": "SubagentStop", "agent_id": agent_id, "agent_type": "orchestra:" + role,
                   "last_assistant_message": message}
        return self.verdict("check_report.py", payload)

    def handback(self, message, role, agent_id="agent-1"):
        payload = self.sub(role, "SubagentHandback", {"message": message})
        payload["agent_id"] = agent_id
        return self.verdict("check_report.py", payload)

    def test_scout_messages(self):
        for n, message in enumerate(self.SCOUT_GOOD):
            self.assertEqual(self.stop(message, "haiku-scout", agent_id="sg%d" % n), "allow", message)
        for n, message in enumerate(self.SCOUT_BAD):
            self.assertEqual(self.stop(message, "haiku-scout", agent_id="sb%d" % n), "block", message[:80])
        self.assertFalse(os.path.exists(os.path.join(self.proj, ".orchestra")))  # no report file anywhere

    def test_editor_messages(self):
        for n, message in enumerate(self.EDITOR_GOOD):
            self.assertEqual(self.stop(message, "haiku-editor", agent_id="eg%d" % n), "allow", message)
        for n, message in enumerate(self.EDITOR_BAD):
            self.assertEqual(self.stop(message, "haiku-editor", agent_id="eb%d" % n), "block", message[:80])
        self.assertFalse(os.path.exists(os.path.join(self.proj, ".orchestra")))

    def test_refusal_gives_the_format(self):
        payload = {"hook_event_name": "SubagentStop", "agent_id": "f1", "agent_type": "orchestra:haiku-editor",
                   "last_assistant_message": "All done."}
        reason = self.run_hook("check_report.py", payload)["reason"]
        self.assertIn("CHECK: exit", reason)
        self.assertNotIn("REPORT:", reason)
        payload.update(agent_id="f2", agent_type="orchestra:haiku-scout")
        self.assertIn("STATUS: NOT FOUND", self.run_hook("check_report.py", payload)["reason"])

    def test_retry_limit(self):
        for role in ("haiku-editor", "haiku-scout"):
            with self.subTest(role=role):
                results = [self.stop("a summary that ignores the format", role, agent_id=role) for _ in range(4)]
                self.assertEqual(results, ["block", "block", "block", "warn"])

    def test_no_metadata_is_recorded(self):
        self.assertEqual(self.handback(EDITOR_DONE, "haiku-editor"), "allow")
        self.assertEqual(self.stop("", "haiku-editor"), "allow")
        self.assertEqual(self.stop("STATUS: FOUND\nsrc/a.py:1", "haiku-scout", agent_id="m2"), "allow")
        self.assertFalse(os.path.exists(os.path.join(self.state, "meta")))

    def test_handback_path(self):
        for role, good, bad in (("haiku-scout", self.SCOUT_GOOD, self.SCOUT_BAD),
                                ("haiku-editor", self.EDITOR_GOOD, self.EDITOR_BAD)):
            for n, message in enumerate(good):
                agent = "%s-g%d" % (role, n)
                self.assertEqual(self.handback(message, role, agent), "allow", message)
                self.assertEqual(self.stop("", role, agent), "allow")  # already handed back through the tool
            for n, message in enumerate(bad):
                self.assertEqual(self.handback(message, role, "%s-b%d" % (role, n)), "deny", message[:80])

    def test_other_roster_subagents_keep_their_format(self):
        self.assertEqual(self.stop(EDITOR_DONE, "sonnet-builder", agent_id="o1"), "block")
        self.assertEqual(self.stop("STATUS: FOUND\nsrc/a.py:1", "opus-analyst", agent_id="o2"), "block")


class HooksFileTest(unittest.TestCase):
    def test_subagent_stop_matcher(self):
        import re
        with open(os.path.join(ROOT, "hooks", "hooks.json"), encoding="utf-8") as f:
            hooks = json.load(f)["hooks"]
        matchers = [entry["matcher"] for entry in hooks["SubagentStop"]]
        self.assertEqual(len(matchers), 1)
        pattern = re.compile(matchers[0])
        for name in ("opus-engineer", "opus-analyst", "sonnet-builder", "sonnet-writer", "haiku-scout", "haiku-editor"):
            self.assertTrue(pattern.search("orchestra:" + name), name)
        for name in ("orchestra:general-purpose", "haiku-scout", "other:haiku-scout", "orchestra:haiku-scout-x"):
            self.assertFalse(pattern.search(name), name)


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
