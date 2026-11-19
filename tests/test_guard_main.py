#!/usr/bin/env python3
"""Tests for scripts/guard_main.py: what the conductor and its subagents may write and run.

Run: python3 tests/test_guard_main.py
"""
import os
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import (BRIEF, BRIEF_KO, FABLE, OTHER_UNIT, REPORT_PATH, SONNET, SPEC_PATH, UNIT, HookTest, request,
                     response)

GUARD = "guard_main.py"
IMPL = ("opus-engineer", "sonnet-builder", "sonnet-writer")
WRITES_LINE = "Conductor writes: src/app.py"

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

# One command of each kind that a `Pre-approved:` key covers
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

# What an implementation subagent may write, whatever the brief lists: (tool, path, verdict)
IMPL_WRITES = [
    ("Write", "src/app.py", "allow"),
    ("Write", REPORT_PATH, "allow"),
    ("Edit", SPEC_PATH, "deny"),
    ("Write", UNIT + "/reviews/T01.md", "deny"),
    ("Write", UNIT + "/brief.md", "deny"),
    ("Write", UNIT + "/files/T01/run.rb", "allow"),
    ("Edit", UNIT + "/files/T01/run.rb", "allow"),
    ("Write", UNIT + "/scratch/run.rb", "deny"),
    ("Write", ".orchestra/files/run.rb", "deny"),
]


def shell(cmd, role=None):
    """A Bash call of the main conversation, or of a roster subagent."""
    tool_input = {"command": cmd}
    return HookTest.sub(role, "Bash", tool_input) if role else HookTest.main("Bash", tool_input)


class GuardWriteTest(HookTest):
    """Principle 8: Edit and Write"""

    def test_what_the_conductor_may_write(self):
        cases = [
            ("src/app.py", "deny"),
            (os.path.join(self.proj, "README.md"), "deny"),
            (UNIT + "/brief.md", "allow"),
            (SPEC_PATH, "allow"),
            (UNIT + "/reviews/T01.md", "allow"),
            (UNIT + "/decisions.md", "allow"),
            (UNIT + "/files/T01/notes.txt", "allow"),
            (REPORT_PATH, "deny"),
            (OTHER_UNIT + "/reports/T07.md", "deny"),
            (os.path.join(tempfile.gettempdir(), "orchestra-note.txt"), "allow"),
            (os.path.expanduser("~/.claude/plans/plan.md"), "allow"),
            (os.path.expanduser("~/.claude/projects/some-project/memory/note.md"), "allow"),
            (os.path.expanduser("~/.orchestra-test-missing/file.txt"), "deny"),
        ]
        for path, expected in cases:
            with self.subTest(path=path):
                self.assertEqual(self.writes(path), expected)
        self.assertEqual(self.write_to("analysis.ipynb", "NotebookEdit"), "deny")

    def test_work_folder_is_kept_out_of_git_by_default(self):
        ignore = os.path.join(self.proj, ".orchestra", ".gitignore")
        self.assertEqual(self.write_to(UNIT + "/brief.md"), "allow")
        with open(ignore, encoding="utf-8") as f:
            self.assertEqual(f.read(), "*\n")
        with open(ignore, "w", encoding="utf-8") as f:
            f.write("")  # the project chose to commit these records
        self.assertEqual(self.write_to(UNIT + "/decisions.md"), "allow")
        with open(ignore, encoding="utf-8") as f:
            self.assertEqual(f.read(), "")

    def test_what_an_implementation_subagent_may_write(self):
        for role in IMPL:
            self.assert_writes(role, IMPL_WRITES)

    def test_what_a_readonly_subagent_may_write(self):
        self.assert_writes("opus-analyst", [
            ("Write", "src/app.py", "deny"),
            ("Write", UNIT + "/reports/T02.md", "allow"),
            ("Write", UNIT + "/brief.md", "deny"),
            ("Write", UNIT + "/files/T02/fetch.rb", "allow"),
            ("Write", UNIT + "/scratch/fetch.rb", "deny"),
        ])


