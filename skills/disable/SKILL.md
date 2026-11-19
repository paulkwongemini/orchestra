---
name: disable
description: Turns conductor mode off for this chat, so that the session works as an ordinary one. Stays off until /orchestra:conduct, /orchestra:conductor, /orchestra:conduct-codex or /orchestra:conduct-agy.
disable-model-invocation: true
---

The user has turned conductor mode off for this session, whichever mode was on. From now on this is an ordinary session, whatever model it runs on. The conductor instructions and the orchestra notes in this conversation no longer apply: you may implement, edit files, and use any subagent yourself.

Confirm to the user in one sentence that conductor mode is off for this chat and that `/orchestra:conduct` turns it back on.
