# orchestra

A Claude Code plugin that keeps the main model in the conductor role when you turn conductor mode on. The conductor, Fable or Opus, designs, delegates, and reviews independently. Opus and Sonnet subagents implement, and Haiku subagents take the work that needs no judgment. As the work goes on, the conductor keeps the project's documents small and true. The principles that need judgment live in the conductor's instructions. The principles that need no judgment are enforced by hooks.

In two further modes the conductor hands the implementation to another vendor's coding CLI instead of Claude subagents: the Codex CLI, or the Antigravity CLI with Gemini models. See "Workers on another vendor's CLI".

Orchestra chooses the model and effort for the workers. You set the conductor's own effort: see "Usage".

## Objectives

Orchestra sets the model and the effort level for each piece of work by Anthropic's official prompting guides for [Fable 5.2](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-2), [Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5), and [Sonnet 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5-5). It has two goals:

- **Cut needless token use.** No task runs on a larger model or a higher effort level than it needs.
- **Let each model do what it does best.** The conductor, Fable or Opus, understands what you want, designs, and reviews. Opus and Sonnet implement. Haiku finds, reads, and makes mechanical edits.

## Workflow

![The Orchestra workflow: you, the conductor, and an Opus or Sonnet subagent](docs/workflow.svg)

## Install

Requirements: Claude Code 2.1.295 or later, and `python3`.

```bash
claude plugin marketplace add paulkwongemini/orchestra
claude plugin install orchestra@orchestra
```

Or in one step:

```bash
claude plugin install orchestra --marketplace paulkwongemini/orchestra
```

Start a new session after installing. To check the environment, run `/orchestra:setup` in a session.

To try the plugin from a local copy without installing it:

```bash
claude --plugin-dir /path/to/orchestra
```

## Usage

Conductor mode is off when a chat starts, and the plugin does nothing. Turn it on for a chat with `/orchestra:conduct`. You can put the request on the same line, as in `/orchestra:conduct fix the login bug`.

While conductor mode is on, the model of the chat decides what happens.

- **The model is Fable or Opus: the model is the conductor.** It works out what you want, delegates the implementation to subagents, and reviews their results. It does not edit your files itself, except the files you approve for it in the brief. See "Files the conductor writes itself". Each time a group of related tasks is done, it has the project's documents brought up to date. The subagents are the same for both conductors.
- **The model is anything else: ordinary mode.** The plugin does nothing.

Change the model in the middle of a chat, and the mode changes with it.

The conductor decides for each message whether to answer it directly or to treat it as work. It answers directly only a question it can settle from the conversation, your project's files, and the web. Anything that asks for a result, or that needs an action you would have to allow, such as reading a server's logs over ssh, is work. Work starts with the interview: the conductor asks what you want, and which kinds of action it may take, before it runs anything that needs your approval.

Conductor mode is meant for work of some size. For a small task, leave it off. If it is already on, turn it off with `/orchestra:disable`, or switch the chat to Sonnet for the task.

