#!/usr/bin/env python3
"""PreToolUse(Edit|Write|NotebookEdit|Bash): enforce who may write what.

Principle 8  The conductor does not implement.
  - Conductor (the main conversation of a session on a Fable model): writes only its own
    documents inside `.orchestra/`. It does not change files from the shell.
  - Read-only subagents: write only their own report and working files under
    `.orchestra/<work>/files/`.
  - Implementation subagents: inside `.orchestra/`, write only their own report and working
    files under `.orchestra/<work>/files/`.
  - The main conversation of a session on any other model is left alone.
Principle 3  Commands that change data, dependencies, repository state, or external
             services run only when the user agreed to their kind of action in advance: the
             work's brief lists it on the `Pre-approved:` line under `## Decision rights`, and
             the user has spoken since the brief last changed. The brief is the one in the
             session's active work folder, the folder whose brief the conductor last read or
             wrote, or for which it last delegated a task.
  - Conductor: data, dependency and git changes that are not pre-approved are refused, with
    the way to get them approved. Pushes, GitHub changes, writing requests to other hosts, ssh
    and deployments that are not pre-approved go to the user for confirmation. A data command
    that names a production environment always goes to the user.
  - Implementation subagents: a command of these kinds that is not pre-approved is refused,
    and the subagent reports BLOCKED. Data commands that name production are always refused.
  - Read-only subagents: commands of these kinds are always refused.
  A command the harness cannot read is refused for the conductor and read-only subagents.
Principles 5 and 6  No role starts a new session with the claude CLI.

The shell check is a tripwire for the common paths. It is not a sandbox.
"""
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402

WHY = {
    "conductor": (
        "This session runs on a Fable model, so you are the conductor, and the conductor does not "
        "implement (Principle 8). Write a task spec and delegate this change to a subagent. You can "
        "write the brief, task specs, review records, and the decision log inside `.orchestra/` "
        "directly with the Write tool. If you have not loaded your instructions yet, invoke the "
        "`orchestra:conductor` skill first."
    ),
    "readonly": (
        "This subagent is read-only. Do not change files. Put any change you think is needed "
        "in your report as a recommendation."
    ),
}
ASK_GIT = "This git command changes repository state, so the user has to confirm it (Principle 3)."
ASK_OUT = "This command reaches outside this machine or is hard to undo, so the user has to confirm it (Principle 3)."
ASK_DATA = "This command looks like it changes data or a schema, so the user has to confirm it (Principle 3)."
DENY_PARSE = (
    "The harness could not read this command, so it cannot tell whether it is safe. Run it again "
    "as simpler, separate commands."
)
DENY_CLAUDE = (
    "Starting a new session with the claude CLI is not allowed (Principles 5 and 6). Delegate "
    "only through the Agent tool and the roster subagents."
)

