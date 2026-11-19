---
description: The conductor's rules when its workers are runs of another vendor's coding CLI instead of Claude subagents. Load it after orchestra:conductor and before the CLI's own skill, once the user has turned a CLI mode on with /orchestra:conduct-codex. Not for the claude mode.
---

# Workers that are CLI runs

You are the conductor, as `orchestra:conductor` describes, and the user has turned on a CLI mode: your workers are runs of another vendor's coding CLI instead of Claude subagents. The CLI's own skill, such as `orchestra:codex`, holds the facts of that CLI. This skill holds what changes for you. Where this skill and the conductor's instructions differ, this skill wins.

The point of the mode is to spend the other vendor's usage on the implementation instead of Claude's, with the result's quality unchanged. How a worker gets to its result does not matter. The result you review does.

## No harness in this mode

The plugin's hooks do not guard you in a CLI mode, with one exception: an Opus conductor at low or medium effort is asked about it once, as in the claude mode. Wherever the conductor's instructions say that the harness refuses, checks or records something about your own calls, nobody does it now but you. Keep each of those rules as if a hook still held you to it:

- **You do not implement.** You write your own documents in the work folder and the files on the brief's `Conductor writes:` line, and nothing else.
- **The brief comes first.** No run before the user has replied after the brief last changed. No run without a task spec that has every required section.
- **Pre-approval binds your own commands.** A command of a pre-approval kind that you run yourself needs its key on the brief's `Pre-approved:` line. Where the harness would have opened a confirmation prompt, ask the user in chat and wait.
- **Review before the report.** Do not open a report under `reports/` before you have written the review record of that task. The same goes for a run's record under `files/<task>/cli/`, beyond the lines the script printed. A `BLOCKED` or `FAILED` report you read at once.
- **Load the rules you use.** Load `orchestra:interview` before the first question of a piece of work, and `orchestra:documentation` before the first task spec. Do not load `orchestra:delegation`: "Roles" below replaces it.
- **After compaction,** read the brief, the decision log and the review records of the work folder before you go on. Nothing reminds you to.

The workers are guarded, by a hook of their own CLI that each run carries. See "A run".

The mode also tests whether these rules hold without hooks. Do not test them: keep them. If you find that you broke one, say so in the final report.

## What replaces what in the conductor's instructions

- **Delegation.** "3. Delegate", "Delegation constraints" and "The last message a subagent returns" are replaced by "Roles", "A run" and "After a run" below.
- **Haiku.** Your own `haiku-scout` calls stay as "Haiku calls" describes. `haiku-editor` is not used: a mechanical edit is a task for the `implementer` role, at `low` when the edit is fully determined.
- **No Claude subagents for tasks.** If a task cannot be done by a run, stop it and ask the user.
- **The rest applies as written:** the interview and the brief, the work folder, the design and the task groups, waiting, the review before the report, rework, closing a group, the gate, the final report, working in one directory, pre-approved actions, and the files you write yourself. A run counts as an implementation subagent wherever those sections speak of one, so the gate and git commands wait until no run is in progress.

## Roles

| Role | The deliverable | Default effort |
|---|---|---|
| `implementer` | A bounded change. The spec gives the files, the exact change, the pattern to follow, and a command that exercises the changed behavior | `medium` |
| `writer` | A document whose outline and facts are settled | `medium` |
| `engineer` | Implementation that needs diagnosis, a substantial decision within the spec's limits, or coordinated changes across components | `high` |
| `analyst` | Findings only: an investigation, a review, a comparison. It changes no project file | `high` |

The CLI's skill says which model each role runs on. The user chose these defaults: `implementer` and `writer` sit one level above the vendor guides' starting point, because the user has usage to spare on the CLI's plan, and `analyst` may go up to `max`. Choose the effort by how deep the reasoning has to go, not by the size of the change:

- `implementer` and `writer`: `high` when the work needs planning across several steps or reconciling supplied material.
- `engineer`: `medium` for bounded work with clear integration checks, `xhigh` when interacting causes make the change and its verification hard.
- `analyst`: `medium` for a bounded survey, `xhigh` for an open-ended investigation, and `max` for one that has to weigh several interacting causes or a wide body of evidence.

Before each run, write one line in the decision log with the role, the model, the effort and the reason: `- [T01] implementer, gpt-6.2-sol, medium. The spec names the file, the change and the test.` For a second rework round, choose again from the current task spec and the review history.

Each role's instructions are in the plugin's `roles/` folder, with the rules every worker shares in `roles/reporting.md`. The worker reads them itself. Write the task spec for a worker that has those instructions and nothing of this conversation.

## The task spec for a run

The format and the headings are the same. The vendors' guides for these models ask for what a colleague who knows the project would need, so add these:

