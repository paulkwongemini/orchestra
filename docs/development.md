# Development notes

Notes for people who change the plugin. Users do not need this page.

## How the mode is decided

A hook is not told which model a session runs on, so the plugin works it out.

- **From the session transcript.** Claude Code records each request, with its model, when the request is sent. That is before the response and its tool calls exist, so a hook knows the model from the first tool call of a session. Each response records its model as well.
- **From the `PostModelSwitch` hook.** It reports every model switch with the old and the new model.

The plugin delivers the instructions in two steps, because a hook has no model to go by at the moment a session starts.

1. At session start, a hook adds a short note for both cases: "if you are running on a Fable model, you are the conductor, so load the `orchestra:conductor` skill. Otherwise ignore this."
2. Fable loads the skill. Until it has, a hook (`scripts/require_skill.py`) refuses every other tool call in the main conversation and tells it to load the skill. The skill counts as loaded when Fable invokes it with the Skill tool or when the user runs `/orchestra:conductor`.

After the conversation is compacted or cleared, the record that the skill is loaded is dropped, and the session-start note says to load it again. The skill's full text is not put into the note itself, because it is longer than a hook's output may be.

After a model switch, a hook adds a note that says which mode applies from then on.

A user can turn conductor mode off for one session with `/orchestra:disable`. A hook on the user's prompt records that, so only a command the user types changes it. `/orchestra:enable` and `/orchestra:conductor` clear the record.

## How the subagent and the effort level are chosen

The rules are in a skill of their own, `orchestra:delegation`, so that they can be long without lengthening the conductor's instructions. `scripts/require_skill.py` records when the skill is loaded, and `scripts/guard_agent.py` refuses a roster delegation until then. Compaction and `/clear` drop the record.

The rules are a skill and not a file beside the conductor skill, because a session cannot read a file in the plugin's folder without a permission prompt, and in a session with nobody to answer the prompt the read is refused. This was seen in a real session.

The rules come from Anthropic's prompting guides for Claude Opus 5.5 and Claude Sonnet 5.5 and from the effort documentation. The default effort of `opus-engineer` is `medium`, the level those guides recommend to start from.

## How the documentation rules are connected

The rules for documents and comments are a skill of their own, `orchestra:documentation`, connected the same way as the delegation rules. `scripts/require_skill.py` records when it is loaded, and `scripts/guard_agent.py` refuses an implementation delegation until then. A delegation to `opus-analyst` does not wait for it, because an analysis changes no document. Compaction and `/clear` drop the record.

The conductor splits the work into task groups, and the document changes of a group are made once, in a docs task, when every task of the group has passed review. `opus-engineer` and `sonnet-builder` are told in their agent files not to edit a document unless the task spec names the file. No hook enforces that, because a hook cannot tell a document from a text file that is the product: in this repository the skills and the agent files are Markdown.

The hook checks only that the rules are loaded. Whether a group is closed before the final report, and whether a review ran the comment and document checks, is left to the conductor.

The conductor's instructions do not use the ownership sentence from "Say who owns the code" in Anthropic's prompting guide for Claude Fable 5.2. That guide measured 70 to 80 percent more output tokens with it, and changes nobody had asked for, many of them new tests and documentation. Both work against what this plugin is for.

## How the brief's confirmation is checked

A hook records the time whenever the user speaks in the main conversation: on every prompt (`UserPromptSubmit`), and when the user answers a question (`PostToolUse` of `AskUserQuestion`). When the conductor delegates implementation, the brief must not be newer than that time. A brief that was written or changed after the user last spoke cannot have been confirmed, so the delegation is refused.

The conductor cannot decide on its own that it has understood the intent, so the instructions fix the last question of that step: after writing the brief it always asks "Do you want me to wait until you confirm the brief?", with the options to start now or to review the brief first. The answer to that question is what passes the check.

The check does not read what the user said. When a session has no record of the user speaking, the check is skipped.

## How DONE reports are checked

`scripts/check_report.py` checks both `SubagentHandback` and `SubagentStop`. Every roster agent returning `DONE` must put a fenced JSON object under `## What I verified` in its report. The object has a nonempty `checks` list and an empty `unverified` list. Each check names its `criterion`, `kind`, `method`, `result`, and `evidence`. All results must be `PASS`. A `command` check also needs an integer `exit_code` of `0`; a `manual` check records the comparison and evidence required by the task spec. The agent files contain complete examples.

Missing or malformed records, failed or unrun checks, missing evidence, nonzero command exits, and remaining unverified requirements refuse a `DONE` handback. `BLOCKED` and `FAILED` can return without passing verification records so that the conductor can address unfinished work. Syntax-only checks do not satisfy executable changes, and agents cannot substitute a manual check for a required command.

The retry limit for malformed stop messages covers an invalid `DONE` too: after three refusals the hook warns the user and lets the subagent stop, so a subagent that cannot fix its report does not keep spending tokens. The conductor's own review decides whether the work is accepted. Text around the JSON block is allowed. A valid `BLOCKED` or `FAILED` report can end the attempt at any time. After an accepted handback, the stop hook reads and validates the report again; a changed report cannot rely on its earlier approval. Older reports need the verification record if they are handed back as `DONE` again.

