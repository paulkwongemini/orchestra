#!/usr/bin/env python3
"""PreToolUse(Agent|Workflow): enforce the delegation rules.

Principle 2  No implementation is delegated before a brief exists and the user has spoken since it last changed.
Principle 4  No model below Sonnet, except for the two Haiku subagents, which run only on Haiku.
Principle 5  No subagent runs on Fable or on the main model. A fork and a subagent without a
             type count, because both run on the main model.
Principle 6  A subagent starts no other subagent, except that `opus-engineer`, `sonnet-builder` and
             `sonnet-writer` may start `haiku-scout` and `haiku-editor`, and `opus-analyst` may
             start `haiku-scout`. A Haiku subagent starts nothing, and no subagent runs a Workflow.
Principle 9  Every delegation goes through a task spec, except a Haiku call.
Principle 11 No implementation is delegated before the documentation rules are loaded.

A Haiku call is a short request in a fixed format (`FIND:` for `haiku-scout`; `DO:`, `FILES:`
and `CHECK:` for `haiku-editor`), at most HAIKU_MAX_PROMPT characters. From the conductor it
needs no task spec and no skill beyond the conductor skill, and it does not change the active
work folder. A `haiku-editor` call from the conductor needs the brief of the active work folder
to be confirmed, by the same strict test the pre-approvals use.

The main-conversation rules apply only in a conductor session: one in which the user turned
conductor mode on, while it runs on a Fable or an Opus model. The roster is the same for both.
Calls inside a subagent that is not on the roster are left alone.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402
from guard_main import brief_approval  # noqa: E402

TASK_REF = re.compile(r"\.orchestra/([^\s/`'\"]+)/tasks/([A-Za-z0-9][\w.\-]*\.md)")
EXAMPLE = "`.orchestra/<work>/tasks/T01-short-name.md`"

JUDGMENT_ADVICE = (
    "If the work needs the conductor's own judgment, make that judgment yourself, write it into "
    "the task spec, and leave only the execution to a roster subagent. If the decision goes "
    "beyond the brief, stop and ask the user."
)


def check_model(model):
    model = str(model or "").strip().lower()
    if not model:
        return
    if c.is_fable(model) or model == "inherit":
        c.deny("A subagent cannot run on Fable or inherit the main model (Principle 5). " + JUDGMENT_ADVICE)
    if "haiku" in model:
        c.deny(
            "This subagent does not run below Sonnet (Principle 4). For a very simple task, give "
            "`orchestra:sonnet-builder` a low effort level. Work that needs no judgment goes to "
            "`orchestra:haiku-scout` or `orchestra:haiku-editor`."
        )
    if "opus" not in model and "sonnet" not in model:
        c.deny("A subagent's model can only be opus or sonnet (Principle 4). You passed: `%s`" % model)


HAIKU_KEYS = {"haiku-scout": ("FIND",), "haiku-editor": ("DO", "FILES", "CHECK")}
HAIKU_FORMAT = {
    "haiku-scout": "FIND: <the question>\nIN: <paths, optional>",
    "haiku-editor": "DO: <the edit>\nFILES: <the paths>\nCHECK: <the command that checks it>",
}
# A request that takes more than a few sentences to state is not mechanical. This limit is what
# keeps judgment out of a Haiku call.
HAIKU_MAX_PROMPT = 1500


def check_haiku_model(model):
    model = str(model or "").strip().lower()
    if model and ("haiku" not in model or c.is_fable(model)):
        c.deny("A Haiku subagent runs only on haiku. Leave `model` out or pass `haiku`. You passed: `%s`" % model)


def check_haiku_prompt(name, prompt, conductor):
    prompt = str(prompt or "")
    problems = []
    if len(prompt) > HAIKU_MAX_PROMPT:
        elsewhere = "write a task spec and delegate it as a task" if conductor else "do the work yourself"
        problems.append(
            "The prompt is %d characters, over the limit of %d. A request this long is not a "
            "mechanical one: %s." % (len(prompt), HAIKU_MAX_PROMPT, elsewhere)
        )
    missing, empty = [], []
    for key in HAIKU_KEYS[name]:
        values = re.findall(r"^[ \t]*" + key + r":(.*)$", prompt, re.M)
        if not values:
            missing.append(key + ":")
        elif not any(v.strip() for v in values):
            empty.append(key + ":")
    if missing:
        problems.append("Missing lines: %s." % ", ".join(missing))
    if empty:
        problems.append("Lines with nothing after the key: %s." % ", ".join(empty))
    if problems:
        c.deny(
            "A call to `%s:%s` is a short request in a fixed format. %s Use this format, each key "
            "at the start of its own line:\n%s" % (c.PLUGIN, name, " ".join(problems), HAIKU_FORMAT[name])
        )


def check_editor_brief(data):
    """A conductor's `haiku-editor` call needs the brief of the active work folder to be confirmed."""
    a = brief_approval(data)
    if a.state == "confirmed":
        return
    after = "Start `%s:haiku-editor` again after they answer." % c.PLUGIN
    if a.state == "no-work":
        why = ("This session has no active work folder, so no confirmed brief covers this edit. Read or "
               "write the work's brief %s first, show it to the user, and ask them to confirm it. %s"
               % (a.shown, after))
    elif a.state == "no-brief":
        why = ("The active work folder has no brief %s, so no confirmed brief covers this edit. Write "
               "it, show it to the user, and ask them to confirm it. %s" % (a.shown, after))
    elif a.state == "newer":
        why = ("The brief %s was written or changed after the user last spoke, so the user has not "
               "confirmed this version of the brief. Show the user the change and ask them to confirm "
               "it. %s" % (a.shown, after))
    else:
        why = ("No user turn is recorded for this session, so the user has not confirmed this version "
               "of the brief %s. Show the user the brief and ask them to confirm it. %s" % (a.shown, after))
    c.deny("A Haiku edit runs only under a brief the user has confirmed (Principle 2). " + why)


