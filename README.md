# Harness Doctor

A page that shows which of your Claude Code instructions help and which cause trouble.

You write CLAUDE.md files, skills and an output style, then keep tweaking them. Harness Doctor reads your own sessions and, for each file, marks every session that used it as a good use (✓) or a bad use (✕). For files with bad uses, it says why they fail and gives the exact edit to make.

## What's on the page

- **One card per file:** your global and project CLAUDE.md files, each skill, and your output style, colored by kind.
- **✓ and ✕ per session:** hover a mark to see what happened and your own words at that moment.
- **Versions:** each card's row shows every version's marks, newest on the left, with a dotted line and a label before each older version, so you can see whether an edit helped. Old versions fold into counts like "v5 2 ✕ 1 ✓" when the row runs out of room. The square shows the version (one Claude Code session's batch of edits): click it for the diff, scroll it to pick an older version.
- **Run harness doctor:** judges the sessions it hasn't seen yet, then re-checks the problems no file covered against your files.
- **Open a card:** it shows a prompt to paste into Claude Code, with a Copy prompt button. The prompt checks the file as it is now against every bad use in view, older versions included: for each, which line would have prevented it, and edits for the ones still open. Hover the ✕ tiles for each moment. Fixing a file takes a conversation, so the page doesn't suggest edits: paste the prompt into Claude Code and work it out there. The edit then shows up as a new version, and later sessions show whether it helped.
- **Right-click to ask Claude:** right-click the page, a card or a ✕ and pick Copy prompt. It copies a prompt to paste into Claude Code, naming the files and sessions so Claude can read the logs itself.
- **Problems tab:** issues that no file covers but keep coming back (2 or more sessions), with their count on the tab. Each problem is a card with a ✕ per session; hover a ✕ to see that moment, or open the card for a prompt that asks Claude where the fix belongs: a new skill, a CLAUDE.md rule or a hook.
- **When a file starts covering a problem:** each run checks the open problems against today's files. If an edit to a file that was there covers it now, the problem becomes a ✕ on the version that lacked it. If a new file covers it, the problem leaves the list.
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

- **Judging:** one Sonnet run per 15 new sessions. Every session with a few messages gets judged, open ones too, since an issue no file covers counts as well; a session with new messages since it was judged gets judged again, and the status line counts those. It reads your messages, each with the end of the Claude message you were answering.
- **Re-checking:** one Sonnet run after judging, only when the open problems or your files changed since the last check. It reads one line per problem and what each file is for.

Each skill load in a session gets its own verdict.

## Privacy

- The server only listens on `127.0.0.1`, and rejects requests that come from other sites.
- To judge, it sends excerpts of your sessions to Claude through `claude -p`, the same way any Claude Code session sends your messages. Those runs use no tools, and don't load your hooks or settings.
- It never writes to your instruction files; fixes happen in your own Claude Code session.
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
