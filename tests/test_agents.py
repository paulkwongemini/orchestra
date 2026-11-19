#!/usr/bin/env python3
"""Tests for scripts/build_agents.py.

Run: python3 tests/test_agents.py

The four task agents in `agents/` are written from `agents-src/`. This fails when someone
changed one side and not the other.
"""
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "scripts", "build_agents.py")
TASK_AGENTS = ("opus-engineer", "opus-analyst", "sonnet-builder", "sonnet-writer")


class AgentSourcesTest(unittest.TestCase):
    def test_agents_match_their_sources(self):
        proc = subprocess.run([sys.executable, BUILD, "--check"], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_every_task_agent_has_a_source_and_no_include_is_left(self):
        for name in TASK_AGENTS:
            with self.subTest(agent=name):
                self.assertTrue(os.path.isfile(os.path.join(ROOT, "agents-src", name + ".md")))
                with open(os.path.join(ROOT, "agents", name + ".md"), encoding="utf-8") as f:
                    self.assertNotIn("{{include", f.read())


if __name__ == "__main__":
    unittest.main()
