"""Read a shell command line: split it into simple commands, with their words and redirections.

The guards judge what this module reads. It runs nothing and decides nothing. A line it cannot
read raises ValueError.
"""
import os
import re
import shlex

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
