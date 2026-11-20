#!/usr/bin/env python3
"""Tests for scripts/board.py, the board of every worker run in progress.

Run: python3 tests/test_board.py

Each test writes the records that `worker.py run` and Codex leave: a run's live.json, its event
stream and its guard's log, and the subagents' session files under a fake CODEX_HOME. It then
prints one frame with `--once`. No model is called.
"""
import json
import os
import subprocess
import sys
import time
import unicodedata
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import UNIT, ProjectTest


def cells(text):
    return sum(0 if unicodedata.combining(ch) else 2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


class BoardTest(ProjectTest):
    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tmp, "codex-home")
        self.started = time.time() - 60

    def run_record(self, task, role, thread, lines=(), ended=False, pid=None):
        """A run of `task` in progress: live.json, an event stream that starts thread `thread`."""
        record = os.path.join(UNIT, "files", task, "cli")
        self.write(os.path.join(record, "live.json"), json.dumps(
            {"round": 1, "started": self.started, "cli": "codex", "pid": pid or os.getpid(),
             "role": role, "kind": "impl", "model": "gpt-6.2-sol", "effort": "medium"}))
        events = [{"type": "thread.started", "thread_id": thread}]
        events += [{"type": "item.completed", "item": {"type": "agent_message", "text": text}} for text in lines]
        self.write(os.path.join(record, "events-1.jsonl"), "".join(json.dumps(e) + "\n" for e in events))
        if ended:
            self.write(os.path.join(record, "run.json"), json.dumps({"rounds": 1}))
        return os.path.join(self.proj, record)

    def subagent(self, thread, parent, name, lines=(), done=False):
        folder = os.path.join(self.home, "sessions", time.strftime("%Y/%m/%d"))
        os.makedirs(folder, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(self.started + 5))
        rows = [{"timestamp": stamp, "type": "session_meta",
                 "payload": {"id": thread, "parent_thread_id": parent, "agent_nickname": name, "timestamp": stamp,
                             "subagent_history_start_ordinal": 1}}]
        rows += [{"timestamp": stamp, "type": "event_msg", "payload": {"type": "item_completed", "item": {
            "type": "AgentMessage", "content": [{"type": "Text", "text": text}]}}} for text in lines]
        if done:
            rows.append({"timestamp": stamp, "type": "event_msg", "payload": {"type": "task_complete"}})
        with open(os.path.join(folder, "rollout-2026-11-20T09-00-00-%s.jsonl" % thread), "w", encoding="utf-8") as f:
            f.write("".join(json.dumps(dict(r, ordinal=n)) + "\n" for n, r in enumerate(rows)))

    def frame(self, size="120x40", window=1):
        proc = self.run_script("worker.py", "board", "--project", self.proj, "--once", size, "--window", str(window),
                               env={"CODEX_HOME": self.home, "CLAUDE_CONFIG_DIR": os.path.join(self.tmp, "claude-config")})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc.stdout

    def test_no_run(self):
        os.makedirs(os.path.join(self.proj, UNIT))
        out = self.frame()
        self.assertIn("0 running", out)
        self.assertIn("Nothing in progress", out)

    def test_each_agent_has_a_pane_and_more_than_four_go_to_a_second_window(self):
        self.run_record("T01", "implementer", "th-1", ["토큰 만료 처리부터 봅니다"])
        self.run_record("T02", "engineer", "th-2", ["Two stores to reconcile."])
        self.run_record("T03", "writer", "th-3", ["Writing the docs."])
        self.subagent("sub-a", "th-2", "Noether", ["Checking redis."])
        self.subagent("sub-b", "th-2", "Dalton", ["Checking memcached."])
        out = self.frame()
        self.assertIn("5 running", out)
        self.assertIn("[1]  2", out)
        for title in ("T01 · implementer", "T02 · engineer", "T02 › Noether", "T02 › Dalton"):
            self.assertIn(title, out)
        self.assertNotIn("T03 · writer", out)
        self.assertIn("토큰 만료 처리부터 봅니다", out)
        self.assertIn("subagents 2", out)
        self.assertIn("goal: Checking redis.", out)  # a Codex subagent's first words stand for its task
        rows = out.rstrip("\n").split("\n")
        self.assertEqual(len(rows), 40)
        self.assertTrue(all(cells(r) <= 120 for r in rows))
        self.assertEqual(cells(rows[1]), 120)  # two panes side by side fill the width, Korean included
        out = self.frame(window=2)
        self.assertIn(" 1  [2]", out)
        self.assertIn("T03 · writer", out)
        self.assertNotIn("T01 · implementer", out)

    def test_never_more_than_two_across_and_one_column_when_narrow(self):
        for n in range(1, 4):
            self.run_record("T0%d" % n, "implementer", "th-%d" % n)
        out = self.frame("200x40")
        self.assertEqual(out.split("\n")[1].count("┌"), 2)  # a third run goes below, not beside
        out = self.frame("80x40")
        self.assertEqual(out.split("\n")[1].count("┌"), 1)
        self.assertIn("[1]  2", out)  # one column of two rows: two panes a window
        out = self.frame("120x20")
        self.assertIn("[1]  2", out)  # one row of two columns when the terminal is low

    def test_a_finished_agent_has_no_pane(self):
        self.run_record("T01", "implementer", "th-1")
        self.run_record("T02", "engineer", "th-2", ended=True)
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        self.run_record("T03", "writer", "th-3", pid=dead.pid)  # a worker.py that died
        self.subagent("sub-a", "th-1", "Noether", done=True)
        self.subagent("sub-b", "th-1", "Dalton", ["still going"])
        out = self.frame()
        self.assertIn("2 running", out)
        self.assertIn("T01 › Dalton", out)
        for gone in ("T02", "T03", "T01 › Noether"):
            self.assertNotIn(gone, out)
        self.assertIn("· Noether finished", out)  # the run's own pane says so

    def test_a_refusal_goes_to_the_pane_of_the_agent_that_made_the_call(self):
        record = self.run_record("T01", "implementer", "th-1")
        self.subagent("sub-a", "th-1", "Noether")
        with open(os.path.join(record, "guard.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.time(), "tool": "Bash", "command": "npm install x", "paths": [],
                                "thread": "sub-a", "reason": "Installing needs the deps key. More."}) + "\n")
        out = self.frame("200x40")
        top = out.split("\n")
        sub_column = top[1].index("T01 › Noether")
        refusal = [r for r in top if "! refused: npm install x — Installing needs the deps key." in r]
        self.assertEqual(len(refusal), 1)
        self.assertGreater(refusal[0].index("refused"), sub_column - 5)  # in the right-hand pane
        self.assertIn("guard 1 ─", out)

    def claude_subagent(self, agent, rows, finished=False, model="claude-opus-5-5", session="sess-1"):
        """A Claude subagent of a session in this project: its transcript, meta and prefix files."""
        slug = __import__("re").sub(r"[^A-Za-z0-9]", "-", os.path.realpath(self.proj))
        folder = os.path.join(self.tmp, "claude-config", "projects", slug, session, "subagents")
        os.makedirs(folder, exist_ok=True)
        base = os.path.join(folder, "agent-" + agent)
        with open(base + ".jsonl", "w", encoding="utf-8") as f:
            f.write("".join(json.dumps(dict(r, timestamp="2026-11-20T18:00:00.000Z")) + "\n" for r in rows))
        with open(base + ".meta.json", "w", encoding="utf-8") as f:
            json.dump(dict({"agentType": "orchestra:sonnet-builder", "description": "Build the thing"},
                           **({"finished": True} if finished else {})), f)
        with open(base + ".prefix.json", "w", encoding="utf-8") as f:
            json.dump({"v": 1, "state": {"model": model}}, f)

    def test_claude_subagents_have_panes_too(self):
        os.makedirs(os.path.join(self.proj, UNIT))
        prompt = {"type": "user", "message": {"role": "user", "content": "%s/tasks/T05-login.md" % UNIT}}
        said = {"type": "assistant", "message": {"id": "m1", "role": "assistant", "model": "claude-sonnet-5-5", "usage": {"input_tokens": 1500, "output_tokens": 500},
                "content": [{"type": "text", "text": "로그인 테스트부터 봅니다"},
                            {"type": "tool_use", "id": "u1", "name": "Bash", "input": {"command": "pytest -q", "description": "Run the login tests"}},
                            {"type": "tool_use", "id": "u2", "name": "Edit", "input": {"file_path": os.path.join(os.path.realpath(self.proj), "src/a.py")}},
                            {"type": "tool_use", "id": "u3", "name": "Bash", "input": {"command": "git push"}},
                            {"type": "tool_use", "id": "u4", "name": "Read", "input": {"file_path": "/very/long/outside/path/to/notes.md"}}]}}
        results = {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "u1", "is_error": True, "content": "2 failed. See the log."},
            {"type": "tool_result", "tool_use_id": "u2", "content": "ok"},
            {"type": "tool_result", "tool_use_id": "u3", "is_error": True, "content": "PreToolUse:Bash hook error: [orchestra] Pushing needs the push key. More."}]}}
        self.write(UNIT + "/tasks/T05-login.md", "## Goal\nSend the user back to the login screen. Then more.\n")
        self.claude_subagent("a1", [prompt, said, results])
        self.claude_subagent("a2", [prompt], finished=True)
        self.claude_subagent("a3", [{"type": "user", "message": {"role": "user", "content": "FIND: where is x"}}],
                             model="claude-haiku-5-5", session="sess-2")
        out = self.frame("200x40")
        self.assertIn("2 running", out)
        self.assertIn("T05 · sonnet-builder", out)
        self.assertIn("sonnet 5.5", out)  # the model the transcript names wins over the prefix file's
        self.assertIn("2k tok", out)
        self.assertIn("goal: Send the user back to the login screen.", out)
        self.assertIn("10:00 로그인 테스트부터 봅니다", out)
        self.assertIn("✗ Run the login tests · pytest -q — 2 failed.", out)
        self.assertIn("✓ edit src/a.py", out)
        self.assertIn("! run git push — refused: Pushing needs the push key.", out)
        self.assertIn("… read …/to/notes.md", out)  # still running, and the path cut to its last parts
        self.assertIn("guard 1", out)
        self.assertIn("sonnet-builder · Build the thing", out)  # no task spec in its prompt
        self.assertIn("haiku 5.5", out)


if __name__ == "__main__":
    unittest.main(verbosity=1)
