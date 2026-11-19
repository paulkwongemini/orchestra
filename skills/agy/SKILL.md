---
name: agy
description: The facts of the Antigravity CLI (agy) for a conductor in agy mode, with the Gemini model each worker role runs on. Load it after orchestra:cli-workers once the user has turned agy mode on with /orchestra:conduct-agy.
user-invocable: false
---

# agy workers

`scripts/worker.py run --cli agy` runs each task with `agy -p`, the Antigravity CLI's one-shot mode. Know what a run has, so that you write task specs it can carry out and read its results correctly.

## Models

| Role | Model |
|---|---|
| `engineer`, `analyst` | Gemini 4 Neon, the deeper reasoner |
| `implementer`, `writer` | Gemini 4 Hydrogen, lighter and faster |

The efforts are `low`, `medium`, `high` and `xhigh`; agy names the effort in the model id, as in `gemini-4-neon-high`. `xhigh` is the top level, so an analysis that would go to `max` on Codex goes to `xhigh` here. Gemini 4 Argon is not used.

## What a run has

- **No interaction.** A tool call that would need approval is refused without waiting, and the run goes on. A worker that needs a decision reports `BLOCKED`.
- **Its own workspace.** The run starts in a folder of the plugin's own under the system's temporary folder, which holds the guard's `.agents/hooks.json`. The project is added with `--add-dir`. That is how the guard reaches the run without touching the user's agy config or project.
- **The sandbox stays on.** `engineer`, `implementer` and `writer` may write in the project, `.orchestra/` included, which is why the guard and the script's check after the run watch it. An `analyst` gets the project for reading only, and writes only its `files/<task>/` folder and the work folder's `reports/`. Folders outside these are not visible to the run. `.git` is read-only, so no worker can commit.
- **Network.** Open through an allow rule, `read_url(*)`, unless the brief's `## Constraints` says `Worker network: blocked`. Then the sandbox blocks it.
- **The guard** sees shell commands as `run_command` and file writes as `write_file` and similar tools. It refuses any request to leave the sandbox, since nobody is there to approve it.
- **Instructions it reads by itself:** the project's `AGENTS.md` and `.agents/hooks.json` when the project is added for writing, so the user's own project hooks run too. An `analyst`, which reads the project without adding it, does not get the project's `AGENTS.md`: put the rules it needs into the task spec.
- **Output.** The script runs agy with `--output-format stream-json`, so the run's events reach its record as they happen and `worker.py watch` can show them. The last event, `result`, holds the worker's last message, the status and the tokens.
- **Resume** with `--conversation` keeps the conversation and the workspace. The script passes the same settings again.
- **Cost.** A short run reads about 15,700 input tokens, with about 18,000 more read from cache.

## Not known yet

- How a run ends when the user's Gemini plan reaches its limit. The script warns when the output mentions a usage limit.
- How to turn off the user's agy plugins and skills for one run. The script does not. `--disable-slash-commands` keeps a prompt from expanding into a skill.
