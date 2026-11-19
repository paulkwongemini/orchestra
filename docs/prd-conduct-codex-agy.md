# PRD: conduct-codex and conduct-agy

Branch: `conduct-codex-gemini`, from `main` at Orchestra 0.2.0. This document is the starting point of the work on that branch. Delete it when the work is done; what stays true goes into the README and `docs/development.md`.

## Goal

Add two conductor modes in which a Claude conductor delegates the implementation to another vendor's coding CLI instead of to Claude subagents:

- **`/orchestra:conduct-codex`**: the workers are Codex CLI models, `gpt-6.2-astra` and `gpt-6.2-sol`.
- **`/orchestra:conduct-agy`**: the workers are Gemini models, run through the Antigravity CLI, `agy`.

The purpose is to spend the Codex (or Gemini) plan's usage on implementation instead of the Claude plan's. Making the other side's usage small as well is a later step, not this one.

Codex mode comes first and is worked out in full. Its specification is written so that agy mode follows from it with the facts of a second CLI filled in.

A second question is tested on the same branch: **can Orchestra work from instructions alone, with no hooks?** In the two new modes the hooks step aside, so those modes are the test. If they hold up, the Claude mode (`/orchestra:conduct`) may drop its hooks too, for consistency across the three modes. The hooks are the more reliable mechanism. The test is whether the instructions are reliable enough.

## Requirements

1. **Commands and modes.** `/orchestra:conduct-codex` and `/orchestra:conduct-agy` turn their mode on for the session, like `/orchestra:conduct` does for the Claude mode. One mode is on at a time. `/orchestra:disable` turns any of them off. Only a command the user types changes the mode.
2. **The hooks step aside.** When Codex or agy mode is on, every Orchestra hook that guards or checks exits at once and decides nothing. The hook that records the mode from the user's commands keeps running, since it is how a mode is turned on and off. This is the "state file check" pattern: hooks stay registered and read the session's mode first.
3. **The same workflow.** Brief, task specs, decision log, independent review before the report, rework, task groups, the gate, and the final report work as in the Claude mode. The work folder layout under `.orchestra/` is the same.
4. **Workers.** The conductor's subagents are replaced by CLI runs. Haiku workers are not used in these modes. The conductor may still make its own `haiku-scout` calls.
5. **Reports.** A worker writes its full report to `.orchestra/<work>/reports/T01.md` and returns the short status lines, as a Claude subagent does.
6. **agy from the same spec.** agy mode reuses the Codex mode's instructions, changing only the CLI facts: commands, flags, models, efforts, isolation, sandbox.

## Out of scope

- Reducing the Codex or Gemini usage per task.
- Changing the Claude mode's behavior. Removing its hooks is a decision that comes after the instruction-only test, with the user.
- The separate `orchestra-codex` plugin, in which a Codex model is the conductor. It is a different product. Its role files are input here (see "Reusable material").

## Facts established

These were found in one session on 2026-11-19. Each was observed unless marked otherwise.

### Codex CLI

The machine has `codex-cli 0.170.0`, logged in with ChatGPT.

