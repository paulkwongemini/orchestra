#!/usr/bin/env python3
"""PreToolUse(every tool): the conductor loads its instructions before it uses any other tool.

When the user turns conductor mode on, the conductor is asked to load the conductor skill. This
hook makes that a requirement: until the skill has been loaded, every other tool call in the
main conversation of a conductor session is refused.

The skill counts as loaded when the model invokes it with the Skill tool, or when the user
runs `/orchestra:conductor`. After the conversation is compacted or cleared it has to be
loaded again, because the instructions may no longer be in the context in full.

The same hook records when the delegation rules (`orchestra:delegation`) and the
documentation rules (`orchestra:documentation`) are loaded. `guard_agent.py` refuses a
delegation until they are.

An Opus conductor that runs at a low or medium effort level is stopped once, at its first tool
call, and told to recommend High or above to the user and ask whether to go on. The user
decides: the next call goes through whatever they answer. The question comes again only when
the effort changes to another low level, or the user turns conductor mode on again.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402

# Tools that are needed to reach the Skill tool itself.
ALWAYS = {"ToolSearch"}


def check_effort(data):
    """Stop an Opus conductor at a low effort level once, so that the user is asked."""
    if not c.is_opus(c.main_model(data)):
        return
    effort = c.main_effort(data)
    if effort not in c.LOW_EFFORTS or c.effort_asked(data) == effort:
        return
    c.mark_effort_asked(data, effort)
    c.deny(
        "This conductor runs on Opus at `%s` effort. Before you go on, tell the user in a chat "
        "message, not with a tool, that the conductor works best at High effort or above, and that "
        "they can change it with the effort control below the prompt input. Ask whether to go on "
        "at `%s` or to change it, and end your turn. Whatever they answer, go on with the request "
        "from where it stands: do not ask again, and do not refuse to work at this level. "
        "Make this call again then." % (effort, effort)
    )


def main():
    data = c.read_input()
    if not c.main_is_conductor(data):
        c.allow()
    tool = data.get("tool_name") or ""
    if tool in ALWAYS:
        c.allow()
    check_effort(data)
    skill = (data.get("tool_input") or {}).get("skill") if tool == "Skill" else None
    if c.is_skill(skill, c.CONDUCTOR_SKILL):
        c.mark_loaded(data, True)
        c.allow()
    for sub_skill in c.SUB_SKILLS:
        if c.is_skill(skill, sub_skill) and c.is_loaded(data):
            c.mark_loaded(data, True, sub_skill)
            c.allow()
    if c.is_loaded(data):
        c.allow()
    c.deny(
        "Conductor mode is on in this session, so you are the conductor, and your instructions are "
        "not loaded yet. Invoke the `%s` skill with the Skill tool first. Then make this call "
        "again if the instructions allow it." % c.CONDUCTOR_SKILL
    )


if __name__ == "__main__":
    main()
