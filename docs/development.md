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

The rules are in a skill of their own, `orchestra:delegation`, so that they can be long without lengthening the conductor's instructions. `scripts/require_skill.py` records when the skill is loaded, and `scripts/guard_agent.py` refuses the delegation of a task until then. A Haiku call does not wait for it, because the conditions for one are in the conductor's instructions. Compaction and `/clear` drop the record.

The rules are a skill and not a file beside the conductor skill, because a session cannot read a file in the plugin's folder without a permission prompt, and in a session with nobody to answer the prompt the read is refused. This was seen in a real session.

The rules come from Anthropic's prompting guides for Claude Opus 5.5 and Claude Sonnet 5.5 and from the effort documentation. The default effort of `opus-engineer` is `medium`, the level those guides recommend to start from.

## How the documentation rules are connected

The rules for documents and comments are a skill of their own, `orchestra:documentation`, connected the same way as the delegation rules. `scripts/require_skill.py` records when it is loaded, and `scripts/guard_agent.py` refuses an implementation delegation until then. A delegation to `opus-analyst` does not wait for it, because an analysis changes no document. Compaction and `/clear` drop the record.

The conductor splits the work into task groups, and the document changes of a group are made once, when every task of the group has passed review: in a docs task, or by the conductor itself when the brief lists every file to change on its `Conductor writes:` line. `opus-engineer` and `sonnet-builder` are told in their agent files not to edit a document unless the task spec names the file. No hook enforces that, because a hook cannot tell a document from a text file that is the product: in this repository the skills and the agent files are Markdown.

The hook checks only that the rules are loaded. Whether a group is closed before the final report, and whether a review ran the comment and document checks, is left to the conductor.

The conductor's instructions do not use the ownership sentence from "Say who owns the code" in Anthropic's prompting guide for Claude Fable 5.2. That guide measured 70 to 80 percent more output tokens with it, and changes nobody had asked for, many of them new tests and documentation. Both work against what this plugin is for.

## How a request becomes work

The rules are under "Direct answer or work" in the conductor's instructions and in step 1 of `orchestra:interview`. They are instructions only. No hook checks whether a message was judged as work, or whether the interview came before an action. The user chose to run them by instruction alone.

The rules were written after real sessions in which a conversation began with a question and then turned to work. The conductor went straight to commands that need approval, such as ssh to a production server to read its logs, and the user had to allow them one at a time before being asked what they wanted.

The hook is looser than the instructions in one place. With no confirmed brief, `scripts/guard_main.py` opens a confirmation prompt for a `push`, `github`, `http`, `ssh` or `deploy` command and does not refuse it. A conductor that does not follow the instructions can still put that prompt in front of the user.

## How the brief's confirmation is checked

A hook records the time whenever the user speaks in the main conversation: on every prompt (`UserPromptSubmit`), and when the user answers a question (`PostToolUse` of `AskUserQuestion`). When the conductor delegates implementation, the brief must not be newer than that time. A brief that was written or changed after the user last spoke cannot have been confirmed, so the delegation is refused.

The conductor cannot decide on its own that it has understood the intent, so the instructions fix the last question of that step: after writing the brief it always asks "Do you want me to wait until you confirm the brief?", with the options to start now or to review the brief first. The answer to that question is what passes the check.

The check does not read what the user said. When a session has no record of the user speaking, the check is skipped.

The interview that leads to the brief is a skill of its own, `orchestra:interview`. No hook checks that it is loaded or that a turn holds one question. The user chose to run it by instruction alone first.

## How pre-approval is checked

A hook call for a shell command does not say which piece of work it belongs to. The plugin records the session's active work folder when the conductor reads or writes a `brief.md`, and when it delegates with a task spec. Reading is included so that a new session that continues earlier work gets its pre-approvals back by reading the brief, which the conductor's instructions already tell it to do. A session with no record has nothing pre-approved.

Confirmation uses the same record of the user speaking as the brief's confirmation, with one difference: when a session has no record of the user speaking, nothing is pre-approved, whereas the delegation check is skipped in that case.

The check does not read what the user said, so any reply after the brief changed counts, a refusal included. The conductor's instructions tell it to remove the key when the user says no.

The rules read the command line of the host shell. The command inside `docker exec … bash -lc '…'` is not parsed, because its paths are container paths that the host's write rules cannot judge. The data rule is the exception: it reads the text of every part of the command, so it also finds a migration inside a wrapper. It leaves out one thing, the arguments of a search program: `grep`, `egrep`, `fgrep`, `rg`, and `git` with `grep`, `log`, `show` or `diff`. A search for `db:seed` passes. A search program run with an option that starts another program, `rg --pre` for example, is not left out. A command that prints or writes such text, an `echo` for example, is still refused, because its output can be piped into a shell. The refusal tells the reader to use the Grep, Read or Write tool for that.