class GuardBashTest(HookTest):
    """Principles 3 and 8: the shell commands, with no brief"""

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
        "cat %s" % REPORT_PATH,
        "rm %s" % REPORT_PATH,
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
    ]

    def test_what_the_conductor_may_run(self):
        self.assert_all("allow", self.bash, self.ALLOW)
        self.assert_all("deny", self.bash, self.DENY)
        self.assert_all("deny", self.bash, self.NOT_PREAPPROVED)

    def test_what_a_readonly_subagent_may_run(self):
        self.assert_all("allow", self.analyst, (
            "git status",
            "rg -n TODO src",
            "ruby fetch.rb > %s/files/T02/out.jsonl" % UNIT,
            "cp src/app.py %s/files/T02/app.py" % UNIT,
        ))
        self.assert_all("deny", self.analyst, (
            "sed -i 's/a/b/' src/app.py",
            "echo x > src/x.txt",
            "ruby fetch.rb > %s/brief.md" % UNIT,
            'git commit -m "x"',
            "claude -p x",
        ))

    def test_what_an_implementation_subagent_may_run(self):
        self.assert_all("allow", self.impl, ("sed -i 's/a/b/' src/app.py", "claude --version"))
        self.assert_all("deny", self.impl, (
            "rm -rf build && npm install",  # nothing is pre-approved
            'git commit -m "x"',
            "claude -p x",
            'bash -c "claude -p x --model fable"',
        ))


