# Harness Doctor

## While working on it
Before changing anything, run the server from this repo, not the installed plugin, so the open page shows your edits:

```bash
D=~/.claude/plugins/data/harness-doctor-harness-doctor
python3 ~/.claude/plugins/cache/harness-doctor/harness-doctor/*/app/harness_doctor.py --stop --data "$D"
python3 ~/Playground/harness-doctor/app/harness_doctor.py --background --dev --data "$D"
```

The page (`app/index.html`) is read from disk on every load, so a browser refresh shows page edits.
`--dev` restarts the server by itself when any `app/*.py` file changes.
Tests: `python3 -m unittest discover -s tests`.
The installed plugin only changes after a version bump, a push to GitHub and a plugin update.

## Where things are
`app/reader.py` reads the logs and file versions, `app/rules.py` cuts files into rules, `app/judge.py` asks Claude, `app/harness_doctor.py` builds the page's data and serves it.
The design and the research live in `~/.claude/domain-knowledge/projects/harness-doctor/` (the author's source of truth); `docs/` is a copy for readers of the repo.
The page loads nothing from the internet: fonts are in `app/fonts/`.
