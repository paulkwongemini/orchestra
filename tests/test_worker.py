#!/usr/bin/env python3
"""Tests for scripts/worker.py.

Run: python3 tests/test_worker.py

A fake `codex` on the PATH records how it was called and acts out a plan: which files it writes,
which report and last message it leaves, what it prints, and its exit code. No model is called.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKER = os.path.join(ROOT, "scripts", "worker.py")
UNIT = ".orchestra/2026-11-19-login-fix"

FAKE_CODEX = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys
    argv = sys.argv[1:]
    stdin = sys.stdin.read()
    with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": argv, "cwd": os.getcwd(), "stdin": stdin}) + "\\n")
    plan = json.load(open(os.environ["FAKE_PLAN"], encoding="utf-8"))
    project = os.environ["FAKE_PROJECT"]
    for rel, text in plan.get("write", {}).items():
        path = os.path.join(project, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w", encoding="utf-8").write(text)
    if "last" in plan:
        open(argv[argv.index("-o") + 1], "w", encoding="utf-8").write(plan["last"])
    record = os.path.dirname(argv[argv.index("-o") + 1])
    for refusal in plan.get("refused", []):
        with open(os.path.join(record, "guard.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(refusal, at=__import__("time").time())) + "\\n")
    print(json.dumps({"type": "thread.started", "thread_id": plan.get("session", "sess-1")}))
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 16541, "cached_input_tokens": 13440, "output_tokens": 812}}))
    sys.stderr.write(plan.get("stderr", ""))
    sys.exit(plan.get("exit", 0))
''')

FAKE_AGY = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys
    argv = sys.argv[1:]
    stdin = sys.stdin.read()
    with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": argv, "cwd": os.getcwd(), "stdin": stdin, "cli": "agy"}) + "\\n")
    plan = json.load(open(os.environ["FAKE_PLAN"], encoding="utf-8"))
    project = os.environ["FAKE_PROJECT"]
    for rel, text in plan.get("write", {}).items():
        path = os.path.join(project, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w", encoding="utf-8").write(text)
    print(json.dumps({"conversation_id": plan.get("session", "conv-1"), "status": plan.get("status", "SUCCESS"),
                      "response": plan.get("last", ""), "num_turns": 1, "duration_seconds": 1.0,
                      "usage": {"input_tokens": 15684, "output_tokens": 639, "thinking_tokens": 215,
                                "cache_read_tokens": 18349, "total_tokens": 16323}}))
    sys.exit(plan.get("exit", 0))
''')

TASK = """## Goal
Send the user back to the login screen when the session expires.

## Scope and limits
Edit only `src/login.py` and the tests in `tests/login/`.

## Context
Session handling lives in src/session.py.

## Acceptance criteria
`pytest tests/login` passes.

## If blocked
Report BLOCKED if src/session.py has to change.