- **Models and efforts.** The local model cache lists `gpt-6.2-astra` and `gpt-6.2-sol`, each with the efforts `low`, `medium`, `high`, `xhigh`, `max`, `ultra`.
- **The user's config is inherited.** `~/.codex/config.toml` sets `model = "gpt-6.2-astra"`, `model_reasoning_effort = "xhigh"`, `service_tier = "priority"`, `approval_policy = "on-request"`, `sandbox_mode = "workspace-write"`, `web_search = "live"`. Each run must set the model and effort it means. `priority` may cost more Codex usage. That is a later concern.
- **Non-interactive runs.** `codex exec` takes the prompt from an argument, or from stdin with `-`.
- **No approval prompts.** The run header shows `approval: never` whatever the config says. Nothing is asked of the user. An action the sandbox denies fails, and the error goes back to the model: writing outside the sandbox gave `Read-only file system`, exit 1, and no file was created.
- **Sandbox `workspace-write`.** The model can write to the `-C` directory, `/tmp`, `$TMPDIR`, and each `--add-dir` directory. It can read anywhere. The header said `network access enabled`.
- **`.git` is read-only.** In a git repository at `-C`, `touch .git/probe` failed with `Read-only file system`, and `git commit` failed on `.git/index.lock`. A worker cannot change git state.
- **The network can be turned off.** `-c sandbox_workspace_write.network_access=false` changes the model's instructions from "Network access is enabled" to "restricted", and `curl https://example.com` then failed with `Could not resolve host`.
- **`--add-dir`** made `.orchestra/<work>/reports/` writable while `tasks/` beside it stayed read-only.
- **`-o <file>`** writes the run's last message. The CLI process writes it, outside the sandbox, so it can go anywhere.
- **Isolation from Codex plugins.** `--disable plugins` turns every Codex plugin off for the run. Measured on the model-visible input: about 82,000 characters and 23 skills before, about 69,000 characters and 7 skills after. Disabling one plugin with `-c 'plugins."<name>".enabled=false'` changed nothing; the `orchestra-codex` conductor skills stayed visible. An earlier relay test used that ineffective form.
- **What stays after `--disable plugins`:**
  - user skills (`~/.agents/skills`, `~/.codex/skills`: `diagnose-crash`, `omarchy`, `omarchy-app`)
  - Codex system skills (`imagegen`, `openai-docs`, `skill-creator`, `skill-installer`)
  - the global `~/.codex/AGENTS.md`, a rule about tracking AGENTS.md in git
  - the MCP server `node_repl`, which `-c 'mcp_servers.node_repl.enabled=false'` removes. `-c 'mcp_servers={}'` removes nothing, so each server in the user's config is turned off by name
  - multi-agent instructions. `--disable multi_agent` did not remove them. The user accepts this: the result is what is reviewed, not how the worker got there.
- **Seeing the model's input without a model call.** `codex debug prompt-input -c 'model="..."' <prompt>` prints the model-visible input as JSON. It does not take `-m`. Use it to check isolation after any change to the flags.
- **Stdin hang.** With the prompt given as an argument and stdin left open, `codex exec` waits forever to read stdin and append it, with no CPU use and no network connection. In Claude Code's shell, stdin is a socket that never closes. Pass the prompt as `- < spec.md`, or add `< /dev/null`.
- **Resume.** `codex exec resume <session-id> '<message>'` continues the same session; the session id is in the run header. `resume` has no `-s`, `-C` or `--add-dir` options, and **a resumed run does not keep the first run's sandbox options**: its header listed no `--add-dir` folder and said `network access enabled` again, which is the user's config. Each resume must pass the settings again with `-c`. `-c 'sandbox_workspace_write.writable_roots=["<dir>"]'` adds a writable folder and `-c sandbox_workspace_write.network_access=false` restricts the network; both showed in the model-visible input, but neither has been run on a resume yet. `--ephemeral` makes a run impossible to resume.
- **Output size.** stdout carries the header, the transcript and the last message. Filtering it to the header lines and the token count keeps it out of the conductor's context.
- **Instructions file.** Codex reads `AGENTS.md`, not `CLAUDE.md`. A project rule that lives only in `CLAUDE.md` has to reach the worker through the task spec.
- **`/tmp` for a read-only run.** `/tmp` and `$TMPDIR` are writable by default. `-c sandbox_workspace_write.exclude_slash_tmp=true` and `-c sandbox_workspace_write.exclude_tmpdir_env_var=true` take them out. Without them, a read-only run on a project under `/tmp` could have written project files.
- **The JSON event stream.** With `--json`, stdout is one event per line: `thread.started` carries the session id as `thread_id`, and `turn.completed` carries `usage` with `input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`, `output_tokens` and `reasoning_output_tokens`.
- **Base cost.** A run that answers one word reads 16,541 input tokens, 13,440 of them cached.
- **Not verified:** how a run behaves when the Codex plan's limit is reached, how to read the Codex usage limit from the command line, whether a resume's token count is per turn or cumulative, and a network check on a resumed run.

Measured runs, all on `gpt-6.2-astra`:

| Run | Effort | Time | Tokens reported |
|---|---|---|---|
| Duration module and tests, first round | high | 35 s | 15,172 |
| The same session resumed with a review comment | high | 17 s | 18,311 (whether this is per turn or cumulative is not known) |
| Two shell writes, a sandbox test | low | 12 s | 3,407 |
| A report written through `--add-dir` | low | 9 s | 3,820 |

Runs through `scripts/worker.py`, in a throwaway git project:

