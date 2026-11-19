#!/usr/bin/env python3
"""Tests for scripts/worker_guard.py, the PreToolUse hook of a worker run.

Run: python3 tests/test_worker_guard.py

Each test feeds the hook a call in the Codex or the agy format and checks its answer.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD = os.path.join(ROOT, "scripts", "worker_guard.py")
UNIT = ".orchestra/2026-11-19-login-fix"


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="orchestra-guard-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.proj = os.path.realpath(os.path.join(self.tmp, "proj"))
        os.makedirs(os.path.join(self.proj, UNIT, "files", "T01", "cli"))
        self.log = os.path.join(self.proj, UNIT, "files", "T01", "cli", "guard.jsonl")

    def context(self, kind="impl", keys=()):
        path = os.path.join(self.tmp, "ctx.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"kind": kind, "project": self.proj, "unit": os.path.join(self.proj, UNIT), "task": "T01",
                       "keys": list(keys), "log": self.log}, f)
        return path

    def hook(self, payload, kind="impl", keys=(), context=None):
        proc = subprocess.run([sys.executable, GUARD, context or self.context(kind, keys)],
                              input=json.dumps(payload).encode(), capture_output=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        out = proc.stdout.decode().strip()
        return json.loads(out) if out else None

    # ---- the two formats

    def codex(self, command=None, patch=None, **kw):
        if patch is not None:
            payload = {"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "tool_input": {"command": patch}, "cwd": self.proj}
        else:
            payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}, "cwd": self.proj}
        out = self.hook(payload, **kw)
        return "allow" if out is None else out["hookSpecificOutput"]["permissionDecision"]

    def agy(self, name, args, prompt="", **kw):
        payload = {"hookEventName": "PreToolUse", "toolCall": {"name": name, "args": args}, "cwd": self.proj,
                   "lastUserInput": prompt}
        return self.hook(payload, **kw)["decision"]

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
        for cmd in ("python3 -m unittest", "curl -sS https://example.com -o /dev/null", "git status", "ls -la",
                    "echo x > src/app.py", "pip download --help"):
            self.assertEqual(self.codex(cmd), "allow", cmd)

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
        self.assertEqual(self.codex(patch=self.patch(UNIT + "/reports/T01.md")), "allow")
        self.assertEqual(self.codex(patch=self.patch(UNIT + "/files/T01/notes.md")), "allow")
        for doc in ("brief.md", "tasks/T01-login.md", "reviews/T01.md", "decisions.md"):
            self.assertEqual(self.codex(patch=self.patch("%s/%s" % (UNIT, doc))), "deny", doc)
        both = "*** Begin Patch\n*** Update File: src/app.py\n@@\n-a\n+b\n*** Update File: %s/brief.md\n@@\n-a\n+b\n*** End Patch" % UNIT
        self.assertEqual(self.codex(patch=both), "deny")
        moved = "*** Begin Patch\n*** Update File: src/app.py\n*** Move to: %s/brief.md\n*** End Patch" % UNIT
        self.assertEqual(self.codex(patch=moved), "deny")

    def test_a_read_only_role(self):
        self.assertEqual(self.codex(patch=self.patch("src/app.py"), kind="readonly"), "deny")
        self.assertEqual(self.codex(patch=self.patch(UNIT + "/reports/T01.md"), kind="readonly"), "allow")
        self.assertEqual(self.codex("echo x > src/app.py", kind="readonly"), "deny")
        self.assertEqual(self.codex("grep -rn login src", kind="readonly"), "allow")
        self.assertEqual(self.codex("npm install x", kind="readonly", keys=["deps"]), "deny")

    # ---- agy

    def test_agy_commands_and_edits(self):
        self.assertEqual(self.agy("run_command", {"CommandLine": "npm install left-pad"}), "deny")
        self.assertEqual(self.agy("run_command", {"CommandLine": "echo hi"}, prompt="please npm install left-pad"), "allow")
        self.assertEqual(self.agy("write_file", {"Path": os.path.join(self.proj, UNIT, "brief.md"), "Content": "x"}), "deny")
        self.assertEqual(self.agy("write_file", {"Path": os.path.join(self.proj, "src/app.py"), "Content": "x"}), "allow")
        self.assertEqual(self.agy("replace_file_content", {"TargetFile": os.path.join(self.proj, UNIT, "decisions.md")}), "deny")

    def test_agy_reading_is_not_writing(self):
        brief = os.path.join(self.proj, UNIT, "brief.md")
        self.assertEqual(self.agy("view_file", {"AbsolutePath": brief}, kind="readonly"), "allow")
        self.assertEqual(self.agy("write_file", {"Path": os.path.join(self.proj, "src/app.py")}, kind="readonly"), "deny")

    def test_agy_does_not_leave_its_sandbox(self):
        self.assertEqual(self.agy("run_command", {"CommandLine": "curl https://example.com", "BypassSandbox": True}), "deny")

    # ---- the log and faults

    def test_refusals_are_logged(self):
        self.codex("npm install left-pad")
        self.codex("ls")
        with open(self.log, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["tool"], rows[0]["command"]), ("Bash", "npm install left-pad"))
        self.assertIn("pre-approve", rows[0]["reason"])

    def test_a_fault_lets_the_call_through(self):
        payload = {"tool_name": "Bash", "tool_input": {"command": "npm install x"}}
        self.assertIsNone(self.hook(payload, context=os.path.join(self.tmp, "missing.json")))
        proc = subprocess.run([sys.executable, GUARD, self.context()], input=b"not json", capture_output=True, timeout=30)
        self.assertEqual((proc.returncode, proc.stdout), (0, b""))


if __name__ == "__main__":
    unittest.main(verbosity=1)
