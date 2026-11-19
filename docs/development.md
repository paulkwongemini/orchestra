# Development notes

Notes for people who change the plugin. Users do not need this page.

## How the mode is decided

Two things decide it: whether the user has turned conductor mode on for the session, and which model the session runs on.

Conductor mode is off when a session starts. The user turns on one of three modes for the session: `claude` with `/orchestra:conduct` or `/orchestra:conductor`, `codex` with `/orchestra:conduct-codex`, and `agy` with `/orchestra:conduct-agy`. `/orchestra:disable` turns it off. A hook on the user's prompt records the mode per session, so only a command the user types changes it. The table of commands is `COMMAND_MODES` in `scripts/common.py`.

The rest of this section, and every section up to "How the CLI modes work", describes the `claude` mode. In `codex` and `agy` every guarding hook reads the mode first and exits without a decision.

While the mode is on, a session on a Fable (or Mythos) or an Opus model is a conductor session. The roster of subagents is the same for both. A session on any other model is an ordinary one.

A hook is not told which model a session runs on, so the plugin works it out.

- **From the session transcript.** Claude Code records each request, with its model, when the request is sent. That is before the response and its tool calls exist, so a hook knows the model from the first tool call of a session. Each response records its model as well.
- **From the `PostModelSwitch` hook.** It reports every model switch with the old and the new model.

The plugin delivers the instructions in two steps, because the skill's full text is longer than a hook's output may be.

1. The command tells the model to load the `orchestra:conductor` skill if it runs on Fable or Opus. A session in which the mode is already on gets the same request in a note at session start, after compaction, and after `/clear`. Before the model is known, the note covers both cases.
2. The conductor loads the skill. Until it has, a hook (`scripts/require_skill.py`) refuses every other tool call in the main conversation and tells it to load the skill. The skill counts as loaded when the conductor invokes it with the Skill tool or when the user runs `/orchestra:conductor`.

After the conversation is compacted or cleared, the record that the skill is loaded is dropped, and the session-start note says to load it again.

After a model switch into or out of Fable and Opus, a hook adds a note that says which mode applies from then on. A switch between Fable and Opus needs none.

Whether `/clear` keeps the session's id, and with it the record that the mode is on, is not documented and has not been checked. If it does not, a cleared session starts with the mode off.

## How an Opus conductor's effort is checked

Each request in the transcript records its effort level, under `params.output_config.effort`, and each response records it as `effort`. `scripts/require_skill.py` reads the newest one at every tool call of the main conversation in a conductor session.

When the conductor runs on Opus at `low` or `medium`, the hook refuses the call once, with a message that tells the conductor to recommend High or above to the user, ask whether to go on, and end its turn. The level it asked about is recorded, so the next call goes through whatever the user answered. The question comes again when the effort changes to the other low level, or when the user turns conductor mode on again. A raised effort needs no answer. When the transcript records no effort, the hook does not ask.

The check is the user's choice: an Opus conductor is meant to run at High or above, but the user decides. A Fable conductor is not checked.

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

The exception exists because of what delegation cost in one piece of work on this plugin. The conductor wrote the final text of two skill files and two documents into task specs, and two Sonnet subagents copied it out. That took about 35,000 subagent tokens and changed nothing in the result. The user chose the scope: files whose wording is the design, and documents whose changed statements the conductor decides. Code, configuration, and tests stay with the subagents.

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
- In a subagent definition, a list of types inside `Agent(...)` is ignored. The agent files therefore list a bare `Agent`, and `scripts/guard_agent.py` does the restricting. It tells the caller by `agent_type` in the hook input, which is the subagent the hook fires in, on a nested call too.
- A subagent does not load the plugin's skills, so the conditions for a call are written out in the four agent files as well as in the conductor's instructions. A change to the conditions goes into three places: `agents-src/_haiku-calls.md`, which three agents share, `agents-src/opus-analyst.md`, which has the scout's half, and the conductor's instructions.

`scripts/check_report.py` checks the last message of a Haiku subagent against its own format, and no report file exists for one. So `scripts/report_gate.py` has nothing to hold back, and the order of review and report that Principle 7 sets for a task is kept another way: the editor's last message may hold nothing but a status, the changed files, and an exit code.

Three things are instructions only. No hook checks whether a request needs no judgment, whether `haiku-editor` stayed inside the files it was given, or whether the caller opened the cited lines or ran the check again. The limit on a call's length is the nearest a hook comes to the first.

## How the task agents are written

