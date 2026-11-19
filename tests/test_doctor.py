#!/usr/bin/env python3
"""Tests for scripts/doctor.py, the setup check.

Run: python3 tests/test_doctor.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import BRIEF, SPEC_PATH, TASK, UNIT, ProjectTest


class DoctorTest(ProjectTest):
    def test_doctor_runs(self):
        self.write(UNIT + "/brief.md", BRIEF)
        self.write(SPEC_PATH, TASK)
        proc = self.run_script("doctor.py", env={"CLAUDE_PROJECT_DIR": self.proj})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("orchestra check", proc.stdout)
        self.assertIn("Hook scripts work", proc.stdout)
        self.assertIn("%s: brief, 1 task specs, 0 reports, 0 review records" % os.path.basename(UNIT), proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=1)