class PreapprovalTest(HookTest):
    """Principle 3: what the brief in the active work folder pre-approves."""

    # ---- the commands that froze the real session

    def test_frozen_command_that_runs_no_data_command_passes_with_nothing_pre_approved(self):
        self.assertEqual(self.bash(FROZEN_A), "allow")

    def test_frozen_commands_pass_with_data(self):
        self.confirmed("Pre-approved: data")
        self.assert_all("allow", self.bash, (FROZEN_A, FROZEN_B, FROZEN_C, FROZEN_D))

    def test_frozen_commands_are_refused_without_the_line(self):
        self.confirmed("")
        for cmd in (FROZEN_B, FROZEN_C, FROZEN_D):
            with self.subTest(cmd=cmd):
                self.assert_refused(GUARD, shell(cmd), "`data`", "`Pre-approved:`", "## Decision rights",
                                    "does not pre-approve")

    # ---- when the line counts (BriefStateTest has the states in which it does not count yet)

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
        self.assertEqual(self.write_to(OTHER_UNIT + "/brief.md", role="opus-engineer"), "deny")
        self.assertEqual(self.bash("bin/rails db:migrate"), "allow")

    def test_sessions_do_not_share_the_active_folder(self):
        self.confirmed("Pre-approved: data")
        self.set_mode("claude", "session-2")
        payload = dict(shell("bin/rails db:migrate"), session_id="session-2")
        self.assertEqual(self.verdict(GUARD, payload), "deny")

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
            with self.subTest(key=key):
                self.assert_refused(GUARD, shell(KEY_COMMANDS[key]), "`%s`" % key, verdict="ask")

    def test_refusals_say_the_way_forward(self):
        self.confirmed("")
        for key in ("data", "deps", "git"):
            with self.subTest(key=key):
                why = self.reason(GUARD, shell(KEY_COMMANDS[key]))
                self.assertIn("`%s`" % key, why)
                self.assertIn("ask them", why)
                self.assertIn("does not depend on the answer", why)
        why = self.reason(GUARD, shell("npm install", "opus-engineer"))
        self.assertIn("`deps`", why)
        self.assertIn("BLOCKED", why)
        self.assertIn("another way", why)
        why = self.reason(GUARD, shell("npm install", "opus-analyst"))
        self.assertIn("read-only", why)
        self.assertIn("recommendation", why)

    def test_refusals_name_the_way_that_needs_no_approval(self):
        self.confirmed("")
        everyone = (None, "opus-engineer", "opus-analyst")
        mentions = ('echo "run bin/rails db:seed first"', "sed -n '/db:migrate/p' README.md")
        for cmd in mentions:
            for role in everyone:
                with self.subTest(cmd=cmd, role=role):
                    self.assert_refused(GUARD, shell(cmd, role), "Grep, Read or Write tool", "needs no pre-approval")
        why = self.reason(GUARD, shell(mentions[0], "opus-engineer"))
        self.assertLess(why.index("Grep, Read or Write tool"), why.index("Do not look for another way"))
        self.assertIn("run the data command itself", why)
        self.confirmed("Pre-approved: data")
        why = self.reason(GUARD, shell("RAILS_ENV=production bin/rails db:migrate", "opus-engineer"))
        self.assertIn("Grep, Read or Write tool", why)
        self.confirmed("")
        for cmd in ("git checkout -- src/a.py", "git mv a b", "git rm x", "git add -A"):
            with self.subTest(cmd=cmd):
                why = self.assert_refused(GUARD, shell(cmd, "opus-engineer"), "change the file directly, without git",
                                          "needs the git command itself")
                self.assertNotIn("Grep, Read or Write tool", why)
        self.assertNotIn("without git", self.reason(GUARD, shell("git checkout -- src/a.py")))
        for role in everyone:
            with self.subTest(role=role):
                why = self.reason(GUARD, shell("npm install", role))
                self.assertIn("`deps`" if "read-only" not in why else "read-only", why)
                self.assertNotIn("Grep, Read or Write tool", why)
                self.assertNotIn("without git", why)

    # ---- production

    def test_production_data_commands_go_to_the_user(self):
        self.confirmed("Pre-approved: data")
        production = (
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
        )
        self.assert_all("ask", self.bash, production)
        self.assert_all("deny", self.impl, production)
        self.assertEqual(self.bash("RAILS_ENV=staging bin/rails db:migrate"), "allow")

    def test_a_test_target_does_not_pass_a_data_command(self):
        self.confirmed("")
        self.assert_all("deny", self.bash, (
            "RAILS_ENV=test bin/rails db:schema:load",
            "cmd/rake RAILS_ENV=test db:migrate",
            "DATABASE_URL=sqlite3:tmp/x.sqlite3 bin/rails db:seed",
        ))

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
        for judge in (self.bash, self.impl, self.analyst):
            self.assert_all("allow", judge, self.SEARCHES)

    def test_other_commands_naming_data_commands_are_still_refused(self):
        self.confirmed("Pre-approved: none")
        for cmd in self.NOT_SEARCHES + self.OTHER_RUNS:
            for role in (None, "opus-engineer"):
                with self.subTest(cmd=cmd, role=role):
                    self.assert_refused(GUARD, shell(cmd, role), "`data`")

    def test_unreadable_line_keeps_the_data_rule_for_a_subagent(self):
        self.confirmed("Pre-approved: none")
        self.assert_refused(GUARD, shell('echo "unterminated; bin/rails db:migrate', "opus-engineer"), "`data`")
        self.assertEqual(self.impl('echo "unterminated'), "allow")

    def test_data_key_covers_the_commands_that_are_not_searches(self):
        self.confirmed("Pre-approved: data")
        self.assert_all("allow", self.bash, self.NOT_SEARCHES)
        self.assert_all("allow", self.impl, self.NOT_SEARCHES)
        self.assertEqual(self.bash("RAILS_ENV=production bin/rails db:migrate"), "ask")
        self.assertEqual(self.bash("RAILS_ENV=production echo $(bin/rails db:migrate)"), "ask")

    # ---- the parser

    def test_quoted_parentheses_inside_substitutions(self):
        cases = [
            ('echo "$(grep -o \'var(--\' f.css | wc -l)"', "allow"),
            ("echo $(echo ')')", "allow"),
            ('echo "$(printf \')\' ; rm -rf src)"', "deny"),
            ("echo $(echo ')'; rm -rf src)", "deny"),
            ('echo "$(echo "$(printf \')\'; rm -rf src)")"', "deny"),
            ('echo "$(echo "$(printf \')\')")"', "allow"),
            ("echo `printf ')'; rm -rf src`", "deny"),
            ("echo $(echo `echo ')'`)", "allow"),
            ("echo $(echo `rm -rf src`)", "deny"),
            ("echo `echo hi`;rm -rf src", "deny"),
            ("echo $(echo hi);rm -rf src", "deny"),
            ('echo "`echo \')\'`";rm -rf src', "deny"),
        ]
        for cmd, expected in cases:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.bash(cmd), expected)

    def test_unreadable_commands_are_refused(self):
        for cmd in ('echo "unterminated', "echo $(ls", "eval " * 8 + "ls"):
            with self.subTest(cmd=cmd):
                self.assert_refused(GUARD, shell(cmd), "could not read", "simpler, separate commands")
                self.assertEqual(self.analyst(cmd), "deny")
                self.assertEqual(self.impl(cmd), "allow")

    # ---- the subagents

    def test_keys_cover_an_implementation_subagent(self):
        cmds = {"git": 'git commit -m x', "push": "git push origin main", "deps": "npm install",
                "data": "bin/rails db:migrate", "ssh": "ssh host ls"}
        self.assert_all("deny", self.impl, cmds.values())
        self.assert_all("allow", self.impl, (
            "sed -i 's/a/b/' src/app.py",
            "rm -rf build",
            "echo x > src/x.txt",
            "curl -o x.zip https://example.com/x.zip",
            "curl -X POST http://localhost:3000/a -d a=1",
        ))
        for key, cmd in cmds.items():
            with self.subTest(key=key):
                self.confirmed("Pre-approved: " + key)
                self.assertEqual(self.impl(cmd), "allow")

    def test_no_key_covers_a_readonly_subagent(self):
        for key, cmd in (("git", "git commit -m x"), ("data", "bin/rails db:migrate")):
            with self.subTest(key=key):
                self.confirmed("")
                self.assertEqual(self.analyst(cmd), "deny")
                self.confirmed("Pre-approved: " + key)
                self.assertEqual(self.analyst(cmd), "deny")
        self.assertEqual(self.analyst("git status"), "allow")

    # ---- curl

    def test_loopback_curl(self):
        self.assert_all("allow", self.bash, (
            "curl -X POST http://127.0.0.1:3000/session -d a=1",
            "curl -X POST http://localhost:3000/ -d a=1",
            "curl -X POST http://[::1]:3000/ -d a=1",
            "curl -s -XPOST localhost:3000/x --data-urlencode 'a=b c'",
            "curl -sS -H 'Content-Type: application/json' --json '{}' http://127.1.2.3/api",
        ))
        self.assert_all("ask", self.bash, (
            "curl -X POST https://api.example.com -d a=1",
            "curl -X POST https://api.example.com -d '{\"a\":1}'",
            'curl -X POST "$B/session" -d a=1',
            "curl -X POST http://127.0.0.1.nip.io/x -d a=1",
            "curl -X POST http://localhost.example.com/x -d a=1",
            "curl -X POST http://localhost@example.com/x -d a=1",
            "curl -X POST http://localhost:3000/x http://example.com/y -d a=1",
            "curl -X POST --resolve localhost:80:203.0.113.5 http://localhost/x -d a=1",
            "curl -x http://proxy.example.com:8080 -X POST http://localhost/x -d a=1",
            "curl -sL -X POST http://localhost/x -d a=1",
            "curl -X POST -d a=1",
        ))
        self.confirmed("Pre-approved: http")
        self.assertEqual(self.bash("curl -X POST https://api.example.com -d a=1"), "allow")
        self.assertEqual(self.bash('curl -X POST "$B/session" -d a=1'), "allow")

    # ---- the line in the brief

    def test_forms_of_the_pre_approved_line(self):
        approved = [
            "- Pre-approved: data",
            "* **Pre-approved:** data, deps",
            "**Pre-approved**: `data`, `deps`",
            "`Pre-approved`: `data` `deps`",
            "pre-approved： data",
            "PRE-APPROVED: data deps",
            "1. Pre-approved: deps,data",
        ]
        not_approved = ["Pre-approved: none", "Pre-approved:", "- Pre-approved: `none`", "Pre-approved: database",
                        "Approved: data", "Not pre-approved: data"]
        for expected, lines in (("allow", approved), ("deny", not_approved)):
            for line in lines:
                with self.subTest(line=line):
                    self.confirmed(line)
                    self.assertEqual(self.bash("bin/rails db:migrate"), expected)

    def test_line_outside_decision_rights_does_not_count(self):
        self.confirmed("", text=BRIEF.replace("Do not change the public API.", "Pre-approved: data"))
        self.assertEqual(self.bash("bin/rails db:migrate"), "deny")

    def test_brief_body_in_another_language(self):
        self.confirmed("- `Pre-approved`: `data`", text=BRIEF_KO)
        self.assertEqual(self.bash("bin/rails db:migrate"), "allow")
        self.assertEqual(self.bash("npm install"), "deny")


