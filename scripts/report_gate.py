#!/usr/bin/env python3
"""PreToolUse(Read|Grep): a report opens only after an independent review record exists (Principle 7).

When the conductor tries to read `.orchestra/<work>/reports/T01.md`, this hook checks that
`.orchestra/<work>/reviews/T01.md` exists, has the required sections, and is newer than the report.

A report opens right away in two cases:
  - Its status is BLOCKED or FAILED. There is no result to review.
  - A read-only subagent wrote it. The report is the result.

This hook guarantees the order. It cannot guarantee the quality of the review.

When the conductor reads `.orchestra/<work>/brief.md`, that folder becomes the session's active
work folder, whose brief decides what is pre-approved (see guard_main.py).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402

MIN_EVIDENCE = 20  # minimum length of the text under the evidence heading


def find_review(unit, tid):
    folder = os.path.join(unit, "reviews")
    try:
        names = [n for n in os.listdir(folder) if n.endswith(".md") and c.task_id(n) == tid]
    except OSError:
        return None
    if not names:
        return None
    return max((os.path.join(folder, n) for n in names), key=os.path.getmtime)


def review_problems(review, report):
    if review is None:
        return ["There is no review record."]
    text = c.read_text(review)
    problems = []
    missing = c.missing_headings(text, c.REVIEW_HEADINGS)
    if missing:
        problems.append("The review record is missing required sections: " + ", ".join(missing) + ".")
    evidence = c.section_body(text, c.REVIEW_EVIDENCE)
    if evidence is not None and len(evidence) < MIN_EVIDENCE:
        problems.append(
            "Under `## %s`, write the commands you ran yourself and their results. For a task with "
            "nothing to run, say what you compared against what." % c.REVIEW_EVIDENCE
        )
    if c.section_body(text, c.REVIEW_VERDICT) == "":
        problems.append("`## %s` is empty." % c.REVIEW_VERDICT)
    if os.path.getmtime(review) < os.path.getmtime(report):
        problems.append(
            "The review record is older than the report. Review the reworked result from the "
            "start and write a new review record."
        )
    return problems


def main():
    data = c.read_input()
    if c.in_cli_mode(data):
        c.allow()  # a CLI mode is on: the conductor keeps the rules by its instructions alone
    if not c.main_is_conductor(data):
        c.allow()
    tool = data.get("tool_name") or ""
    ti = data.get("tool_input") or {}

    if tool == "Grep":
        target = ti.get("path")
        if target and c.in_reports(c.resolve(target, data), data):
            c.deny(
                "Do not search a reports folder (Principle 7). Write the review record first, "
                "then open the report with the Read tool."
            )
        c.allow()

    target = ti.get("file_path")
    if not target:
        c.allow()
    path = c.resolve(target, data)
    brief_of = c.brief_unit(path, data)
    if brief_of:
        c.set_active_work(data, brief_of)
    if not c.in_reports(path, data) or not os.path.isfile(path):
        c.allow()

    unit, _rest = c.split_unit(path, data)
    tid = c.task_id(path)
    if c.read_status(path) in ("BLOCKED", "FAILED"):
        c.allow()
    if c.load_meta(data, unit, tid).get("kind") == "readonly":
        c.allow()

    problems = review_problems(find_review(unit, tid), path)
    if not problems:
        c.allow()
    c.deny(
        "Review the result independently before reading the report (Principle 7). "
        "1) Review the result on its own: read the diff, run the verification commands yourself, "
        "and check the acceptance criteria in the task spec. "
        "2) Write the review record at `.orchestra/%s/reviews/%s.md`. Required sections: %s. "
        "3) Then open the report again. "
        "Current problem: %s"
        % (os.path.basename(unit), tid, ", ".join("## " + h for h in c.REVIEW_HEADINGS), " ".join(problems))
    )


if __name__ == "__main__":
    main()