def haiku_target(stype):
    """The Haiku subagent a call inside a subagent names, or None. Only the plugin's own name counts."""
    stype = str(stype or "").strip()
    prefix = c.PLUGIN + ":"
    name = stype[len(prefix):] if stype.startswith(prefix) else None
    return name if name in c.HAIKU else None


def subagent_call(data, tool, ti):
    """Principle 6, for an Agent or Workflow call inside a roster subagent."""
    caller = c.bare(data.get("agent_type"))
    if caller in c.HAIKU:
        c.deny(
            "A Haiku subagent starts no other subagent (Principle 6). Do the request yourself, or "
            "say in your last message that you could not."
        )
    target = haiku_target(ti.get("subagent_type")) if tool != "Workflow" else None
    if target is None:
        c.deny(
            "A subagent cannot start other subagents (Principle 6), except `%s:haiku-scout` and "
            "`%s:haiku-editor` for a request that needs no judgment. If the task is too big, report "
            "BLOCKED and ask the conductor to split it." % (c.PLUGIN, c.PLUGIN)
        )
    if target == "haiku-editor" and c.ROSTER[caller]["kind"] == "readonly":
        c.deny(
            "A read-only subagent starts nothing that edits (Principle 6). Put the change in your "
            "report as a recommendation. `%s:haiku-scout` can find and read for you." % c.PLUGIN
        )
    check_haiku_model(ti.get("model"))
    check_haiku_prompt(target, ti.get("prompt"), conductor=False)
    c.allow()


def check_task_spec(data, prompt):
    """Principle 9. Returns the work folder that the task spec belongs to, or refuses the call."""
    m = TASK_REF.search(prompt or "")
    if not m:
        c.deny(
            "Every delegation goes through a task spec (Principle 9). Write the task spec at "
            + EXAMPLE + " and put that path in the prompt. Required sections: "
            + ", ".join(c.TASK_HEADINGS)
        )
    unit = os.path.join(c.work_dir(data), m.group(1))
    shown = ".orchestra/%s/tasks/%s" % (m.group(1), m.group(2))
    path = os.path.join(unit, "tasks", m.group(2))
    if not os.path.isfile(path):
        c.deny("The task spec `%s` does not exist (Principle 9). Write it with the Write tool first." % shown)
    text = c.read_text(path)
    missing = c.missing_headings(text, c.TASK_HEADINGS)
    if missing:
        c.deny(
            "The task spec is missing required sections (Principle 9): %s. Add each one as a "
            "`## Heading`." % ", ".join(missing)
        )
    empty = c.empty_sections(text, c.TASK_HEADINGS)
    if empty:
        c.deny("These sections of the task spec are empty (Principle 9): %s" % ", ".join(empty))
    return unit