These records are declarations, not independent proof that commands ran or that every acceptance criterion was covered. The hook checks their structure and declared results. The conductor still checks the final result, acceptance coverage, and evidence independently before accepting it. This check does not collect or compare tool execution logs.

## Running the tests

```bash
python3 tests/test_hooks.py
claude plugin validate .
```

`tests/test_hooks.py` feeds each hook script JSON shaped like what Claude Code sends and checks the verdict. It starts no session, so it makes no model calls.

## What has been verified

A record of what was checked and how. Update it when a check is added or repeated.

- **DONE verification records, by the hook tests on 2026-11-12.** All 112 tests passed on 2026-11-13, including the tests for missing or invalid verification records, failed or unrun checks, command exit codes, manual evidence, text around the JSON block, unfinished statuses, the retry limit on invalid `DONE` claims, and reports changed after handback. The plugin validator passed with an icon-missing warning. The new verification-record format has not yet been exercised in a real model session.
- **The documentation rules, by the hook tests on 2026-11-13.** All 117 tests passed, including the tests for an implementation delegation refused until `orchestra:documentation` is loaded, an analysis delegation that does not wait for it, and the record dropped by compaction. Not exercised in a real session: the conductor loading the skill before its first task spec, the task groups in the decision log, the close of a group with and without a docs task, and the comment and document checks in a review.
- **In real sessions, with Claude Code 2.1.322 in the terminal.** The same request, "create a file", went to a Sonnet session and to a Fable session with the plugin loaded.
  - The Sonnet session wrote the file itself. The plugin did not interfere.
  - The Fable session loaded the conductor skill first, wrote a brief and a task spec, and delegated to `orchestra:sonnet-builder`. The subagent returned only the status lines. The conductor ran its own checks, wrote the review record, and then read the report.
- **The other paths, in real Fable sessions.**
  - A refused edit: told to write a file itself, the conductor called Write and the hook refused it. A shell command with inline code that writes files was refused as well.
  - A `BLOCKED` report: the subagent stopped, changed nothing, and returned its question.
  - A rework round: the conductor sent the task back to the same subagent, reviewed the new result, wrote a new review record, and then read the new report.
  - A model switch: a Fable session continued on Sonnet could write files, and a Sonnet session continued on Fable could not.
- **Turning the mode off and on, in a real Fable session.** After `/orchestra:disable`, Fable wrote a file itself. After `/orchestra:enable` in the same session, the same edit was refused. `/orchestra:conductor` after a disable is covered by the test suite only.
- **The brief's confirmation, in a real Fable session.** Told "do not ask me anything", Fable wrote the brief and delegated in the same turn, and the delegation was refused. After a second prompt that confirmed the brief, the same delegation went through. Not exercised in a real session: an answer to `AskUserQuestion` counting as the confirmation. The test suite covers it.
- **Loading the skill, in a real Fable session.** Asked what a file says, Fable loaded the skill first and then read the file. After `/compact` in the same session, it loaded the skill again before its next tool call. In a session told "do not load any skill", the Read call was refused, and Fable stopped and reported that instead of loading the skill.
- **The hand-back tool, in a real Fable session in auto mode.** The subagent delivered its last message through the `SubagentHandback` tool, with the text in the tool input's `message` field, which is the field the hook reads. The hook accepted it and recorded the task, and the conductor reviewed and then read the report as usual. Not exercised in a real session: a hand-back in the wrong format being refused. The test suite covers it.
- **Proposed extras, in a real Fable session.** In a small repository with two near-identical report modules, asked to add a third, Fable listed two extras in the brief (a shared module, and tests) with reasons and with what happens if each is dropped, named them in its last question, and changed no code. Not exercised: the implementation after the answer, and a review that fails a change outside the task's scope.
- **The delegation rules, in a real Fable session.** Fable loaded `orchestra:delegation` before its first delegation without being refused first, gave a well-specified task to `sonnet-builder` at the default effort, and recorded the choice with its reason in the decision log. Not exercised: a task that the rules send to Opus, and an effort level other than the default.
- **In the desktop app, in one real session.** A Fable chat loaded the conductor skill, confirmed a brief, delegated to `sonnet-builder`, reviewed, and then read the report. After `/orchestra:disable` Fable wrote a file itself, and `/orchestra:conductor` brought the conductor back. Switching the chat to Sonnet and back with the model picker changed the mode both ways. Not exercised in the app: a refused edit, `/orchestra:enable`, and `/orchestra:setup`.
- **About hook input, in real sessions.** At the first tool call of a session, a hook can read the model of the request from the transcript. `SessionStart` does not carry the model. `PostModelSwitch` carries the old and the new model when a session is resumed.
- **By the test suite.** Every verdict of every hook script.
- **By the plugin tools.** The plugin and its marketplace file validate, and the plugin installs from a marketplace.
