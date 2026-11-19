# PRD: conduct-codex and conduct-gemini

Branch: `conduct-codex-gemini`, from `main` at Orchestra 0.2.0. This document is the starting point of the work on that branch. Delete it when the work is done; what stays true goes into the README and `docs/development.md`.

## Goal

Add two conductor modes in which a Claude conductor delegates the implementation to another vendor's coding CLI instead of to Claude subagents:

- **`/orchestra:conduct-codex`**: the workers are Codex CLI models, `gpt-6.2-astra` and `gpt-6.2-sol`.
- **`/orchestra:conduct-gemini`**: the workers are Gemini CLI models.

The purpose is to spend the Codex (or Gemini) plan's usage on implementation instead of the Claude plan's. Making the other side's usage small as well is a later step, not this one.

Codex mode comes first and is worked out in full. Its specification is written so that Gemini mode follows from it with the facts of a second CLI filled in.

A second question is tested on the same branch: **can Orchestra work from instructions alone, with no hooks?** In the two new modes the hooks step aside, so those modes are the test. If they hold up, the Claude mode (`/orchestra:conduct`) may drop its hooks too, for consistency across the three modes. The hooks are the more reliable mechanism. The test is whether the instructions are reliable enough.

## Requirements

1. **Commands and modes.** `/orchestra:conduct-codex` and `/orchestra:conduct-gemini` turn their mode on for the session, like `/orchestra:conduct` does for the Claude mode. One mode is on at a time. `/orchestra:disable` turns any of them off. Only a command the user types changes the mode.
2. **The hooks step aside.** When Codex or Gemini mode is on, every Orchestra hook that guards or checks exits at once and decides nothing. The hook that records the mode from the user's commands keeps running, since it is how a mode is turned on and off. This is the "state file check" pattern: hooks stay registered and read the session's mode first.
3. **The same workflow.** Brief, task specs, decision log, independent review before the report, rework, task groups, the gate, and the final report work as in the Claude mode. The work folder layout under `.orchestra/` is the same.
4. **Workers.** The conductor's subagents are replaced by CLI runs. Haiku workers are not used in these modes. The conductor may still make its own `haiku-scout` calls.
5. **Reports.** A worker writes its full report to `.orchestra/<work>/reports/T01.md` and returns the short status lines, as a Claude subagent does.
6. **Gemini from the same spec.** Gemini mode reuses the Codex mode's instructions, changing only the CLI facts: commands, flags, models, efforts, isolation, sandbox.

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
- **`--add-dir`** made `.orchestra/<work>/reports/` writable while `tasks/` beside it stayed read-only.
- **`-o <file>`** writes the run's last message. The CLI process writes it, outside the sandbox, so it can go anywhere.
- **Isolation from Codex plugins.** `--disable plugins` turns every Codex plugin off for the run. Measured on the model-visible input: about 82,000 characters and 23 skills before, about 69,000 characters and 7 skills after. Disabling one plugin with `-c 'plugins."<name>".enabled=false'` changed nothing; the `orchestra-codex` conductor skills stayed visible. An earlier relay test used that ineffective form.
- **What stays after `--disable plugins`:**
  - user skills (`~/.agents/skills`, `~/.codex/skills`: `diagnose-crash`, `omarchy`, `omarchy-app`)
  - Codex system skills (`imagegen`, `openai-docs`, `skill-creator`, `skill-installer`)
  - the global `~/.codex/AGENTS.md`, a rule about tracking AGENTS.md in git
  - the MCP server `node_repl`, which `-c 'mcp_servers.node_repl.enabled=false'` removes
  - multi-agent instructions. `--disable multi_agent` did not remove them. The user accepts this: the result is what is reviewed, not how the worker got there.
- **Seeing the model's input without a model call.** `codex debug prompt-input -c 'model="..."' <prompt>` prints the model-visible input as JSON. It does not take `-m`. Use it to check isolation after any change to the flags.
- **Stdin hang.** With the prompt given as an argument and stdin left open, `codex exec` waits forever to read stdin and append it, with no CPU use and no network connection. In Claude Code's shell, stdin is a socket that never closes. Pass the prompt as `- < spec.md`, or add `< /dev/null`.
- **Resume.** `codex exec resume <session-id> '<message>'` continues the same session; the session id is in the run header. `resume` has no `-s`, `-C` or `--add-dir` options. Whether an `--add-dir` of the first run still applies on resume is not verified. `--ephemeral` makes a run impossible to resume.
- **Output size.** stdout carries the header, the transcript and the last message. Filtering it to the header lines and the token count keeps it out of the conductor's context.
- **Instructions file.** Codex reads `AGENTS.md`, not `CLAUDE.md`. A project rule that lives only in `CLAUDE.md` has to reach the worker through the task spec.
- **Not verified:** whether `workspace-write` lets the model write inside `.git`, how a run behaves when the Codex plan's limit is reached, and how to read the Codex usage limit from the command line.

Measured runs, all on `gpt-6.2-astra`:

| Run | Effort | Time | Tokens reported |
|---|---|---|---|
| Duration module and tests, first round | high | 35 s | 15,172 |
| The same session resumed with a review comment | high | 17 s | 18,311 (whether this is per turn or cumulative is not known) |
| Two shell writes, a sandbox test | low | 12 s | 3,407 |
| A report written through `--add-dir` | low | 9 s | 3,820 |

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

### Gemini CLI

Nothing is known yet. On this machine no `gemini` command is on the PATH. A `~/.gemini` folder exists.

## Checklist for adding a CLI worker

Every fact under "Codex CLI" answers one of these questions. Gemini mode needs the same answers:

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

## Open decisions

The interview for the first task settles these:

- **Roles and models.** Which role goes to `gpt-6.2-astra` and which to `gpt-6.2-sol`, and at which efforts. One option is to map the Opus roles to Astra and the Sonnet roles to Sol. Another is Sol for every worker, as `orchestra-codex` does.
- **The conductor's model in these modes.** Fable and Opus as in 0.2.0, or any Claude model.
- **The plan's limit.** When the Codex or Gemini limit is reached: stop and ask, or fall back to Claude subagents.
- **Where the role prompts live.** Copied into this plugin, or read from an installed `orchestra-codex`.
- **Closing the gaps.** For each hook duty listed above: an instruction, a CLI flag, a check after the run (such as comparing `.orchestra/` and `git status` before and after), or an accepted gap.
- **The network.** Whether a worker runs with network access by default, or only when the brief pre-approves `deps` or `http`.
- **The evaluation.** How the instruction-only test is judged: which violations count, how many runs, and how they compare with the hooks.

## Acceptance criteria

- `/orchestra:conduct-codex` turns Codex mode on. With it on, every guarding hook exits without a decision, and the mode can be switched to the Claude mode or off.
- A piece of work runs end to end in Codex mode with the same documents as the Claude mode: brief, task specs, decision log, worker reports, review records written before the reports are read, at least one rework through `resume`, and the final report.
- The implementation's tokens show up on the Codex plan, not on the Claude plan.
- The instruction-only test is recorded: what was run, what the conductor kept to without a hook, and every point where it did not.
- A specification of Codex mode exists from which Gemini mode can be written with only the checklist's answers changed. Gemini mode is built from it once Gemini CLI is installed.