DEV_OK = {"/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"}
KEYWORDS = {
    "if", "then", "else", "elif", "fi", "do", "done", "while", "until", "for", "case", "esac",
    "{", "}", "!", "function", "select", "in", "time",
}
WRAPPERS = {
    "sudo", "doas", "env", "command", "builtin", "exec", "nice", "ionice", "nohup", "stdbuf",
    "unbuffer", "timeout", "xargs", "setsid",
}
VALUE_OPTS = {
    "sudo": {"-u", "-g", "-C", "-p", "-h", "-U", "-r", "-t"},
    "doas": {"-u", "-C"},
    "env": {"-u", "-C", "-S"},
    "nice": {"-n"},
    "ionice": {"-c", "-n", "-p"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "xargs": {
        "-I", "-n", "-P", "-L", "-d", "-s", "-E", "-a",
        "--max-args", "--max-procs", "--delimiter", "--replace", "--arg-file",
    },
}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
EDITORS = {"ed", "ex", "vi", "vim", "nvim", "nano", "pico", "emacs"}
MUTATE_ALL = {"rm", "rmdir", "unlink", "shred", "touch", "truncate", "chmod", "chown", "chgrp", "mv"}
MUTATE_LAST = {"cp", "install", "rsync", "ln"}
INTERPRETERS = {"python", "python2", "python3", "node", "nodejs", "deno", "bun", "ruby", "perl", "php", "lua"}

# Signs that inline code writes files or runs other commands
WRITE_CODE = re.compile(
    r"""
      open\s*\([^)]*,\s*['"][^'"]*[wax+]
    | \.write(?:_text|_bytes|lines)?\s*\(
    | \bwriteFile(?:Sync)?\b | \bappendFile(?:Sync)?\b | createWriteStream
    | \bfs\.(?:rm|rmSync|unlink|unlinkSync|rename|renameSync|mkdir|mkdirSync|copyFile|cp)\b
    | \bFile\.(?:write|open|delete|rename|unlink)\b | \bFileUtils\. | \bIO\.write\b
    | \bshutil\.
    | \bos\.(?:remove|unlink|rename|replace|rmdir|removedirs|makedirs|mkdir|system|popen|exec\w*|spawn\w*)\b
    | \bsubprocess\b | child_process | \bexecSync\b | \bspawnSync\b
    | \bPath\([^)]*\)\.(?:unlink|rename|replace|touch|mkdir|rmdir)\b
    | \bsystem\s*\( | \bexec\s*\(
    """,
    re.X,
)

# Data and schema changes that are hard to undo
RISKY = re.compile(
    r"\bdb:(?:migrate(?::(?:up|down|redo|reset))?|rollback|drop|reset|setup|seed|schema:load|structure:load)(?![:\w])"
    r"|\bmigrate\s+(?:deploy|reset|dev)\b"
    r"|\balembic\s+(?:upgrade|downgrade)\b"
    r"|\bmanage\.py\s+(?:migrate|flush|loaddata)\b"
    r"|\b(?:drop|truncate)\s+(?:table|database|schema)\b",
    re.I,
)

PRODUCTION = re.compile(
    r"(?<![\w])(?:RAILS_ENV|RACK_ENV|NODE_ENV|APP_ENV|DJANGO_ENV)=[\"']?(?:production|prod)(?!\w)"
    r"|(?<![\w-])(?:-e|--env|--environment)(?:\s+|=)[\"']?(?:production|prod)(?!\w)",
    re.I,
)

KINDS = {
    "data": "changes data or a schema",
    "deps": "changes dependencies or installed packages",
    "git": "changes repository state with git",
    "push": "pushes commits to a remote",
    "github": "creates or changes something on GitHub",
    "http": "sends a request that writes to another host",
    "ssh": "connects to another machine",
    "deploy": "changes a deployment or publishes an image",
}
# A confirmation prompt stops the conductor's whole turn until the user answers; in a real
# overnight session four of them held the work for almost seven hours. A refusal returns at
# once, so the conductor puts the question to the user itself and keeps working on what does
# not depend on it. The outward kinds keep the prompt, because the user's answer to it is the
# explicit permission for that one command.
CONDUCTOR_REFUSED = {"data", "deps", "git"}
DATA_TEXT_ONLY = (
    "If the command only searches for, prints or writes text that names such a command, do that "
    "with the Grep, Read or Write tool instead, without the text on a command line. That needs no "
    "pre-approval."
)
GIT_FILES_DIRECTLY = (
    "To restore, move or delete a file in the working tree, change the file directly, without git."
)
ASK_FOR = {"push": ASK_GIT, "github": ASK_OUT, "http": ASK_OUT, "ssh": ASK_OUT, "deploy": ASK_OUT}

GIT_READONLY = {
    "status", "diff", "log", "show", "blame", "annotate", "grep", "ls-files", "ls-tree", "ls-remote",
    "rev-parse", "rev-list", "describe", "shortlog", "cat-file", "merge-base", "reflog", "whatchanged",
    "name-rev", "for-each-ref", "show-ref", "check-ignore", "count-objects", "version", "help", "var",
    "diff-tree", "diff-files", "diff-index", "range-diff", "fetch",
}

_JS = {"install", "i", "add", "remove", "rm", "uninstall", "update", "up", "upgrade", "link", "unlink", "publish", "dedupe", "prune"}
PKG = {
    "npm": _JS | {"ci", "un"},
    "pnpm": _JS,
    "yarn": _JS | {""},
    "bun": _JS,
    "pip": {"install", "uninstall"},
    "pip3": {"install", "uninstall"},
    "poetry": {"add", "remove", "install", "update", "lock", "publish"},
    "pipenv": {"install", "uninstall", "update", "lock", "sync"},
    "bundle": {"", "install", "add", "remove", "update", "lock"},
    "gem": {"install", "uninstall", "update", "push"},
    "cargo": {"add", "remove", "rm", "install", "uninstall", "update", "publish"},
    "composer": {"install", "require", "remove", "update"},
    "apt": {"install", "remove", "purge", "upgrade", "full-upgrade", "dist-upgrade", "autoremove", "update"},
    "apt-get": {"install", "remove", "purge", "upgrade", "dist-upgrade", "autoremove", "update"},
    "dnf": {"install", "remove", "upgrade", "update"},
    "yum": {"install", "remove", "upgrade", "update"},
    "brew": {"install", "uninstall", "remove", "upgrade", "update", "tap", "untap", "link", "unlink"},
}
PACMAN = {"pacman", "yay", "paru"}

OUTBOUND_ALWAYS = {"ssh", "scp", "sftp"}
OUTBOUND_SUB = {
    "kubectl": {
        "apply", "delete", "patch", "scale", "rollout", "create", "replace", "edit", "drain",
        "cordon", "uncordon", "exec", "label", "annotate", "set",
    },
    "terraform": {"apply", "destroy", "import"},
    "tofu": {"apply", "destroy", "import"},
    "docker": {"push"},
    "podman": {"push"},
    "helm": {"install", "upgrade", "uninstall", "rollback", "delete"},
}
GH_MUT = {
    "pr": {"create", "merge", "close", "reopen", "comment", "review", "edit", "ready", "lock", "unlock"},
    "issue": {"create", "close", "reopen", "comment", "edit", "delete", "transfer", "lock", "unlock", "pin", "unpin"},
    "release": {"create", "delete", "edit", "upload"},
    "repo": {"create", "delete", "fork", "rename", "archive", "unarchive", "edit", "sync"},
    "workflow": {"run", "enable", "disable"},
    "run": {"rerun", "cancel", "delete"},
    "gist": {"create", "delete", "edit"},
    "secret": {"set", "delete"},
    "variable": {"set", "delete"},
    "label": {"create", "edit", "delete", "clone"},
}

# Only options known to take a value belong here: a word taken for a value is not checked as a URL.
CURL_SHORT_VALUE = set("AbcCdDeEFHKmoPQrtTuUwxXyYz")
CURL_LONG_VALUE = {
    "--abstract-unix-socket", "--alt-svc", "--aws-sigv4", "--cacert", "--capath", "--cert",
    "--cert-type", "--ciphers", "--config", "--connect-timeout", "--connect-to", "--continue-at",
    "--cookie", "--cookie-jar", "--create-file-mode", "--data", "--data-ascii", "--data-binary",
    "--data-raw", "--data-urlencode", "--dns-servers", "--dump-header", "--etag-compare",
    "--etag-save", "--expect100-timeout", "--form", "--form-string", "--header", "--hsts",
    "--interface", "--json", "--key", "--key-type", "--limit-rate", "--local-port", "--max-filesize",
    "--max-redirs", "--max-time", "--netrc-file", "--noproxy", "--oauth2-bearer", "--output",
    "--output-dir", "--pass", "--preproxy", "--proxy", "--proxy-header", "--proxy-user", "--quote",
    "--range", "--referer", "--request", "--request-target", "--resolve", "--retry", "--retry-delay",
    "--retry-max-time", "--socks4", "--socks4a", "--socks5", "--socks5-hostname", "--speed-limit",
    "--speed-time", "--stderr", "--time-cond", "--trace", "--trace-ascii", "--unix-socket",
    "--upload-file", "--url", "--url-query", "--user", "--user-agent", "--variable", "--write-out",
}
# With any of these, the URLs on the command line do not show where the request goes.
CURL_ELSEWHERE = {
    "-x", "--proxy", "--preproxy", "--socks4", "--socks4a", "--socks5", "--socks5-hostname",
    "--resolve", "--connect-to", "-K", "--config", "-L", "--location", "--location-trusted",
}
_URL = re.compile(r"^(?:([A-Za-z][A-Za-z0-9+.\-]*)://)?(?:[^@/?#]*@)?(\[[^\]/?#]*\]|[^:/?#]*)(?::\d*)?(?:[/?#].*)?$", re.S)


# ---------------------------------------------------------------- where each role may write

def _home_claude():
    return os.path.realpath(os.path.expanduser("~/.claude"))


def conductor_may_write(path, data):
    home = _home_claude()
    if c.inside(path, os.path.join(home, "plans")):
        return True
    if c.inside(path, os.path.join(home, "projects")) and "/memory/" in path + "/":
        return True
    if c.inside(path, c.project_dir(data)):
        return c.inside(path, c.work_dir(data)) and not c.in_reports(path, data)
    return any(c.inside(path, root) for root in c.temp_roots(data))


def readonly_may_write(path, data):
    if c.inside(path, c.project_dir(data)):
        return c.in_reports(path, data) or c.in_files(path, data)
    return any(c.inside(path, root) for root in c.temp_roots(data))


def impl_may_write(path, data):
    if c.inside(path, c.work_dir(data)):
        return c.in_reports(path, data) or c.in_files(path, data)
    return True


class Approval:
    """What the brief in the session's active work folder pre-approves.

    state: `no-work` (no active work folder), `no-brief`, `no-turn` (no user turn recorded),
    `newer` (the brief changed after the user last spoke), or `confirmed`.
    """

    def __init__(self, unit, listed, state):
        self.unit = unit
        self.listed = listed
        self.state = state
        self.keys = listed if state == "confirmed" else set()

    @property
    def shown(self):
        return "`.orchestra/%s/brief.md`" % os.path.basename(self.unit) if self.unit else "`.orchestra/<work>/brief.md`"


def brief_approval(data):
    unit = c.active_work(data)
    if unit is None:
        return Approval(None, set(), "no-work")
    brief = os.path.join(unit, "brief.md")
    if not os.path.isfile(brief):
        return Approval(unit, set(), "no-brief")
    listed = c.preapproved_keys(c.read_text(brief))
    # Read the time after the text, so a change in between counts as unconfirmed.
    try:
        changed = os.path.getmtime(brief)
    except OSError:
        return Approval(unit, set(), "no-brief")
    turn = c.last_user_turn(data)
    # Stricter than check_brief in guard_agent.py, which lets a brief through when no user turn
    # is recorded. A pre-approved action runs with nobody present to confirm it, so it must not
    # be approved because a record is missing.
    if turn is None:
        return Approval(unit, listed, "no-turn")
    if changed > turn:
        return Approval(unit, listed, "newer")
    return Approval(unit, listed, "confirmed")


class Ctx:
    def __init__(self, data, role):
        self.data = data
        self.role = role
        self._approval = None

    @property
    def approval(self):
        if self._approval is None:
            self._approval = brief_approval(self.data)
        return self._approval

    def category(self, key, production=False):
        """The verdict on a command of one pre-approval category, for this role."""
        kind = "This command %s, which the pre-approval key `%s` covers" % (KINDS[key], key)
        text_only = " " + DATA_TEXT_ONLY if key == "data" else ""
        if self.role == "readonly":
            return ("deny", "This subagent is read-only, and a read-only subagent does not run a command "
                            "that %s. Put the command in your report as a recommendation.%s"
                            % (KINDS[key], text_only))
        if production:
            if self.role == "conductor":
                return ("ask", ASK_DATA + " It names a production environment, which the `data` key "
                                          "does not cover.")
            return ("deny", "This command %s and names a production environment, which no pre-approval "
                            "covers.%s Do not look for another way to run the data command itself. "
                            "Report `BLOCKED` with the command and why the task needs it."
                            % (KINDS[key], text_only))
        if key in self.approval.keys:
            return None
        if self.role == "impl":
            if key == "git":
                return ("deny", "%s. The work's brief does not pre-approve it. %s Report `BLOCKED` only "
                                "when the task needs the git command itself, with the command and why "
                                "the task needs it." % (kind, GIT_FILES_DIRECTLY))
            target = "the data command itself" if key == "data" else "it"
            return ("deny", "%s. The work's brief does not pre-approve it.%s Do not look for another way "
                            "to run %s. Report `BLOCKED` with the command and why the task needs it."
                            % (kind, text_only, target))
        if key in CONDUCTOR_REFUSED:
            return ("deny", "%s (Principle 3).%s %s" % (kind, text_only, self.conductor_refusal(key)))
        return ("ask", "%s It is pre-approved when the brief's `Pre-approved:` line lists `%s`."
                       % (ASK_FOR[key], key))

    def conductor_refusal(self, key):
        a = self.approval
        ask_user = ("ask them, and run the command again after they answer. Meanwhile, carry on with "
                    "the work that does not depend on the answer.")
        add_key = ("add `%s` to the `Pre-approved:` line under `## Decision rights` in the brief, tell "
                   "the user what will run and why, " % key) + ask_user
        if a.state == "no-work":
            return ("This session has no active work folder, so no brief pre-approves it. Read or write "
                    "the work's brief %s first. If it does not pre-approve `%s`, %s" % (a.shown, key, add_key))
        if key in a.listed and a.state == "newer":
            return ("The brief %s lists `%s`, but it was written or changed after the user last spoke, "
                    "so the user has not confirmed this version of the brief. Show the user the change "
                    "and tell them what will run and why, " % (a.shown, key)) + ask_user
        if key in a.listed and a.state == "no-turn":
            return ("The brief %s lists `%s`, but no user turn is recorded for this session, so the "
                    "user has not confirmed this version of the brief. Show the user the brief and tell "
                    "them what will run and why, " % (a.shown, key)) + ask_user
        return "The brief %s does not pre-approve it. To run it, %s" % (a.shown, add_key)

    def may_write(self, text):
        if text in DEV_OK or text.startswith("/dev/fd/"):
            return True
        if not text or "__SUB__" in text:
            return False  # the target is the output of a command substitution, so it is unknown
        path = c.resolve(text, self.data)
        if self.role == "conductor":
            return conductor_may_write(path, self.data)
        if self.role == "readonly":
            return readonly_may_write(path, self.data)
        return impl_may_write(path, self.data)

    @property
    def why(self):
        return WHY.get(self.role, "")


# ---------------------------------------------------------------- reading shell commands

_HEREDOC = re.compile(r"(?<!<)<<(?!<)-?[ \t]*(?:'([^'\n]+)'|\"([^\"\n]+)\"|\\?([A-Za-z_]\w*))")


def strip_heredocs(cmd):
    """Take heredoc bodies out. Returns (command without bodies, [body, ...])."""
    lines = cmd.split("\n")
    kept, bodies = [], []
    i = 0
    while i < len(lines):
        line = lines[i]
        kept.append(line)
        i += 1
        for m in _HEREDOC.finditer(line):
            tag = m.group(1) or m.group(2) or m.group(3)
            j = i
            while j < len(lines) and lines[j].strip() != tag:
                j += 1
            if j < len(lines):  # treat it as a heredoc only when the terminator is found
                bodies.append("\n".join(lines[i:j]))
                i = j + 1
    return "\n".join(kept), bodies


def split_lines(cmd):
    """Split on newlines outside quotes. A trailing backslash joins lines."""
    lines, cur, quote = [], [], None
    i, n = 0, len(cmd)
    while i < n:
        ch = cmd[i]
        if quote is None:
            if ch == "\\" and i + 1 < n:
                if cmd[i + 1] == "\n":
                    i += 2
                    continue
                cur.append(cmd[i:i + 2])
                i += 2
                continue
            if ch in "'\"":
                quote = ch
            elif ch == "\n":
                lines.append("".join(cur))
                cur = []
                i += 1
                continue
        elif quote == "'":
            if ch == "'":
                quote = None
        else:
            if ch == "\\" and i + 1 < n:
                cur.append(cmd[i:i + 2])
                i += 2
                continue
            if ch == '"':
                quote = None
        cur.append(ch)
        i += 1
    lines.append("".join(cur))
    return lines


def _quote_end(text, j, quote):
    """Index just past the quote that closes the one opened before `j`."""
    n = len(text)
    if quote == "'":
        k = text.find("'", j)
        if k < 0:
            raise ValueError("unclosed single quote")
        return k + 1
    while j < n:
        ch = text[j]
        if ch == "\\":
            j += 2
        elif ch == '"':
            return j + 1
        elif text.startswith("$(", j):
            j = _paren_end(text, j + 2)
        elif ch == "`":
            j = _backtick_end(text, j + 1)
        else:
            j += 1
    raise ValueError("unclosed double quote")


def _backtick_end(text, j):
    """Index just past the backtick that closes the one opened before `j`."""
    n = len(text)
    while j < n:
        if text[j] == "\\":
            j += 2
        elif text[j] == "`":
            return j + 1
        else:
            j += 1
    raise ValueError("unclosed backtick")


def _paren_end(text, j):
    """Index just past the `)` that closes a `$(` opened before `j`. Quoted text is skipped."""
    depth, n = 1, len(text)
    while j < n:
        ch = text[j]
        if ch == "\\":
            j += 2
            continue
        if ch in "'\"":
            j = _quote_end(text, j + 1, ch)
            continue
        if ch == "`":
            j = _backtick_end(text, j + 1)
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if not depth:
                return j + 1
        j += 1
    raise ValueError("unclosed $(")


def extract_substitutions(line):
    """Pull out the commands inside `$(...)` and backticks.

    Returns (line with placeholders, [inner command, ...]).
    """
    out, subs = [], []
    i, n = 0, len(line)
    in_single = in_double = False
    while i < n:
        ch = line[i]
        if in_single:
            out.append(ch)
            if ch == "'":
                in_single = False
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            out.append(line[i:i + 2])
            i += 2
            continue
        if ch == '"':
            in_double = not in_double
        elif ch == "'" and not in_double:
            in_single = True
        elif ch == "$" and line.startswith("$(", i) and not line.startswith("$((", i):
            j = _paren_end(line, i + 2)
            subs.append(line[i + 2:j - 1])
            out.append("__SUB__")
            i = j
            continue
        elif ch == "`":
            j = _backtick_end(line, i + 1)
            subs.append(line[i + 1:j - 1])
            out.append("__SUB__")
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out), subs