def check_brief(data, unit):
    """Principle 2. Delegating implementation requires a brief in the same work folder."""
    shown = "`.orchestra/%s/brief.md`" % os.path.basename(unit)
    brief = os.path.join(unit, "brief.md")
    if not os.path.isfile(brief):
        c.deny(
            "Before delegating implementation, write down the user's intent as a brief and get it "
            "confirmed (Principle 2). Write %s and show it to the user. Required sections: %s"
            % (shown, ", ".join(c.BRIEF_HEADINGS))
        )
    missing = c.missing_headings(c.read_text(brief), c.BRIEF_HEADINGS)
    if missing:
        c.deny("The brief %s is missing required sections (Principle 2): %s" % (shown, ", ".join(missing)))
    # The user can only have confirmed a brief that existed, in this form, when they last spoke.
    turn = c.last_user_turn(data)
    if turn is not None and os.path.getmtime(brief) > turn:
        c.deny(
            "The brief %s was written or changed after the user last spoke, so the user has not "
            "confirmed this version (Principle 2). Show it to the user and ask them to confirm it. "
            "Delegate after they answer." % shown
        )


def main():
    data = c.read_input()
    tool = data.get("tool_name") or "Agent"
    ti = data.get("tool_input") or {}

    if c.is_subagent(data):
        if c.bare(data.get("agent_type")) in c.ROSTER:
            subagent_call(data, tool, ti)
        c.allow()

    if not c.main_is_conductor(data):
        c.allow()

    if tool == "Workflow":
        c.deny(
            "Workflows run agents that are not on the roster, so they are not used here "
            "(Principles 4 and 5). Split the work and delegate each part directly to a roster "
            "subagent: " + c.roster_text()
        )

    stype = str(ti.get("subagent_type") or "").strip()
    if not stype:
        c.deny(
            "A subagent started without a type runs on the main model, the conductor's own model "
            "(Principle 5). Choose one from the roster: " + c.roster_text()
        )
    if stype.lower() == "fork":
        c.deny(
            "A fork is a copy of yourself on the main model, so it cannot be created (Principle 5). "
            + JUDGMENT_ADVICE
        )
    name = c.bare(stype)
    foreign = ":" in stype and not stype.startswith(c.PLUGIN + ":")
    if foreign or name not in c.ROSTER:
        c.deny("`%s` is not on the roster (Principles 4 and 5). Roster: %s" % (stype, c.roster_text()))

    if name in c.HAIKU:
        check_haiku_model(ti.get("model"))
        check_haiku_prompt(name, ti.get("prompt"), conductor=True)
        if name == "haiku-editor":
            check_editor_brief(data)
        c.allow()
    check_model(ti.get("model"))
    if not c.is_loaded(data, c.DELEGATION_SKILL):
        c.deny(
            "Choose the subagent and the effort level by the delegation rules, which are not "
            "loaded yet (Principle 4). Invoke the `%s` skill with the Skill tool, choose by its "
            "rules, and then delegate." % c.DELEGATION_SKILL
        )
    impl = c.ROSTER[name]["kind"] == "impl"
    if impl and not c.is_loaded(data, c.DOCUMENTATION_SKILL):
        c.deny(
            "Decide what this task does to the project's documents and comments by the "
            "documentation rules, which are not loaded yet (Principle 11). Invoke the `%s` skill "
            "with the Skill tool, check the task spec against its rules, and then delegate."
            % c.DOCUMENTATION_SKILL
        )
    unit = check_task_spec(data, ti.get("prompt"))
    c.set_active_work(data, unit)
    if impl:
        check_brief(data, unit)
    c.allow()


if __name__ == "__main__":
    main()
