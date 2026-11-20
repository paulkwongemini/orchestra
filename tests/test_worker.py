#!/usr/bin/env python3
"""Tests for scripts/worker.py.

Run: python3 tests/test_worker.py

A fake `codex` on the PATH records how it was called and acts out a plan: which files it writes,
which report and last message it leaves, what it prints, and its exit code.
No model is called.
"""
import json
import os
import sys
import textwrap
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import BRIEF, REPORT_DONE, REPORT_PATH, ROOT, SPEC_PATH, STATUS_DONE, TASK, UNIT, ProjectTest

# The fake starts by logging the call and writing the plan's files.
FAKE_START = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, sys, time
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
''')

FAKE_CODEX = FAKE_START + textwrap.dedent('''\
    if "last" in plan:
        open(argv[argv.index("-o") + 1], "w", encoding="utf-8").write(plan["last"])
    record = os.path.dirname(argv[argv.index("-o") + 1])
    for refusal in plan.get("refused", []):
        with open(os.path.join(record, "guard.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(refusal, at=time.time())) + "\\n")
    print(json.dumps({"type": "thread.started", "thread_id": plan.get("session", "sess-1")}))
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 16541, "cached_input_tokens": 13440, "output_tokens": 812}}))
    sys.stderr.write(plan.get("stderr", ""))
    sys.exit(plan.get("exit", 0))