## Report
Report path: `.orchestra/2026-11-19-login-fix/reports/T01.md`
"""

CHECK = {"criterion": "The login tests pass.", "kind": "command", "method": "pytest tests/login",
         "result": "PASS", "exit_code": 0, "evidence": "12 passed"}
REPORT_DONE = "STATUS: DONE\n\n## What I verified\n```json\n" + json.dumps({"checks": [CHECK], "unverified": []}) + "\n```\n"
LAST_DONE = "STATUS: DONE\nTASK: T01\nREPORT: %s/reports/T01.md\nFILES: src/login.py" % UNIT


class WorkerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="orchestra-worker-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.proj = os.path.join(self.tmp, "proj")
        self.bin = os.path.join(self.tmp, "bin")
        self.codex_home = os.path.join(self.tmp, "codex-home")
        for d in (self.proj, self.bin, self.codex_home):
            os.makedirs(d)
        for name, text in (("codex", FAKE_CODEX), ("agy", FAKE_AGY)):
            fake = os.path.join(self.bin, name)
            with open(fake, "w", encoding="utf-8") as f:
                f.write(text)
            os.chmod(fake, 0o755)
        with open(os.path.join(self.codex_home, "config.toml"), "w", encoding="utf-8") as f:
            f.write('model = "gpt-6.2-astra"\n[mcp_servers.node_repl]\ncommand = "x"\n[mcp_servers."my.server"]\ncommand = "y"\n')
        self.log = os.path.join(self.tmp, "calls.jsonl")
        self.plan_file = os.path.join(self.tmp, "plan.json")
        self.write(UNIT + "/brief.md", "## Goal\nx\n")
        self.spec = self.write(UNIT + "/tasks/T01-login-fix.md", TASK)
        self.write("src/login.py", "old\n")
        self.plan({"write": {"src/login.py": "new\n", UNIT + "/reports/T01.md": REPORT_DONE}, "last": LAST_DONE})

    def write(self, rel, text):
        path = os.path.join(self.proj, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def plan(self, plan):
        with open(self.plan_file, "w", encoding="utf-8") as f:
            json.dump(plan, f)

    def worker(self, *args, path=None):
        env = dict(os.environ, PATH=path if path is not None else self.bin + os.pathsep + os.environ.get("PATH", ""),
                   CODEX_HOME=self.codex_home, FAKE_LOG=self.log, FAKE_PLAN=self.plan_file, FAKE_PROJECT=self.proj)
        proc = subprocess.run([sys.executable, WORKER] + list(args), capture_output=True, env=env, timeout=60)
        return proc.returncode, proc.stdout.decode("utf-8")

    def run_task(self, *extra, role="implementer"):
        return self.worker("run", "--cli", "codex", "--task", self.spec, "--role", role, *extra)

    def calls(self):
        with open(self.log, encoding="utf-8") as f:
            return [json.loads(line) for line in f]

    @staticmethod
    def option(argv, name):
        """The values given to an option, in order."""
        return [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == name]

    # ---- the command

    def test_an_implementation_run(self):
        code, out = self.run_task()
        self.assertEqual(code, 0, out)
        call = self.calls()[0]
        argv = call["argv"]
        self.assertEqual(argv[:1], ["exec"])
        self.assertEqual(self.option(argv, "-m"), ["gpt-6.2-sol"])
        configs = self.option(argv, "-c")
        self.assertIn('model_reasoning_effort="medium"', configs)
        self.assertIn("mcp_servers.node_repl.enabled=false", configs)
        self.assertIn('mcp_servers."my.server".enabled=false', configs)
        self.assertIn('sandbox_mode="workspace-write"', configs)
        self.assertIn("sandbox_workspace_write.writable_roots=[]", configs)
        self.assertIn("sandbox_workspace_write.network_access=true", configs)
        self.assertEqual(self.option(argv, "--disable"), ["plugins"])
        hooks = [v for v in configs if v.startswith("hooks.PreToolUse=")]
        self.assertEqual(len(hooks), 1)
        self.assertIn('matcher=".*"', hooks[0])
        self.assertIn("worker_guard.py", hooks[0])
        self.assertIn("--dangerously-bypass-hook-trust", argv)
        context = os.path.join(self.proj, UNIT, "files", "T01", "cli", "guard.json")
        self.assertIn(context, hooks[0])
        with open(context, encoding="utf-8") as f:
            ctx = json.load(f)
        self.assertEqual((ctx["kind"], ctx["task"], ctx["keys"]), ("impl", "T01", []))
        self.assertTrue(ctx["log"].endswith("files/T01/cli/guard.jsonl"))
        self.assertIn("--json", argv)
        self.assertEqual(self.option(argv, "-C"), [os.path.realpath(self.proj)])
        self.assertEqual(argv[-1], "-")
        self.assertTrue(self.option(argv, "-o")[0].endswith(UNIT + "/files/T01/cli/last-message-1.md"))
        self.assertEqual(call["cwd"], os.path.realpath(self.proj))
        self.assertIn(os.path.join(ROOT, "roles", "implementer.md"), call["stdin"])
        self.assertIn(os.path.realpath(self.spec), call["stdin"])
        self.assertIn(UNIT + "/reports/T01.md", call["stdin"])

    def test_the_role_decides_the_model_and_the_effort(self):
        for role, model, effort in (("engineer", "gpt-6.2-astra", "high"), ("analyst", "gpt-6.2-astra", "high"),
                                    ("implementer", "gpt-6.2-sol", "medium"), ("writer", "gpt-6.2-sol", "medium")):
            self.run_task(role=role)
            argv = self.calls()[-1]["argv"]
            self.assertEqual(self.option(argv, "-m"), [model], role)
            self.assertIn('model_reasoning_effort="%s"' % effort, self.option(argv, "-c"), role)
        self.run_task("--effort", "max", role="engineer")
        self.assertIn('model_reasoning_effort="max"', self.option(self.calls()[-1]["argv"], "-c"))

    def test_the_brief_decides_the_network_and_the_keys(self):
        self.write(UNIT + "/brief.md", "## Constraints\nWorker network: blocked\n\n## Decision rights\nPre-approved: deps, http\n")
        self.run_task()
        configs = self.option(self.calls()[-1]["argv"], "-c")
        self.assertIn("sandbox_workspace_write.network_access=false", configs)
        with open(os.path.join(self.proj, UNIT, "files", "T01", "cli", "guard.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["keys"], ["deps", "http"])
        self.write(UNIT + "/brief.md", "## Constraints\nWorker network: open\n")
        self.run_task()
        self.assertIn("sandbox_workspace_write.network_access=true", self.option(self.calls()[-1]["argv"], "-c"))

    def test_the_guards_refusals_are_listed(self):
        self.plan({"write": {"src/login.py": "new\n", UNIT + "/reports/T01.md": REPORT_DONE}, "last": LAST_DONE,
                   "refused": [{"tool": "Bash", "command": "npm install left-pad", "paths": [],
                                "reason": "This command installs or changes dependencies. The work's brief does not pre-approve it."}]})
        code, out = self.run_task()
        self.assertEqual(code, 0, out)
        self.assertIn("GUARD: refused Bash `npm install left-pad`: This command installs or changes dependencies", out)

    def test_an_analyst_writes_only_its_report_folder_and_its_files(self):
        self.plan({"write": {UNIT + "/reports/T01.md": REPORT_DONE}, "last": LAST_DONE})
        code, out = self.run_task(role="analyst")
        self.assertEqual(code, 0, out)
        call = self.calls()[0]
        files = os.path.realpath(os.path.join(self.proj, UNIT, "files", "T01"))
        self.assertEqual(self.option(call["argv"], "-m"), ["gpt-6.2-astra"])
        self.assertEqual(self.option(call["argv"], "-C"), [files])
        self.assertEqual(call["cwd"], files)
        roots = [v for v in self.option(call["argv"], "-c") if v.startswith("sandbox_workspace_write.writable_roots=")]
        self.assertEqual(json.loads(roots[0].split("=", 1)[1]), [os.path.realpath(os.path.join(self.proj, UNIT, "reports"))])
        self.assertIn("sandbox_workspace_write.exclude_slash_tmp=true", self.option(call["argv"], "-c"))
        self.assertIn("sandbox_workspace_write.exclude_tmpdir_env_var=true", self.option(call["argv"], "-c"))

    def test_an_implementation_role_keeps_tmp(self):
        self.run_task()
        configs = self.option(self.calls()[0]["argv"], "-c")
        self.assertIn("sandbox_workspace_write.exclude_slash_tmp=false", configs)
        self.assertIn("sandbox_workspace_write.exclude_tmpdir_env_var=false", configs)

    # ---- what it prints and keeps

    def test_the_summary_and_the_record(self):
        code, out = self.run_task()
        self.assertEqual(code, 0, out)
        self.assertIn("TASK T01 | codex gpt-6.2-sol medium |", out)
        self.assertIn("tokens in 16,541 (cached 13,440), out 812", out)
        self.assertIn("SESSION: sess-1", out)
        self.assertIn("STATUS: DONE", out)
        self.assertIn("CHANGED: src/login.py\n", out)
        self.assertNotIn("WARN", out)
        self.assertNotIn("12 passed", out)  # the report's text stays unread until the review
        with open(os.path.join(self.proj, UNIT, "files", "T01", "cli", "run.json"), encoding="utf-8") as f:
            record = json.load(f)
        self.assertEqual(record["session_id"], "sess-1")
        self.assertEqual(record["model"], "gpt-6.2-sol")

    def test_a_change_outside_the_scope_is_flagged(self):
        self.plan({"write": {"src/login.py": "new\n", "tests/login/test_a.py": "t\n", "src/other.py": "x\n",
                             UNIT + "/reports/T01.md": REPORT_DONE}, "last": LAST_DONE})
        code, out = self.run_task()
        self.assertEqual(code, 2, out)
        self.assertIn("WARN: Changed outside the paths under `## Scope and limits`: src/other.py", out)
        self.assertNotIn("test_a.py", out.split("WARN")[1])

    def test_a_change_to_the_conductors_documents_is_flagged(self):
        self.plan({"write": {"src/login.py": "new\n", UNIT + "/brief.md": "## Goal\nchanged\n",
                             UNIT + "/files/T01/scratch.txt": "ok\n", UNIT + "/reports/T01.md": REPORT_DONE},
                   "last": LAST_DONE})
        code, out = self.run_task()
        self.assertEqual(code, 2, out)
        self.assertIn("Changed under `.orchestra/` outside the task's report and `files/T01/`: %s/brief.md" % UNIT, out)
        self.assertNotIn("scratch.txt", out)

    def test_a_done_report_without_a_valid_record_is_flagged(self):
        self.plan({"write": {"src/login.py": "new\n", UNIT + "/reports/T01.md": "STATUS: DONE\nAll good.\n"}, "last": LAST_DONE})
        code, out = self.run_task()
        self.assertEqual(code, 2, out)
        self.assertIn("WARN: The report says DONE, but its verification record does not pass", out)

    def test_a_blocked_report_needs_no_record_and_shows_the_question(self):
        last = "STATUS: BLOCKED\nTASK: T01\nREPORT: %s/reports/T01.md\nQUESTION: May I change src/session.py?" % UNIT
        self.plan({"write": {UNIT + "/reports/T01.md": "STATUS: BLOCKED\n"}, "last": last})
        code, out = self.run_task()
        self.assertEqual(code, 0, out)
        self.assertIn("STATUS: BLOCKED", out)
        self.assertIn("QUESTION: May I change src/session.py?", out)

    def test_a_missing_report_is_flagged(self):
        self.plan({"write": {"src/login.py": "new\n"}, "last": LAST_DONE})
        code, out = self.run_task()
        self.assertEqual(code, 2, out)
        self.assertIn("WARN: No report at `%s/reports/T01.md`." % UNIT, out)

    def test_a_read_only_role_that_changes_the_project_is_flagged(self):
        self.plan({"write": {"src/login.py": "new\n", UNIT + "/reports/T01.md": REPORT_DONE}, "last": LAST_DONE})
        code, out = self.run_task(role="analyst")
        self.assertEqual(code, 2, out)
        self.assertIn("WARN: A read-only role changed project files: src/login.py", out)

    def test_a_failed_run_and_a_usage_limit(self):
        self.plan({"exit": 1, "stderr": "Error: You've hit your usage limit. Try again later."})
        code, out = self.run_task()
        self.assertEqual(code, 1, out)
        self.assertIn("WARN: The CLI exited with 1.", out)
        self.assertIn("usage limit. Stop this task and tell the user.", out)

    # ---- resume

    def test_resume_passes_the_settings_again(self):
        self.run_task("--effort", "medium", role="engineer")
        self.write(UNIT + "/brief.md", "## Decision rights\nPre-approved: deps\n")  # approved between the rounds
        message = self.write(UNIT + "/files/T01/rework-1.md", "The test for an expired token fails: see tests/login.\n")
        self.plan({"write": {"src/login.py": "newer\n", UNIT + "/reports/T01.md": REPORT_DONE}, "last": LAST_DONE, "session": "sess-1"})
        code, out = self.worker("resume", "--task", self.spec, "--message", message)
        self.assertEqual(code, 0, out)
        first, second = self.calls()
        argv = second["argv"]
        self.assertEqual(argv[:3], ["exec", "resume", "sess-1"])
        self.assertEqual(argv[-1], "The test for an expired token fails: see tests/login.")
        for value in self.option(first["argv"], "-c"):
            self.assertIn(value, self.option(argv, "-c"))
        self.assertEqual(self.option(argv, "-m"), ["gpt-6.2-astra"])
        self.assertEqual(self.option(argv, "--disable"), ["plugins"])
        self.assertTrue(self.option(argv, "-o")[0].endswith("last-message-2.md"))
        self.assertEqual(second["cwd"], first["cwd"])
        self.assertEqual(second["stdin"], "")
        self.assertIn("--dangerously-bypass-hook-trust", argv)
        with open(os.path.join(self.proj, UNIT, "files", "T01", "cli", "guard.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["keys"], ["deps"])

    def test_resume_needs_an_earlier_run_and_a_message(self):
        message = self.write(UNIT + "/files/T01/rework-1.md", "fix it\n")
        code, out = self.worker("resume", "--task", self.spec, "--message", message)
        self.assertEqual(code, 1)
        self.assertIn("No earlier run of T01", out)
        self.run_task()
        empty = self.write(UNIT + "/files/T01/empty.md", "  \n")
        code, out = self.worker("resume", "--task", self.spec, "--message", empty)
        self.assertEqual(code, 1)
        self.assertIn("is empty", out)

    # ---- what it refuses to start

    def test_bad_inputs(self):
        cases = [
            (("run", "--cli", "codex", "--task", self.spec, "--role", "tester"), "Unknown role"),
            (("run", "--cli", "codex", "--task", self.spec, "--role", "engineer", "--effort", "huge"), "Unknown effort"),
            (("run", "--cli", "agy", "--task", self.spec, "--role", "engineer", "--effort", "max"), "Unknown effort `max` for agy"),
            (("run", "--cli", "other", "--task", self.spec, "--role", "engineer"), "Unknown CLI"),
            (("run", "--cli", "codex", "--task", os.path.join(self.proj, "src/login.py"), "--role", "engineer"), "is not a task spec"),
        ]
        for args, text in cases:
            code, out = self.worker(*args)
            self.assertEqual(code, 1, args)
            self.assertIn(text, out, args)
        for cli in ("codex", "agy"):
            code, out = self.worker("run", "--cli", cli, "--task", self.spec, "--role", "engineer", path=self.tmp)
            self.assertEqual(code, 1, cli)
            self.assertIn("`%s` is not on the PATH" % cli, out)
        self.assertFalse(os.path.exists(self.log))

    # ---- agy

    def agy_settings(self, argv):
        return json.loads(self.option(argv, "--settings")[0])

    def test_an_agy_implementation_run(self):
        code, out = self.worker("run", "--cli", "agy", "--task", self.spec, "--role", "implementer")
        self.assertEqual(code, 0, out)
        call = self.calls()[0]
        argv = call["argv"]
        self.assertEqual(argv[0], "-p")
        for text in (os.path.join(ROOT, "roles", "implementer.md"), os.path.realpath(self.spec), UNIT + "/reports/T01.md"):
            self.assertIn(text, argv[1])
        self.assertEqual(call["stdin"], "")
        self.assertEqual(self.option(argv, "--model"), ["gemini-4-hydrogen-medium"])
        self.assertEqual(self.option(argv, "--output-format"), ["json"])
        self.assertIn("--disable-slash-commands", argv)
        self.assertEqual(self.option(argv, "--add-dir"), [os.path.realpath(self.proj)])
        self.assertEqual(self.agy_settings(argv), {"permissions": {"allow": ["read_url(*)", "read_file(%s)" % os.path.join(ROOT, "roles")]}})
        workspace = call["cwd"]
        self.assertFalse(workspace.startswith(os.path.realpath(self.proj)))
        with open(os.path.join(workspace, ".agents", "hooks.json"), encoding="utf-8") as f:
            hooks = json.load(f)
        handler = hooks["orchestra-guard"]["PreToolUse"][0]
        self.assertEqual(handler["matcher"], "*")
        self.assertIn("worker_guard.py", handler["hooks"][0]["command"])
        self.assertIn(os.path.join(self.proj, UNIT, "files", "T01", "cli", "guard.json"), handler["hooks"][0]["command"])
        self.assertIn("TASK T01 | agy gemini-4-hydrogen medium |", out)
        self.assertIn("SESSION: conv-1", out)
        self.assertIn("STATUS: DONE", out)
        self.assertIn("tokens in 15,684 (cached 18,349), out 854", out)
        with open(os.path.join(self.proj, UNIT, "files", "T01", "cli", "last-message-1.md"), encoding="utf-8") as f:
            self.assertEqual(f.read(), LAST_DONE)

    def test_an_agy_analyst_reads_the_project_and_writes_only_its_folders(self):
        self.plan({"write": {UNIT + "/reports/T01.md": REPORT_DONE}, "last": LAST_DONE})
        code, out = self.worker("run", "--cli", "agy", "--task", self.spec, "--role", "analyst", "--effort", "xhigh")
        self.assertEqual(code, 0, out)
        argv = self.calls()[0]["argv"]
        self.assertEqual(self.option(argv, "--model"), ["gemini-4-neon-xhigh"])
        unit = os.path.realpath(os.path.join(self.proj, UNIT))
        self.assertEqual(self.option(argv, "--add-dir"), [os.path.join(unit, "files", "T01"), os.path.join(unit, "reports")])
        self.assertEqual(self.agy_settings(argv)["permissions"]["allow"],
                         ["read_url(*)", "read_file(%s)" % os.path.join(ROOT, "roles"), "read_file(%s)" % os.path.realpath(self.proj)])

    def test_agy_with_the_network_blocked(self):
        self.write(UNIT + "/brief.md", "## Constraints\nWorker network: blocked\n")
        self.worker("run", "--cli", "agy", "--task", self.spec, "--role", "implementer")
        self.assertEqual(self.agy_settings(self.calls()[0]["argv"]), {"permissions": {"allow": ["read_file(%s)" % os.path.join(ROOT, "roles")]}})

    def test_an_agy_resume(self):
        self.worker("run", "--cli", "agy", "--task", self.spec, "--role", "engineer")
        message = self.write(UNIT + "/files/T01/rework-1.md", "Fix the expired-token case.\n")
        code, out = self.worker("resume", "--task", self.spec, "--message", message)
        self.assertEqual(code, 0, out)
        first, second = self.calls()
        self.assertEqual(second["argv"][:2], ["-p", "Fix the expired-token case."])
        self.assertEqual(self.option(second["argv"], "--conversation"), ["conv-1"])
        for opt in ("--model", "--add-dir", "--settings"):
            self.assertEqual(self.option(second["argv"], opt), self.option(first["argv"], opt), opt)
        self.assertEqual(second["cwd"], first["cwd"])

    def test_an_agy_run_that_fails(self):
        self.plan({"status": "ERROR", "last": "", "exit": 1})
        code, out = self.worker("run", "--cli", "agy", "--task", self.spec, "--role", "implementer")
        self.assertEqual(code, 1, out)
        with open(os.path.join(self.proj, UNIT, "files", "T01", "cli", "last-message-1.md"), encoding="utf-8") as f:
            self.assertIn("agy status: ERROR", f.read())

    def test_a_report_path_outside_the_work_folder_is_refused(self):
        self.write(UNIT + "/tasks/T02-x.md", TASK.replace(UNIT + "/reports/T01.md", "src/report.md"))
        code, out = self.worker("run", "--cli", "codex", "--task", os.path.join(self.proj, UNIT, "tasks", "T02-x.md"),
                                "--role", "engineer")
        # No path under reports/ is named, so the default is used. The fake wrote T01's report,
        # which is another task's, so that is flagged too.
        self.assertEqual(code, 2, out)
        self.assertIn("No report at `%s/reports/T02.md`." % UNIT, out)
        self.assertIn("outside the task's report and `files/T02/`: %s/reports/T01.md" % UNIT, out)
        self.write(UNIT + "/tasks/T03-x.md", TASK.replace(UNIT + "/reports/T01.md", ".orchestra/other/reports/T03.md"))
        code, out = self.worker("run", "--cli", "codex", "--task", os.path.join(self.proj, UNIT, "tasks", "T03-x.md"),
                                "--role", "engineer")
        self.assertEqual(code, 1)
        self.assertIn("is not under this work folder's `reports/`", out)


if __name__ == "__main__":
    unittest.main(verbosity=1)
