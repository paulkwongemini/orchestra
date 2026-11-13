#!/usr/bin/env python3
"""PreToolUse(Edit|Write|NotebookEdit|Bash): enforce who may write what.

Principle 8  The conductor does not implement.
  - Conductor (the main conversation of a session on a Fable model): writes only its own
    documents inside `.orchestra/`. It does not change files from the shell.
  - Read-only subagents: write only their own report.
  - Implementation subagents: inside `.orchestra/`, write only their own report.
  - The main conversation of a session on any other model is left alone.
Principle 3  Commands that change repository state, dependencies, or external
             services go to the user for confirmation.
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
ASK_PKG = "This command changes dependencies or installed packages, so the user has to confirm it (Principle 3)."
ASK_OUT = "This command reaches outside this machine or is hard to undo, so the user has to confirm it (Principle 3)."
ASK_DATA = "This command looks like it changes data or a schema, so the user has to confirm it (Principle 3)."
ASK_PARSE = "The harness could not parse this command, so it cannot tell whether it is safe. The user has to confirm it."
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
        return c.in_reports(path, data)
    return any(c.inside(path, root) for root in c.temp_roots(data))


def impl_may_write(path, data):
    if c.inside(path, c.work_dir(data)):
        return c.in_reports(path, data)
    return True


class Ctx:
    def __init__(self, data, role):
        self.data = data
        self.role = role

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
            depth, j = 1, i + 2
            while j < n and depth:
                if line[j] == "(":
                    depth += 1
                elif line[j] == ")":
                    depth -= 1
                j += 1
            if depth:
                raise ValueError("unclosed $(")
            subs.append(line[i + 2:j - 1])
            out.append("__SUB__")
            i = j
            continue
        elif ch == "`":
            j = line.find("`", i + 1)
            if j < 0:
                raise ValueError("unclosed backtick")
            subs.append(line[i + 1:j])
            out.append("__SUB__")
            i = j + 1
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
        self.redirs = []


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

    while i < n:
        _raw, text, is_op = tokens[i]
        if not is_op:
            cur.words.append(text)
            i += 1
            continue
        target = next_word(i)
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
        if cur.words or cur.redirs:
            segs.append(cur)
        cur = Seg()
        i += 1
    if cur.words or cur.redirs:
        segs.append(cur)
    return segs


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


def git_rule(args):
    a = list(args)
    while a and a[0].startswith("-"):
        opt = a.pop(0)
        if opt in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") and a:
            a.pop(0)
    if not a:
        return None
    sub, rest = a[0], a[1:]
    ask = ("ask", ASK_GIT)
    if sub in GIT_READONLY:
        return None
    if sub == "branch":
        if any(re.match(r"^-[dDmMcCfu]", x) or x in ("--delete", "--move", "--copy", "--force",
               "--set-upstream-to", "--unset-upstream", "--edit-description") for x in rest):
            return ask
        listing = ("--list", "-l", "--contains", "--no-contains", "--merged", "--no-merged", "--points-at")
        if all(x.startswith("-") for x in rest) or any(x in listing for x in rest):
            return None
        return ask
    if sub == "tag":
        if not rest or (any(x in ("-l", "--list", "-n", "--contains", "--points-at", "--merged", "-v", "--verify")
                            for x in rest) and not any(x in ("-d", "--delete", "-a", "-s", "-f") for x in rest)):
            return None
        return ask
    if sub == "remote":
        return None if (not rest or rest[0] in ("-v", "--verbose", "show", "get-url")) else ask
    if sub == "stash":
        return None if (rest and rest[0] in ("list", "show")) else ask
    if sub == "worktree":
        return None if (rest and rest[0] == "list") else ask
    if sub == "config":
        reading = ("--get", "--get-all", "--list", "-l", "--get-regexp", "--show-origin", "--show-scope")
        return None if any(x in reading for x in rest) else ask
    if sub == "submodule":
        return None if (not rest or rest[0] in ("status", "summary")) else ask
    if sub == "notes":
        return None if (not rest or rest[0] in ("list", "show")) else ask
    return ask


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


def find_rule(args, ctx, heredocs, depth):
    if "-delete" in args:
        return ("deny", "`find -delete` deletes files. " + ctx.why)
    for flag in ("-fprint", "-fprint0", "-fprintf", "-fls"):
        if flag in args:
            k = args.index(flag)
            if k + 1 >= len(args) or not ctx.may_write(args[k + 1]):
                return ("deny", "`find %s` writes to a file. %s" % (flag, ctx.why))
    i = 0
    while i < len(args):
        if args[i] in ("-exec", "-execdir", "-ok", "-okdir"):
            j, sub = i + 1, []
            while j < len(args) and args[j] not in ("__SEMI__", "+"):
                sub.append(args[j])
                j += 1
            seg = Seg()
            seg.words = ["xargs"] + sub  # treat it as a run whose targets are unknown
            result = judge_segment(seg, ctx, heredocs, depth + 1)
            if result:
                return result
            i = j
        i += 1
    return None


def curl_rule(args, ctx):
    outbound = False
    i = 0
    while i < len(args):
        a = args[i]
        nxt = args[i + 1] if i + 1 < len(args) else None
        if a == "--output" or re.fullmatch(r"-[A-Za-z]*o", a):
            if nxt is None or not ctx.may_write(nxt):
                return ("deny", "`curl -o` creates a file. " + ctx.why)
            i += 2
            continue
        if a.startswith("--output="):
            if not ctx.may_write(a.split("=", 1)[1]):
                return ("deny", "`curl --output` creates a file. " + ctx.why)
        elif a in ("--remote-name", "--remote-header-name") or re.fullmatch(r"-[A-Za-z]*[OJ][A-Za-z]*", a):
            return ("deny", "`curl -O` creates a file in the current folder. " + ctx.why)
        elif a in ("-X", "--request"):
            if nxt and nxt.upper() in ("POST", "PUT", "PATCH", "DELETE"):
                outbound = True
        elif re.fullmatch(r"-X(POST|PUT|PATCH|DELETE)", a, re.I):
            outbound = True
        elif a.startswith(("--data", "--form", "--upload-file", "--json")):
            outbound = True
        elif not a.startswith("--") and a.startswith(("-d", "-F", "-T")):
            outbound = True
        i += 1
    return ("ask", ASK_OUT) if outbound else None


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


def pkg_rule(name, args):
    sub = first_plain(args)
    if name in PACMAN:
        if any(re.match(r"^-(S(?![silg])|R|U)", a) for a in args):
            return ("ask", ASK_PKG)
        return None
    if name == "go":
        if sub in ("get", "install") or (sub == "mod" and first_plain(args, 1) in ("tidy", "download", "vendor", "init", "edit")):
            return ("ask", ASK_PKG)
        return None
    if name == "uv":
        if sub in ("add", "remove", "sync", "lock", "tool") or (sub == "pip" and first_plain(args, 1) in ("install", "uninstall", "sync")):
            return ("ask", ASK_PKG)
        return None
    if sub in PKG[name]:
        return ("ask", ASK_PKG)
    return None


def outbound_rule(name, args):
    if name in OUTBOUND_ALWAYS:
        return ("ask", ASK_OUT)
    if name == "gh":
        group, verb = first_plain(args), first_plain(args, 1)
        if verb in GH_MUT.get(group, ()):
            return ("ask", ASK_OUT)
        if group == "api":
            for i, a in enumerate(args):
                if a in ("-X", "--method") and i + 1 < len(args) and args[i + 1].upper() != "GET":
                    return ("ask", ASK_OUT)
                if a in ("-f", "-F", "--field", "--raw-field", "--input"):
                    return ("ask", ASK_OUT)
        return None
    if first_plain(args) in OUTBOUND_SUB[name]:
        return ("ask", ASK_OUT)
    return None


def tool_rule(name, args, via_xargs, ctx, heredocs, depth):
    """Per-command rules for the conductor and for read-only subagents."""
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
    if name == "git":
        return git_rule(args)
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
        return find_rule(args, ctx, heredocs, depth)
    if name == "curl":
        return curl_rule(args, ctx)
    if name == "wget":
        return wget_rule(args, ctx)
    if name in PKG or name in PACMAN or name in ("go", "uv"):
        return pkg_rule(name, args)
    if name in OUTBOUND_ALWAYS or name in OUTBOUND_SUB or name == "gh":
        return outbound_rule(name, args)
    return None


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
        return None if ctx.role == "impl" else ("ask", ASK_PARSE)
    results = []
    if ctx.role != "impl" and RISKY.search(cmd):
        results.append(("ask", ASK_DATA))
    body, heredocs = strip_heredocs(cmd)
    for line in split_lines(body):
        if not line.strip():
            continue
        try:
            line, subs = extract_substitutions(line)
            masked, table = mask_quotes(line)
            tokens = tokenize(masked, table)
        except ValueError:
            if ctx.role != "impl":
                results.append(("ask", ASK_PARSE))
            continue
        for inner in subs:
            results.append(judge_command(inner, ctx, depth + 1))
        for i, (raw, _text, is_op) in enumerate(tokens):
            if not is_op and raw.startswith("#"):  # a comment: drop the rest of the line
                tokens = tokens[:i]
                break
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
            c.allow()
        if c.in_reports(path, data):
            c.deny("Reports are written by subagents. The conductor cannot write or edit a report (Principle 7).")
        c.deny("You cannot edit `%s` directly. %s" % (path_text, WHY["conductor"]))
    if role == "readonly":
        if readonly_may_write(path, data):
            c.allow()
        c.deny(
            "You cannot edit `%s`. %s The only file you may write is your own report in "
            "`.orchestra/<work>/reports/`." % (path_text, WHY["readonly"])
        )
    if impl_may_write(path, data):
        c.allow()
    c.deny(
        "Inside `.orchestra/`, a subagent may write only its own report in "
        "`.orchestra/<work>/reports/`. The brief, the task specs, and the review records belong "
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
