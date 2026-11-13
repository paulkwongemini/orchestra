#!/usr/bin/env python3
"""SessionStart, PostModelSwitch, the user's prompts and answers: keep track of whether the session is in conductor mode and of when the user last spoke, and tell the main conversation.

The harness applies only while the main conversation runs on a Fable model. A session on any
other model is an ordinary session, and the guards leave it alone.

A hook is not told the model when a session starts, so a new session gets a short note that
covers both cases. Claude Code tells the model which model it is, so the model can pick the
case that applies. Once the model is known, only the case that applies is sent.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402

CONDUCTOR = (
    "you are the conductor of this session. You work out what the user wants, design the "
    "approach, delegate the implementation to subagents, and review their results yourself. You do "
    "not implement. Before you do anything else, load your instructions by invoking the `%s` skill, "
    "and follow them for the rest of the session." % c.CONDUCTOR_SKILL
)
BOOT_UNKNOWN = (
    "<orchestra>\n"
    "The orchestra harness is installed. It applies only to a session that runs on a Fable model.\n"
    "If you are running on a Fable model, " + CONDUCTOR + "\n"
    "If you are running on any other model, this is an ordinary session. Ignore this note.\n"
    "</orchestra>"
)
BOOT_FABLE = (
    "<orchestra>\n"
    "This session runs on a Fable model, so " + CONDUCTOR + "\n"
    "</orchestra>"
)
BOOT_AGAIN = (
    "<orchestra>\n"
    "This session runs on a Fable model, so you are the conductor. The conversation was compacted "
    "or cleared, so your instructions may no longer be here in full. Before you do anything else, "
    "load them again by invoking the `%s` skill. The harness refuses other tool calls until you "
    "do. Then find the work folder under `.orchestra/` and carry on from where the work stands.\n"
    "</orchestra>" % c.CONDUCTOR_SKILL
)
SWITCH_ON = (
    "<orchestra>\n"
    "This session now runs on a Fable model, so from here on " + CONDUCTOR + "\n"
    "</orchestra>"
)
SWITCH_OFF = (
    "<orchestra>\n"
    "This session no longer runs on a Fable model. The conductor rules do not apply now: work as "
    "an ordinary session. They apply again if the session is switched back to a Fable model.\n"
    "</orchestra>"
)


COMMAND = re.compile(r"^\s*/%s:(disable|enable|conductor)(?:\s|$)" % c.PLUGIN)


def command_of(data):
    """The orchestra command the user typed, if any: disable, enable, or conductor."""
    name = str(data.get("command_name") or "")
    if name:
        name = name.lstrip("/")
        short = name.split(":")[-1]
        if short in ("disable", "enable", "conductor") and (":" not in name or name.startswith(c.PLUGIN + ":")) \
                and data.get("command_source", "plugin") == "plugin":
            return short
        return None
    m = COMMAND.match(str(data.get("prompt") or ""))
    return m.group(1) if m else None


def main():
    data = c.read_input()
    event = data.get("hook_event_name") or ""
    if c.is_subagent(data):
        c.allow()

    if event == "PostToolUse":
        c.mark_user_turn(data)  # the user answered a question the conductor asked
        c.allow()

    if event in ("UserPromptSubmit", "UserPromptExpansion"):
        c.mark_user_turn(data)
        # Only a command the user types turns conductor mode off or on for the session.
        command = command_of(data)
        if command == "disable":
            c.set_disabled(data, True)
        elif command in ("enable", "conductor"):
            c.set_disabled(data, False)
        if command == "conductor":
            c.mark_loaded(data, True)  # the command puts the skill's text into the conversation
        c.allow()

    if event == "PostModelSwitch":
        to_model = data.get("to_model")
        c.record_switch(data, to_model)
        if data.get("source") == "resume" or c.is_disabled(data):
            c.allow()  # a restored model, not a change. SessionStart has already said what applies
        if c.is_fable(to_model):
            c.add_context(event, SWITCH_ON)
        if c.is_fable(data.get("from_model")):
            c.add_context(event, SWITCH_OFF)
        c.allow()

    # SessionStart
    c.prune_state()
    again = data.get("source") in ("compact", "clear")
    if again:
        c.mark_loaded(data, False)  # the instructions have to be loaded again
        c.mark_loaded(data, False, c.DELEGATION_SKILL)
    if c.is_disabled(data):
        c.allow()
    known = c.main_model_is_fable(data)
    if known is None:
        c.add_context("SessionStart", BOOT_UNKNOWN)
    if known:
        c.add_context("SessionStart", BOOT_AGAIN if again else BOOT_FABLE)
    c.allow()


if __name__ == "__main__":
    main()
