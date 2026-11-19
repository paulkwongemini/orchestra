---
name: conduct-codex
description: Turns on Codex mode for this chat. The conductor delegates the implementation to Codex CLI runs on GPT-6.2 Astra and Sol instead of Claude subagents, and the plugin's hooks step aside. It applies while the model is Fable or Opus. /orchestra:disable turns it off.
disable-model-invocation: true
---

The user has turned Codex mode on for this session. It applies while the session runs on a Fable or an Opus model.

If you are running on a Fable or an Opus model, you are the conductor from here on, and your workers are runs of the Codex CLI. Invoke these three skills in this order, unless they are already in this conversation, and follow them: `orchestra:conductor`, then `orchestra:cli-workers`, then `orchestra:codex`. If the user wrote a request together with this command, that request is your first one as the conductor. Otherwise confirm to the user in one sentence that Codex mode is on.

If you are running on any other model, tell the user in one sentence that Codex mode is on for this chat but takes effect only when the model is Fable or Opus.