| Run | Model, effort | Time | Input tokens (cached), output |
|---|---|---|---|
| `implementer`: a slugify module and its tests | Sol, low | 28 s | 128,161 (120,960), 1,288 |
| The same task resumed with one added rule | Sol, low | 20 s | 189,694 (180,736), 2,413 |
| `analyst`: a sandbox probe, `/tmp` still writable | Astra, low | 19 s | 91,439 (85,376), 608 |
| The same probe with `/tmp` taken out | Astra, low | 21 s | 91,377 (85,376), 664 |

Each result passed the conductor's own review. The resumed run kept the session and the settings. In the last probe, writing a project file failed with `Read-only file system`, `curl` could not resolve its host, and the run's own `files/` folder was writable.

The relay worked end to end once. The conductor wrote a task spec. Codex implemented it. In each round the conductor ran Codex's tests and its own check script before reading the report; in the second round the script held 83 invalid inputs among its cases. One case showed that the spec was ambiguous, and the clarification went back through `resume`. The second round passed the full review. The work folder is `/home/paulkwon/plugins/.orchestra/2026-11-19-codex-relay-test/`, outside this repository.

A first-run command with all the points above applied. It is put together from the runs above and has not been run in this exact form. The runs used a `-C` folder under `files/`. With `-C` at the project root, `.orchestra/` is inside the writable area already and `--add-dir` adds nothing:

```bash
codex exec -m gpt-6.2-astra -c 'model_reasoning_effort="high"' --disable plugins \
  -s workspace-write -C <folder the task may write> \
  --add-dir .orchestra/<work>/reports \
  -o .orchestra/<work>/files/T01-last.txt \
  - < .orchestra/<work>/tasks/T01-name.md
```

### Orchestra's hooks meet a CLI worker

What the hooks do for a Claude subagent, and what happens to a `codex exec` run in the Claude mode as it stands:

- **No delegation gate.** `scripts/guard_agent.py` checks the brief's confirmation, the task spec, the loaded rules and the model for the Agent tool only. `codex exec` is a Bash command and passes none of those checks.
- **No report check.** `scripts/check_report.py` runs on `SubagentStop` and `SubagentHandback`, which a CLI run never triggers. The report format it checks matches `orchestra-codex/roles/reporting.md`: a JSON `checks` list with `exit_code: 0`, and an empty `unverified`.
- **No pre-approval check.** The pre-approval keys are checked on the shell commands of Claude sessions. Commands the worker runs inside Codex are invisible to them.
- **No file ownership.** With `-C` at the project root, the worker can write anywhere in the project, `.orchestra/` included (the brief, task specs and review records). A Claude subagent is kept out of those files by a hook.
- **No "subagent running" signal.** A background `codex exec` is not a subagent to Claude Code, so nothing marks the moments when the gate or a git command may run.
- **In the way.** `scripts/guard_main.py` refuses the conductor's shell redirect, so the conductor could not save a run's event stream to `files/`. `scripts/report_gate.py` still holds back `reports/` until a review record exists, which is the wanted behavior.
- **Claude Code's side.** A foreground Bash call waits at most 10 minutes; a longer run goes to the background and Claude Code reports when it exits. Without an allow rule such as `Bash(codex exec:*)`, each run may ask the user for permission.
- **The conductor's effort is readable.** Each request in the session transcript records it under `params.output_config.effort`.

Requirement 2 removes the first six points: in Codex mode the hooks decide nothing. Whatever those hooks did must then be done by the instructions, or by the CLI's own sandbox, or be accepted as a gap. That is the instruction-only test.

### Claude Code hooks, from the documentation

These come from code.claude.com, checked by a documentation agent and not tried in a session.

- **No runtime switch.** A plugin cannot turn its own hooks on and off while the session runs. A hook that should act only in one mode reads a state file and exits early. Orchestra already does this for its on and off states.
- **Skill-scoped hooks.** A skill can declare `hooks:` in its frontmatter. They are registered when the skill is invoked and stay for the rest of the session. There is no documented way to remove them. Their behavior across compaction and `/clear` is not documented.
- **The `if` field.** A hook handler can carry one permission-style rule, such as `"if": "Bash(codex exec *)"`, on `PreToolUse`, `PostToolUse` and a few other events.
- **Combined decisions.** When several `PreToolUse` hooks decide, `deny` wins over `defer`, `ask` and `allow`.
- **`/clear`.** Whether `/clear` keeps the session id is not documented. Orchestra keeps the mode per session id.

