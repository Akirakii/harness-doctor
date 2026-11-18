# Harness Doctor

A page that shows which of the rules and facts you wrote for Claude Code hold up in real sessions, and which fail.

You write a CLAUDE.md, skills, an output style and notes, then keep tweaking them. Harness Doctor reads your own sessions and asks Claude one narrow question per rule: did this rule's moment come up, and was it followed? Every rule gets its own marks, a status, and a history across your edits. For what fails, it copies a prompt to fix it in a Claude Code conversation.

## What's on the page

- **One card per file,** in one list: CLAUDE.md, the output style, each skill, and each note or folder of your knowledge. The stripe's color and the word under the name say which kind it is.
- **A status per rule,** from four words:
  - **Regression:** a bad use on the rule as it's written now, and it was clean before its last edit.
  - **Failing:** a bad use on the rule as it's written now.
  - **Healthy:** used as it's written now, with no bad use.
  - **No data:** no use as it's written now.

  A card's header sums up its rules ("1 regression", "2 failing"). Editing one rule doesn't reset the marks of the others.
- **Open a card** for its rules: a dot per rule (red, green, hollow), and the word "Regression" where it applies. Put the mouse on a rule to see its ✓, ✕ and ○ (didn't load) marks, newest wording first. Put the mouse on a mark for that moment: what happened, your words and Claude's. Click a mark to keep it open, with a command that opens that session. Click a rule to read its text.
- **History:** the file's edits, newest first. Each edit shows what it did to the rules (Regression, Fixed, Failing), its diff, and the file as it was.
- **Files that load on a trigger** are judged in two parts: the line that loads them (a skill's description, a note's "read when") and what they say. "Didn't load" means the moment came up and the file stayed closed.
- **Not covered yet:** problems seen in 2 or more sessions that nothing you wrote is about, as the last card of their group.
- **Groups by scope:** "Everything" first, then what's shared, then a project when you pick it. The picker lists the projects you worked in during the last 7 days, then "Show all". Files with no marks fold away.
- **Judge N new sessions:** judges your newest unjudged sessions, 10 per press, all at the same time.
- **Copy fix prompt:** copies `/harness-doctor:fix`. Paste it into Claude Code and the `fix` skill goes through every regression, failing rule and uncovered problem, worst first and one at a time. Last, it checks why the unused rules never came up, and whether each should be removed. Fixing takes a conversation, so the page doesn't suggest edits.
- **Wrong marks:** the judge is a model, so some bad marks are wrong. In the fix conversation, after your yes, a wrong mark is turned off with its reason. It stays on the page, struck through, with the reason on hover and a count on the card ("1 turned off"), and no longer counts against its rule. A problem nothing covers can be turned off the same way; it needs 2 sessions, so it leaves the page when fewer are left.
- **The judge's own card,** "judge rules": the marks turned off as wrong are the judge's bad marks, counted from the last edit of its rules. Under them are your rules for the judge (`judge-rules.md` in the data folder: one bullet per kind of moment it keeps misreading, added by the fix skill after your yes, or by hand) and the instructions built into the plugin. Your rules are added to every later judge run.
- **The plugin's own skills** (`harness-doctor:fix`, `harness-doctor:open`) have cards too, judged like your skills. The doctor keeps each text of them it has seen, so their marks survive a plugin update where a step didn't change.

## Your knowledge files

If you keep notes for Claude in `~/.claude/domain-knowledge/` with `INDEX.md` files, each index line that names a file or a folder and ends in a "read when" cell becomes a card:

```
| Note | What it holds | Read when |
|---|---|---|
| `machine-setup.md` | paths and scripts | running a game or a long job |
| `redo-race/` | the current design | any design work now |
```

A short note (up to 8,000 characters) is cut into its facts, each with its own marks. A longer doc or a folder is judged only on being read when its moment came up; reading any file inside a folder counts. Without that folder, the page shows your CLAUDE.md files, skills and output styles.

## Install

As a Claude Code plugin:

```
/plugin marketplace add Akirakii/harness-doctor
/plugin install harness-doctor@harness-doctor
```

Then type `/harness-doctor:open`, or ask Claude to "open harness doctor". It starts a small local server and opens the page in your browser.

Without the plugin:

```
python3 app/harness_doctor.py            # opens http://127.0.0.1:8737
python3 app/harness_doctor.py --help     # --port, --data, --no-open, --background, --stop, --report, --turn-off, --turn-on
```

Needs Python 3.10 or later and the `claude` command on your PATH. There's nothing else to install.

## What it costs

It only calls Claude when you press **Judge N new sessions**, using your own Claude Code login (`claude -p`, Sonnet):

- Two runs per session: one for the files that are always loaded, one for the files that load on a trigger. Fewer rules per run made the judge agree with itself more often.
- About 25 to 35 cents of usage per session in the author's setup (around 100 rules), so about $3 per press. The button's tooltip shows the estimate.
- A file that wasn't loaded in a session is sent as its one loading line; only files that were loaded are sent in full.
- A session is judged once it's finished, and again only if it grew.

## How far to trust the marks

The marks are Claude's judgment, and they vary. Judging the same 4 sessions twice, 56% of the bad marks showed up both times; the rest were found in only one of the runs. Marks rarely contradict each other (a rule passing in one run and failing in the other), so read a red rule as "worth a look", not as a measurement. The `fix` skill checks each mark with you before changing anything, and turns a wrong one off only after your yes.

Your rules for the judge change what later runs mark, and a rule that is too wide hides real failures: the page can't show a failure the judge no longer reports. Keep each one about a single kind of moment, and read the judge's card after adding one.

## Privacy

- The server only listens on `127.0.0.1`, and rejects requests that come from other sites.
- The page loads nothing from the internet; its fonts ship with the plugin.
- A judge run skips your user settings, apart from the ones that say how Claude Code logs in (`env`, `apiKeyHelper`, the AWS helpers), so it works behind a company gateway, Bedrock or Vertex. `HARNESS_DOCTOR_MODEL` picks another model than Sonnet. When a run gets no answer, the page shows what Claude Code said.
- To judge, it sends excerpts of your sessions and the text of your rules to Claude through `claude -p`, the same way any Claude Code session sends your messages. Those runs use no tools, and don't load your hooks or settings.
- It never writes to your files; fixes happen in your own Claude Code session.
- Its marks stay on your machine, in the plugin's data folder (`~/.claude/plugins/data/harness-doctor…/`, or `~/.local/share/harness-doctor` when run by hand). They're dropped after 30 days, and when you uninstall the plugin.
- The list of marks turned off and your rules for the judge (`judge-rules.md`) live in that folder too, so keep a copy of `judge-rules.md` before uninstalling.

## How it works, and what can break

It reads files Claude Code already keeps:

| What | Where | Used for |
|---|---|---|
| Session logs | `~/.claude/projects/*/*.jsonl` | your messages, Claude's replies and tool calls, which skills loaded and which files were read, every edit to a file it follows |
| File-history snapshots | `~/.claude/file-history/` | the text of each version |

None of these formats are documented, so a Claude Code update can break it.

Other limits:

- A file is cut into rules by its shape: a numbered or bulleted item is a rule, and a section of plain prose is one rule. A file written another way is cut badly.
- A rule is followed across edits by its title. Rename a rule and its marks start over.
- Edits you make in your own editor aren't in the logs, so they don't show up as versions; only the latest one is noticed.
- Edits Claude made through shell commands (`cat >`, `sed -i`) are read on a best-effort basis.
- It looks at the last 30 days, the same window Claude Code keeps logs for.
- Marks from version 1 aren't carried over.
- A session judged again (because it grew) keeps a turned-off mark off only when the new mark quotes the same words.
- The installed plugin and a copy run from its repo keep separate histories of the plugin's own skills.
- Marks made before a rule for the judge was written stay as they are; only later runs follow it.

## Layout

```
.claude-plugin/   plugin.json, marketplace.json
skills/open/      /harness-doctor:open: starts the server and opens the page
skills/fix/       /harness-doctor:fix: goes through what's failing and proposes edits
app/              harness_doctor.py (server and page data), reader.py (logs and versions), rules.py (files cut into
                  rules), judge.py (the question asked of Claude), index.html (the page), fonts/
docs/             the 2.0 design and the research behind the judge
tests/            python3 -m unittest discover -s tests
```

## License

MIT. The bundled fonts, Fredoka and Nunito, are under the SIL Open Font License.
