---
description: Checks the environment the orchestra harness runs in. Confirms that the hook scripts work, and lists the work folders in this project with how far each one has got.
disable-model-invocation: true
---

Run this command.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py"
```

Show the output to the user as it is. For each `[warn]` line, tell the user the fix that the line describes.

This check changes nothing. Do not edit settings files unless the user asks you to.