class BriefStateTest(HookTest):
    """A line in the brief counts only in the active work folder, once the user has confirmed that version of the brief."""

    # The line, the call it lets through once it counts, and how a refusal names the line and what it lists
    CALLS = [
        ("Pre-approved: data", shell("bin/rails db:migrate"), "`Pre-approved:`", "lists `data`"),
        (WRITES_LINE, HookTest.main("Write", {"file_path": "src/app.py", "content": "x"}), "`Conductor writes:`",
         "lists this file"),
    ]

    def test_no_active_work_folder(self):
        for line, call, names_the_line, _ in self.CALLS:
            with self.subTest(line=line):
                self.brief(line)
                self.spoke(3000)
                self.assert_refused(GUARD, call, "no active work folder", names_the_line)
                self.assertEqual(self.bash("echo x > src/app.py"), "deny")

    def test_brief_newer_than_the_last_turn(self):
        for line, call, _, lists in self.CALLS:
            with self.subTest(line=line):
                self.spoke(1000)
                self.brief(line, at=2000)
                self.activate()
                self.assert_refused(GUARD, call, lists, "has not confirmed this version of the brief", "ask them")
                unlisted = self.main("Write", {"file_path": "src/other.py", "content": "x"})
                self.assertIn("does not list this file", self.reason(GUARD, unlisted))
                self.spoke(3000)
                self.assertEqual(self.verdict(GUARD, call), "allow")

    def test_no_user_turn_recorded(self):
        for line, call, _, lists in self.CALLS:
            with self.subTest(line=line):
                self.brief(line)
                self.activate()
                self.assert_refused(GUARD, call, lists, "no user turn is recorded",
                                    "has not confirmed this version of the brief")
                self.assert_all("ask", self.bash, ("git push origin main", "ssh host ls"))

    def test_active_work_folder_without_a_brief(self):
        self.confirmed(WRITES_LINE)
        os.remove(os.path.join(self.proj, UNIT, "brief.md"))
        self.assert_refused(GUARD, self.CALLS[1][1], "has no brief")