The four task agents share most of their rules, and an agent file cannot include another file. Their sources are in `agents-src/`: one file per agent, and the shared rules as the parts `_*.md`. `scripts/build_agents.py` copies the parts into `agents/`. Change a source or a part, run the script, and keep both in the same commit. A test fails when `agents/` differs from what the sources give. The two Haiku agents share nothing and are written in `agents/` directly.

The roles of the CLI workers are not built this way, because a CLI worker can read a second file: each file in `roles/` names `roles/shared.md`.

## How the CLI modes work

The modes exist to spend the Codex or Antigravity CLI's plan on the implementation instead of the Claude plan, with the quality of the result unchanged. How a worker reaches its result does not matter. The result the conductor reviews does.

The user set the installation boundary: the plugin installs with the two commands in the README and needs no second step. No background service, no change to the user's Codex or agy configuration, and no file written into the user's project to set a mode up. Whatever a run needs is in the plugin's own files or in the settings that `scripts/worker.py` passes for that one run. That is why the guard reaches Codex through `-c` on the command line, and reaches agy through a workspace folder of the plugin's own.

The instructions have three layers, so that a further CLI adds one skill and one class in `scripts/worker.py`:

1. `orchestra:conductor`, unchanged, with `orchestra:interview` and `orchestra:documentation`.
2. `orchestra:cli-workers`, shared by the CLI modes: what changes when the workers are runs, and which rules nobody enforces now.
3. `orchestra:codex` or `orchestra:agy`: the facts of that CLI.

The modes are also a test of whether the conductor keeps its rules without hooks. `scripts/audit.py` reads a finished session's transcript and its work folder, and lists each point where a hook would have refused or flagged a call. The same audit runs on a `claude`-mode session, where the findings are attempts that a hook stopped, so the two kinds of mode compare on one measure. Whether the `claude` mode drops its hooks is the user's decision, taken from the audit's results. No bar is set in advance. The question about a low effort on Opus stays in every mode, because the conductor cannot know its own effort and no instruction can replace the hook.

