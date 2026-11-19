#!/usr/bin/env python3
"""SubagentStop and PreToolUse(SubagentHandback): check handbacks and DONE verification records.

A subagent writes its full report to a file in its work folder's `reports/` folder and returns
only the lines below to the conductor. This keeps a narrative summary out of the conductor's
context until the conductor has reviewed the result independently.

    STATUS: DONE | BLOCKED | FAILED
    TASK: T01
    REPORT: .orchestra/<work>/reports/T01.md
    FILES: changed files (optional)
    QUESTION: the question, when BLOCKED

DONE requires declared passing checks in the report. The conductor still checks their
coverage and truth independently.

The two Haiku subagents write no report file, and no report path or metadata is checked or
recorded for them. Their last message has its own format:

    haiku-scout   first non-blank line `STATUS: FOUND` or `STATUS: NOT FOUND`, then anything,
                  at most SCOUT_MAX_LENGTH characters
    haiku-editor  only these lines, at most EDITOR_MAX_LENGTH characters:
                      STATUS: DONE | FAILED
                      FILES: changed files, separated by commas, or none
                      CHECK: exit <integer>
                  DONE needs `CHECK: exit 0`.

After MAX_ATTEMPTS retries the hook warns and allows a stop, for an invalid DONE as well: a
subagent that cannot fix its report must not keep spending tokens, and the conductor's own
review is what decides whether the work is accepted.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as c  # noqa: E402

MAX_ATTEMPTS = 3
MAX_LENGTH = 2000
KEY_LINE = re.compile(r"^\W*(STATUS|TASK|REPORT|FILES|QUESTION)\W*[:：]\s*(.*)$", re.I)
SCOUT_MAX_LENGTH = 4000
EDITOR_MAX_LENGTH = MAX_LENGTH
EDITOR_KEY_LINE = re.compile(r"^\W*(STATUS|FILES|CHECK)\W*[:：]\s*(.*)$", re.I)
EDITOR_KEYS = ("STATUS", "FILES", "CHECK")

FORMAT_HELP = (
    "Your handback or report is incomplete. Write the full report to the report "
    "file that the task spec names, and make your last message exactly the lines below. Add no "
    "explanation or summary.\n"
    "STATUS: DONE | BLOCKED | FAILED\n"
    "TASK: <task ID>\n"
    "REPORT: <the report path from the task spec>\n"
    "FILES: <changed files, separated by commas>\n"
    "QUESTION: <only when BLOCKED: the question the conductor has to answer>\n"
    "Use DONE only when all required checks have passed. Continue checks you can run; "
    "report BLOCKED when you need help to verify, or FAILED when the result still fails."
)


SCOUT_HELP = (
    "Your last message does not follow the format. Make its first line `STATUS: FOUND` or "
    "`STATUS: NOT FOUND`, followed by what you found: paths, line numbers, and the lines that "
    "answer the question. Keep the whole message within %d characters." % SCOUT_MAX_LENGTH
)

EDITOR_HELP = (
    "Your last message does not follow the format. Make it exactly the lines below. Add no "
    "explanation or summary.\n"
    "STATUS: DONE | FAILED\n"
    "FILES: <changed files, separated by commas, or none>\n"
    "CHECK: exit <the exit code of the CHECK command>\n"
    "Use DONE only when the CHECK command exited with 0."
)


def clean(value):
    return value.strip().strip("*`'\" ").strip()


def parse(text, key_line=KEY_LINE):
    """Returns (fields, lines that are outside the format). `key_line` matches the lines that open a field."""
    fields, stray, current = {}, [], None
    for raw in (text or "").strip().splitlines():
        line = raw.strip()
        if not line or line.startswith("```"):
            continue
        m = key_line.match(line)
        if m:
            current = m.group(1).upper()
            fields[current] = clean(m.group(2))
        elif current == "QUESTION":
            fields[current] = (fields[current] + "\n" + line).strip()
        elif current == "FILES" and re.match(r"^[-*•]\s+\S", line):
            fields[current] = (fields[current] + "\n" + line).strip()
        else:
            stray.append(line)  # a narrative sentence under FILES lands here too
    return fields, stray


def verification_problems(path):
    """Validate declared results, not tool execution or semantic coverage of the task."""
    body = c.section_body(c.read_text(path), "What I verified") or ""
    block = re.search(r"^```json[ \t]*\n(.*?)\n```[ \t]*$", body, re.S | re.I | re.M)  # text around it is fine
    if not block:
        return ["DONE needs a JSON code block under `## What I verified`, with checks and unverified fields."]
    try:
        verification = json.loads(block.group(1))
    except ValueError:
        return ["The verification code block is not valid JSON."]
    if not isinstance(verification, dict):
        return ["The verification JSON must be an object with checks and unverified fields."]

    problems = []
    if verification.get("unverified") != []:
        problems.append("DONE requires an empty unverified list; required checks cannot remain unverified.")
    checks = verification.get("checks")
    if not isinstance(checks, list) or not checks:
        return problems + ["DONE requires a nonempty checks list."]
    for number, check in enumerate(checks, 1):
        label = "Verification check %d: " % number
        if not isinstance(check, dict):
            problems.append(label + "must be an object.")
            continue
        for key in ("criterion", "method", "evidence"):
            if not isinstance(check.get(key), str) or not check[key].strip():
                problems.append(label + key + " must be nonempty text.")
        if check.get("result") != "PASS":
            problems.append(label + "result must be PASS for DONE.")
        if check.get("kind") == "command":
            if type(check.get("exit_code")) is not int or check["exit_code"] != 0:
                problems.append(label + "a command must have exit_code 0.")
        elif check.get("kind") != "manual":
            problems.append(label + "kind must be command or manual.")
    return problems


def validate(text, data):
    """Returns (problems, info)."""
    problems = []
    fields, stray = parse(text)
    status = (fields.get("STATUS") or "").upper()
    claims_done = status == "DONE"
    task = fields.get("TASK") or ""
    report = fields.get("REPORT") or ""

    if status not in c.STATUSES:
        problems.append("The STATUS line is missing, or its value is not exactly DONE, BLOCKED, or FAILED.")
        status = ""
    if not task:
        problems.append("The TASK line is missing.")
    elif not re.fullmatch(r"[\w.\-]+", task):
        problems.append("TASK takes only the task ID, for example T01.")
    if not report:
        problems.append("The REPORT line is missing.")
    elif len(report.split()) > 1:
        problems.append("REPORT takes only the report path.")
    if stray:
        problems.append("There are lines outside the format. Put explanations in the report file.")
    if len(text or "") > MAX_LENGTH:
        problems.append("The last message is too long. Put the content in the report file.")

    if status == "BLOCKED":
        if not fields.get("QUESTION"):
            problems.append("When BLOCKED, the QUESTION line must hold the question the conductor has to answer.")
    elif fields.get("QUESTION"):
        problems.append("Use the QUESTION line only when BLOCKED.")

    if files_hold_prose(fields.get("FILES") or ""):
        problems.append("FILES takes only file paths. Put explanations in the report file.")

    work_folder = None
    if report:
        path = report if os.path.isabs(report) else os.path.join(c.project_dir(data), report)
        path = os.path.realpath(path)
        if not c.in_reports(path, data):
            problems.append("The REPORT path must be inside `.orchestra/<work>/reports/`, as the task spec gives it.")
        elif not os.path.isfile(path) or os.path.getsize(path) == 0:
            problems.append("The report file `%s` does not exist or is empty. Write the full report there." % report)
        else:
            in_file = c.read_status(path)
            claims_done = claims_done or in_file == "DONE"
            if in_file not in c.STATUSES:
                problems.append(
                    "The first line of the report file must be `STATUS: DONE`, `STATUS: BLOCKED`, "
                    "or `STATUS: FAILED`."
                )
            elif status in c.STATUSES and in_file != status:
                problems.append(
                    "The STATUS in the report file (%s) differs from the STATUS in the last message (%s)."
                    % (in_file, status)
                )
            if task and c.task_id(path) != c.task_id(task):
                problems.append("TASK (%s) does not match the task that the report file name refers to." % task)
            if claims_done:
                problems.extend(verification_problems(path))
            work_folder, _rest = c.split_work_folder(path, data)
    return problems, {"status": status, "task": task, "report": report, "work_folder": work_folder,
                      "claims_done": claims_done}


def files_hold_prose(files):
    """Whether a FILES value holds anything but paths: an entry of several words is a sentence."""
    for item in re.split(r"[,\n]", files):
        item = item.strip().lstrip("-*• ").strip("`").strip()
        if len(item.split()) > 1 and item.lower() not in ("none", "n/a"):
            return True
    return False


def validate_scout(text):
    problems = []
    first = next((line.strip() for line in (text or "").splitlines() if line.strip()), "")
    m = re.match(r"^\W*STATUS\W*[:：]\s*(.*)$", first, re.I)
    status = " ".join(clean(m.group(1)).upper().split()) if m else ""
    if status not in ("FOUND", "NOT FOUND"):
        problems.append("The first line is not `STATUS: FOUND` or `STATUS: NOT FOUND`.")
    if len(text or "") > SCOUT_MAX_LENGTH:
        problems.append("The last message is longer than %d characters. Keep only what answers the question."
                        % SCOUT_MAX_LENGTH)
    return problems


def validate_editor(text):
    # Only the three key lines: the caller learns the result from the diff and from running the
    # check itself, and a narrative from the subagent would stand in for that.
    fields, stray = parse(text, EDITOR_KEY_LINE)
    problems = []
    missing = [k for k in EDITOR_KEYS if k not in fields]
    if missing:
        problems.append("These lines are missing: %s." % ", ".join(missing))
    status = (fields.get("STATUS") or "").upper()
    if "STATUS" in fields and status not in ("DONE", "FAILED"):
        problems.append("STATUS must be exactly DONE or FAILED.")
    check = re.fullmatch(r"exit\s+(-?\d+)", fields.get("CHECK") or "", re.I)
    if "CHECK" in fields and not check:
        problems.append("CHECK takes only `exit` and the exit code of the CHECK command, for example `exit 0`.")
    if status == "DONE" and check and int(check.group(1)) != 0:
        problems.append("DONE needs the CHECK command to exit with 0. Report FAILED, or fix the edit and run the check again.")
    if "FILES" in fields and not fields["FILES"]:
        problems.append("FILES is empty. Write the changed files, or none.")
    elif files_hold_prose(fields.get("FILES") or ""):
        problems.append("FILES takes only file paths.")
    if stray:
        problems.append("There are lines outside the format.")
    if len(text or "") > EDITOR_MAX_LENGTH:
        problems.append("The last message is longer than %d characters." % EDITOR_MAX_LENGTH)
    return problems


def check(agent, text, data):
    """Returns (problems, info, format help) for the last message of a roster subagent."""
    if agent == "haiku-scout":
        return validate_scout(text), {}, SCOUT_HELP
    if agent == "haiku-editor":
        return validate_editor(text), {}, EDITOR_HELP
    problems, info = validate(text, data)
    return problems, info, FORMAT_HELP


def record(data, agent, agent_id, info):
    if not info.get("work_folder"):
        return
    c.save_meta(data, info["work_folder"], c.task_id(info["task"]), {
        "agent": agent,
        "kind": c.ROSTER[agent]["kind"],
        "status": info["status"],
        "agent_id": agent_id,
    })


def bump(counter):
    try:
        with open(counter, encoding="utf-8") as f:
            n = int(f.read().strip() or "0")
    except (OSError, ValueError):
        n = 0
    n += 1
    try:
        with open(counter, "w", encoding="utf-8") as f:
            f.write(str(n))
    except OSError:
        pass
    return n


def remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def main():
    data = c.read_input()
    if c.in_cli_mode(data):
        c.allow()  # a CLI mode is on: the conductor keeps the rules by its instructions alone
    agent = c.bare(data.get("agent_type"))
    if agent not in c.ROSTER:
        c.allow()  # the conductor's own internal agents and agents off the roster are left alone

    agent_id = c.safe_name(data.get("agent_id") or "unknown")
    handback_flag = os.path.join(c.state_dir(), "handback-" + agent_id)
    counter = os.path.join(c.state_dir(), "attempts-" + agent_id)

    if data.get("hook_event_name") == "PreToolUse":
        # The report is being delivered through the SubagentHandback tool
        remove(handback_flag)
        message = (data.get("tool_input") or {}).get("message") or ""
        problems, info, help_text = check(agent, message, data)
        if problems:
            c.deny(help_text + "\nProblems: " + " ".join(problems))
        record(data, agent, agent_id, info)
        try:
            with open(handback_flag, "w", encoding="utf-8") as f:
                f.write(message)
        except OSError:
            pass
        c.allow()

    message = data.get("last_assistant_message") or ""
    if os.path.exists(handback_flag):
        message = c.read_text(handback_flag)
        remove(handback_flag)

    problems, info, help_text = check(agent, message, data)
    if not problems:
        record(data, agent, agent_id, info)
        remove(counter)
        c.allow()

    if bump(counter) > MAX_ATTEMPTS:
        remove(counter)
        c.warn_user(
            "Subagent %s could not fix its report in %d attempts, so it was allowed to finish. "
            "Its last message or its verification record does not follow the format." % (agent, MAX_ATTEMPTS)
        )
    c.block(help_text + "\nProblems: " + " ".join(problems))


if __name__ == "__main__":
    main()