class ConductorWritesTest(HookTest):
    """Principle 8: the project files the confirmed brief lets the conductor write itself."""

    def test_listed_file_is_allowed(self):
        self.confirmed(WRITES_LINE)
        self.assertEqual(self.writes("src/app.py"), "allow")
        self.assertEqual(self.writes(os.path.join(self.proj, "src", "app.py")), "allow")
        self.write("src/app.py", "x")
        self.assertEqual(self.writes("src/app.py"), "allow")
        self.assertEqual(self.writes("src/../src/app.py"), "allow")

    def test_relative_entry_is_resolved_against_the_project(self):
        self.write("src/app.py", "x")
        self.confirmed(WRITES_LINE)
        payload = self.main("Write", {"file_path": "app.py", "content": "x"})
        payload["cwd"] = os.path.join(self.proj, "src")
        self.assertEqual(self.verdict(GUARD, payload), "allow")
        payload["tool_input"]["file_path"] = "src/app.py"
        self.assertEqual(self.verdict(GUARD, payload), "deny")

    def test_absolute_and_home_entries(self):
        self.confirmed("Conductor writes: %s" % os.path.join(self.proj, "docs", "guide.md"))
        self.assertEqual(self.writes("docs/guide.md"), "allow")
        with unittest.mock.patch.dict(os.environ, {"HOME": self.tmp}):
            self.confirmed("Conductor writes: ~/proj/docs/a.md")
            self.assertEqual(self.writes("docs/a.md"), "allow")
            self.assertEqual(self.writes("docs/guide.md"), "deny")

    def test_unlisted_file_is_denied_with_the_way(self):
        self.confirmed(WRITES_LINE)
        for path in ("src/other.py", "README.md"):
            with self.subTest(path=path):
                self.assert_refused(GUARD, self.main("Write", {"file_path": path, "content": "x"}),
                                    "You cannot edit", "delegate this change", "`Conductor writes:`",
                                    "## Decision rights", "does not list this file",
                                    "Code, configuration and tests are delegated")

    def test_brief_of_another_work_folder_does_not_count(self):
        self.brief(WRITES_LINE, unit=OTHER_UNIT)
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
        self.confirmed(WRITES_LINE)
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
                self.assertEqual(self.writes("src/app.py"), "deny")
                self.assertEqual(self.writes("none"), "deny")

    def test_line_outside_decision_rights_does_not_count(self):
        self.confirmed("", text=BRIEF.replace("Do not change the public API.", WRITES_LINE))
        self.assertEqual(self.writes("src/app.py"), "deny")

    def test_entries_that_name_no_single_project_file(self):
        self.write("src/app.py", "x")
        outside = "/etc/orchestra-test-missing.txt"
        cases = [  # the entry, the writes it does not allow, the commands it does not allow
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
            (REPORT_PATH, [REPORT_PATH], []),
            (UNIT + "/reports/", [REPORT_PATH], []),
        ]
        for entry, targets, cmds in cases:
            with self.subTest(entry=entry):
                self.confirmed("Conductor writes: " + entry)
                self.assert_all("deny", self.writes, targets + ["src/app.py"])
                self.assert_all("deny", self.bash, cmds)
        self.confirmed("Conductor writes: src/*.py, src/app.py")
        self.assertEqual(self.writes("src/app.py"), "allow")
        self.assertEqual(self.writes("src/*.py"), "deny")

    def test_the_line_does_not_override_the_work_folder_rules(self):
        self.confirmed("Conductor writes: %s, %s/decisions.md" % (REPORT_PATH, UNIT))
        self.assert_refused(GUARD, self.main("Write", {"file_path": REPORT_PATH, "content": "x"}),
                            "Reports are written by subagents")
        self.assertEqual(self.write_to(REPORT_PATH, "Edit"), "deny")
        self.assertEqual(self.writes(UNIT + "/decisions.md"), "allow")
        self.assertEqual(self.writes(UNIT + "/tasks/T02-next.md"), "allow")
        self.assertEqual(self.bash("echo x > %s" % REPORT_PATH), "deny")

    def test_forms_of_the_conductor_writes_line(self):
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
                unlisted = ({"src/app.py", "docs/guide.md", "docs/user guide.md"} - set(paths)) | {"docs/user", "guide.md"}
                self.assert_all("allow", self.writes, paths)
                self.assert_all("deny", self.writes, sorted(unlisted))

    def test_entries_keep_their_underscores(self):
        for line in ("Conductor writes: _config.yml, __init__.py, notes_",
                     "- **Conductor writes:** `_config.yml`, `__init__.py`, `notes_`",
                     "Conductor writes:_config.yml\nConductor writes: __init__.py\nConductor writes: notes_"):
            with self.subTest(line=line):
                self.confirmed(line)
                self.assert_all("allow", self.write_to, ("_config.yml", "__init__.py", "notes_"))
                self.assert_all("deny", self.write_to, ("config.yml", "init__.py", "__init__", "notes"))

    def test_brief_body_in_another_language(self):
        self.confirmed("- `Conductor writes`: `src/app.py`", text=BRIEF_KO)
        self.assertEqual(self.writes("src/app.py"), "allow")
        self.assertEqual(self.writes("src/other.py"), "deny")

    def test_listed_notebook(self):
        self.confirmed("Conductor writes: analysis.ipynb")
        self.assertEqual(self.write_to("analysis.ipynb", "NotebookEdit"), "allow")
        self.assertEqual(self.write_to("other.ipynb", "NotebookEdit"), "deny")

    def test_shell_writes_to_a_listed_file(self):
        self.write("src/app.py", "x")
        self.confirmed(WRITES_LINE)
        self.assert_all("allow", self.bash, (
            "echo x > src/app.py",
            "echo x >> src/app.py",
            "echo x | tee src/app.py",
            "cp /tmp/orchestra-a.py src/app.py",
        ))
        self.assert_all("deny", self.bash, (
            "echo x > src/other.py",
            "echo x | tee src/app.py src/other.py",
            "cp src/app.py src/other.py",
            "sed -i 's/a/b/' src/app.py",
            "perl -pi -e 's/a/b/' src/app.py",
            "vim src/app.py",
            "patch src/app.py < fix.diff",
            "python3 -c \"open('src/app.py','w').write('x')\"",
            "find src -name app.py -delete",
        ))

    def test_the_line_gives_a_readonly_subagent_nothing(self):
        self.confirmed(WRITES_LINE)
        self.assert_writes("opus-analyst", [("Write", "src/app.py", "deny"), ("Edit", "src/app.py", "deny")])
        self.assertEqual(self.analyst("echo x > src/app.py"), "deny")

    def test_the_line_does_not_limit_an_implementation_subagent(self):
        self.confirmed("Conductor writes: src/app.py, %s/brief.md, %s/reviews/T01.md" % (UNIT, UNIT))
        for role in ("sonnet-builder", "opus-engineer"):
            self.assert_writes(role, IMPL_WRITES + [("Write", "src/other.py", "allow")])