The role files in `roles/` follow OpenAI's and Google's own prompting guides, [Prompting GPT-6.2 Astra](https://learn.chatgpt.com/guides/prompting-gpt-6-2-astra) and Google's [Engineer agentic prompts](https://docs.cloud.google.com/gemini/docs/discover/engineer-agentic-prompts). What the two ask of a coding worker is mostly the same, so the roles are shared by both CLIs. Where they differ, on how many tests to write, the task spec decides: it names the tests.

Facts about the CLIs that the code does not show:

- **Codex plugins.** `--disable plugins` turns every Codex plugin off for a run. Turning one plugin off with `-c 'plugins."<name>".enabled=false'` changed nothing in the model's input. `--disable multi_agent` does not remove Codex's multi-agent instructions, which the user accepts.
- **Seeing the model's input.** `codex debug prompt-input -c 'model="..."' <prompt>` prints what the model receives, without a model call. Use it after any change to the flags.
- **agy's hook input** carries `lastUserInput`, the whole prompt. A guard that searched its whole input would find the words of the task spec and refuse every call, so `scripts/worker_guard.py` reads only the call.
- **agy on a project under `/tmp`.** The first agy runs used one. The sandbox and its allow rules were then confirmed on a project under the home folder.

A new CLI needs an answer to each of these before its class is written:

1. How to run it once without interaction, with the prompt from a file, and whether anything makes it wait on stdin.
2. How to choose the model and the effort for one run, over the user's configuration.
3. How to keep the user's plugins, skills and MCP servers out of the run, and how to see the model's input to check that.
4. Where it may write, how to open one more folder, and whether it can reach the network.
5. What happens when it wants an approval: whether it asks, fails, or goes ahead.
6. How to pass it a hook for one run, and what the hook's input and answer look like.
7. How to get the last message and the token counts, and how to keep the rest of the output out of the conductor's context.
8. How to resume a session with a review comment, and which settings a resume keeps.
9. Which instruction files it reads by itself.
10. What happens at its plan's limit.

Not known for either CLI: how a run ends at the plan's limit, and whether a resumed Codex run reports its tokens per turn or in total.

## Running the tests

```bash
python3 -m unittest discover -s tests
python3 scripts/build_agents.py --check
claude plugin validate .
```

Each file under `tests/` also runs on its own, as `python3 tests/test_guard_main.py`. None of them starts a session or calls a model.

| File | What it runs |
|---|---|
| `test_session.py`, `test_require_skill.py`, `test_guard_agent.py`, `test_guard_main.py`, `test_reports.py` | The hook script of that name, and for `test_reports.py` both `report_gate.py` and `check_report.py`. Each test feeds the script JSON shaped like what Claude Code sends and checks the verdict |
| `test_doctor.py` | `scripts/doctor.py` |
| `test_worker.py` | `scripts/worker.py` against a fake `codex` and a fake `agy` on the PATH |
| `test_worker_guard.py` | The worker guard, on calls in the Codex and the agy formats |
| `test_audit.py` | The audit, on transcripts that the test writes |
| `test_agents.py` | That `agents/` matches `agents-src/` |

`tests/support.py` holds what they share: the fixtures, the temporary project, and the helpers that run a script and read its verdict.

## What has been verified

A record of what has been checked and how. Keep it true for the current version: when a change makes an entry false, rewrite the entry, and when a check is added or repeated, add it.

### By the hook tests

The tests cover every verdict of every hook script, among them:

- **Mode and model.** A new Fable or Opus session left alone until the mode is turned on. `conduct` and `conductor` each turning it on, and only as a command the user types. The model read from the newest request or response, and from a recorded switch. The switch notes into and out of Fable and Opus, and none between them.
- **The Opus conductor.** The same roster as Fable, with a fork, a call without a type and an agent off the roster refused. At `low` or `medium` effort, stopped once and then let through, and asked again only at the other low level or after the mode is turned on again. Not stopped at `high` and above, at an unknown effort, on Fable, or in an ordinary session.
- **Loading the rules.** Every tool refused until the conductor skill is loaded, and loaded again after compaction or `/clear`. A delegation refused until `orchestra:delegation` is loaded, and an implementation delegation until `orchestra:documentation` is too. An analysis delegation does not wait for the documentation rules.
- **The brief's confirmation.** An implementation delegation refused when the brief is newer than the user's last prompt or answer, and when another session or a subagent spoke instead.
- **Pre-approval.** Each key passing its own kind of command and no other. A brief newer than the user's last reply, a session with no record of the user speaking, and a session with no active work folder each pre-approving nothing. A production-marked data command going to the user with `data` pre-approved. A subagent that implements refused without the key and passing with it, and a read-only subagent refused with it. A writing `curl` request to a loopback host passing. A search for the name of a data command passing, while the same words anywhere else in the command are still refused. Four commands taken from a real session that was stopped by them pass with `data` pre-approved.
- **The files the conductor writes.** A listed file passing Write, Edit, NotebookEdit and a shell redirection. An unlisted file refused with the way to get it listed. A listed file refused when the brief is unconfirmed, when no work folder is active, and when only another work folder's brief lists it. A pattern, a directory, a path outside the project and a path inside `.orchestra/` naming nothing. `sed -i` on a listed file still refused.
- **Reports.** A `DONE` handback refused for a missing or invalid verification record, a failed or unrun check, a nonzero exit code, missing evidence, and an unverified requirement. `BLOCKED` and `FAILED` returning without one. The retry limit. A report changed after handback checked again. A report that does not open before its review record, and a reworked report that needs a new one.
- **The `files/` folder.** A subagent writing under `.orchestra/<work>/files/`, the same write refused elsewhere in the work folder, and the conductor reading `files/` without a review record.
- **The Haiku subagents.** The conductor starting `haiku-scout` with no task spec, and `haiku-editor` only under a confirmed brief. A call refused for a missing line and for a prompt over 1,500 characters. The subagents that implement starting both Haiku subagents and nothing else, `opus-analyst` starting only the scout, and a Haiku subagent starting nothing. A model that does not belong to the subagent refused both ways. The editor writing project files, nothing inside `.orchestra/`, and no command of a pre-approval kind. A further 32 inputs made by hand gave the expected verdicts.
- **The CLI modes.** `conduct-codex` and `conduct-agy` setting their mode, every guard stepping aside in it, and the effort question still asked. `scripts/worker.py` against a fake `codex` and a fake `agy` on the PATH: the settings of a first run and of a resume, the brief's network line and keys, and each warning of the check after a run. `scripts/worker_guard.py` on calls in the Codex and the agy formats. `scripts/audit.py` on transcripts that the test writes.

### In real sessions

These ran on Fable with conductor mode on from the first prompt, in the terminal or in the desktop app. The first entry ran with Claude Code 2.1.322 in the terminal.

- **Delegation and review.** Asked to create a file, the conductor loaded its skill first, wrote a brief and a task spec, and delegated to `sonnet-builder`. The subagent returned only the status lines. The conductor ran its own checks, wrote the review record, and then read the report. The same request in a Sonnet session was done directly, and the plugin did not interfere.
- **Refusals.** Told to write a file itself, the conductor called Write and was refused. A shell command with inline code that writes files was refused as well. Told "do not ask me anything", it wrote the brief and delegated in the same turn, and the delegation was refused until a later prompt confirmed the brief. An answer to `AskUserQuestion` counted as the confirmation.
- **Loading the skill.** Asked what a file says, the conductor loaded the skill before reading the file, and loaded it again after `/compact`. Told "do not load any skill", its Read call was refused, and it stopped and reported that.
- **The other paths.** A `BLOCKED` subagent stopped, changed nothing, and returned its question. A rework round went back to the same subagent, and the new result got a new review record before the new report was read. A hand-back through the `SubagentHandback` tool was accepted, with the text in the tool input's `message` field, which is the field the hook reads.
- **Mode and model.** After `/orchestra:disable` the session wrote a file itself, and `/orchestra:conductor` brought the conductor back. Switching the model to Sonnet and back, in the terminal and with the desktop app's picker, changed the mode both ways.
- **The interview and the brief.** The conductor asked one question per turn with the question tool, chose each next question from the last answer, wrote the brief only after the last answer, opened it in a side pane, and asked the fixed last question alone in its turn. In a small repository with two near-identical modules, asked to add a third, it listed two extras in the brief with reasons and changed no code.
- **The delegation rules.** The conductor loaded `orchestra:delegation` before its first delegation without being refused first, gave a well-specified task to `sonnet-builder` at the default effort, and recorded the choice and its reason in the decision log.
- **The Haiku calls.** The conductor's `haiku-scout` call passed with no task spec, ran at `effort: medium`, and handed back in the required form. The four lines it cited matched the conductor's own search. It took about 7,000 subagent tokens and 8 seconds. From inside `opus-analyst`, a scout call was allowed and its 18 cited lines matched the files, a call without `FIND:` was refused, and a `haiku-editor` call was refused, which shows that the hook receives the caller's `agent_type` on a nested call.
- **Hook input.** At the first tool call of a session, a hook can read the model of the request from the transcript, and each request records its effort level under `params.output_config.effort`. `SessionStart` does not carry the model. `PostModelSwitch` carries the old and the new model when a session is resumed.
- **Installation.** The plugin and its marketplace file validate, and the plugin installs from a marketplace.
- **Codex runs.** Through `scripts/worker.py` in a throwaway git project: an `implementer` run on Sol and its resume with one added rule, which kept the session and the settings, and an `analyst` run on Astra in which a write to a project file failed, `curl` could not resolve its host with the network blocked, and the run's own `files/` folder was writable. Each result passed the conductor's review. The guard refused `claude -p` and let `ls` through.
- **agy runs.** An `implementer` run on Hydrogen that passed review, its resume in the same conversation, and an `analyst` run on Neon that read the project, could not write it, and wrote its own folder. The guard refused a project write by the read-only role, and `claude -p`.
- **A relay by hand,** before the script existed: the conductor wrote a task spec, Codex implemented it, the conductor ran Codex's tests and its own check before it read the report, and a clarification went back through a resume. The second round passed.
- **The audit** on a real session's transcript found the one call that a hook had refused, and a task spec written before `orchestra:documentation` was loaded.

### Not yet exercised in a real session

- A whole piece of work in Codex mode or in agy mode, run by a conductor in a new session and followed by the audit. Whether the implementation's tokens show on the other plan and not on the Claude plan.
- `/orchestra:conduct` turning the mode on, a request on the same line reaching the conductor, an Opus conductor, the effort question, and whether the mode survives `/clear`.
- A `haiku-editor` call that runs, a Haiku call from a subagent that implements, Haiku chosen on its own when the conditions hold and only then, and whether token use falls.
- The interview asking for pre-approval and writing the line, a pre-approved command running without a prompt, a refusal leading the conductor to change the brief and ask, and a subagent refused and reporting `BLOCKED`.
- The interview asking which files the conductor may write, the conductor writing a listed file, and its decision-log line.
- The interview asking about a worktree, or not asking when the project's instructions forbid one. A task spec listing its files by path, the conductor comparing the file lists of tasks, and the gate run with no subagent editing.
- A conversation that starts with a question and turns to work, and a follow-up to confirmed work going ahead without a new interview.
- The task groups in the decision log, the close of a group with and without a docs task, and the comment and document checks in a review.
- An interview that leaves nothing to ask, the user ending the interview early, a task that the delegation rules send to Opus, and an effort level other than the default.
- A `DONE` report from a real subagent carrying the verification record, a hand-back in the wrong format being refused, and a review that fails a change outside the task's scope.
