#!/usr/bin/env python3
"""Write the four task agents in `agents/` from their sources in `agents-src/`.

    build_agents.py            write agents/*.md
    build_agents.py --check    exit 1 when a file in agents/ differs from what its source gives

An agent file is a whole system prompt: it cannot include another file, and a subagent cannot
read a file in the plugin's folder without a permission prompt. So the rules that the task
agents share are written once, as the parts `agents-src/_*.md`, and copied into each agent
here. A line `{{include name}}` in a source or a part stands for the part `_name.md`.

Change a source or a part, run this script, and keep both in the same commit. The two Haiku
agents share nothing and are written in `agents/` directly.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = os.path.join(ROOT, "agents-src")
AGENTS = os.path.join(ROOT, "agents")
INCLUDE = re.compile(r"^\{\{include ([a-z0-9-]+)\}\}$", re.M)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def expand(text, depth=0):
    if depth > 5:
        raise ValueError("parts include each other in a loop")
    return INCLUDE.sub(lambda m: expand(read(os.path.join(SOURCES, "_%s.md" % m.group(1))).rstrip("\n"), depth + 1), text)


def built():
    """{agent file name: its text} for every source."""
    return {name: expand(read(os.path.join(SOURCES, name)))
            for name in sorted(os.listdir(SOURCES)) if name.endswith(".md") and not name.startswith("_")}


def main(argv=None):
    check = "--check" in (sys.argv[1:] if argv is None else argv)
    stale = []
    for name, text in built().items():
        path = os.path.join(AGENTS, name)
        if os.path.isfile(path) and read(path) == text:
            continue
        stale.append(name)
        if not check:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
    if check and stale:
        print("Out of date: %s. Run scripts/build_agents.py." % ", ".join("agents/" + n for n in stale))
        return 1
    if not check:
        print("Written: %s." % ", ".join(stale) if stale else "Nothing to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
