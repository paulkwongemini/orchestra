"""What the test files share: the fixtures, a project in a temporary folder, and the hook runner.

Every test file puts its own folder on the path before it imports this module, so it runs both as
`python3 tests/test_x.py` and under `python3 -m unittest discover -s tests`.
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
SPEC_PATH = UNIT + "/tasks/T01-login-fix.md"
REPORT_PATH = UNIT + "/reports/T01.md"
REVIEW_PATH = UNIT + "/reviews/T01.md"

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
Change only `src/login.py` and the tests in `tests/login/`.

## Context
Session handling lives in src/session.py.

## Acceptance criteria
`pytest tests/login` passes.

## If blocked
Report BLOCKED if src/session.py has to change.

## Report
Report path: `%s`
""" % REPORT_PATH

REVIEW = """## Scope reviewed
The whole diff of src/login.py.

## Checks run
$ pytest tests/login
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
pytest tests/login 이 통과한다.

## If blocked
src/session.py 를 고쳐야 하면 BLOCKED로 보고한다.

## Report
보고서 경로: `%s`
""" % REPORT_PATH

REVIEW_KO = """## Scope reviewed
src/login.py 의 diff 전체.

## Checks run
$ pytest tests/login
12 passed in 0.8s

## Verdict
통과.
"""

