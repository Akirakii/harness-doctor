# Harness Doctor

A page that shows which of your Claude Code instructions help and which cause trouble.

You write CLAUDE.md files, skills and an output style, then keep tweaking them. Harness Doctor reads your own sessions and, for each file, marks every session that used it as a good use (✓) or a bad use (✕). For files with bad uses, it says why they fail and gives the exact edit to make.

## What's on the page

- **One card per file:** your global and project CLAUDE.md files, each skill, and your output style, colored by kind.
- **✓ and ✕ per session:** hover a mark to see what happened and your own words at that moment.
- **Versions:** each card's square shows its version (one Claude Code session's batch of edits). Click it for the diff, scroll it to go back through versions and their marks.
- **Run harness doctor:** judges the sessions it hasn't seen yet, then diagnoses each file with new bad uses and suggests edits, each with a line on why, shown as lines to cut and lines to add.
- **Apply:** toggle the suggested edits you want, then press Apply in the card's corner to write them into the file. It shows up as a new version, so later sessions show whether it helped. Revert puts the old text back while the file is still as Apply left it.
- **Right-click to ask Claude:** right-click the page, a card, a ✕ or a suggestion and pick Copy prompt. It copies a prompt to paste into Claude Code, naming the files and sessions so Claude can read the logs itself.
- **Project picker:** look at one project's files and sessions, or Global.

## Install

As a Claude Code plugin:

```
/plugin marketplace add Akirakii/harness-doctor
/plugin install harness-doctor@harness-doctor
```

Then type `/harness-doctor:open`, or ask Claude to "open harness doctor". It starts a small local server and opens the page in your browser.

You can also ask in chat, for example "why does my explain-simple skill keep failing?", and the `diagnose` skill answers there.

Without the plugin:

```
python3 app/harness_doctor.py            # opens http://127.0.0.1:8737
python3 app/harness_doctor.py --help     # --port, --data, --no-open, --background, --stop
```

Needs Python 3.10 or later and the `claude` command on your PATH. There's nothing else to install.

## What it costs

It only calls Claude when you press **Run harness doctor**, using your own Claude Code login (`claude -p`):

- **Judging:** one Sonnet run per 15 new sessions. It reads your messages, each with the end of the Claude message you were answering.
- **Diagnosing:** one Opus run, at high effort, per file that has a new bad use. It reads the file, its last 3 versions, and the messages around each issue.

Each session is judged once, and each file version is diagnosed once, unless new sessions come in.

## Privacy

- The server only listens on `127.0.0.1`, and rejects requests that come from other sites.
- To judge, it sends excerpts of your sessions to Claude through `claude -p`, the same way any Claude Code session sends your messages. Those runs use no tools, and don't load your hooks or settings.
- It only writes to your files when you press Apply or Revert, and only to the file on that card.
- Its results stay on your machine, in the plugin's data folder (`~/.claude/plugins/data/harness-doctor…/`, or `~/.local/share/harness-doctor` when run by hand). They're deleted 30 days after they're made, and when you uninstall the plugin.

## How it works, and what can break

It reads files Claude Code already keeps:

| What | Where | Used for |
|---|---|---|
| Session logs | `~/.claude/projects/*/*.jsonl` | which skills loaded, your messages, every edit to a CLAUDE.md, skill or output style |
| File-history snapshots | `~/.claude/file-history/` | the text of each version |
| /insights scores, if you've run it | `~/.claude/usage-data/facets/` | extra evidence when judging |

None of these formats are documented, so a Claude Code update can break it.

Other limits:

- Edits you make in your own editor aren't in the logs, so they don't show up as versions.
- Edits Claude made through shell commands (`cat >`, `sed -i`) are read on a best-effort basis.
- It looks at the last 30 days, the same window Claude Code keeps logs for.
- The ✓ and ✕ marks are Claude's judgment, so they can be wrong.

## Layout

```
.claude-plugin/   plugin.json, marketplace.json
skills/open/      /harness-doctor:open: starts the server and opens the page
skills/diagnose/  the instructions Claude follows to judge and diagnose (also usable in chat)
app/              harness_doctor.py (server, no dependencies) and index.html (the page)
tests/            python3 -m unittest discover tests
```

## License

MIT
