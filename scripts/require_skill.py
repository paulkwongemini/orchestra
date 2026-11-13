#!/usr/bin/env python3
"""PreToolUse(every tool): the conductor loads its instructions before it uses any other tool.

The note at session start asks a Fable session to load the conductor skill. This hook makes
that a requirement: until the skill has been loaded, every other tool call in the main
conversation of a conductor session is refused.

The skill counts as loaded when the model invokes it with the Skill tool, or when the user
runs `/orchestra:conductor`. After the conversation is compacted or cleared it has to be
loaded again, because the instructions may no longer be in the context in full.

The same hook records when the delegation rules (`orchestra:delegation`) and the
documentation rules (`orchestra:documentation`) are loaded. `guard_agent.py` refuses a
delegation until they are.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402

# Tools that are needed to reach the Skill tool itself.
ALWAYS = {"ToolSearch"}


def main():
    data = c.read_input()
    if not c.main_is_conductor(data):
        c.allow()
    tool = data.get("tool_name") or ""
    skill = (data.get("tool_input") or {}).get("skill") if tool == "Skill" else None
    if c.is_skill(skill, c.CONDUCTOR_SKILL):
        c.mark_loaded(data, True)
        c.allow()
    for sub_skill in c.SUB_SKILLS:
        if c.is_skill(skill, sub_skill) and c.is_loaded(data):
            c.mark_loaded(data, True, sub_skill)
            c.allow()
    if tool in ALWAYS or c.is_loaded(data):
        c.allow()
    c.deny(
        "This session runs on a Fable model, so you are the conductor, and your instructions are "
        "not loaded yet. Invoke the `%s` skill with the Skill tool first. Then make this call "
        "again if the instructions allow it." % c.CONDUCTOR_SKILL
    )


if __name__ == "__main__":
    main()
