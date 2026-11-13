---
description: Turns conductor mode back on for this chat after /orchestra:disable.
disable-model-invocation: true
---

The user has turned conductor mode back on for this session. It applies while the session runs on a Fable model.

If you are running on a Fable model, you are the conductor again from here on. Invoke the `orchestra:conductor` skill to load your instructions, unless they are already in this conversation, and follow them. Confirm to the user in one sentence that conductor mode is on.

If you are running on any other model, tell the user in one sentence that conductor mode is on for this chat but takes effect only when the model is Fable.
