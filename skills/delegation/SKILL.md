---
name: delegation
description: The conductor's rules for choosing which subagent and which effort level a task gets. The conductor loads it before its first delegation in a session. Not for sessions on other models.
user-invocable: false
---

# Choosing the subagent and the effort level

These rules select the model and effort of Opus and Sonnet subagents only. The user sets the Fable conductor's effort directly in the UI, using the control below the prompt input. Orchestra does not change the conductor's effort.

Use this before every task's initial delegation and again for its second rework round, using the current task spec and review history. The first rework round goes back to the same subagent with its model and effort unchanged; it needs no new selection. Decide in three steps: the kind of work, then the model, then the effort level. Do not pick by how hard the task feels. Answer the questions below from the task spec and review history.

## Step 1. The kind of work

A change to a file on the brief's `Conductor writes:` line needs no subagent. You make it yourself, as your instructions describe under "Files you write yourself". The steps below are for the tasks you delegate.

| The task produces | Subagent family |
|---|---|
| A change to code, configuration, tests, or scripts | An implementation subagent. Go to step 2 |
| Findings only, with no change to any file: a review, a root-cause investigation, a comparison of options | `orchestra:opus-analyst` |
| A document whose outline you have already written into the task spec | `orchestra:sonnet-writer` |
| A document whose structure the subagent has to work out | Decide the outline yourself first. If you cannot, have `orchestra:opus-analyst` gather what you need, then write the outline |

## Step 2. Opus or Sonnet, for implementation

Give the task to `orchestra:opus-engineer` when **any** of these is true. Otherwise give it to `orchestra:sonnet-builder`.

1. **The cause is unknown.** The task is to fix a failure, and the task spec cannot say which code causes it.
2. **The task spec leaves a design choice open.** The subagent has to choose between approaches, or decide where the change belongs, and the choice affects code outside the files it edits.
3. **The parts depend on each other.** The change touches several modules, and it is only correct when all of them agree. A list of unrelated edits in many files does not count.
4. **A mistake would pass the checks.** The work involves concurrency, security, money, data migration, or a public interface, and the acceptance commands would not catch a subtle error.
5. **There is no check to run.** Nothing in the acceptance criteria can be run to tell a right result from a wrong one.
6. **The approach needs to be reassessed as the work proceeds.** You expect new findings during implementation to require fresh diagnosis or design decisions before the next step can be specified. Duration, file count, and repeated use of a known procedure alone do not trigger this rule.
7. **Sonnet has already failed it.** The task failed review twice on Sonnet and the task spec was not the cause.

A task is a Sonnet task when the task spec names the files and the change, the repository already has a pattern to follow, and a command shows whether it worked.

When rule 2 is the only reason for Opus, first try to remove it: make the design choice yourself and write it into the task spec. A task with no open choice is often a Sonnet task.

## Step 3. The effort level

Each subagent has a default. Pass `effort` on the Agent call only when a row below tells you to change it.

### `orchestra:sonnet-builder`, default `medium`

| Effort | Use it when |
|---|---|
| `low` | The change is mechanical and has one step: a rename, a moved file, a changed constant, the same edit repeated across files. The task spec must give the exact command that checks it |
| `medium` | The default. The task is well specified |
| `high` | No Opus rule from step 2 applies, and the approach is already decided, but completing the task takes many implementation and verification steps. Or the task spec leaves small choices that do not reach beyond the edited files. A mechanical one-step change repeated across files still uses `low` |
| `xhigh`, `max` | Do not use. Give the task to `orchestra:opus-engineer` instead |

Why: at `low`, Sonnet keeps its thinking short and sometimes reports a change as done without running a check, so `low` needs an exact check in the task spec. At `low` and `medium` on long tasks it is more likely to stop and ask before the work is done, so long tasks that still qualify for Sonnet go to `high`. At `xhigh` and `max` it starts its own rounds of review and makes related fixes that nobody asked for.

### `orchestra:opus-engineer`, default `medium`

| Effort | Use it when |
|---|---|
| `low` | Do not use. A task simple enough for it is a Sonnet task |
| `medium` | The default. Most Opus tasks, including a change carried through several modules until the tests pass |
| `high` | Rule 1 or rule 4 from step 2 applies, or, when reassigning for the second rework round, the review history includes a failure at `medium` |
| `xhigh` | The second rework round only: the task failed review at `high`, and the task spec was not the cause. Start a new subagent |
| `max` | Do not use |

Why: Opus at `medium` already performs at the level where earlier Opus models needed `high`, in fewer steps and with fewer tokens. Above `high` it thinks much longer for a gain that has to be measured, so `xhigh` is kept for a task that has already failed.

### `orchestra:opus-analyst`, default `high`

| Effort | Use it when |
|---|---|
| `medium` | The question is bounded and has one answer in the code: where something is defined, what calls it, which of two listed options the code already uses |
| `high` | The default. A review, a root-cause investigation, a comparison of approaches |
| `xhigh` | An earlier analysis of the same question turned out wrong or could not reach a conclusion |
| `low`, `max` | Do not use |

### `orchestra:sonnet-writer`, default `medium`

| Effort | Use it when |
|---|---|
| `low` | The work is a conversion: reformat, translate, or fill a template from content that is already written |
| `medium` | The default. The outline is in the task spec and the facts are in named sources |
| `high` | The document has to bring together many sources, or be consistent with a long existing document |
| `xhigh`, `max` | Do not use |

## Record the choice

Before the initial delegation and before reassignment for the second rework round, write one line in `.orchestra/<work>/decisions.md` with the task ID, chosen subagent, model, effective effort, and the rules or table rows that decided the model and effort, supported by facts from the task spec or review history. Record the effective effort even when the Agent call uses the default. Add a new entry for the second-round reassignment.

For the first rework round, record the failure evidence and requested correction instead of making a new model or effort decision.

```
- [T01] sonnet-builder (Sonnet), medium. Model: step 2, no Opus rule applies; files and change are named, pattern exists in reports/daily.py, pytest checks it. Effort: well-specified task, medium default.
- [T02] opus-engineer (Opus), high. Model: step 2, rule 1; the cause of the timeout is unknown. Effort: opus-engineer high row, rule 1 applies.
```

## What follows from the choice in the task spec

- **Sonnet at any level.** Name the files, name the pattern to follow, and give the exact commands to run. Sonnet tends to add tests, documentation, and small supporting files that were not asked for. If you do not want them, say so under `## Scope and limits`.
- **Sonnet at `low`.** The acceptance criteria must contain a command that exercises the change. A syntax check does not count.
- **Opus.** State the goal and the limits, and leave the method to it. For a long task, list the parts under `## Acceptance criteria` so it can tell when all of them are done.
