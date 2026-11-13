---
name: open
description: Open the Harness Doctor page in the browser, starting its local server if it isn't running. Use when the user says "open harness doctor", "show harness doctor", "harness dashboard", "how are my skills doing", or asks to stop the harness doctor server.
allowed-tools: Bash(python3 *)
---

# Open Harness Doctor

Run this, then tell the user the URL it prints, in one line:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/app/harness_doctor.py" --background --data "${CLAUDE_PLUGIN_DATA}"
```

If a server is already running, it just opens the page again.
If it prints that it didn't start, show the user the last lines of the log file it names.

To stop it, when the user asks:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/app/harness_doctor.py" --stop --data "${CLAUDE_PLUGIN_DATA}"
```