def mask_quotes(line):
    """Replace quoted parts with placeholders, so a `>` or `|` inside quotes is not read as an operator."""
    out, table = [], []
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if ch == "\\" and i + 1 < n:
            out.append(line[i:i + 2])
            i += 2
            continue
        if ch == "'":
            j = line.find("'", i + 1)
            if j < 0:
                raise ValueError("unclosed single quote")
            table.append(line[i + 1:j])
            out.append("__Q%d__" % (len(table) - 1))
            i = j + 1
            continue
        if ch == '"':
            j, buf = i + 1, []
            while j < n and line[j] != '"':
                if line[j] == "\\" and j + 1 < n and line[j + 1] in '"\\$`\n':
                    buf.append(line[j + 1])
                    j += 2
                else:
                    buf.append(line[j])
                    j += 1
            if j >= n:
                raise ValueError("unclosed double quote")
            table.append("".join(buf))
            out.append("__Q%d__" % (len(table) - 1))
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out), table


_Q = re.compile(r"__Q(\d+)__")


def tokenize(masked, table):
    """Returns [(masked token, real text, is it an operator)]."""
    lex = shlex.shlex(masked.replace("\\;", " __SEMI__ "), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    tokens = []
    for raw in lex:
        is_op = bool(raw) and all(ch in "();<>|&" for ch in raw)
        text = _Q.sub(lambda m: table[int(m.group(1))], raw)
        tokens.append((raw, text, is_op))
    return tokens


class Seg:
    def __init__(self):
        self.words = []
        self.raws = []
        self.redirs = []
        self.targets = []


def build_segments(tokens):
    """Split into simple commands at the operators, and collect output redirection targets."""
    segs, cur = [], Seg()
    i, n = 0, len(tokens)

    def next_word(k):
        return tokens[k + 1][1] if k + 1 < n and not tokens[k + 1][2] else None

    def drop_fd():
        # A file descriptor number in front of a redirection, like the 2 in `2>file`
        if cur.words and re.fullmatch(r"\d{1,2}", cur.words[-1]):
            cur.words.pop()
            cur.raws.pop()

    while i < n:
        raw, text, is_op = tokens[i]
        if not is_op:
            cur.words.append(text)
            cur.raws.append(raw)
            i += 1
            continue
        target = next_word(i)
        if target is not None and text in (">", ">>", ">|", "&>", "&>>", ">&", "<&", "<", "<<", "<<<", "<>"):
            cur.targets += [target, tokens[i + 1][0]]
        if text in (">", ">>", ">|", "&>", "&>>"):
            drop_fd()
            if target is not None:
                cur.redirs.append(target)
                i += 2
            else:
                i += 1
            continue
        if text in (">&", "<&"):
            drop_fd()
            if target is not None:
                if text == ">&" and not re.fullmatch(r"\d+|-", target):
                    cur.redirs.append(target)
                i += 2
            else:
                i += 1
            continue
        if text in ("<", "<<", "<<<", "<>"):
            drop_fd()
            i += 2 if target is not None else 1
            continue
        if cur.words or cur.redirs or cur.targets:
            segs.append(cur)
        cur = Seg()
        i += 1
    if cur.words or cur.redirs or cur.targets:
        segs.append(cur)
    return segs


def read_line(line):
    """Parse one line of a command. Raises ValueError when it cannot be read.

    Returns (line with placeholders, [inner command, ...], tokens, tokens of a trailing comment).
    The first item of each token here is its text with the quotes kept.
    """
    line, subs = extract_substitutions(line)
    masked, table = mask_quotes(line)
    tokens = [(_Q.sub(lambda m: '"%s"' % table[int(m.group(1))], raw), text, is_op)
              for raw, text, is_op in tokenize(masked, table)]
    for i, (raw, _text, is_op) in enumerate(tokens):
        if not is_op and raw.startswith("#"):
            return line, subs, tokens[:i], tokens[i:]
    return line, subs, tokens, []


_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def unwrap(words):
    """Strip leading shell keywords, variable assignments, and wrappers such as sudo, env, and xargs."""
    via_xargs = False
    w = list(words)
    while w:
        head = w[0]
        if head in KEYWORDS or _ASSIGN.match(head):
            w.pop(0)
            continue
        name = os.path.basename(head)
        if name not in WRAPPERS:
            break
        w.pop(0)
        if name == "xargs":
            via_xargs = True
        takes_value = VALUE_OPTS.get(name, ())
        while w and (w[0].startswith("-") or (name == "env" and _ASSIGN.match(w[0]))):
            opt = w.pop(0)
            if opt in takes_value and w:
                w.pop(0)
        if name == "timeout" and w:
            w.pop(0)  # the duration argument
    return w, via_xargs


def worst(results):
    results = [r for r in results if r]
    for kind in ("deny", "ask"):
        for r in results:
            if r[0] == kind:
                return r
    return None


def first_plain(args, start=0):
    plain = [a for a in args if not a.startswith("-")]
    return plain[start] if len(plain) > start else ""


# ---------------------------------------------------------------- per-command rules

def claude_rule(args):
    if args:
        if args[0] in ("--version", "-v", "--help", "-h", "doctor"):
            return None
        if args[0] in ("plugin", "plugins") and len(args) > 1 and args[1] in ("validate", "list", "--help"):
            return None
    return ("deny", DENY_CLAUDE)


def git_rule(args, ctx):
    a = list(args)
    while a and a[0].startswith("-"):
        opt = a.pop(0)
        if opt in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") and a:
            a.pop(0)
    if not a:
        return None
    sub, rest = a[0], a[1:]
    if sub in GIT_READONLY:
        return None
    if sub == "push":
        return ctx.category("push")
    changes = ctx.category("git")
    if sub == "branch":
        if any(re.match(r"^-[dDmMcCfu]", x) or x in ("--delete", "--move", "--copy", "--force",
               "--set-upstream-to", "--unset-upstream", "--edit-description") for x in rest):
            return changes
        listing = ("--list", "-l", "--contains", "--no-contains", "--merged", "--no-merged", "--points-at")
        if all(x.startswith("-") for x in rest) or any(x in listing for x in rest):
            return None
        return changes
    if sub == "tag":
        if not rest or (any(x in ("-l", "--list", "-n", "--contains", "--points-at", "--merged", "-v", "--verify")
                            for x in rest) and not any(x in ("-d", "--delete", "-a", "-s", "-f") for x in rest)):
            return None
        return changes
    if sub == "remote":
        return None if (not rest or rest[0] in ("-v", "--verbose", "show", "get-url")) else changes
    if sub == "stash":
        return None if (rest and rest[0] in ("list", "show")) else changes
    if sub == "worktree":
        return None if (rest and rest[0] == "list") else changes
    if sub == "config":
        reading = ("--get", "--get-all", "--list", "-l", "--get-regexp", "--show-origin", "--show-scope")
        return None if any(x in reading for x in rest) else changes
    if sub == "submodule":
        return None if (not rest or rest[0] in ("status", "summary")) else changes
    if sub == "notes":
        return None if (not rest or rest[0] in ("list", "show")) else changes
    return changes


def inline_codes(name, args):
    """Inline code passed to an interpreter as an argument."""
    codes = []
    for i, a in enumerate(args[:-1]):
        nxt = args[i + 1]
        if name.startswith("python") and a == "-c":
            codes.append(nxt)
        elif name in ("node", "nodejs", "deno", "bun") and a in ("-e", "--eval", "-p", "--print", "eval"):
            codes.append(nxt)
        elif name in ("ruby", "perl", "lua") and re.fullmatch(r"-[A-Za-z0-9]*[eE]", a):
            codes.append(nxt)
        elif name == "php" and a == "-r":
            codes.append(nxt)
    return codes


def find_rule(args, ctx):
    """File-writing flags of find. Commands run by -exec are judged by find_exec_rule."""
    if "-delete" in args:
        return ("deny", "`find -delete` deletes files. " + ctx.why)
    for flag in ("-fprint", "-fprint0", "-fprintf", "-fls"):
        if flag in args:
            k = args.index(flag)
            if k + 1 >= len(args) or not ctx.may_write(args[k + 1]):
                return ("deny", "`find %s` writes to a file. %s" % (flag, ctx.why))
    return None


def find_exec_rule(args, ctx, heredocs, depth):
    results = []
    i = 0
    while i < len(args):
        if args[i] in ("-exec", "-execdir", "-ok", "-okdir"):
            j, sub = i + 1, []
            while j < len(args) and args[j] not in ("__SEMI__", "+"):
                sub.append(args[j])
                j += 1
            seg = Seg()
            seg.words = ["xargs"] + sub  # treat it as a run whose targets are unknown
            results.append(judge_segment(seg, ctx, heredocs, depth + 1))
            i = j
        i += 1
    return worst(results)


class CurlCall:
    """What a curl command line does: the files it writes, whether it writes to a server, and where."""

    def __init__(self, args):
        self.outputs = []
        self.remote_name = False
        self.writes = False
        self.elsewhere = False
        self.urls = []
        i = 0
        while i < len(args):
            a = args[i]
            nxt = args[i + 1] if i + 1 < len(args) else None
            if a == "--":
                self.urls.extend(args[i + 1:])
                break
            if a.startswith("--"):
                opt, eq, val = a.partition("=")
                takes = opt in CURL_LONG_VALUE and not eq
                self._option(opt, val if eq else (nxt if takes else None))
                i += 2 if takes else 1
                continue
            if a.startswith("-") and len(a) > 1:
                used_next = False
                for k in range(1, len(a)):
                    flag = a[k]
                    if flag in CURL_SHORT_VALUE:
                        attached = a[k + 1:]
                        used_next = not attached
                        self._option("-" + flag, attached or nxt)
                        break
                    self._option("-" + flag, None)
                i += 2 if used_next else 1
                continue
            self.urls.append(a)
            i += 1

    def _option(self, opt, value):
        if opt in ("-o", "--output"):
            self.outputs.append(value)
        elif opt in ("-O", "-J", "--remote-name", "--remote-name-all", "--remote-header-name"):
            self.remote_name = True
        elif opt in ("-X", "--request"):
            if value and value.upper() in ("POST", "PUT", "PATCH", "DELETE"):
                self.writes = True
        elif opt in ("-d", "-F", "-T") or opt.startswith(("--data", "--form", "--upload-file", "--json")):
            self.writes = True
        elif opt == "--url":
            self.urls.append(value or "")
        if opt in CURL_ELSEWHERE:
            self.elsewhere = True


def is_loopback_url(text):
    """True when the URL names a loopback host literally: localhost, 127.x.x.x or [::1]."""
    m = _URL.match(text or "")
    if not m:
        return False
    scheme, host = (m.group(1) or "").lower(), m.group(2).lower()
    if scheme not in ("", "http", "https"):
        return False
    if host == "localhost" or host == "[::1]":
        return True
    parts = host.split(".")
    return (len(parts) == 4 and parts[0] == "127"
            and all(p.isdigit() and len(p) <= 3 and int(p) <= 255 for p in parts))


def curl_file_rule(call, ctx):
    for target in call.outputs:
        if target is None or not ctx.may_write(target):
            return ("deny", "`curl -o` creates a file. " + ctx.why)
    if call.remote_name:
        return ("deny", "`curl -O` creates a file in the current folder. " + ctx.why)
    return None


def curl_http_rule(call, ctx):
    if not call.writes:
        return None
    if not call.elsewhere and call.urls and all(is_loopback_url(u) for u in call.urls):
        return None
    return ctx.category("http")


def wget_rule(args, ctx):
    if "--spider" in args:
        return None
    for i, a in enumerate(args):
        if re.fullmatch(r"-[A-Za-z]*O-", a) or a == "--output-document=-":
            return None
        if re.fullmatch(r"-[A-Za-z]*O", a) or a == "--output-document":
            target = args[i + 1] if i + 1 < len(args) else ""
            if target == "-" or ctx.may_write(target):
                return None
            break
        if a.startswith("--output-document="):
            if ctx.may_write(a.split("=", 1)[1]):
                return None
            break
    return ("deny", "`wget` downloads and saves a file. " + ctx.why)


def pkg_changes(name, args):
    sub = first_plain(args)
    if name in PACMAN:
        return any(re.match(r"^-(S(?![silg])|R|U)", a) for a in args)
    if name == "go":
        return sub in ("get", "install") or (sub == "mod" and first_plain(args, 1) in ("tidy", "download", "vendor", "init", "edit"))
    if name == "uv":
        return sub in ("add", "remove", "sync", "lock", "tool") or (sub == "pip" and first_plain(args, 1) in ("install", "uninstall", "sync"))
    return sub in PKG[name]


def outbound_key(name, args):
    """The pre-approval key of an outbound command, or None when it changes nothing outside."""
    if name in OUTBOUND_ALWAYS:
        return "ssh"
    if name == "gh":
        group, verb = first_plain(args), first_plain(args, 1)
        if verb in GH_MUT.get(group, ()):
            return "github"
        if group == "api":
            for i, a in enumerate(args):
                if a in ("-X", "--method") and i + 1 < len(args) and args[i + 1].upper() != "GET":
                    return "github"
                if a in ("-f", "-F", "--field", "--raw-field", "--input"):
                    return "github"
        return None
    if first_plain(args) in OUTBOUND_SUB[name]:
        return "deploy"
    return None


def category_rule(name, args, ctx, heredocs, depth):
    """Rules for the pre-approval categories. They apply to every role."""
    results = []
    if name == "git":
        results.append(git_rule(args, ctx))
    if name == "find":
        results.append(find_exec_rule(args, ctx, heredocs, depth))
    if name == "curl":
        results.append(curl_http_rule(CurlCall(args), ctx))
    if (name in PKG or name in PACMAN or name in ("go", "uv")) and pkg_changes(name, args):
        results.append(ctx.category("deps"))
    if name in OUTBOUND_ALWAYS or name in OUTBOUND_SUB or name == "gh":
        key = outbound_key(name, args)
        if key:
            results.append(ctx.category(key))
    return worst(results)


def file_rule(name, args, via_xargs, ctx, heredocs):
    """Rules for commands that write files. They apply to the conductor and read-only subagents."""
    plain = [a for a in args if not a.startswith("-")]

    if name in MUTATE_ALL or name in MUTATE_LAST:
        paths = plain
        if name in ("chmod", "chown", "chgrp"):
            paths = paths[1:]
        if name in MUTATE_LAST:
            paths = paths[-1:]
        if via_xargs or not paths or not all(ctx.may_write(p) for p in paths):
            return ("deny", "`%s` changes files. %s" % (name, ctx.why))
        return None
    if name == "dd":
        for a in args:
            if a.startswith("of=") and not ctx.may_write(a[3:]):
                return ("deny", "`dd of=` writes to a file. " + ctx.why)
        return None
    if name == "patch":
        if "--dry-run" in args:
            return None
        return ("deny", "`patch` changes files. " + ctx.why)
    if name in ("sed", "gsed"):
        if any(a == "--in-place" or a.startswith("--in-place=")
               or (not a.startswith("--") and re.match(r"^-[A-Za-z]*i", a)) for a in args):
            return ("deny", "`sed -i` edits files in place. " + ctx.why)
        return None
    if name in ("awk", "gawk", "mawk"):
        if ("-i" in args and "inplace" in args) or "-iinplace" in args:
            return ("deny", "`awk -i inplace` edits files in place. " + ctx.why)
        if any(re.search(r">>?\s*\"", a) for a in plain):
            return ("deny", "This awk script appears to write to a file. " + ctx.why)
        return None
    if name == "tee":
        if not all(ctx.may_write(p) for p in plain):
            return ("deny", "`tee` writes to a file here. " + ctx.why)
        return None
    if name in EDITORS:
        return ("deny", "`%s` is a file editor. %s" % (name, ctx.why))
    if name in INTERPRETERS or re.fullmatch(r"python\d(\.\d+)?", name):
        if name == "perl" and any(
            not a.startswith(("--", "-M", "-I")) and re.match(r"^-[A-Za-z0-9]*i", a) for a in args
        ):
            return ("deny", "`perl -i` edits files in place. " + ctx.why)
        codes = inline_codes(name, args)
        if heredocs and (not plain or "-" in args):
            codes += heredocs
        if any(WRITE_CODE.search(code) for code in codes):
            return ("deny", "The inline code appears to write files or run other commands. " + ctx.why)
        return None
    if name == "find":
        return find_rule(args, ctx)
    if name == "curl":
        return curl_file_rule(CurlCall(args), ctx)
    if name == "wget":
        return wget_rule(args, ctx)
    return None


def tool_rule(name, args, via_xargs, ctx, heredocs, depth):
    """Per-command rules. An implementation subagent gets only the category rules."""
    results = [category_rule(name, args, ctx, heredocs, depth)]
    if ctx.role != "impl":
        results.append(file_rule(name, args, via_xargs, ctx, heredocs))
    return worst(results)


# ---------------------------------------------------------------- the data rule

# A program that prints its text, such as echo, printf, cat, sed or awk, is not a search here,
# because its output can be piped into a shell, where the text runs. A search program counts as
# one only without an option that runs another program, such as `rg --pre` or the pager of
# `git grep -O`, because that program could be the data command its arguments name.
GREPS = {"grep", "egrep", "fgrep"}
RG_RUNS = {"--pre", "--hostname-bin"}
GIT_SEARCH = {"grep", "log", "show", "diff"}
FIND_EXEC = {"-exec", "-execdir", "-ok", "-okdir"}
# Options of a wrapper that take no value. Before the program, any other option might take the
# next word as its value, and that word would then not be the program.
FLAG_OPTS = {
    "sudo": {"-E", "-H", "-n", "-S", "-k", "--preserve-env", "--set-home", "--non-interactive", "--stdin"},
    "env": {"-", "-i", "-0", "-v", "--ignore-environment", "--null", "--debug"},
    "xargs": {"-0", "-r", "-t", "-x", "--null", "--no-run-if-empty", "--verbose", "--exit"},
    "timeout": {"-v", "--verbose", "--preserve-status", "--foreground"},
    "ionice": {"-t", "--ignore"},
    "setsid": {"-c", "-f", "-w", "--ctty", "--fork", "--wait"},
    "command": {"-p"},
}


def _long_option(arg, full, shortest):
    """Whether `arg` is the long option `full`, written whole or shortened to at least `shortest`."""
    name = arg.split("=", 1)[0]
    return name.startswith(shortest) and full.startswith(name)


def git_searches(args):
    a = list(args)
    while a and a[0].startswith("-"):
        opt = a.pop(0)
        if opt == "-C" and a:
            a.pop(0)
        elif opt not in ("--no-pager", "-P"):
            return False
    if not a or a[0] not in GIT_SEARCH:
        return False
    sub, rest = a[0], a[1:]
    for x in rest:
        if _long_option(x, "--ext-diff", "--ex"):
            return False
        # git takes a long option shortened to a unique prefix, and -O inside a bundle of short
        # options, as in -nO<pager>
        if sub == "grep" and (_long_option(x, "--open-files-in-pager", "--op") or re.match(r"-[^-]*O", x)):
            return False
    return True


def is_search(word, args):
    name = os.path.basename(word)
    if word != name and not word.startswith("/"):
        return False  # a relative path such as ./grep names a local program
    if name in GREPS:
        return True
    if name == "rg":
        return not any(a.split("=", 1)[0] in RG_RUNS for a in args)
    if name == "git":
        return git_searches(args)
    return False


def program_is_certain(lead):
    """Whether the word after the stripped words is the program the wrappers run."""
    if not lead:
        return True
    prev = lead[-1]
    if not prev.startswith("-") or prev == "--" or (prev.startswith("--") and "=" in prev):
        return True
    wrapper = next((os.path.basename(w) for w in reversed(lead) if os.path.basename(w) in WRAPPERS), "")
    if prev in FLAG_OPTS.get(wrapper, ()):
        return True
    return not prev.startswith("--") and len(prev) > 2 and prev[:2] in VALUE_OPTS.get(wrapper, ())


def counted_mask(words):
    """Which words of a simple command the data rule reads.

    Returns ([True for each word it reads, False for each it leaves out], whether any were left out).
    """
    rest, _via_xargs = unwrap(words)
    k = len(words) - len(rest)
    if not rest or not program_is_certain(words[:k]):
        return [True] * len(words), False
    if is_search(rest[0], rest[1:]):
        return [True] * k + [False] * len(rest), True
    if os.path.basename(rest[0]) != "find":
        return [True] * len(words), False
    mask, searched = [True] * (k + 1), False
    args, i = rest[1:], 0
    while i < len(args):
        mask.append(True)
        if args[i] in FIND_EXEC:
            j = i + 1
            while j < len(args) and args[j] not in ("__SEMI__", ";", "+"):
                j += 1
            inner, inner_searched = counted_mask(args[i + 1:j])
            mask += inner
            searched = searched or inner_searched
            i = j
            continue
        i += 1
    return mask, searched


def data_hit(cmd, depth=0):
    """Whether the command names a data command anywhere but in a search program's arguments.

    Returns (hit, whether any search program's arguments were left out).
    """
    if depth > 5:
        return bool(RISKY.search(cmd)), False
    body, bodies = strip_heredocs(cmd)
    pieces, searched = [], False
    for line in split_lines(body):
        if not line.strip():
            continue
        try:
            line, subs, tokens, comment = read_line(line)
        except ValueError:
            pieces.append(line)
            continue
        for inner in subs:
            hit, inner_searched = data_hit(inner, depth + 1)
            if hit:
                return True, True
            searched = searched or inner_searched
        line_searched = False
        for seg in build_segments(tokens):
            mask, seg_searched = counted_mask(seg.words)
            pieces.append(" ".join([w for w, m in zip(seg.words, mask) if m] + seg.targets))
            # Removing quotes can join a name to the next word, as in db:migrate"VERSION=1", and
            # the shell may still run it, so the words as written count as well.
            pieces.append(" ".join(r for r, m in zip(seg.raws, mask) if m))
            line_searched = line_searched or seg_searched
        pieces.append(" ".join(text for _raw, text, _op in comment))
        pieces.append(" ".join(raw for raw, _text, _op in comment))
        if not line_searched:
            pieces.append(line)
        searched = searched or line_searched
    pieces.extend(bodies)
    if RISKY.search("\n".join(pieces)):
        return True, searched
    if not searched and RISKY.search(cmd):
        return True, False
    return False, searched


def judge_segment(seg, ctx, heredocs, depth):
    words, via_xargs = unwrap(seg.words)
    name = os.path.basename(words[0]) if words else ""
    args = words[1:]
    results = []

    # Applies to every role: nested shells and the claude CLI
    if name in SHELLS:
        code = None
        for i, a in enumerate(args[:-1]):
            if re.fullmatch(r"-[A-Za-z]*c", a):
                code = args[i + 1]
                break
        if code is not None:
            results.append(judge_command(code, ctx, depth + 1))
        elif heredocs and not any(not a.startswith("-") for a in args):
            results.extend(judge_command(h, ctx, depth + 1) for h in heredocs)
    elif name == "eval":
        results.append(judge_command(" ".join(args), ctx, depth + 1))
    elif name == "claude":
        results.append(claude_rule(args))

    if ctx.role != "impl":
        for target in seg.redirs:
            if not ctx.may_write(target):
                results.append(("deny", "Redirecting output to `%s` writes a file. %s" % (target, ctx.why)))
                break
    if name:
        results.append(tool_rule(name, args, via_xargs, ctx, heredocs, depth))
    return worst(results)


def judge_command(cmd, ctx, depth=0):
    """Judge a whole command. Returns None (pass) or ("deny" | "ask", reason)."""
    if depth > 5:
        return None if ctx.role == "impl" else ("deny", DENY_PARSE)
    results = []
    if data_hit(cmd, depth)[0]:
        # Nothing lets a data command through because its text looks like it targets a test or
        # throwaway database. In a real project an environment assignment placed in front of a
        # wrapper script was dropped before the command ran, and a schema load reached a remote
        # database, so the command text does not prove the target. A sign of production in the
        # text counts only the other way: it takes the command out of the `data` key.
        results.append(ctx.category("data", production=bool(PRODUCTION.search(cmd))))
    body, heredocs = strip_heredocs(cmd)
    for line in split_lines(body):
        if not line.strip():
            continue
        try:
            _line, subs, tokens, _comment = read_line(line)
        except ValueError:
            if ctx.role != "impl":
                results.append(("deny", DENY_PARSE))
            continue
        for inner in subs:
            results.append(judge_command(inner, ctx, depth + 1))
        for seg in build_segments(tokens):
            results.append(judge_segment(seg, ctx, heredocs, depth))
    return worst(results)


# ---------------------------------------------------------------- entry point

_REPORTS_IN_COMMAND = re.compile(r"\.orchestra/[^\s/'\"]+/reports")


def handle_bash(cmd, data, role):
    if role == "conductor" and _REPORTS_IN_COMMAND.search(cmd):
        c.deny(
            "Do not handle the reports folder from the shell (Principle 7). Write the review "
            "record first, then open the report with the Read tool."
        )
    result = judge_command(cmd, Ctx(data, role))
    if result is None:
        c.allow()
    kind, reason = result
    if kind == "deny":
        c.deny(reason)
    c.ask(reason)


def handle_write(path_text, data, role):
    path = c.resolve(path_text, data)
    if role == "conductor":
        if conductor_may_write(path, data):
            if c.inside(path, c.work_dir(data)):
                c.ensure_gitignore(data)
            brief_of = c.brief_unit(path, data)
            if brief_of:
                c.set_active_work(data, brief_of)
            c.allow()
        if c.in_reports(path, data):
            c.deny("Reports are written by subagents. The conductor cannot write or edit a report (Principle 7).")
        c.deny("You cannot edit `%s` directly. %s" % (path_text, WHY["conductor"]))
    if role == "readonly":
        if readonly_may_write(path, data):
            c.allow()
        c.deny(
            "You cannot edit `%s`. %s The only files you may write are your own report in "
            "`.orchestra/<work>/reports/` and the working files of your task under "
            "`.orchestra/<work>/files/`." % (path_text, WHY["readonly"])
        )
    if impl_may_write(path, data):
        c.allow()
    c.deny(
        "Inside `.orchestra/`, a subagent may write only its own report in "
        "`.orchestra/<work>/reports/` and the working files of its task under "
        "`.orchestra/<work>/files/`. The brief, the task specs, and the review records belong "
        "to the conductor."
    )


def main():
    data = c.read_input()
    tool = data.get("tool_name") or ""
    ti = data.get("tool_input") or {}

    if c.is_subagent(data):
        name = c.bare(data.get("agent_type"))
        if name not in c.ROSTER:
            c.allow()
        role = "readonly" if c.ROSTER[name]["kind"] == "readonly" else "impl"
    elif c.main_is_conductor(data):
        role = "conductor"
    else:
        c.allow()

    try:
        if tool in ("Edit", "Write", "NotebookEdit"):
            path_text = ti.get("file_path") or ti.get("notebook_path")
            if not path_text:
                c.allow()
            handle_write(path_text, data, role)
        elif isinstance(ti.get("command"), str):
            handle_bash(ti["command"], data, role)
    except SystemExit:
        raise
    except Exception as exc:  # a fault in the guard does not block the work, but a person confirms it
        if role != "impl":
            c.ask(
                "The guard script failed (%s) and could not check this call. The user has to "
                "confirm it." % type(exc).__name__
            )
    c.allow()


if __name__ == "__main__":
    main()
