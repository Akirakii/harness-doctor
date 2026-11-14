# Harness Doctor

## While working on it
Before changing anything, run the server from this repo, not the installed plugin, so the open page shows your edits:

```bash
D=~/.claude/plugins/data/harness-doctor-harness-doctor
python3 ~/.claude/plugins/cache/harness-doctor/harness-doctor/*/app/harness_doctor.py --stop --data "$D"
python3 ~/Playground/harness-doctor/app/harness_doctor.py --background --dev --data "$D"
```

The page (`app/index.html`) is read from disk on every load, so a browser refresh shows page edits.
`--dev` restarts the server by itself when `app/harness_doctor.py` changes.
Tests: `python3 -m unittest discover -s tests`.
The installed plugin only changes after a version bump, a push to GitHub and a plugin update.
