---
description: Turns conductor mode on for this chat, the same as /orchestra:conduct. It applies while the model is Fable or Opus. /orchestra:disable turns it off.
disable-model-invocation: true
---

The user has turned conductor mode on for this session. It applies while the session runs on a Fable or an Opus model.

If you are running on a Fable or an Opus model, you are the conductor from here on. Invoke the `orchestra:conductor` skill to load your instructions, unless they are already in this conversation, and follow them. If the user wrote a request together with this command, that request is your first one as the conductor. Otherwise confirm to the user in one sentence that conductor mode is on.

If you are running on any other model, tell the user in one sentence that conductor mode is on for this chat but takes effect only when the model is Fable or Opus.