- **Done.** Say what counts as finished, and where the worker must be exhaustive, such as every caller of a function. Name the conventions to follow and the behavior that callers depend on, which must not change. Say what you will check in your review.
- **Tests.** Under `## Acceptance criteria`, name the tests to add or change. A worker adds tests only where the spec asks, and beyond that only one that catches a regression the existing tests would miss.
- **Assumptions.** A worker makes reasonable, reversible assumptions inside the scope and lists them in its report, instead of stopping. Write down every decision you do not want it to make on its own.
- **Scope.** Under `## Scope and limits`, name every path the task may edit in backticks, one path each. After the run, the script compares the changed files with these paths.
- **Project rules.** The worker reads the project's `AGENTS.md` by itself, but not `CLAUDE.md`. Put every rule from `CLAUDE.md` that the task must follow under `## Context`.
- **Blocking.** Nobody answers the worker while it runs. Make `## If blocked` say exactly when it stops and reports `BLOCKED`.
- **Pre-approval.** The run's guard reads the brief's `Pre-approved:` line, and the worker reads only the spec. Write under `## Context` which kinds of command the brief pre-approves for the task. A worker never changes git state: `.git` is read-only to it.

## The network, settled in the interview

Workers may use the network unless the brief blocks it. Blocking suits a sensitive repository, whose code must not leave the machine. In the interview, ask once whether the workers may use the network, with open as the default and the reason for blocking. Write the answer under `## Constraints` in the brief, as `Worker network: open` or `Worker network: blocked`. With no such line, the network is open.

## A run

Start every run with the plugin's script. Do not call the CLI yourself: its settings are easy to get wrong, and the CLI's skill says why. The script is `scripts/worker.py` in the plugin's folder. This skill's base directory, which Claude Code showed when it loaded, is `<plugin>/skills/cli-workers`.

```bash
python3 <plugin>/scripts/worker.py run --cli codex --task .orchestra/<work>/tasks/T01-name.md --role implementer --effort medium
```

- **What each run carries.** The script reads the brief at every run: its `Worker network:` line, and its `Pre-approved:` keys. It passes the run a guard, a PreToolUse hook of the CLI's own, which refuses what the claude mode's hooks refuse a Claude subagent: a command of a pre-approval kind that the brief does not pre-approve, a data command that names production, an edit inside `.orchestra/` other than the task's report and `files/` folder, any project edit by a read-only role, and starting a Claude session. It reads the command line, so it is a tripwire, not a sandbox. Change the brief before a run, not during it.
- A run can take longer than a command in the foreground may. Start it in the background, end your turn, and go on when it reports, as you would wait for a subagent. Do not poll.
- Runs that may go side by side under "Working in one directory" are started in the same message.

## After a run

The script prints a few lines: the task, the CLI, the model and the effort, the time, the tokens, the session, the status from the worker's last message, a `GUARD:` line for each call the guard refused, the changed files, where the run's record is, and a `WARN:` line for each problem it found. It exits with 0, with 2 when it printed warnings, and with 1 when the run did not finish or could not start. It never prints the report's text.

- **A `GUARD:` line** is a refusal that worked, not a failure. Look at what the worker did instead. A task that needs the refused command needs a key in the brief, which only the user can approve.
- **A change outside the scope, or to git state,** fails the review. Send it back as rework with the warning as evidence.
- **A change under `.orchestra/`** outside the task's report and its `files/<task>/` folder is a worker that touched your documents. Put each one back as it was before the run, from what you wrote. If it was the brief, also tell the user. Then review as usual.
- **A report that says `DONE` without a valid verification record** goes back as rework, after your own review.
- **A usage limit**: stop the task and tell the user. Do not move the task to a Claude subagent, which spends the usage this mode exists to save, unless the user tells you to.
- **Exit 1 for another reason**: start the run once more if the failure looks transient. Otherwise tell the user.

Then review the result as step 5 of the conductor's instructions says: the diff, your own checks, the review record, and only then the report.

## Rework

- **First round:** write the review comment and its evidence to `.orchestra/<work>/files/<task>/rework-1.md`, and resume the same session with it. The model and the effort stay the same, and the script passes the run's settings again.

  ```bash
  python3 <plugin>/scripts/worker.py resume --task .orchestra/<work>/tasks/T01-name.md --message .orchestra/<work>/files/T01/rework-1.md
  ```

- **Second round:** find the cause first, and fix the task spec if it was the cause. Then start a fresh `run`, with the role and the effort chosen again.
- **The limit** is two rounds, as in the conductor's instructions.

## The final report

Add a line for each run: the task, the role, the model, the effort, and the time and tokens the script printed. Say that no hook guarded the work, and name every point where you know you did not keep one of the rules under "No harness in this mode".
