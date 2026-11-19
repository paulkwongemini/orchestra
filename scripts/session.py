#!/usr/bin/env python3
"""SessionStart, PostModelSwitch, the user's prompts and answers: keep track of whether the session is in conductor mode and of when the user last spoke, and tell the main conversation.

Conductor mode is off when a session starts. The user turns it on for the session with
`/orchestra:conduct`, `/orchestra:enable` or `/orchestra:conductor`, and off with
`/orchestra:disable`. While it is on, the harness applies when the main conversation runs on
a Fable or an Opus model. On any other model the session is an ordinary one, and the guards
leave it alone.

A hook is not always told the model, so a note that is sent before the model is known covers
both cases. Claude Code tells the model which model it is, so the model can pick the case that
applies. Once the model is known, only the case that applies is sent.
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
    "The user turned conductor mode on for this session. It applies only while the session runs "
    "on a Fable or an Opus model.\n"
    "If you are running on a Fable or an Opus model, " + CONDUCTOR + "\n"
    "If you are running on any other model, this is an ordinary session. Ignore this note.\n"
    "</orchestra>"
)
BOOT_ON = (
    "<orchestra>\n"
    "The user turned conductor mode on for this session, and it runs on a Fable or an Opus "
    "model, so " + CONDUCTOR + "\n"
    "</orchestra>"
)
BOOT_AGAIN = (
    "<orchestra>\n"
    "Conductor mode is on for this session, and it runs on a Fable or an Opus model, so you are "
    "the conductor. The conversation was compacted or cleared, so your instructions may no longer "
    "be here in full. Before you do anything else, load them again by invoking the `%s` skill. The "
    "harness refuses other tool calls until you do. Then find the work folder under `.orchestra/` "
    "and carry on from where the work stands.\n"
    "</orchestra>" % c.CONDUCTOR_SKILL
)
SWITCH_ON = (
    "<orchestra>\n"
    "Conductor mode is on for this session, and it now runs on a Fable or an Opus model, so from "
    "here on " + CONDUCTOR + "\n"
    "</orchestra>"
)
SWITCH_OFF = (
    "<orchestra>\n"
    "This session no longer runs on a Fable or an Opus model. The conductor rules do not apply "
    "now: work as an ordinary session. They apply again if the session is switched back to a "
    "Fable or an Opus model.\n"
    "</orchestra>"
)

ON_COMMANDS = ("conduct", "enable", "conductor")
COMMANDS = ON_COMMANDS + ("disable",)
COMMAND = re.compile(r"^\s*/%s:(%s)(?:\s|$)" % (c.PLUGIN, "|".join(COMMANDS)))


def command_of(data):
    """The orchestra command the user typed, if any: conduct, enable, conductor, or disable."""
    name = str(data.get("command_name") or "")
    if name:
        name = name.lstrip("/")
        short = name.split(":")[-1]
        if short in COMMANDS and (":" not in name or name.startswith(c.PLUGIN + ":")) \
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
        # Only a command the user types turns conductor mode on or off for the session.
        command = command_of(data)
        if command == "disable":
            c.set_enabled(data, False)
        elif command in ON_COMMANDS:
            c.set_enabled(data, True)
            c.mark_effort_asked(data, None)  # turning it on asks about a low effort again
        if command == "conductor":
            c.mark_loaded(data, True)  # the command puts the skill's text into the conversation
        c.allow()

    if event == "PostModelSwitch":
        to_model, from_model = data.get("to_model"), data.get("from_model")
        c.record_switch(data, to_model)
        if data.get("source") == "resume" or not c.is_enabled(data):
            c.allow()  # a restored model, not a change. SessionStart has already said what applies
        now, before = c.is_conductor_model(to_model), c.is_conductor_model(from_model)
        if now and not before:
            c.add_context(event, SWITCH_ON)
        if before and not now:
            c.add_context(event, SWITCH_OFF)
        c.allow()  # Fable to Opus or back: the session stays a conductor session

    # SessionStart
    c.prune_state()
    again = data.get("source") in ("compact", "clear")
    if again:
        c.mark_loaded(data, False)  # the instructions have to be loaded again
        for sub_skill in c.SUB_SKILLS:
            c.mark_loaded(data, False, sub_skill)
    if not c.is_enabled(data):
        c.allow()  # conductor mode is off until the user turns it on
    known = c.main_model_can_conduct(data)
    if known is None:
        c.add_context("SessionStart", BOOT_UNKNOWN)
    if known:
        c.add_context("SessionStart", BOOT_AGAIN if again else BOOT_ON)
    c.allow()


if __name__ == "__main__":
    main()
