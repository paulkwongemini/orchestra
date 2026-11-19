---
name: codex
description: The facts of the Codex CLI for a conductor in Codex mode, with the models each worker role runs on. Load it after orchestra:cli-workers once the user has turned Codex mode on with /orchestra:conduct-codex.
user-invocable: false
---

# Codex workers

`scripts/worker.py run --cli codex` runs each task with `codex exec`. Know what a run has, so that you write task specs it can carry out and read its results correctly.

## Models

| Role | Model |
|---|---|
| `engineer`, `analyst` | `gpt-6.2-astra` |
| `implementer`, `writer` | `gpt-6.2-sol` |

Both models take the efforts `low`, `medium`, `high`, `xhigh`, `max` and `ultra`. The roles use `medium` to `xhigh`, `max` for the hardest analyses, and `low` for an edit that is fully determined. `ultra` lets Codex split the work across subagents of its own: use it only when the user asks for it.

## What a run has

- **No interaction.** The run never asks for approval. An action its sandbox denies fails, and the worker sees the error. A worker that needs a decision reports `BLOCKED`.
- **No user plugins or MCP servers.** The script turns off the user's Codex plugins and every MCP server in their Codex config. The user's own skills, Codex's built-in skills and the global `~/.codex/AGENTS.md` stay. The worker may start subagents of its own; that is its business.
- **Writing.** `engineer`, `implementer` and `writer` may write anywhere in the project, `.orchestra/` included, which is why the script checks the changes afterwards. An `analyst` may write only its `files/<task>/` folder and the work folder's `reports/`; `/tmp` is taken from it too, so a project under `/tmp` stays read-only to it. The implementation roles keep `/tmp` for their tests. `.git` is read-only for every role, so no worker can commit, switch or stash.
- **Network.** Open, unless the brief's `## Constraints` says `Worker network: blocked`.
- **The guard.** The script passes the run a `PreToolUse` hook through `-c`, with `--dangerously-bypass-hook-trust`, so it needs no review and changes no config. It sees shell commands as `Bash` and file edits as `apply_patch`. The flag also lets the user's other enabled Codex hooks run without their review.
- **Instructions it reads by itself:** the project's `AGENTS.md`. Not `CLAUDE.md`.
- **The user's Codex config** sets the default model, effort and service tier. The script sets the model and the effort of every run. It leaves the service tier as the user set it.
- **Resume** continues the worker's conversation but does not keep the first run's sandbox settings, so the script passes them again.
- **Cost.** A run starts with about 16,500 input tokens of Codex's own context before the task, most of it cached.

## Not known yet

- How a run ends when the user's Codex plan reaches its limit. The script warns when the CLI's output mentions a usage limit.

## Checking the isolation

After the user updates the Codex CLI, check that the run still sees no user plugins. This shows what the model receives, without a model call:

```bash
codex debug prompt-input -c 'model="gpt-6.2-astra"' --disable plugins hi
```