COMMAND_CHECK = {
    "criterion": "The login tests pass.",
    "kind": "command",
    "method": "pytest tests/login",
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


def verification(record):
    """A DONE report whose verification block holds this text."""
    return "STATUS: DONE\n\n## What I verified\n```json\n" + record + "\n```\n"


def verified_report(checks, unverified=()):
    return verification(json.dumps({"checks": checks, "unverified": list(unverified)}, ensure_ascii=False))


REPORT_DONE = verified_report([COMMAND_CHECK])
STATUS_DONE = "STATUS: DONE\nTASK: T01\nREPORT: %s\nFILES: src/login.py" % REPORT_PATH


def spec_prompt(unit=UNIT):
    """The prompt of a delegation: the path of the task spec T01 in a work folder."""
    return "Read the task spec at `%s/tasks/T01-login-fix.md` and carry it out." % unit


def request(model):
    """A transcript record of a request that has been sent. It exists before the response does."""
    return {"type": "api-request", "params": {"model": model}, "querySource": "sdk"}


def at_effort(model, effort):
    """A request sent at an effort level, as Claude Code records it."""
    entry = request(model)
    entry["params"]["output_config"] = {"effort": effort}
    return entry


def response(model, when="2026-11-12T10:00:00.000Z", side=False):
    """A transcript record of a response."""
    return {"type": "assistant", "isSidechain": side, "timestamp": when,
            "message": {"model": model, "content": [{"type": "text", "text": "ok"}]}}


class ProjectTest(unittest.TestCase):
    """A test with an empty project in a temporary folder."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="orchestra-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.proj = os.path.join(self.tmp, "proj")
        os.makedirs(self.proj)

    def write(self, rel, text, mtime=None):
        path = os.path.join(self.proj, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def run_script(self, script, *args, stdin="", env=None):
        """Run one of the plugin's scripts. Returns the finished process, with its output as text."""
        return subprocess.run([sys.executable, os.path.join(SCRIPTS, script)] + list(args), input=stdin,
                              capture_output=True, encoding="utf-8", errors="replace",
                              env=dict(os.environ, **(env or {})), timeout=60)

    def run_json(self, script, *args, stdin="", env=None):
        """Run a script that has to exit with 0. Returns the JSON it printed, or None if it printed nothing."""
        proc = self.run_script(script, *args, stdin=stdin, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stdout.strip()
        return json.loads(out) if out else None

    def assert_all(self, expected, judge, cases):
        """Check that judge(case) gives the expected verdict for every case. A failing case names itself."""
        for case in cases:
            with self.subTest(case=case):
                self.assertEqual(judge(case), expected)


class HookTest(ProjectTest):
    """A test of the hook scripts: it feeds one JSON shaped like what Claude Code sends, and checks the verdict.

    By default the session is new, runs on Fable and has just sent its first request, the user has
    turned conductor mode on, and the delegation rules and the documentation rules are loaded.
    """

    def setUp(self):
        super().setUp()
        self.state = os.path.join(self.tmp, "state")
        self.transcript = os.path.join(self.tmp, "session.jsonl")
        os.makedirs(os.path.join(self.state, "sessions"))
        self.set_transcript([request(FABLE)])
        self.on = self.marker("mode")
        self.rules = self.marker("delegation")
        self.doc_rules = self.marker("documentation")
        self.set_mode("claude")
        open(self.rules, "w").close()
        open(self.doc_rules, "w").close()
        self.stopped = 0

    def marker(self, kind, session="session-1"):
        """The file in which the hooks keep one fact about a session."""
        return os.path.join(self.state, "sessions", "%s.%s" % (session, kind))

    def set_transcript(self, entries):
        if entries is None:
            if os.path.exists(self.transcript):
                os.remove(self.transcript)
            return
        with open(self.transcript, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, separators=(",", ":")) + "\n")

    def set_mode(self, mode, session="session-1"):
        """Record a conductor mode for a session, as the user's command does."""
        with open(self.marker("mode", session), "w", encoding="utf-8") as f:
            f.write(mode)

    # ---- running a hook

    def run_hook(self, script, payload):
        payload = dict({"cwd": self.proj, "session_id": "session-1", "transcript_path": self.transcript}, **payload)
        env = {"CLAUDE_PROJECT_DIR": self.proj, "CLAUDE_PLUGIN_ROOT": ROOT, "CLAUDE_PLUGIN_DATA": self.state}
        return self.run_json(script, stdin=json.dumps(payload), env=env)

    @staticmethod
    def judge(out):
        """The verdict in a hook's output."""
        if out is None:
            return "allow"
        if out.get("decision") == "block":
            return "block"
        if "systemMessage" in out:
            return "warn"
        return out.get("hookSpecificOutput", {}).get("permissionDecision", "unknown")

    def verdict(self, script, payload):
        return self.judge(self.run_hook(script, payload))

    def refusal(self, script, payload):
        """The verdict and the reason given for it, from one run of the hook."""
        out = self.run_hook(script, payload)
        said = out or {}
        return self.judge(out), said.get("hookSpecificOutput", {}).get("permissionDecisionReason") or said.get("reason") or ""

    def reason(self, script, payload):
        return self.refusal(script, payload)[1]

    def assert_refused(self, script, payload, *fragments, verdict="deny"):
        """Check the verdict of one run and that its reason holds every fragment. Returns the reason."""
        got, why = self.refusal(script, payload)
        self.assertEqual(got, verdict)
        for fragment in fragments:
            self.assertIn(fragment, why)
        return why

    @staticmethod
    def note(out):
        """What a hook's output adds to the model's context."""
        return out["hookSpecificOutput"]["additionalContext"] if out else None

    # ---- the calls

    @staticmethod
    def main(tool, tool_input):
        """A tool call of the main conversation."""
        return {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input}

    @staticmethod
    def sub(role, tool, tool_input):
        """A tool call of a roster subagent."""
        return {
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "tool_input": tool_input,
            "agent_id": "agent-1",
            "agent_type": "orchestra:" + role,
        }

    def bash(self, cmd):
        """The verdict on a shell command of the main conversation."""
        return self.verdict("guard_main.py", self.main("Bash", {"command": cmd}))

    def sh(self, role, cmd):
        """The verdict on a shell command of a roster subagent."""
        return self.verdict("guard_main.py", self.sub(role, "Bash", {"command": cmd}))

    def impl(self, cmd):
        return self.sh("opus-engineer", cmd)

    def analyst(self, cmd):
        return self.sh("opus-analyst", cmd)

    def write_to(self, path, tool="Write", role=None):
        """The verdict on a Write, Edit or NotebookEdit call, of the main conversation or of a roster subagent."""
        tool_input = {"notebook_path" if tool == "NotebookEdit" else "file_path": path, "content": "x"}
        payload = self.sub(role, tool, tool_input) if role else self.main(tool, tool_input)
        return self.verdict("guard_main.py", payload)

    def writes(self, path):
        """Whether the main conversation's Write and Edit to the path are allowed, as one verdict."""
        write, edit = self.write_to(path), self.write_to(path, "Edit")
        self.assertEqual(write, edit, path)
        return write

    def write_src(self):
        """Whether the main conversation may write a source file. Denied only in a conductor session."""
        return self.write_to("src/app.py")

    def assert_writes(self, role, cases):
        """Check a subagent's verdict for each (tool, path, verdict)."""
        for tool, path, expected in cases:
            with self.subTest(role=role, tool=tool, path=path):
                self.assertEqual(self.write_to(path, tool, role), expected)

    def use(self, tool="Read", tool_input=None, **extra):
        """The verdict of require_skill.py on a tool call of the main conversation."""
        payload = dict(self.main(tool, tool_input or {"file_path": "src/app.py"}), **extra)
        return self.verdict("require_skill.py", payload)

    def skill(self, name="orchestra:conductor"):
        """The verdict on loading a skill."""
        return self.use("Skill", {"skill": name})

    def agent(self, **tool_input):
        """The verdict on an Agent call of the main conversation."""
        return self.verdict("guard_agent.py", self.main("Agent", tool_input))

    def delegate(self, name="sonnet-builder", prompt=None, **extra):
        """The verdict on a delegation to a roster subagent, with the task spec T01 unless a prompt is given."""
        return self.agent(subagent_type="orchestra:" + name, prompt=prompt or spec_prompt(), **extra)

    def stop(self, message, role="opus-engineer", agent_id=None):
        """The verdict on a roster subagent's last message.

        check_report.py counts the refusals of one subagent, so each call is a new subagent unless one is named.
        """
        if agent_id is None:
            self.stopped += 1
            agent_id = "stopped-%d" % self.stopped
        payload = {"hook_event_name": "SubagentStop", "agent_id": agent_id, "agent_type": "orchestra:" + role,
                   "last_assistant_message": message}
        return self.verdict("check_report.py", payload)

    def handback(self, message, role="opus-engineer", agent_id="agent-1"):
        """The verdict on a report that a roster subagent delivers through the SubagentHandback tool."""
        payload = dict(self.sub(role, "SubagentHandback", {"message": message}), agent_id=agent_id)
        return self.verdict("check_report.py", payload)

    # ---- the session's events

    def say(self, prompt, session="session-1"):
        """The user's prompt."""
        return self.run_hook("session.py", {"hook_event_name": "UserPromptSubmit", "prompt": prompt, "session_id": session})

    def expand(self, name, prompt=None):
        """A slash command, as Claude Code reports its expansion."""
        return self.run_hook("session.py", {"hook_event_name": "UserPromptExpansion", "command_name": name,
                                            "command_source": "plugin", "prompt": prompt or "/" + name})

    def session_start(self, source, **extra):
        return self.run_hook("session.py", dict({"hook_event_name": "SessionStart", "source": source}, **extra))

    def switch(self, to_model, from_model=SONNET, source="picker"):
        payload = {"hook_event_name": "PostModelSwitch", "from_model": from_model, "to_model": to_model,
                   "requested_model": to_model, "source": source}
        return self.run_hook("session.py", payload)

    def spoke(self, at, event="UserPromptSubmit", session="session-1"):
        """Record that the user spoke at a time: with a prompt, or with the answer to a question (`PostToolUse`)."""
        payload = {"hook_event_name": event, "session_id": session}
        if event == "PostToolUse":
            payload.update(tool_name="AskUserQuestion", tool_input={})
        else:
            payload["prompt"] = "ok"
        self.run_hook("session.py", payload)
        os.utime(self.marker("turn", session), (at, at))

    # ---- the brief

    def brief(self, line="", unit=UNIT, at=2000, text=BRIEF):
        """Write a work folder's brief, with a line added at the end of `## Decision rights`."""
        self.write(unit + "/brief.md", text + ("\n" + line + "\n" if line else ""), mtime=at)

    def activate(self, unit=UNIT, how="read"):
        """Make a work folder the active one: the conductor reads its brief, writes it, or delegates one of its tasks."""
        if how == "read":
            self.assertEqual(self.verdict("report_gate.py", self.main("Read", {"file_path": unit + "/brief.md"})), "allow")
        elif how == "write":
            self.assertEqual(self.write_to(unit + "/brief.md"), "allow")
        else:
            self.write(unit + "/tasks/T01-login-fix.md", TASK)
            self.assertEqual(self.delegate("opus-analyst", spec_prompt(unit)), "allow")

    def confirmed(self, line, unit=UNIT, text=BRIEF):
        """A brief with this line, confirmed by the user, in the active work folder."""
        self.brief(line, unit=unit, text=text)
        self.spoke(3000)
        self.activate(unit)