class HaikuGuardTest(HookTest):
    """What the Haiku subagents may write and run."""

    def test_scout_is_read_only(self):
        self.assert_writes("haiku-scout", [("Edit", "src/app.py", "deny"), ("Write", "src/app.py", "deny")])
        self.assertEqual(self.sh("haiku-scout", "echo x > src/x.txt"), "deny")
        self.assertEqual(self.sh("haiku-scout", "rg -n TODO src"), "allow")
        self.confirmed("Pre-approved: git")
        self.assertEqual(self.impl('git commit -m "x"'), "allow")  # the key is in force
        self.assertEqual(self.sh("haiku-scout", 'git commit -m "x"'), "deny")

    def test_editor_writes(self):
        readme = os.path.join(self.proj, "README.md")
        orchestra = (REPORT_PATH, UNIT + "/files/T01/run.rb", UNIT + "/brief.md", SPEC_PATH, ".orchestra/notes.md")
        for tool in ("Edit", "Write"):
            self.assert_writes("haiku-editor", [(tool, "src/app.py", "allow"), (tool, readme, "allow")]
                               + [(tool, path, "deny") for path in orchestra])
        self.assert_writes("haiku-editor", [("NotebookEdit", "analysis.ipynb", "allow"),
                                            ("NotebookEdit", UNIT + "/files/a.ipynb", "deny")])

    def test_editor_shell_writes(self):
        editor = lambda cmd: self.sh("haiku-editor", cmd)
        for path in (REPORT_PATH, UNIT + "/files/T01/out.txt", UNIT + "/brief.md"):
            self.assert_all("deny", editor, ("echo x > " + path, "echo x >> " + path, "echo x | tee " + path,
                                             "cp src/a.py " + path, "rm " + path, "sed -i s/a/b/ " + path))
        self.assert_all("allow", editor, (
            "echo x > src/x.txt",
            "sed -i 's/a/b/' src/app.py",
            "cp %s/brief.md /tmp/orchestra-b.md" % UNIT,
            "cat %s/brief.md" % UNIT,
            "python3 tests/x.py",
            "pytest -q > /tmp/orchestra-out.txt 2>&1",
        ))

    def test_editor_runs_no_category_command(self):
        for key in ("git", "deps", "data", "push", "ssh"):
            with self.subTest(key=key):
                cmd = KEY_COMMANDS[key]
                self.assertEqual(self.sh("haiku-editor", cmd), "deny")
                self.confirmed("Pre-approved: " + key)
                self.assertEqual(self.impl(cmd), "allow")  # the key is in force
                self.assert_refused(GUARD, shell(cmd, "haiku-editor"), "FAILED", cmd)
        self.assertEqual(self.sh("haiku-editor", "RAILS_ENV=production " + KEY_COMMANDS["data"]), "deny")
        self.assertEqual(self.sh("haiku-editor", "claude -p x"), "deny")
        self.assertEqual(self.sh("haiku-editor", "git status"), "allow")