## How the files the conductor writes are checked

The conductor may write a project file when the brief of the session's active work folder lists its path on the `Conductor writes:` line under `## Decision rights`. `scripts/guard_main.py` finds that brief and its confirmation the same way as for pre-approval, with the same strict rule: when a session has no record of the user speaking, nothing is listed. Like a pre-approval key, the line holds for as long as that brief's folder is the session's active work folder. That it does not carry over to new work is an instruction.

The exception exists because of what delegation cost in the work on version 0.1.6. The conductor wrote the final text of two skill files and two documents into task specs, and two Sonnet subagents copied it out. That took about 35,000 subagent tokens and changed nothing in the result. The user chose the scope: files whose wording is the design, and documents whose changed statements the conductor decides. Code, configuration, and tests stay with the subagents.

The hook checks only the path. It cannot tell a skill from a source file, so which files may go on the line is left to the instructions and to the user, who sees the line in the brief before confirming it. The hook does not check what is written either.

An entry is matched as one exact file. Underscores at the ends of an entry are kept, because a file name may begin or end with one. Asterisks at the ends are dropped as Markdown emphasis.

The shell check uses the same list. A redirection, `tee`, `cp`, `mv` or `rm` aimed at a listed file passes, so the conductor can also delete or move a listed file from the shell. Commands that are refused whatever the path, such as `sed -i`, an editor, and inline code that writes, are still refused. The instructions tell the conductor to use the Edit and Write tools.

A file the conductor writes has no report and no review record, so `scripts/report_gate.py` and `scripts/check_report.py` have nothing to check for it. The conductor's own checks, and its line in the decision log, are instructions only.

## How subagents share one directory

The rules under "Working in one directory" in the conductor's instructions are instructions only. No hook checks which files a subagent edits, whether two tasks hold the same file, when a whole-tree check runs, or whether a delegation uses worktree isolation. The user chose to run them by instruction alone first.

The hook is looser than the instructions in one place. With `git` or `push` pre-approved in the brief, `scripts/guard_main.py` lets an implementation subagent run those commands. The agent files tell the subagents not to, and the conductor's instructions keep those commands for the conductor.

The default is one working directory with no worktree, because results then need no merging. A worktree is used only with an approval that the interview writes into the brief.

## How DONE reports are checked

`scripts/check_report.py` checks both `SubagentHandback` and `SubagentStop`. Every Opus or Sonnet subagent returning `DONE` must put a fenced JSON object under `## What I verified` in its report. The object has a nonempty `checks` list and an empty `unverified` list. Each check names its `criterion`, `kind`, `method`, `result`, and `evidence`. All results must be `PASS`. A `command` check also needs an integer `exit_code` of `0`; a `manual` check records the comparison and evidence required by the task spec. The agent files contain complete examples.

Missing or malformed records, failed or unrun checks, missing evidence, nonzero command exits, and remaining unverified requirements refuse a `DONE` handback. `BLOCKED` and `FAILED` can return without passing verification records so that the conductor can address unfinished work. Syntax-only checks do not satisfy executable changes, and agents cannot substitute a manual check for a required command.

The retry limit for malformed stop messages covers an invalid `DONE` too: after three refusals the hook warns the user and lets the subagent stop, so a subagent that cannot fix its report does not keep spending tokens. The conductor's own review decides whether the work is accepted. Text around the JSON block is allowed. A valid `BLOCKED` or `FAILED` report can end the attempt at any time. After an accepted handback, the stop hook reads and validates the report again; a changed report cannot rely on its earlier approval. Older reports need the verification record if they are handed back as `DONE` again.

These records are declarations, not independent proof that commands ran or that every acceptance criterion was covered. The hook checks their structure and declared results. The conductor still checks the final result, acceptance coverage, and evidence independently before accepting it. This check does not collect or compare tool execution logs.

## How Haiku calls are checked

The user set one test for using Haiku: the same result for fewer tokens, with nothing changed in how the user works with the conductor. The design rests on these prices per million tokens, input and output: Haiku $0.10 and $0.50, Sonnet $2 and $10, Opus $4 and $20, Fable $8 and $40. At a twentieth of Sonnet's price and an eightieth of Fable's, starting a Haiku subagent costs next to nothing. So the conditions for a call do not ask how large the work is. They ask whether the result could differ, and they admit only work that a stronger model checks against the files in less effort than doing it. That is where the reason for the model floor does not apply.

A subagent runs on one model, so its searches and repeated edits cannot go to a cheaper model one tool call at a time. The user chose to let the Opus and Sonnet subagents start the Haiku subagents themselves, as the one exception to Principle 6.

