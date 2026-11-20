#!/usr/bin/env python3
"""Tests for scripts/worker_guard.py, the PreToolUse hook of a worker run.

Run: python3 tests/test_worker_guard.py

Each test feeds the hook a call in the Codex format and checks its answer.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import REPORT_PATH, UNIT, ProjectTest


class WorkerGuardTest(ProjectTest):
    def setUp(self):
        super().setUp()
        self.proj = os.path.realpath(self.proj)
        record = os.path.join(self.proj, UNIT, "files", "T01", "cli")
        os.makedirs(record)
        self.log = os.path.join(record, "guard.jsonl")
        self.activity = os.path.join(record, "activity.jsonl")

    def context(self, kind="impl", keys=()):
        path = os.path.join(self.tmp, "ctx.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"kind": kind, "project": self.proj, "work_folder": os.path.join(self.proj, UNIT), "task": "T01",
                       "keys": list(keys), "log": self.log, "activity": self.activity}, f)
        return path

    def hook(self, payload, kind="impl", keys=(), context=None):
        return self.run_json("worker_guard.py", context or self.context(kind, keys), stdin=json.dumps(payload))

    @staticmethod
    def rows(path):
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f]

    # ---- the call format

    def codex(self, command=None, patch=None, **kw):
        """The verdict on a Codex call: a shell command, or a patch."""
        tool, tool_input = ("apply_patch", {"command": patch}) if patch is not None else ("Bash", {"command": command})
        out = self.hook({"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "cwd": self.proj}, **kw)
        return "allow" if out is None else out["hookSpecificOutput"]["permissionDecision"]

    @staticmethod
    def patch(path):
        return "*** Begin Patch\n*** Add File: %s\n+x\n*** End Patch" % path

    # ---- shell commands

    def test_commands_of_a_pre_approval_kind(self):
        self.assertEqual(self.codex("npm install left-pad"), "deny")
        self.assertEqual(self.codex("npm install left-pad", keys=["deps"]), "allow")
        self.assertEqual(self.codex("curl -X POST https://api.example.com/x -d a=1"), "deny")
        self.assertEqual(self.codex("curl -X POST https://api.example.com/x -d a=1", keys=["http"]), "allow")
        self.assertEqual(self.codex("git push origin main"), "deny")
        self.assertEqual(self.codex("bin/rails db:migrate"), "deny")
        self.assertEqual(self.codex("RAILS_ENV=production bin/rails db:migrate", keys=["data"]), "deny")

    def test_ordinary_commands_and_the_network_pass(self):
        self.assert_all("allow", self.codex, ("python3 -m unittest", "curl -sS https://example.com -o /dev/null",
                                              "git status", "ls -la", "echo x > src/app.py", "pip download --help"))

    def test_no_claude_session(self):
        self.assertEqual(self.codex('claude -p "fix it"'), "deny")

    def test_shell_redirects_follow_the_claude_mode(self):
        # As for a Claude subagent, an implementation role's redirects are not judged: its file
        # edits are, and worker.py's check after the run flags a change under .orchestra/.
        self.assertEqual(self.codex("echo x > %s/brief.md" % UNIT), "allow")
        self.assertEqual(self.codex("echo x > %s/brief.md" % UNIT, kind="readonly"), "deny")
        self.assertEqual(self.codex("echo x > %s/files/T01/out.txt" % UNIT, kind="readonly"), "allow")

    # ---- file edits

    def test_codex_edits(self):
        self.assertEqual(self.codex(patch=self.patch("src/app.py")), "allow")
        self.assertEqual(self.codex(patch=self.patch(REPORT_PATH)), "allow")
        self.assertEqual(self.codex(patch=self.patch(UNIT + "/files/T01/notes.md")), "allow")
        documents = ("brief.md", "tasks/T01-login.md", "reviews/T01.md", "decisions.md")
        self.assert_all("deny", lambda doc: self.codex(patch=self.patch("%s/%s" % (UNIT, doc))), documents)
        both = "*** Begin Patch\n*** Update File: src/app.py\n@@\n-a\n+b\n*** Update File: %s/brief.md\n@@\n-a\n+b\n*** End Patch" % UNIT
        self.assertEqual(self.codex(patch=both), "deny")
        moved = "*** Begin Patch\n*** Update File: src/app.py\n*** Move to: %s/brief.md\n*** End Patch" % UNIT
        self.assertEqual(self.codex(patch=moved), "deny")

    def test_a_read_only_role(self):
        self.assertEqual(self.codex(patch=self.patch("src/app.py"), kind="readonly"), "deny")
        self.assertEqual(self.codex(patch=self.patch(REPORT_PATH), kind="readonly"), "allow")
        self.assertEqual(self.codex("echo x > src/app.py", kind="readonly"), "deny")
        self.assertEqual(self.codex("grep -rn login src", kind="readonly"), "allow")
        self.assertEqual(self.codex("npm install x", kind="readonly", keys=["deps"]), "deny")

    # ---- the log and faults

    def test_refusals_are_logged(self):
        self.codex("npm install left-pad")
        self.codex("ls")
        rows = self.rows(self.log)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["tool"], rows[0]["command"]), ("Bash", "npm install left-pad"))
        self.assertIn("pre-approve", rows[0]["reason"])

    def test_every_call_goes_to_the_activity_log(self):
        self.codex("npm install left-pad")
        self.codex("ls")
        self.codex(patch=self.patch(os.path.join(self.proj, "src", "a.py")))
        rows = self.rows(self.activity)
        self.assertEqual([(r["tool"], r["command"], r["refused"]) for r in rows],
                         [("Bash", "npm install left-pad", True), ("Bash", "ls", False), ("apply_patch", "", False)])
        self.assertEqual(rows[2]["paths"], [os.path.join(self.proj, "src", "a.py")])

    def test_a_fault_lets_the_call_through(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "npm install x"}}
        self.assertIsNone(self.hook(payload, context=os.path.join(self.tmp, "missing.json")))
        proc = self.run_script("worker_guard.py", self.context(), stdin="not json")
        self.assertEqual((proc.returncode, proc.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main(verbosity=1)