class OrdinarySessionTest(HookTest):
    """A session that is not a conductor session: the guard judges nothing."""

    def test_an_ordinary_session_is_left_alone(self):
        self.set_transcript([request(SONNET)])
        self.assert_all("allow", self.bash, (
            "rm -rf build", "sed -i 's/a/b/' src/app.py", 'git commit -m "x"', "echo hi > src/x.txt",
            FROZEN_B, FROZEN_C, FROZEN_D, 'echo "unterminated', "git push origin main", "npm install",
        ))
        answered = [request(SONNET), response(SONNET)]
        self.set_transcript(answered)
        off_roster = dict(self.main("Write", {"file_path": "src/app.py"}), agent_id="a9", agent_type="general-purpose")
        self.assertEqual(self.verdict(GUARD, off_roster), "allow")
        self.assert_all("allow", self.write_to, ("src/app.py", REPORT_PATH))
        # A confirmed `Conductor writes:` line changes nothing either.
        self.set_transcript([request(FABLE)])
        self.confirmed(WRITES_LINE)
        self.set_transcript(answered)
        self.assert_all("allow", self.writes, ("src/app.py", "src/other.py", REPORT_PATH))
        self.assertEqual(self.bash("sed -i 's/a/b/' src/app.py"), "allow")


if __name__ == "__main__":
    unittest.main(verbosity=1)