- Claude Code lets a subagent start subagents up to three layers below the main conversation by default. A Haiku call from a subagent is two layers down. With `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` set to `1`, the subagents are not given the `Agent` tool and do all of the work themselves. The conductor's own Haiku calls still work.
- In a subagent definition, a list of types inside `Agent(...)` is ignored. The agent files therefore list a bare `Agent`, and `scripts/guard_agent.py` does the restricting. It tells the caller by `agent_type` in the hook input, which Claude Code's documentation says is the subagent the hook fires in.
- A subagent does not load the plugin's skills, so the conditions for a call are written out in the four agent files as well as in the conductor's instructions. The section is the same, word for word, in `opus-engineer.md`, `sonnet-builder.md` and `sonnet-writer.md`. `opus-analyst.md` has the scout's half. A change to the conditions goes into all five places.

`scripts/check_report.py` checks the last message of a Haiku subagent against its own format, and no report file exists for one. So `scripts/report_gate.py` has nothing to hold back, and the order of review and report that Principle 7 sets for a task is kept another way: the editor's last message may hold nothing but a status, the changed files, and an exit code.

Three things are instructions only. No hook checks whether a request needs no judgment, whether `haiku-editor` stayed inside the files it was given, or whether the caller opened the cited lines or ran the check again. The limit on a call's length is the nearest a hook comes to the first.

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
- **The `files/` folder and the rule against reading earlier reports, by the hook tests on 2026-11-13.** All 118 tests passed, including the tests for a subagent writing under `.orchestra/<work>/files/` with Write, Edit, and a shell redirect, the same write refused elsewhere in the work folder, and the conductor reading `files/` without a review record. Not exercised in a real session: the conductor naming a `files/` folder in a task spec, and a subagent leaving earlier reports unread. The second is an instruction only; no hook enforces it.
- **Pre-approval, by the hook tests on 2026-11-14.** All 149 tests passed, including the tests for: a key passing its own kind of command and no other; a brief that is newer than the user's last reply, a session with no record of the user speaking, and a session with no active work folder each pre-approving nothing; a production-marked data command going to the user with `data` pre-approved; a subagent that implements refused without the key and passing with it; a read-only subagent refused with the key; a writing `curl` request to a loopback host passing; a command with a quoted parenthesis inside `$(...)` being read correctly; and a search for the name of a data command passing for the conductor and for both kinds of subagent, while the same words anywhere else in the command are still refused. The four commands that stopped a real overnight session on 2026-11-13 were replayed through the guard's judging function: with `data` pre-approved all four pass. Not exercised in a real session: the interview asking for pre-approval and writing the line, a pre-approved command running without a prompt, a refusal leading the conductor to change the brief and ask, a subagent being refused and reporting `BLOCKED`, and a subagent's hook call carrying the session id of the main conversation, which finding the brief depends on.
- **The shared-directory rules and the worktree policy, on 2026-11-14.** The change touched instruction files only. All 149 hook tests and the plugin validator passed after it. Not exercised in a real session: a task spec listing its files by path, the conductor comparing the file lists of tasks before it delegates, a subagent leaving a git change to the conductor, the gate run with no subagent editing and recorded in the decision log, the interview asking about a worktree, and the interview not asking when the project's instructions forbid one.
- **The interview-first rules, on 2026-11-16.** The change touched instruction files only. All 149 hook tests and the plugin validator passed after it. Not exercised in a real session: a conversation that starts with a question and then turns to work, a question that needs an action the user would have to allow being treated as work, the interview holding such an action back until the brief is confirmed, and a follow-up to confirmed work going ahead without a new interview.
- **The files the conductor writes, by the hook tests on 2026-11-16.** All 171 tests passed, including the tests for: a listed file passing Write, Edit, NotebookEdit and a shell redirection; an unlisted file refused with the way to get it listed; a listed file refused when the brief is newer than the user's last reply, when no user turn is recorded, when no work folder is active, and when only the brief of another work folder lists it; a pattern, a directory, a path outside the project and a path inside `.orchestra/` naming nothing; an entry keeping its underscores; `sed -i` on a listed file still refused; and both kinds of subagent judged as before. Not exercised in a real session: the interview asking which files the conductor may write, the conductor writing a listed file, a refused write leading it to delegate, the decision-log line for a file it wrote, and the close of a task group with documents written by the conductor.
- **The Haiku subagents, by the hook tests on 2026-11-18.** All 195 tests passed, including the tests for: the conductor starting `haiku-scout` with no task spec, no brief and no skill beyond its instructions; the conductor starting `haiku-editor` only under a confirmed brief, refused when no work folder is active, when there is no brief, when the brief is newer than the user's last reply, and when no user turn is recorded; a call refused for a missing or empty line and for a prompt over 1,500 characters; the three subagents that implement starting both Haiku subagents and nothing else; `opus-analyst` starting only the scout; a Haiku subagent starting nothing; a model that does not belong to the subagent refused both ways; the scout judged as read-only; the editor writing project files, nothing inside `.orchestra/`, and no command of a pre-approval kind with its key pre-approved; both last-message formats, the retry limit, and the hand-back path. A further 32 inputs made by hand for the review gave the expected verdicts. The plugin validator passed. Not exercised in a real session: any Haiku call. That covers an `Agent` call inside a subagent reaching the hook with the caller's `agent_type`, a subagent with `Agent` among its tools being offered the Haiku subagents, `effort: medium` being accepted for a Haiku subagent, the conductor and the subagents using Haiku when the conditions hold and only then, and whether token use falls.
- **In real sessions, with Claude Code 2.1.322 in the terminal.** The same request, "create a file", went to a Sonnet session and to a Fable session with the plugin loaded.
  - The Sonnet session wrote the file itself. The plugin did not interfere.
  - The Fable session loaded the conductor skill first, wrote a brief and a task spec, and delegated to `orchestra:sonnet-builder`. The subagent returned only the status lines. The conductor ran its own checks, wrote the review record, and then read the report.
