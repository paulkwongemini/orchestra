#!/usr/bin/env python3
"""Tests for scripts/session.py and for what every hook reads from it: whether the session is a
conductor session, and in which mode.

Run: python3 tests/test_session.py

A source-file write through guard_main.py is the probe: it is denied only in a conductor session.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import FABLE, OPUS, REPORT_PATH, SONNET, HookTest, at_effort, request, response


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
        self.assertEqual(self.agent(subagent_type="fork"), "deny")
        self.assertEqual(self.agent(prompt="x"), "deny")
        self.assertEqual(self.agent(subagent_type="general-purpose"), "deny")

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
        self.set_mode("claude", "session-2")
        self.set_transcript(None)
        self.switch(FABLE)
        payload = dict(self.main("Write", {"file_path": "src/app.py"}), session_id="session-2")
        self.assertEqual(self.verdict("guard_main.py", payload), "allow")


class SwitchTest(HookTest):
    """Conductor mode is off when a session starts. The user turns it on and off for one session."""

    def test_off_when_a_session_starts(self):
        os.remove(self.on)
        for model in (FABLE, OPUS):
            with self.subTest(model=model):
                self.set_transcript([request(model)])
                self.assertEqual(self.write_src(), "allow")
                self.assertEqual(self.agent(subagent_type="fork"), "allow")
                self.assertEqual(self.use(), "allow")

    def test_each_command_turns_it_on(self):
        for command in ("/orchestra:conduct", "/orchestra:conductor fix the login bug"):
            with self.subTest(command=command):
                os.remove(self.on)
                self.assertEqual(self.write_src(), "allow")
                self.say(command)
                self.assertEqual(self.write_src(), "deny")

    def test_disable_and_each_command_that_turns_it_back_on(self):
        self.assertEqual(self.write_src(), "deny")
        for command in ("/orchestra:conduct", "/orchestra:conductor fix the login bug"):
            with self.subTest(command=command):
                self.say("/orchestra:disable")
                self.assertEqual(self.write_src(), "allow")
                self.assertEqual(self.agent(subagent_type="fork"), "allow")
                self.say(command)
                self.assertEqual(self.write_src(), "deny")

    def test_expansion_event_works_too(self):
        for name in ("orchestra:disable", "disable"):
            with self.subTest(name=name):
                self.expand(name, "/orchestra:disable")
                self.assertEqual(self.write_src(), "allow")
                self.expand("orchestra:conduct")
                self.assertEqual(self.write_src(), "deny")
        self.expand("other:disable")
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
        self.assertIsNone(self.session_start("compact"))
        self.assertIsNone(self.switch(FABLE, from_model=SONNET))
        self.assertEqual(self.write_src(), "allow")


class CliModeTest(HookTest):
    """In the CLI mode (`codex`) the hooks step aside, except the Opus effort question."""

    def guards(self):
        """The verdicts of each guard on calls it refuses in the `claude` mode."""
        self.write(REPORT_PATH, "STATUS: DONE\nno verification record\n")
        spawn = self.sub("sonnet-builder", "Agent", {"subagent_type": "orchestra:opus-engineer", "prompt": "x"})
        return {
            "write": self.write_src(),
            "fork": self.agent(subagent_type="fork"),
            "untyped": self.agent(prompt="x"),
            "migrate": self.bash("bin/rails db:migrate"),
            "load": self.use(),
            "report": self.verdict("report_gate.py", self.main("Read", {"file_path": REPORT_PATH})),
            "subagent": self.verdict("guard_agent.py", spawn),
            "handback": self.stop("I did it.", agent_id="agent-1"),
        }

    def test_the_claude_mode_guards(self):
        verdicts = self.guards()
        self.assertEqual(verdicts.pop("handback"), "block")
        for name, verdict in verdicts.items():
            self.assertEqual(verdict, "deny", name)

    def test_every_guard_steps_aside_in_a_cli_mode(self):
        self.say("/orchestra:conduct-codex")
        for name, verdict in self.guards().items():
            self.assertEqual(verdict, "allow", name)

    def test_commands_switch_between_modes(self):
        self.say("/orchestra:conduct-codex fix the login bug")
        self.assertEqual(self.write_src(), "allow")
        self.say("/orchestra:conduct")
        self.assertEqual(self.write_src(), "deny")
        self.say("/orchestra:conduct-codex")
        self.assertEqual(self.write_src(), "allow")
        self.say("/orchestra:conductor")
        self.assertEqual(self.write_src(), "deny")
        self.say("/orchestra:conduct-codex")
        self.say("/orchestra:disable")
        self.assertEqual(self.write_src(), "allow")
        self.assertEqual(self.agent(subagent_type="fork"), "allow")

    def test_only_the_exact_command_counts(self):
        for prompt in ("/orchestra:conduct-codexx", "/orchestra:conduct-cod", "please run /orchestra:conduct-codex"):
            with self.subTest(prompt=prompt):
                self.say(prompt)
                self.assertEqual(self.write_src(), "deny")
        self.expand("orchestra:conduct-codex")
        self.assertEqual(self.write_src(), "allow")
        self.expand("other:conduct")
        self.assertEqual(self.write_src(), "allow")

    def test_a_cli_mode_gets_no_notes(self):
        self.say("/orchestra:conduct-codex")
        self.set_transcript([request(FABLE), response(FABLE)])
        for source in ("startup", "resume", "compact", "clear"):
            with self.subTest(source=source):
                self.assertIsNone(self.session_start(source))
        self.assertIsNone(self.switch(FABLE, from_model=SONNET))
        self.assertIsNone(self.switch(SONNET, from_model=OPUS))

    def test_the_effort_question_still_asks_once(self):
        self.say("/orchestra:conduct-codex")
        self.set_transcript([at_effort(OPUS, "low")])
        self.assertEqual(self.use(), "deny")
        self.assertEqual(self.use(), "allow")
        self.set_transcript([at_effort(OPUS, "high")])
        self.say("/orchestra:conduct-codex")
        self.assertEqual(self.use(), "allow")
        self.set_transcript([at_effort(SONNET, "low")])
        self.assertEqual(self.use(), "allow")

    def test_the_mode_is_per_session(self):
        self.set_mode("codex", "session-2")
        self.assertEqual(self.write_src(), "deny")


class SessionNoteTest(HookTest):
    """What the model is told about its role."""

    def test_new_session_gets_no_note(self):
        os.remove(self.on)
        for transcript in (None, [request(FABLE), response(FABLE)], [request(OPUS), response(OPUS)]):
            with self.subTest(transcript=transcript):
                self.set_transcript(transcript)
                self.assertIsNone(self.session_start("startup"))

    def test_unknown_model_with_the_mode_on_gets_a_note_for_both_cases(self):
        self.set_transcript(None)
        text = self.note(self.session_start("resume"))
        self.assertIn("If you are running on a Fable or an Opus model", text)
        self.assertIn("any other model", text)
        self.assertIn("orchestra:conductor", text)

    def test_known_conductor_session_gets_the_conductor_note(self):
        for model in (FABLE, OPUS):
            with self.subTest(model=model):
                self.set_transcript([request(model), response(model)])
                text = self.note(self.session_start("resume"))
                self.assertIn("turned conductor mode on", text)
                self.assertNotIn("any other model", text)

    def test_known_ordinary_session_gets_no_note(self):
        self.set_transcript([request(SONNET), response(SONNET)])
        self.assertIsNone(self.session_start("resume"))

    def test_switch_notes(self):
        self.assertIn("now runs on a Fable or an Opus model", self.note(self.switch(FABLE, from_model=SONNET)))
        self.assertIn("now runs on a Fable or an Opus model", self.note(self.switch(OPUS, from_model=SONNET)))
        self.assertIn("no longer runs on a Fable or an Opus model", self.note(self.switch(SONNET, from_model=OPUS)))
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
        self.set_transcript(None)
        self.assertIsNone(self.session_start("startup", agent_id="a1", agent_type="orchestra:opus-engineer"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