### Antigravity CLI (`agy`)

No `gemini` command is on the PATH. The user named the Antigravity CLI, `agy` 1.5.12, as the way to run Gemini models; it is installed, with its data under `~/.gemini`.

- `agy --help` lists a one-shot mode (`-p`), `--model`, `--effort` (`low` to `max`), `--add-dir`, `--sandbox`, `--conversation <id>` to resume, `--output-format json`, `--settings`, `--disable-slash-commands`, and `plugin` and `mcp` subcommands.
- `agy models` lists `gemini-4-neon`, `gemini-4-argon` and `gemini-4-hydrogen` at several efforts, `gemini-3.8-flash`, and Claude and GPT-OSS models. The effort is part of the model id, as in `gemini-4-neon-high`.
- Nothing else is known. The checklist below has not been answered for it, and no model has been called through it.
- The user will use Gemini 4 Neon and Gemini 4 Hydrogen only, not Argon (2026-11-19).

### The vendors' prompting guides

Researched on 2026-11-19. The points marked as read were opened by the conductor; the rest come from a research agent's summary.

- **OpenAI, GPT-6.2.** [Prompting GPT-6.2 Astra](https://learn.chatgpt.com/guides/prompting-gpt-6-2-astra) and [the GPT-6.2 API guide](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-6.2-astra), both read.
  - One guide for the whole family: its techniques "apply across the GPT-6.2 model family", Astra and Sol alike, and no Sol guide exists. Astra is for the most demanding tasks; Sol for complex work at lower cost.
  - For a coding worker: say what counts as finished and where to be exhaustive; skip new tests unless requested, required by the repository, or needed to catch a regression the existing tests would miss; no dense one-liners and no needless indirection; persist to a full result, make reasonable reversible assumptions and state them, and ask only for a significant change of scope, a real blocker, a decision only the user can make, or before an irreversible action; make the final answer self-contained; on a shared machine, limit concurrent builds and tests, and clean up background jobs and temporary files.
  - `AGENTS.md` holds only what cannot be inferred from the code, tests and docs.
  - Effort: Medium for a quick survey, High for tracing one cause across systems, Extra High when causes may interact, Ultra for work that splits cleanly; start new integrations at `medium`.
- **Google, Gemini 4.** [Engineer agentic prompts](https://docs.cloud.google.com/gemini/docs/discover/engineer-agentic-prompts), read, and the Neon and Hydrogen model pages and Antigravity's rules and CLI pages, summarized.
  - Read: stable guidance as labelled XML-style blocks such as `<role>` and `<rules>`, without an outer wrapper tag; boundaries as specific rules; a plan, verify and execute loop; structured output formats; destructive actions gated outside the prompt.
  - Summarized: the same prompting for Neon and Hydrogen. Neon is the deeper reasoner and Antigravity's planner; Hydrogen is lighter, for everyday coding, and carries out plans. Thinking levels `low` to `xhigh`, `high` by default, `medium` for general coding. Antigravity reads `AGENTS.md` and `GEMINI.md`, with a 20,000-token budget for always-on rules. Nothing on persistence or on a final report.
- **What this changes.** The guidance worth having is mostly the same for both vendors, so it went into the shared role files: assumptions instead of stopping, tests only where the spec asks, a self-contained report, cleanup, and code style for `engineer` and `implementer`. The one real conflict, how many tests to write, is settled by the task spec, which now names the tests. Per-vendor text is small and goes into each CLI's first message to the worker.

## Checklist for adding a CLI worker

Every fact under "Codex CLI" answers one of these questions. agy mode needs the same answers:

1. How to run it once without interaction, with the prompt from a file.
2. How to choose the model and the effort for one run, overriding the user's config.
3. How to keep the user's plugins, extensions, skills and MCP servers out of the run, and how to see the model's input without a model call to check that.
4. Where it may write, how to open one more folder, and whether it can reach the network.
5. What happens when it wants an approval: whether it asks, fails, or goes ahead.
6. How to get the last message into a file, and how to keep the output out of the conductor's context.
7. How to resume the same session with a review comment, and which options a resume keeps.
8. Whether anything makes it wait on stdin.
9. Which instruction files it reads by itself.
10. How it reports tokens and time, and what happens at its plan's limit.

## Reusable material

- `orchestra-codex/roles/*.md`: role prompts for engineer, analyst, implementer and writer, with the shared reporting rules in `reporting.md`. Their report format is the one `scripts/check_report.py` checks. The Claude agent files in `agents/` assume Claude tools and Haiku calls, so they do not fit a CLI worker as they are.
- `orchestra-codex/skills/delegation/SKILL.md`: Sol effort routing per role (implementer and writer `low`, engineer `high`, analyst `xhigh`), taken from OpenAI's GPT-6.2 guides. Its dispatch section uses Codex's in-app `spawn_agent`, which `codex exec` does not have.

## Design

Proposed on 2026-11-19, from the facts above and the user's criterion: spend the other plan's usage instead of Claude's, with the result's quality unchanged. The process inside a worker does not matter; the reviewed result does.

### Modes

- One mode per session: off, `claude`, `codex` or `agy`. It replaces the on record of 0.2.0 with a per-session mode file.
- `/orchestra:conduct`, `/orchestra:enable` and `/orchestra:conductor` set `claude`. `/orchestra:conduct-codex` sets `codex`, and `/orchestra:conduct-agy` sets `agy`. `/orchestra:disable` sets off. Typing another mode's command switches to it.
- The conductor runs on Fable or Opus in every mode, as in 0.2.0. The workers are the only difference between the modes.

### Hooks in the CLI modes

- Every hook script reads the mode first. In `codex` or `agy` it exits with no decision: `require_skill.py`, `guard_agent.py`, `guard_main.py`, `report_gate.py`, `check_report.py`, and the notes of `session.py`. The one exception is the Opus effort question in `require_skill.py`, which still asks once. See "Answers from the user".
- `session.py` still records the mode from the user's commands and the user's turns. Nothing else runs.
- Compaction keeps a skill's text, so the CLI modes send no reload note after it.

### Instructions

Three layers, so that agy adds one file:

1. **`orchestra:conductor`**, unchanged: the workflow, the brief, review before the report, rework, task groups, the gate, the final report. `orchestra:interview` and `orchestra:documentation` apply as they are.
2. **`orchestra:cli-workers`**, new, shared by Codex and agy: what changes when the workers are CLI runs. It replaces "Delegate", "Haiku calls", "Delegation constraints" and the hand-back format of the conductor's instructions, and `orchestra:delegation`. It also says that no harness runs in this mode, so each rule the conductor's instructions describe as enforced by the harness is the conductor's to keep, with the checks it runs itself.
3. **`orchestra:codex`**, new: the Codex facts, as the answers to "Checklist for adding a CLI worker". `orchestra:agy` is the same file for agy.

`/orchestra:conduct-codex` loads layer 1, then layers 2 and 3.

### Workers and roles

- The roles are those of `orchestra-codex`: `engineer`, `analyst`, `implementer`, `writer`. Their files are copied into this plugin under `roles/`, shared by both CLIs, with the reporting rules in `roles/reporting.md`. A worker reads its role file and its task spec by absolute path. The skill's base directory, which Claude Code prints when a skill loads, gives the plugin's path.
- The models follow the Claude roster's split between judgment and execution, and the efforts are the user's choice of 2026-11-19. `engineer` runs on `gpt-6.2-astra` at `high` by default, `medium` for bounded work, and `xhigh` for interacting causes. `analyst` runs on `gpt-6.2-astra` at `high` by default, `medium` for a bounded survey, and up to `xhigh` or `max` as the investigation demands. `implementer` and `writer` run on `gpt-6.2-sol` at `medium` by default and `high` when the work needs planning, one level above the GPT-6.2 guides' starting point, because the user has Codex usage to spare. Quality comes first in this step.
- No Haiku workers. The conductor may still make its own `haiku-scout` calls.
- agy mode follows the same split: `engineer` and `analyst` on Gemini 4 Neon, `implementer` and `writer` on Gemini 4 Hydrogen. Neon defaults to `high`, Google's default; Hydrogen to `medium`, which Google gives for general coding. The first message to a Gemini worker adds the plan, verify and execute loop and the task's test command. To be confirmed when Gemini mode is built.

### Runs

A script, `scripts/worker.py`, makes each run so that the conductor does not assemble the flags by hand. It is a tool the conductor runs. It enforces nothing.

- **`run`** takes the task spec, the role, the effort and the CLI. It builds the command with every fact above applied: the model and effort, plugins and MCP servers off, the sandbox, the folders the role may write, the network, the prompt from a file, and stdin closed.
- **Writable folders by role.** An implementation role gets the project. A read-only role gets only its report path and its `files/` folder. `.git` stays read-only either way.
- **The network** is restricted unless the brief pre-approves `deps` or `http`.
- **`resume`** takes a review comment and passes the same settings again with `-c`, because a resume does not keep them.
- **What it keeps.** It saves the session id, the last message and the token count under `files/<task>/`. It prints a few lines: the status line, the time, the tokens, the changed files, and warnings.
- **After each run it checks the run against its limits.** These are the hook duties that a check after the run can cover: files changed outside the task spec's scope, any change to the brief, the task specs, the review records or the decision log, a change to git state, and a `DONE` report without a valid verification record. That last check uses the validator of `check_report.py`. It prints problems, never the report's text, so the review still comes first.
- **Background runs.** A run longer than a foreground Bash call allows goes to the background, and the conductor ends its turn as it does for a subagent.

### Where the plan's limit is reached

The task stops with `BLOCKED` and the conductor tells the user. It does not fall back to Claude subagents, because that spends the usage this mode exists to save.

### The instruction-only test

- `scripts/audit.py` reads a session's transcript and work folder after the work, and lists every point where a hook would have refused or flagged something. Examples: a project file changed by the conductor outside its `Conductor writes:` line, a worker run before the brief was confirmed, a report read before its review record existed, a run without a task spec, and a git command while a run was in progress.
- The same audit runs on Claude-mode sessions, where the hooks hold the count at zero, so the two modes compare on one measure.

## Status

On 2026-11-19 these are built on the branch, with tests that call no model:

- **Modes and hooks.** One mode per session in `scripts/common.py`. `/orchestra:conduct-codex` sets `codex`. In `codex` and `agy` every guard exits at once, and `require_skill.py` keeps only the Opus effort question. 7 hook tests, 217 in all.
- **Instructions.** `skills/conduct-codex`, `skills/cli-workers` and `skills/codex`, and the four roles under `roles/`.
- **Runs.** `scripts/worker.py` with 16 tests against a fake `codex`, and the four real runs above.
- **The audit.** `scripts/audit.py` with 8 tests on synthetic transcripts. On this session's own transcript it found the one call a hook refused in the Fable part, and a task spec written before `orchestra:documentation` was loaded.

Not done yet:

- **A whole piece of work in Codex mode,** run by a conductor in a new session with this branch's plugin, followed by the audit.
- **The README and the development notes,** once that run has shown how the mode behaves.
- **agy mode,** from the checklist's answers for `agy`.

## Answers from the user

Given on 2026-11-19:

- **The bar for dropping the hooks** is not set in advance. The user decides from the audit's results.
- **The Opus effort question stays in the CLI modes**, as the one hook that still acts there. It asks; it does not guard. The conductor cannot know its own effort, so an instruction cannot replace it.
- **Pre-approved for this work:** Codex runs that spend Codex usage for the tests, and the use of `agy`. Not pre-approved: commits and pushes, which are asked for when they come up.

## Acceptance criteria

- `/orchestra:conduct-codex` turns Codex mode on. With it on, every guarding hook exits without a decision, and the mode can be switched to the Claude mode or off.
- A piece of work runs end to end in Codex mode with the same documents as the Claude mode: brief, task specs, decision log, worker reports, review records written before the reports are read, at least one rework through `resume`, and the final report.
- The implementation's tokens show up on the Codex plan, not on the Claude plan.
- `scripts/worker.py` makes first runs and resumes with the documented settings, and its check after a run catches a change outside the task's scope, a change to the conductor's documents, and an invalid `DONE` report. Hook-style tests cover each of these without a model call.
- The instruction-only test is recorded: what was run, what the conductor kept to without a hook, and every point where it did not, as `scripts/audit.py` lists them for the CLI mode and for the Claude mode.
- A specification of Codex mode exists from which agy mode can be written with only the checklist's answers changed. agy mode is built from it.