- **The other paths, in real Fable sessions.**
  - A refused edit: told to write a file itself, the conductor called Write and the hook refused it. A shell command with inline code that writes files was refused as well.
  - A `BLOCKED` report: the subagent stopped, changed nothing, and returned its question.
  - A rework round: the conductor sent the task back to the same subagent, reviewed the new result, wrote a new review record, and then read the new report.
  - A model switch: a Fable session continued on Sonnet could write files, and a Sonnet session continued on Fable could not.
- **Turning the mode off and on, in a real Fable session.** After `/orchestra:disable`, Fable wrote a file itself. After `/orchestra:enable` in the same session, the same edit was refused. `/orchestra:conductor` after a disable is covered by the test suite only.
- **The brief's confirmation, in a real Fable session.** Told "do not ask me anything", Fable wrote the brief and delegated in the same turn, and the delegation was refused. After a second prompt that confirmed the brief, the same delegation went through. An answer to `AskUserQuestion` counted as the confirmation in a real session in the desktop app on 2026-11-13.
- **Loading the skill, in a real Fable session.** Asked what a file says, Fable loaded the skill first and then read the file. After `/compact` in the same session, it loaded the skill again before its next tool call. In a session told "do not load any skill", the Read call was refused, and Fable stopped and reported that instead of loading the skill.
- **The hand-back tool, in a real Fable session in auto mode.** The subagent delivered its last message through the `SubagentHandback` tool, with the text in the tool input's `message` field, which is the field the hook reads. The hook accepted it and recorded the task, and the conductor reviewed and then read the report as usual. Not exercised in a real session: a hand-back in the wrong format being refused. The test suite covers it.
- **Proposed extras, in a real Fable session.** In a small repository with two near-identical report modules, asked to add a third, Fable listed two extras in the brief (a shared module, and tests) with reasons and with what happens if each is dropped, named them in its last question, and changed no code. Not exercised: the implementation after the answer, and a review that fails a change outside the task's scope.
- **The delegation rules, in a real Fable session.** Fable loaded `orchestra:delegation` before its first delegation without being refused first, gave a well-specified task to `sonnet-builder` at the default effort, and recorded the choice with its reason in the decision log. Not exercised: a task that the rules send to Opus, and an effort level other than the default.
- **In the desktop app, in one real session.** A Fable chat loaded the conductor skill, confirmed a brief, delegated to `sonnet-builder`, reviewed, and then read the report. After `/orchestra:disable` Fable wrote a file itself, and `/orchestra:conductor` brought the conductor back. Switching the chat to Sonnet and back with the model picker changed the mode both ways. Not exercised in the app: a refused edit, `/orchestra:enable`, and `/orchestra:setup`.
- **About hook input, in real sessions.** At the first tool call of a session, a hook can read the model of the request from the transcript. `SessionStart` does not carry the model. `PostModelSwitch` carries the old and the new model when a session is resumed.
- **By the test suite.** Every verdict of every hook script.
- **By the plugin tools.** The plugin and its marketplace file validate, and the plugin installs from a marketplace.
- **The interview skill, in one real session in the desktop app on 2026-11-16, with version 0.1.5.** The conductor asked one question per turn with the question tool, chose each next question from the last answer, wrote the brief only after the last answer, opened it in a side pane, and asked the fixed last question alone in its turn. Not exercised: an interview that leaves nothing to ask, and the user ending the interview early.
