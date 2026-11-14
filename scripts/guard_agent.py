#!/usr/bin/env python3
"""PreToolUse(Agent|Workflow): enforce the delegation rules.

Principle 2  No implementation is delegated before a brief exists and the user has spoken since it last changed.
Principle 4  No model below Sonnet.
Principle 5  No subagent runs on Fable. A fork counts.
Principle 6  A subagent cannot start other subagents.
Principle 9  Every delegation goes through a task spec.
Principle 11 No implementation is delegated before the documentation rules are loaded.

The main-conversation rules apply only in a conductor session, which is a session whose
model is a Fable model.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402

TASK_REF = re.compile(r"\.orchestra/([^\s/`'\"]+)/tasks/([A-Za-z0-9][\w.\-]*\.md)")
EXAMPLE = "`.orchestra/<work>/tasks/T01-short-name.md`"

FABLE_ADVICE = (
    "If the work needs Fable-level judgment, make that judgment yourself, write it into the "
    "task spec, and leave only the execution to Opus. If the decision goes beyond the brief, "
    "stop and ask the user."
)


def check_model(model):
    model = str(model or "").strip().lower()
    if not model:
        return
    if c.is_fable(model) or model == "inherit":
        c.deny("A subagent cannot run on Fable (Principle 5). " + FABLE_ADVICE)
    if "haiku" in model:
        c.deny(
            "Models below Sonnet are not used (Principle 4). For a very simple task, give "
            "`orchestra:sonnet-builder` a low effort level."
        )
    if "opus" not in model and "sonnet" not in model:
        c.deny("A subagent's model can only be opus or sonnet (Principle 4). You passed: `%s`" % model)


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
            c.deny(
                "A subagent cannot start other subagents (Principle 6). If the task is too big, "
                "report BLOCKED and ask the conductor to split it."
            )
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
            "A subagent started without a type runs on the main model, Fable (Principle 5). "
            "Choose one from the roster: " + c.roster_text()
        )
    if stype.lower() == "fork":
        c.deny(
            "A fork is a copy of yourself running on Fable, so it cannot be created (Principle 5). "
            + FABLE_ADVICE
        )
    name = c.bare(stype)
    foreign = ":" in stype and not stype.startswith(c.PLUGIN + ":")
    if foreign or name not in c.ROSTER:
        c.deny("`%s` is not on the roster (Principles 4 and 5). Roster: %s" % (stype, c.roster_text()))

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