For a Fable conductor, we recommend starting at `high`, following [Anthropic's Claude Fable 5.2 prompting guide](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-2#calibrate-effort). Choose the level to suit the work:

- **`high`**: the recommended starting point for complex coding, research, and extended autonomous work.
- **`medium`**: for interactive development and routine tasks when it maintains the quality you need with less time and fewer tokens.
- **`xhigh`**: for especially demanding, extended tasks only when your evaluations show an improvement worth the additional time and token use.

For an Opus conductor, use `high` or above. When an Opus conductor runs at `low` or `medium`, it stops at its first tool call at that level, recommends `high` or above, and asks whether to go on. This happens when you turn conductor mode on at a low level, and also when you lower the effort later in the chat. Say to go on, and it works at the level you chose without asking again. It asks again only when you change to the other low level or turn conductor mode on again.

Set the conductor's effort yourself using the control below the prompt input. Orchestra does not change it.

| Command | What it does |
|---|---|
| `/orchestra:conduct` | Turns conductor mode on for this chat |
| `/orchestra:conductor` | Turns conductor mode on and loads the conductor's instructions right away. Use it on Fable or Opus: on another model it puts the instructions into a chat that stays ordinary |
| `/orchestra:conduct-codex` | Turns conductor mode on with Codex CLI runs as the workers |
| `/orchestra:conduct-agy` | Turns conductor mode on with Antigravity CLI runs, on Gemini models, as the workers |
| `/orchestra:disable` | Turns conductor mode off for this chat, whichever mode was on. The chat works as an ordinary session |
| `/orchestra:setup` | Checks the environment and lists the work folders. Changes nothing |

The commands apply to the chat you type them in. One mode is on at a time, and typing another mode's command switches to it. Only you can turn a mode on or off. The conductor cannot do it for itself.

The mode is kept through compaction. Whether it is kept through `/clear` has not been checked. If the conductor does not come back after `/clear`, type `/orchestra:conduct` again.

## Pre-approved actions

Work often runs while you are away, so the conductor settles in the interview which kinds of action it may take without asking you again. It asks about each kind the work needs and writes the ones you approve on one line of the brief, under `## Decision rights`:

`Pre-approved: data, deps`

| Key | Kind of command |
|---|---|
| `data` | Changes to data or a schema: migrations, seeds, schema loads |
| `deps` | Installing or changing dependencies and packages |
| `git` | Git commands that change repository state, except `git push` |
| `push` | `git push` |
| `github` | `gh` commands that create or change something |
| `http` | A `curl` request that writes to another machine |
| `ssh` | `ssh`, `scp`, `sftp` |
| `deploy` | `kubectl`, `terraform`, `helm`, `docker push`, and the like |

- No key includes another. A line with `none`, or no line, pre-approves nothing.
- The conductor writes a key only when you approved that kind of action in an answer of your own. Telling it to stop asking is not an approval.
- A key counts once you have replied after the brief last changed, and it holds for that piece of work only.
- Your project's instructions come first. The conductor does not ask you to pre-approve an action they forbid, and a procedure they require around an action, such as a check before a commit, still applies.
- A data command that names a production environment is not covered by `data`. You are always asked.
- Commands that change git state, a push included, are run by the conductor only. Subagents use git only to read. See "Working in one directory".

When a command's kind is not pre-approved:

- Run by the conductor, for `data`, `deps` and `git`: it is refused. The conductor adds the key to the brief, tells you what it wants to run and why, and asks you. Meanwhile it keeps working on what does not depend on your answer.
- Run by the conductor, for `push`, `github`, `http`, `ssh` and `deploy`: a confirmation prompt opens, and the conductor's turn waits until you answer it.
- Run by a subagent that implements: it is refused, the subagent reports `BLOCKED`, and the conductor asks you.
- A read-only subagent never runs these commands, pre-approved or not. Neither does a Haiku subagent.

The check reads the command line and is a tripwire for the common commands, not a sandbox. It does not look inside a container: a command run through `docker exec` is checked only for data and schema changes.

## Files the conductor writes itself

The conductor delegates every change to your project, with one exception that you open for each piece of work. For some files the task spec would already hold the final text, and a subagent could only copy it out. The conductor may write such a file itself when you approve it in the interview. It writes the approved paths on one line of the brief, under `## Decision rights`:

`Conductor writes: skills/review/SKILL.md, README.md`

- Two kinds of file may go on the line: a file whose wording is the design, such as a skill, a prompt, or an agent's instructions, and a document in which the conductor decides each statement that changes. Code, configuration, and tests are always delegated.
- An entry is the path of one file, relative to the project directory or absolute. A pattern or a directory matches nothing. A path outside the project or inside `.orchestra/` matches nothing.
- The conductor writes a path on the line only when you approved that file in an answer of your own.
- The line counts once you have replied after the brief last changed, and it holds for that piece of work only.
- A file the conductor writes has no second writer and no independent review. The conductor runs the checks that cover it, records the change in the decision log, and lists the file in its final report.
- A hook checks the path. It does not check what kind of file it is or what is written to it. Which files may go on the line is an instruction.

## Haiku for work that needs no judgment

Part of the work needs no judgment: finding where something is, or making the same edit in twenty places. Two subagents do it on Haiku, at a small fraction of the cost of the other models. The aim is the same result for fewer tokens.

| Subagent | What it does | Who may start it |
|---|---|---|
| `orchestra:haiku-scout` | Finds and reads. Changes nothing | The conductor, and every Opus or Sonnet subagent |
| `orchestra:haiku-editor` | Makes a mechanical edit and runs the check it is given | The conductor, once you have confirmed the brief, and the subagents that implement |

- Haiku gets only work that the caller can check against the files in less effort than doing it: a scout's answer by opening the lines it cites, an edit by reading the diff and running the check. A judgment, a check that decides whether work is accepted, and a question where the answer "none" matters never go to Haiku.
- The caller checks what comes back, and does the work itself when the check fails. Work that Haiku did inside a task belongs to the subagent that has the task, and the conductor reviews that task as before.
- A call is a prompt in a fixed form, `FIND:` for the scout and `DO:`, `FILES:`, `CHECK:` for the editor, of at most 1,500 characters. It has no task spec. A hook refuses any other form.
- A Haiku subagent starts no subagent. `haiku-scout` cannot change a file. `haiku-editor` cannot write inside `.orchestra/`, and cannot run a command of a pre-approval kind, whatever the brief pre-approves.
- `haiku-editor` returns only a status, the changed files, and the exit code of the check, so the caller learns the result from the diff.
- Nothing changes for you. There is no new question in the interview and no new line in the brief.

No hook checks whether a request really needs no judgment, whether the editor stayed inside the files it was given, or whether the caller ran the check again. Those are instructions.

## Workers on another vendor's CLI

`/orchestra:conduct-codex` and `/orchestra:conduct-agy` keep the conductor and its workflow, and change who implements. Each task is one run of the Codex CLI, or of the Antigravity CLI (`agy`) on a Gemini model. The implementation then spends that vendor's plan instead of your Claude plan. The conductor still writes the brief and the task specs, reviews each result before it reads the report, and sends failed work back.

Requirements: the CLI of the mode, installed and signed in. The plugin changes nothing in its configuration and writes no setup file into your project.

| Role | Does | Codex model | agy model |
|---|---|---|---|
| `engineer` | Implementation that needs diagnosis or a decision inside the task's limits | GPT-6.2 Astra | Gemini 4 Neon |
| `analyst` | Findings only. Changes no project file | GPT-6.2 Astra | Gemini 4 Neon |
| `implementer` | A bounded change whose files, pattern and check the task spec names | GPT-6.2 Sol | Gemini 4 Hydrogen |
| `writer` | A document whose outline and facts are settled | GPT-6.2 Sol | Gemini 4 Hydrogen |

- **The plugin's hooks step aside.** In these modes no hook guards the conductor, except the question about a low effort on Opus. The conductor keeps the same rules by its instructions alone, and says in its final report where it did not.
- **Each run carries its own guard.** The conductor starts every run with `scripts/worker.py`, which passes the CLI a hook for that run only. The hook refuses what the plugin's hooks refuse a Claude subagent: a command of a pre-approval kind that the brief does not pre-approve, an edit to the conductor's documents, any project edit by the `analyst`, and starting a Claude session. It reads the command line and is a tripwire, not a sandbox. The CLI's own sandbox keeps `.git` read-only.
- **The run is checked afterwards.** The script compares the changed files with the paths in the task spec, and warns about a change outside them, a change to git state, and a `DONE` report without a valid verification record. It prints a few lines and never the report's text.
- **The network is open unless you block it.** The interview asks once. `Worker network: blocked` under `## Constraints` in the brief turns it off, which suits a repository whose code must not leave the machine.
- **You can follow a run.** The conductor gives you a command to run in a terminal of your own: `python3 <plugin>/scripts/worker.py watch --task <task spec>`. It shows the worker's messages, commands and edits, and the guard's refusals, as they happen.
- **At the plan's limit the task stops.** The conductor tells you, and does not move the task to a Claude subagent unless you say so.
- **`scripts/audit.py <transcript>`** lists every point in a finished session where the conductor broke, or tried to break, a rule that the hooks guard. It is how the two kinds of mode are compared.

## Working in one directory

Subagents work in your working directory, several at a time, with no worktree between them. Results need no merging, and every task sees the current code. The conductor keeps the tasks out of each other's way with five rules:

- **Each task owns its files.** A task spec lists the files or directories the task may edit, and the subagent edits nothing else.
- **One writer per file.** A file belongs to one task until that task has passed review. Two tasks that need the same file run one after the other.
- **A task that depends on another waits for it.** That includes tasks in different files that share an agreement, such as a type, an interface, a schema, or a dependency version. A task that changes the database, the dependencies, or build output runs alone.
- **Only the conductor changes git state.** It commits, switches, resets, and pushes itself, when no subagent that edits files is running. Subagents use git only to read.
- **Whole-tree checks wait for a quiet tree.** A task that runs beside another checks only its own files. The full test suite, a build, or a type check is run by the conductor when no subagent is editing, so a result is never taken from a tree that was changing.

The conductor does not use a worktree unless you approve one for that piece of work:

- When your project's instructions forbid worktrees, it does not consider one and does not ask.
- Otherwise it asks in the interview, and only when the work needs two states of the tree at the same moment, such as comparing two commits side by side. Two tasks that want the same file, or tasks that would finish sooner side by side, are run one after the other instead.
- Your answer is written under `## Constraints` in the brief.

These rules are instructions. No hook enforces them.

## What is in it

| Kind | Name | Role |
|---|---|---|
| Skill | `orchestra:conduct` | Turns conductor mode on for the chat. `orchestra:conduct-codex` and `orchestra:conduct-agy` turn it on with CLI runs as the workers, and `orchestra:disable` turns it off. Only you can run them |
| Skill | `orchestra:setup` | Checks the environment and lists the work folders. Changes nothing. Only you can run it |
| Skill | `orchestra:conductor` | The conductor's instructions. The conductor loads it when you turn conductor mode on, and again after the chat is compacted or cleared. A hook refuses every other tool call until it is loaded |
| Skill | `orchestra:interview` | How the conductor finds out what you want before any work starts: one question per turn, each next question decided by your last answer, then the brief. It also asks which kinds of action you pre-approve for the work, which files the conductor may write itself, and whether the work may use a worktree when it needs one. The conductor loads it as soon as a request is work, before it asks you anything or takes any action for the request. No hook checks this |
| Skill | `orchestra:delegation` | The rules for choosing the subagent and the effort level of each task. The conductor loads it before its first delegation, and a hook refuses a delegation until it is loaded |
| Skill | `orchestra:documentation` | The rules for what the work writes into the project's documents and comments, and for changing or deleting documents each time a group of related tasks is done. The conductor loads it before its first task spec, and a hook refuses an implementation delegation until it is loaded |
| Skill | `orchestra:cli-workers` | What changes for the conductor when its workers are CLI runs. Loaded in the Codex and agy modes, in place of `orchestra:delegation` |
| Skill | `orchestra:codex`, `orchestra:agy` | The facts of each CLI, and the model each role runs on |
| Role file | `roles/*.md` | The instructions a CLI worker reads: one file per role, and the rules they share in `roles/shared.md` |
| Script | `scripts/worker.py` | Starts, resumes and checks a CLI run, and lets you follow one |
| Script | `scripts/audit.py` | Lists where a session's conductor broke, or tried to break, a rule the hooks guard |
| Subagent | `orchestra:opus-engineer` | Opus, effort medium. Hard implementation, complex debugging |
| Subagent | `orchestra:opus-analyst` | Opus, effort high. Architecture review, deep analysis. Read-only |
| Subagent | `orchestra:sonnet-builder` | Sonnet, effort medium. Simple code, repetitive work |
| Subagent | `orchestra:sonnet-writer` | Sonnet, effort medium. Documents whose structure is already decided, including the document changes at the close of a task group |
| Subagent | `orchestra:haiku-scout` | Haiku, effort medium. Finds and reads. Read-only |
| Subagent | `orchestra:haiku-editor` | Haiku, effort medium. Mechanical edits that the request determines completely |

## Principles and how each is enforced

The enforcement below applies in a conductor session whose workers are Claude subagents. With CLI workers the hooks step aside, and every row is an instruction. See "Workers on another vendor's CLI".

| Principle | Enforcement | File |
|---|---|---|
| 1. The main model, Fable or Opus, is the orchestrator once you turn conductor mode on | Instructions. Conductor mode is off until `/orchestra:conduct`, and only a command you type changes it | `skills/conductor/SKILL.md` |
| 2. Understand the intent before working | Instructions (`skills/conductor/SKILL.md`, `skills/interview/SKILL.md`): each message is judged anew as a direct answer or as work, the interview comes before any action that needs your approval, one question per turn, then the brief. Implementation cannot be delegated without a brief, or before the user has replied since the brief last changed | `scripts/guard_agent.py` |
| 3. Stop and ask when an exception comes up | Instructions. A command that changes data, dependencies, repository state, or something outside this machine runs without a question only when the brief pre-approves its kind. Otherwise it is refused or goes to you. See "Pre-approved actions" | `skills/interview/SKILL.md`, `scripts/guard_main.py` |
| 4. Tasks run on Opus or Sonnet, with model and effort chosen by the delegation rules. Haiku runs only work that needs no judgment | Agent types off the roster are refused. So is a model that does not belong to the subagent: `haiku` for an Opus or Sonnet subagent, any other model for a Haiku subagent. The choice between Opus and Sonnet and the effort level follow written rules (`skills/delegation/SKILL.md`), which must be loaded before a task is delegated. When Haiku may be used is an instruction. See "Haiku for work that needs no judgment" | `scripts/guard_agent.py` |
| 5. No Fable subagents, and no copies of the conductor | Forks, `model: fable` or `inherit`, calls without a type, and workflows are refused, whether the conductor is Fable or Opus | `scripts/guard_agent.py` |
| 6. Subagents do not start subagents, except a Haiku subagent | An `Agent` call inside a subagent is refused unless it starts `haiku-scout` or `haiku-editor` with a prompt in the fixed form. A read-only subagent may start only `haiku-scout`. A Haiku subagent has no `Agent` tool, and a call from one is refused | `agents/*.md`, `scripts/guard_agent.py` |
| 7. Review the result independently, then read the report | A `DONE` handback requires passing verification records. Reports arrive only as files. A report does not open without a review record. A `haiku-editor` returns no account of its work: only a status, the changed files, and the exit code of the check | `scripts/check_report.py`, `scripts/report_gate.py` |
| 8. The conductor waits for delegated work instead of doing it | Instructions. The conductor's Edit, Write, and file-changing shell commands are refused, except for the files that the brief lists on its `Conductor writes:` line once you have confirmed it. The shell check catches the common ways to change a file and sends the work back to delegation. It is not a sandbox | `skills/conductor/SKILL.md`, `scripts/guard_main.py` |
| 9. Every task goes through a task spec | The delegation of a task is refused without a task spec file that has the required sections. A Haiku call is not a task: it takes a prompt in a fixed form, and is refused in any other form | `scripts/guard_agent.py` |
| 10. Failed work goes back to the subagent | Instructions. A reworked report needs a new review record before it opens | `scripts/report_gate.py` |
| 11. Documents and comments hold only what the code cannot show. Documents are brought up to date each time a group of related tasks is done, not by the subagents that implement | Instructions (`skills/documentation/SKILL.md`), which must be loaded before an implementation delegation. Whether a group is closed is not checked by a hook | `agents/opus-engineer.md`, `agents/sonnet-builder.md`, `scripts/guard_agent.py` |
| 12. Subagents share one working directory without damaging each other's work, and no worktree is used without your approval | Instructions: each task owns its files, one writer per file, dependent tasks run in order, only the conductor changes git state, and whole-tree checks run only when no subagent is editing. No hook checks these | `skills/conductor/SKILL.md`, `skills/interview/SKILL.md`, `agents/opus-engineer.md`, `agents/sonnet-builder.md`, `agents/sonnet-writer.md` |

Principle 8 is about patience, not about a waiting mode. Subagents usually run in the background, and the conductor ends its turn and resumes when one of them reports. However long that takes, the conductor does not take the work back. The hooks leave it unable to change project files other than those you approved in the brief, so the plugin needs no extra setting for this.

## Documents and comments

Every document in a project costs context in each later session that reads it, and an agent trusts a document that is out of date. So the conductor keeps the documents small and true. The work spends some tokens on this, and later work spends fewer on finding out what is still correct.

- **The reason for the code is in the code.** A reader should understand it from the code itself.
- **Other text has to pass one test.** Without this text, would a capable reader who has only the code make a wrong decision? If not, it is not written. A fact passes only when it came from outside the code: you said it, something was measured, or an outside system behaved that way.
- **Each fact has one place.** A reason that belongs to one place in the code is a short comment there. A fact with no one place, such as what the product is for, a constraint from outside the code, or a plan, goes in a document. What changed and why is left to git.
- **A document says what is true now.** A statement that the work makes false is rewritten, not added to. A plan or a spec that the work has implemented is deleted.
- **Documents are updated once per task group.** The conductor splits the work into groups of related tasks. When every task of a group has passed review, it works out what the group made false or unnecessary and delegates those changes as one task, or writes them itself when you approved those files in the brief. The subagents that implement do not edit documents.
- **Only what the work caused.** A document that was already out of date, a new document, and the removal of a whole file are extras. The conductor proposes them and does not make them on its own.

The test is not applied to documents for people who do not read the code, such as a user guide, or to a history the project keeps on purpose, such as a changelog. The full rules are in `skills/documentation/SKILL.md`.

## Work folder

The conductor and the subagents exchange documents through `.orchestra/` in the project. Each piece of work has its own folder there, named with the date and a short name.

```
.orchestra/
├── .gitignore                       keeps the folder out of git
└── 2026-11-12-login-fix/            one folder per piece of work
    ├── brief.md                     the intent confirmed with the user. Written by the conductor
    ├── decisions.md                 decisions made without asking, and the task groups. Written by the conductor
    ├── tasks/T01-session-expiry.md  task spec. Written by the conductor
    ├── reports/T01.md               report. Written by the subagent
    ├── reviews/T01.md               independent review record. Written by the conductor
    └── files/T01/                   working files of a task, such as a one-off script or captured output. Written by the subagent.
                                     A CLI run's record is in files/T01/cli/
```

Work folders do not share anything. A brief or a review record in one folder does not count for another, and task `T01` of one piece of work has nothing to do with `T01` of the next.

The required headings of each document are under "Document formats" in `skills/conductor/SKILL.md`. The list the hooks check is in `scripts/common.py`. Change both together. Only the headings are fixed. The body of a document can be in any language.

The plugin creates `.orchestra/.gitignore` with the content `*`, so nothing in the folder reaches git by accident. To commit these records, replace the content of that file. An empty file commits everything. The plugin does not touch the file once it exists, so do not delete it, or it is created again.

The plugin never deletes a work folder. The review records are the evidence that results were checked independently. Delete finished folders yourself when you no longer need them.

## Development

How to run the tests, and what has been verified in real sessions, is in [docs/development.md](docs/development.md).