''')

class WorkerTest(ProjectTest):
    def setUp(self):
        super().setUp()
        self.bin = os.path.join(self.tmp, "bin")
        self.codex_home = os.path.join(self.tmp, "codex-home")
        for d in (self.bin, self.codex_home):
            os.makedirs(d)
        fake = os.path.join(self.bin, "codex")
        with open(fake, "w", encoding="utf-8") as f:
            f.write(FAKE_CODEX)
        os.chmod(fake, 0o755)
        with open(os.path.join(self.codex_home, "config.toml"), "w", encoding="utf-8") as f:
            f.write('model = "gpt-6.2-astra"\n[mcp_servers.node_repl]\ncommand = "x"\n[mcp_servers."my.server"]\ncommand = "y"\n')
        self.log = os.path.join(self.tmp, "calls.jsonl")
        self.plan_file = os.path.join(self.tmp, "plan.json")
        self.write(UNIT + "/brief.md", BRIEF)
        self.spec = self.write(SPEC_PATH, TASK)
        self.write("src/login.py", "old\n")
        self.plan({"write": {"src/login.py": "new\n", REPORT_PATH: REPORT_DONE}, "last": STATUS_DONE})

    def plan(self, plan):
        """What the fake CLI does in its next runs."""
        with open(self.plan_file, "w", encoding="utf-8") as f:
            json.dump(plan, f)

    def worker(self, *args, path=None):
        """Run worker.py. Returns its exit code and what it printed."""
        env = {"PATH": path if path is not None else self.bin + os.pathsep + os.environ.get("PATH", ""),
               "CODEX_HOME": self.codex_home, "FAKE_LOG": self.log, "FAKE_PLAN": self.plan_file, "FAKE_PROJECT": self.proj}
        proc = self.run_script("worker.py", *args, env=env)
        return proc.returncode, proc.stdout

    def run_task(self, *extra, role="implementer", cli="codex", task=None, path=None):
        return self.worker("run", "--cli", cli, "--task", task or self.spec, "--role", role, *extra, path=path)

    def calls(self):
        """The calls of the fake CLIs, in order."""
        with open(self.log, encoding="utf-8") as f:
            return [json.loads(line) for line in f]

    def record(self, name):
        """The path of a file in the record of T01's runs."""
        return os.path.join(self.proj, UNIT, "files", "T01", "cli", name)

    def read_record(self, name):
        with open(self.record(name), encoding="utf-8") as f:
            return json.load(f) if name.endswith(".json") else f.read()

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
        self.assertIn("sandbox_workspace_write.exclude_slash_tmp=false", configs)  # an implementation role keeps /tmp
        self.assertIn("sandbox_workspace_write.exclude_tmpdir_env_var=false", configs)
        self.assertEqual(self.option(argv, "--disable"), ["plugins", "apps", "goals", "image_generation"])
        hooks = [v for v in configs if v.startswith("hooks.PreToolUse=")]
        self.assertEqual(len(hooks), 1)
        self.assertIn('matcher=".*"', hooks[0])
        self.assertIn("worker_guard.py", hooks[0])
        self.assertIn("--dangerously-bypass-hook-trust", argv)
        self.assertIn(self.record("guard.json"), hooks[0])
        ctx = self.read_record("guard.json")
        self.assertEqual((ctx["kind"], ctx["task"], ctx["keys"]), ("impl", "T01", []))
        self.assertTrue(ctx["log"].endswith("files/T01/cli/guard.jsonl"))
        self.assertIn("--json", argv)
        self.assertEqual(self.option(argv, "-C"), [os.path.realpath(self.proj)])
        self.assertEqual(argv[-1], "-")
        self.assertTrue(self.option(argv, "-o")[0].endswith(UNIT + "/files/T01/cli/last-message-1.md"))
        self.assertEqual(call["cwd"], os.path.realpath(self.proj))
        self.assertIn(os.path.join(ROOT, "roles", "implementer.md"), call["stdin"])
        self.assertIn(os.path.realpath(self.spec), call["stdin"])
        self.assertIn(REPORT_PATH, call["stdin"])

    def test_the_role_decides_the_model_and_the_effort(self):
        for role, model, effort in (("engineer", "gpt-6.2-astra", "high"), ("analyst", "gpt-6.2-astra", "high"),
                                    ("implementer", "gpt-6.2-sol", "medium"), ("writer", "gpt-6.2-sol", "medium")):
            with self.subTest(role=role):
                self.run_task(role=role)
                argv = self.calls()[-1]["argv"]
                self.assertEqual(self.option(argv, "-m"), [model])
                self.assertIn('model_reasoning_effort="%s"' % effort, self.option(argv, "-c"))
        self.run_task("--effort", "max", role="engineer")
        self.assertIn('model_reasoning_effort="max"', self.option(self.calls()[-1]["argv"], "-c"))

    def test_the_brief_gives_the_keys_and_the_network_is_always_open(self):
        # A line from briefs written before the setting was dropped changes nothing.
        self.write(UNIT + "/brief.md", "## Constraints\nWorker network: blocked\n\n## Decision rights\nPre-approved: deps, http\n")
        self.run_task()
        configs = self.option(self.calls()[-1]["argv"], "-c")
        self.assertIn("sandbox_workspace_write.network_access=true", configs)
        self.assertNotIn("sandbox_workspace_write.network_access=false", configs)
        self.assertEqual(self.read_record("guard.json")["keys"], ["deps", "http"])

    def test_an_analyst_writes_only_its_report_folder_and_its_files(self):
        self.plan({"write": {REPORT_PATH: REPORT_DONE}, "last": STATUS_DONE})
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
        record = self.read_record("run.json")
        self.assertEqual(record["session_id"], "sess-1")
        self.assertEqual(record["model"], "gpt-6.2-sol")

    def test_the_guards_refusals_are_listed(self):
        self.plan({"write": {"src/login.py": "new\n", REPORT_PATH: REPORT_DONE}, "last": STATUS_DONE,
                   "refused": [{"tool": "Bash", "command": "npm install left-pad", "paths": [],
                                "reason": "This command installs or changes dependencies. The work's brief does not pre-approve it."}]})
        code, out = self.run_task()
        self.assertEqual(code, 0, out)
        self.assertIn("GUARD: refused Bash `npm install left-pad`: This command installs or changes dependencies", out)

    def test_a_change_outside_the_scope_is_flagged(self):
        self.plan({"write": {"src/login.py": "new\n", "tests/login/test_a.py": "t\n", "src/other.py": "x\n",
                             REPORT_PATH: REPORT_DONE}, "last": STATUS_DONE})
        code, out = self.run_task()
        self.assertEqual(code, 2, out)
        self.assertIn("WARN: Changed outside the paths under `## Scope and limits`: src/other.py", out)
        self.assertNotIn("test_a.py", out.split("WARN")[1])

    def other_run(self, task, scope, started, edits=(), ended=None):
        """Another run's record in the same project: live.json, its guard's log of edits, and run.json once over."""
        spec = self.write("%s/tasks/%s-x.md" % (UNIT, task), TASK.replace("`src/login.py` and the tests in `tests/login/`", scope)
                          .replace(REPORT_PATH, "%s/reports/%s.md" % (UNIT, task)))
        record = "%s/files/%s/cli" % (UNIT, task)
        self.write(record + "/live.json", json.dumps({"round": 1, "started": started, "spec": spec,
                                                      "report": os.path.join(self.proj, UNIT, "reports", task + ".md")}))
        self.write(record + "/activity.jsonl", "".join(json.dumps(
            {"at": started + 1, "tool": "apply_patch", "command": "", "paths": [os.path.join(os.path.realpath(self.proj), p)],
             "refused": False}) + "\n" for p in edits))
        if ended is not None:
            path = self.write(record + "/run.json", json.dumps({"rounds": 1}))
            os.utime(path, (ended, ended))

    def test_the_changes_of_runs_alongside_are_put_down_to_them(self):
        now = time.time()
        self.other_run("T02", "`src/other.py`", now - 30, edits=["src/shared.py"])
        self.other_run("T03", "`src/old.py`", now - 300, ended=now - 200)  # over before this run began
        self.plan({"write": {"src/login.py": "new\n", REPORT_PATH: REPORT_DONE,
                             # T02's own record and report, written while this run went
                             UNIT + "/files/T02/cli/events-1.jsonl": "{}\n", UNIT + "/reports/T02.md": "STATUS: DONE\n",
                             "src/other.py": "theirs\n", "src/shared.py": "theirs\n", "src/old.py": "?\n",
                             "elsewhere/x.py": "nobody's\n"},
                   "last": STATUS_DONE})
        code, out = self.run_task()
        self.assertEqual(code, 2, out)
        self.assertIn("ALONGSIDE: T02 ran at the same time. Changes put down to them: T02 src/other.py, src/shared.py", out)
        self.assertNotIn("T03", out.split("ALONGSIDE")[1].split("\n")[0])
        self.assertNotIn("Changed under `.orchestra/`", out)
        changed = [l for l in out.split("\n") if l.startswith("CHANGED:")][0]
        self.assertEqual(changed, "CHANGED: elsewhere/x.py, src/login.py, src/old.py")
        self.assertIn("WARN: Changed outside the paths under `## Scope and limits`: elsewhere/x.py, src/old.py", out)

    def test_an_edit_of_its_own_stays_its_own_alongside_another_run(self):
        now = time.time()
        self.other_run("T02", "`src/other.py`", now - 30)
        seen = json.dumps({"at": now + 60, "tool": "apply_patch", "command": "", "refused": False,
                           "paths": [os.path.join(os.path.realpath(self.proj), "src/other.py")]}) + "\n"
        self.plan({"write": {"src/login.py": "new\n", "src/other.py": "mine\n", REPORT_PATH: REPORT_DONE,
                             UNIT + "/files/T01/cli/activity.jsonl": seen},  # T01's guard saw the edit
                   "last": STATUS_DONE})
        code, out = self.run_task()
        self.assertEqual(code, 2, out)
        self.assertIn("WARN: Changed outside the paths under `## Scope and limits`: src/other.py", out)

    def test_a_change_to_the_conductors_documents_is_flagged(self):
        self.plan({"write": {"src/login.py": "new\n", UNIT + "/brief.md": "## Goal\nchanged\n",
                             UNIT + "/files/T01/scratch.txt": "ok\n", REPORT_PATH: REPORT_DONE},
                   "last": STATUS_DONE})
        code, out = self.run_task()
        self.assertEqual(code, 2, out)
        self.assertIn("Changed under `.orchestra/` outside the task's report and `files/T01/`: %s/brief.md" % UNIT, out)
        self.assertNotIn("scratch.txt", out)

    def test_a_done_report_without_a_valid_record_is_flagged(self):
        self.plan({"write": {"src/login.py": "new\n", REPORT_PATH: "STATUS: DONE\nAll good.\n"}, "last": STATUS_DONE})
        code, out = self.run_task()
        self.assertEqual(code, 2, out)
        self.assertIn("WARN: The report says DONE, but its verification record does not pass", out)

    def test_a_blocked_report_needs_no_record_and_shows_the_question(self):
        last = "STATUS: BLOCKED\nTASK: T01\nREPORT: %s\nQUESTION: May I change src/session.py?" % REPORT_PATH
        self.plan({"write": {REPORT_PATH: "STATUS: BLOCKED\n"}, "last": last})
        code, out = self.run_task()
        self.assertEqual(code, 0, out)
        self.assertIn("STATUS: BLOCKED", out)
        self.assertIn("QUESTION: May I change src/session.py?", out)

    def test_a_missing_report_is_flagged(self):
        self.plan({"write": {"src/login.py": "new\n"}, "last": STATUS_DONE})
        code, out = self.run_task()
        self.assertEqual(code, 2, out)
        self.assertIn("WARN: No report at `%s`." % REPORT_PATH, out)

    def test_a_read_only_role_that_changes_the_project_is_flagged(self):
        self.plan({"write": {"src/login.py": "new\n", REPORT_PATH: REPORT_DONE}, "last": STATUS_DONE})
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
        self.plan({"write": {"src/login.py": "newer\n", REPORT_PATH: REPORT_DONE}, "last": STATUS_DONE, "session": "sess-1"})
        code, out = self.worker("resume", "--task", self.spec, "--message", message)
        self.assertEqual(code, 0, out)
        first, second = self.calls()
        argv = second["argv"]
        self.assertEqual(argv[:3], ["exec", "resume", "sess-1"])
        self.assertEqual(argv[-1], "The test for an expired token fails: see tests/login.")
        for value in self.option(first["argv"], "-c"):
            self.assertIn(value, self.option(argv, "-c"))
        self.assertEqual(self.option(argv, "-m"), ["gpt-6.2-astra"])
        self.assertEqual(self.option(argv, "--disable"), ["plugins", "apps", "goals", "image_generation"])
        self.assertTrue(self.option(argv, "-o")[0].endswith("last-message-2.md"))
        self.assertEqual(second["cwd"], first["cwd"])
        self.assertEqual(second["stdin"], "")
        self.assertIn("--dangerously-bypass-hook-trust", argv)
        self.assertEqual(self.read_record("guard.json")["keys"], ["deps"])

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

    def test_bad_arguments_start_no_run(self):
        cases = [  # the arguments after `run`, and what the refusal says
            ((), {"role": "tester"}, "Unknown role"),
            (("--effort", "huge"), {"role": "engineer"}, "Unknown effort"),
            ((), {"role": "engineer", "cli": "other"}, "Unknown CLI"),
            ((), {"role": "engineer", "task": os.path.join(self.proj, "src/login.py")}, "is not a task spec"),
            ((), {"role": "engineer", "cli": "codex", "path": self.tmp}, "`codex` is not on the PATH"),
        ]
        for extra, named, text in cases:
            with self.subTest(text):
                code, out = self.run_task(*extra, **named)
                self.assertEqual(code, 1)
                self.assertIn(text, out)
        self.assertFalse(os.path.exists(self.log))

    def test_a_report_path_outside_the_work_folder_is_refused(self):
        other = self.write(UNIT + "/tasks/T02-x.md", TASK.replace(REPORT_PATH, "src/report.md"))
        code, out = self.run_task(role="engineer", task=other)
        # No path under reports/ is named, so the default is used. The fake wrote T01's report,
        # which is another task's, so that is flagged too.
        self.assertEqual(code, 2, out)
        self.assertIn("No report at `%s/reports/T02.md`." % UNIT, out)
        self.assertIn("outside the task's report and `files/T02/`: %s" % REPORT_PATH, out)
        elsewhere = self.write(UNIT + "/tasks/T03-x.md", TASK.replace(REPORT_PATH, ".orchestra/other/reports/T03.md"))
        code, out = self.run_task(role="engineer", task=elsewhere)
        self.assertEqual(code, 1)
        self.assertIn("is not under this work folder's `reports/`", out)

    # ---- watch

    def test_watch_shows_a_codex_run(self):
        self.run_task()
        code, out = self.worker("watch", "--task", self.spec, "--no-follow")
        self.assertEqual(code, 0, out)
        self.assertIn("start  session sess-1", out)
        self.assertIn("end    tokens in 16,541, out 812", out)
        with open(self.record("guard.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": time.time(), "tool": "Bash", "command": "git push",
                                "paths": [], "reason": "Pushing needs a key. More."}) + "\n")
        code, out = self.worker("watch", "--task", self.spec, "--no-follow")
        self.assertEqual(code, 0, out)
        self.assertIn("GUARD  refused Bash `git push`: Pushing needs a key", out)
        code, out = self.worker("watch", "--task", self.spec)  # the run is over, so following stops at once
        self.assertEqual(code, 0, out)
        self.assertIn("The run is over", out)

    def session_file(self, thread, meta, lines=()):
        """A Codex session file under the fake CODEX_HOME: its first line, then `lines`."""
        folder = os.path.join(self.codex_home, "sessions", time.strftime("%Y/%m/%d"))
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "rollout-2026-11-20T08-31-31-%s.jsonl" % thread)
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"timestamp": "2026-11-20T16:31:31.000Z", "type": "session_meta",
                                "payload": dict(meta, id=thread, timestamp="2026-11-20T16:31:31.000Z")}) + "\n")
            for n, line in enumerate(lines, 1):
                f.write(json.dumps(dict(line, ordinal=n)) + "\n")

    @staticmethod
    def completed(item):
        return {"timestamp": "2026-11-20T16:31:33.000Z", "type": "event_msg", "payload": {"type": "item_completed", "item": item}}

    def test_watch_shows_the_subagents(self):
        self.run_task()
        inherited = self.completed({"type": "AgentMessage", "content": [{"type": "Text", "text": "the parent's history"}]})
        own = [
            {"type": "event_msg", "payload": {"type": "thread_settings_applied"}},
            self.completed({"type": "AgentMessage", "content": [{"type": "Text", "text": "Running the checks."}]}),
            self.completed({"type": "CommandExecution", "command": ["/usr/bin/bash", "-lc", "pytest -q"], "exit_code": 2}),
            self.completed({"type": "FileChange", "changes": {os.path.join(self.proj, "src", "a.py"): {"type": "update"}}}),
            {"timestamp": "2026-11-20T16:31:40.000Z", "type": "event_msg", "payload": {"type": "task_complete"}},
        ]
        self.session_file("sub-1", {"parent_thread_id": "sess-1", "agent_nickname": "Noether",
                                    "subagent_history_start_ordinal": 2}, [inherited] + own)
        self.session_file("sub-2", {"parent_thread_id": "sub-1", "agent_nickname": "Dalton", "subagent_history_start_ordinal": 1},
                          [self.completed({"type": "AgentMessage", "content": [{"type": "Text", "text": "nested"}]})])
        self.session_file("other", {"parent_thread_id": "another-run", "agent_nickname": "Euler"},
                          [self.completed({"type": "AgentMessage", "content": [{"type": "Text", "text": "not this run"}]})])
        with open(self.record("activity.jsonl"), "a", encoding="utf-8") as f:
            for thread, command in (("sess-1", "ls"), ("sub-1", "pytest -q"), ("unseen", "make")):
                f.write(json.dumps({"at": time.time(), "tool": "Bash", "command": command, "paths": [],
                                    "refused": False, "thread": thread}) + "\n")
        code, out = self.worker("watch", "--task", self.spec, "--no-follow")
        self.assertEqual(code, 0, out)
        self.assertIn("agent  Noether started", out)
        self.assertIn("[Noether] say    Running the checks.", out)
        self.assertIn("[Noether] $      pytest -q", out)
        self.assertIn("[Noether]        exit 2", out)
        self.assertIn("[Noether] edit   update src/a.py", out)
        self.assertIn("agent  Noether finished", out)
        self.assertIn("[Dalton] say    nested", out)
        self.assertNotIn("the parent's history", out)
        self.assertNotIn("not this run", out)
        code, out = self.worker("watch", "--task", self.spec, "--no-follow", "--calls")
        self.assertEqual(code, 0, out)
        self.assertIn("call   Bash ls", out)
        self.assertIn("call   [Noether] Bash pytest -q", out)
        self.assertIn("call   [subagent] Bash make", out)
        self.assertNotIn("[Noether] $", out)  # with --calls the guard's log shows the commands
        self.assertIn("[Noether] say    Running the checks.", out)

    def test_watch_before_any_run(self):
        code, out = self.worker("watch", "--task", self.spec, "--no-follow")
        self.assertEqual(code, 1)
        self.assertIn("No run of T01 has started yet", out)


if __name__ == "__main__":
    unittest.main(verbosity=1)
