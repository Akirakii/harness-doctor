# Harness Doctor 2.0: the settled design

Settled with the user in chat on 2026-11-16, through mock pages.
The page everything below describes: `mock/harness-history.html` (sample data).
Research behind the judge: `docs/judge-research.md`.
Parts tagged (Claude) are Claude's picks the user accepted without discussing them; everything else the user asked for or chose.

## What the doctor judges
- Every item is a piece of text with a scope (everything, one project, one task), a way it gets loaded (always, or when something sends Claude to it), and what it says (an instruction or a fact).
- The harness (CLAUDE.md, output style, skills) and the domain knowledge (notes) are treated the same. A note is one more kind of file.
- The unit is the rule: one part of a file with its own verdicts. In CLAUDE.md and the output style a rule is a numbered or bulleted item that starts with bold text, up to the next one; its group is the heading above it. (Claude)
- A file that loads on a trigger (a skill, a note) has two parts judged apart: the line that loads it (description, "read when") and what it says.
- Only the notes an index sends Claude to are shown; task folders are left out. (Claude)

## The four statuses
Each rule gets one, from its marks on the file as it is now and its marks before the last edit.
- Regression: a bad use now, and the rule was clean before the last edit.
- Failing: a bad use now, and it wasn't clean before, or there's nothing before.
- Healthy: used now, with no bad use.
- No data: no use on the file as it is now. Grey, and never counted as an issue.
A file takes the worst status under it. "Fixed" is not a status: it leads to the same action as Healthy.

## The page
- One list, grouped by scope: "Everything" first, then each project. No tabs, no folders.
- One card per file. The stripe's color and a word under the name give its kind (rules, output style, skill, note).
- A card's header shows only its summary: "1 regression", "2 failing", or one status word.
- An open card has two views, "Rules" and "History".
- Rules: group labels are plain (never folded). A rule is a dot (red, green, hollow) and its title; only "Regression" is written as a word.
  - Mouse on a rule: its ✓ and ✕ tiles take the status word's place, newest version first, labelled "latest", then "v6", and so on ("latest" only when an older version is on the row).
  - Mouse on a tile: that moment (what happened, the user's words, Claude's words). A click keeps it open, with "Open this session".
  - Click a rule: the text that defines it.
- History: the file's edits, newest first. Each edit shows one small card per rule it touched (Regression, Fixed, Failing), then its diff, cut after a few lines. "Show full diff" and "Show source file" sit on the edit's top line.
- "Not covered yet" is the last card of each scope, with a dashed orange outline: problems seen in 2 or more sessions that nothing written is about. A problem seen in one project sits under that project.
- Top of the page: the project picker and "Judge N new sessions". "Copy fix prompt" floats in the bottom right corner. Always night mode; no refresh button.

## Fixing
- The page shows; a Claude Code conversation judges and fixes.
- "Copy fix prompt" copies one command for the whole page: every regression, failing rule and uncovered problem, worst first, one at a time.
- Nothing feeds back: the conversation writes no verdicts into the doctor's data and the judge gets no examples from past marks. No "This mark is wrong" on the page.

## The judge
- A narrow question per rule and session: did the rule's moment come up, and was it followed? Answers: pass, fail, unknown. Not "which rule is to blame".
- Keeping cost down: a file that wasn't loaded in a session is sent as its one loading line; only files that were loaded are sent in full. The logs show what was loaded without a Claude call.
- Not built yet. First step: a trial run on real sessions (`proto/rule_judge.py`) to see the marks and the cost.

## Open
- How to cut skills and notes into rules.
- Saved cases replayed on an edit ("Test my edit"), from the research.
- Notes found by reading a file alone (dead paths, overlapping triggers): last, if at all.

## Added after the first real runs (2026-11-16)
Three trial runs of the per-rule judge on 10 real sessions (`proto/rule_judge.py`; `proto/real_page.py` puts the marks into `mock/real.html`).
Each run cost about $1.60 on Sonnet, 16 cents a session, and 40 or more rules came up at least once (checked).

### The domain knowledge on the page
- Whatever an index line points at gets a card, a file or a folder: top-level notes, shared docs, a project's docs, task folders, and each project folder.
- A file inside a task folder gets no card. Reading any file in the folder counts as reading the folder.
- Only a short note (up to 8,000 characters) is cut into facts. A longer doc and every folder is one row, its "read when" line, judged as "was it read when its moment came up". (Claude: the 8,000)
- Groups, in order: Everything, Shared, Every project, then a project. Every note, doc and folder is called "domain knowledge" under its name, with its domain beside it ("domain knowledge · games"). (user)
- The picker decides what shows: the general groups always, a project's group only when picked. It lists the projects in use, then "Show all".
- A project is in use when a session made 2 or more file calls inside it in the last 7 days. (Claude: 7 days and 2 calls; with 30 days, 10 of 15 projects counted, since many were tried and dropped that month.)
- Files with no mark fold away behind "Show N more with no data", per group.

### What the runs showed
- A rule needs a name that survives edits: its id is its file plus its title, never its position.
- The cut: a list item at the left edge is a rule (titled by its bold start, else its first sentence); a section with only prose is one rule named by its heading.
- A note or folder must not be judged in a session older than itself. The trial uses the file's creation time on disk; the real build needs versions for notes as it has for CLAUDE.md.
- The judge isn't steady: the same sessions gave "One line and one idea" 7 ✕, then 4, then 1, as more rules were added to check.
- A rule that applies to every reply fails a whole session on one slip. Open: count such a rule per reply.

### The name stays (user, 2026-11-16)
- Harness Doctor, also now that it judges the domain knowledge. Names weighed and dropped: Setup Doctor, Context Doctor, Context Health, Claude Health, Playbook Health, By the Book. "Claude Doctor" is taken by another tool (millionco).

## Built (2026-11-16, version 2.0.0)
The plugin now does what this doc describes. Code: `app/reader.py` (logs and versions), `app/rules.py` (files cut into rules), `app/judge.py` (the question asked of Claude), `app/harness_doctor.py` (page data, server, `--report`), `app/index.html` (the page), `skills/fix`.
Picks made while building, all (Claude) unless marked:
- **Two judge runs per session,** one for the always-loaded files and one for the files that load on a trigger. Measured on 4 sessions judged twice: 56% of bad marks were found both times, against 36% with one run. Passing and failing rarely swap; the judge mostly misses a different half each time. It costs about 45% more.
- **Counts per mark:** the judge reports how many moments of a rule came up in a session and how many broke it, shown on a mark's hover card ("broke it in about 4 of 20"). The counts are rough: 6 of 20 one run, 14 of 20 the next. A session still gets one mark per rule.
- **A rule has its own history.** Its marks reset only when its own text changes, not when another rule in the file is edited. Without this, a file edited daily showed "No data" on every rule. "Regression" compares the rule's current wording with its previous one.
- **10 sessions per press,** newest first, about 25 to 35 cents each. A session is judged when it's finished, and again only if it grew. A run that gets no answer leaves the session unjudged.
- **Statuses are worked out on the server** and the page only shows them, so the page and the fix report can't disagree.
- **The `diagnose` skill is gone;** `fix` does its job from the doctor's report (`harness_doctor.py --report`).
- **Dropped from version 1:** the "judge them again" banner when the judge's instructions change, the re-check of uncovered problems against today's files, /insights scores as extra evidence, and the old saved marks.
- **A project's own CLAUDE.md, skills and output styles** show under that project, named by the folder Claude Code was started in.
- The page's fonts ship with the plugin, so it loads nothing from the internet.

Still open:
- An uncovered problem stays listed until its sessions age out, also after a rule is written for it.
- A rule that applies to every reply still turns red from one slip in a session.
- A worktree counts as its own project (a folder named `<project>-something` beside `<project>`).
- Saved cases replayed on an edit; notes found by reading a file alone.

## Added 2026-11-18 (in the repo, not released)
- **Tile rows** (user): a long row slides with press and drag, no wheel, and fades on the side that has more. Older wordings sit behind a thin line with dimmed tiles, and the line's hover card says "before <date>". The word "latest" stays, and a row with marks only on older wordings says "no data on latest".
- **Unused rules** (user): the report ends with the rules that have no mark on any wording, one line per file, and the `fix` skill's last step finds why each never came up and whether to keep, reword, merge or remove it. The list of reasons is Claude's own, not researched.
- **Login** (a guess, not confirmed): on a private Claude network the judge run couldn't log in. Likely cause: the run skips the user's settings, which is where a gateway, Bedrock or a key helper is set. The run now hands over the login settings by name, `HARNESS_DOCTOR_MODEL` picks the model, and the page shows Claude Code's own error. Checked only that settings passed this way take effect; the user's fix on that network wasn't seen.

## Added 2026-11-19 (version 2.0.5): the fix skill can correct the judge
Asked for by the user in chat; built by Claude while the user was away, after two critic rounds (task folder `judge-rules-and-turned-off-marks/`, read from this project's index).
This reverses "Nothing feeds back" above (user): "not all issues are issues".
- **A wrong mark can be turned off** (user): the fix skill runs `--turn-off <session> "<rule id>" --why "…"` after the user's yes. The mark stays on the page, struck through, with the reason and a count on the card; it has no say in its rule's status. `--turn-on` undoes it. A problem nothing covers can be turned off too. (Claude: the yes, the reason, staying in sight)
- **The judge has rules the user can change** (user): `judge-rules.md` in the data folder, one bullet per kind of moment the judge keeps misreading. Every later judge run gets them after the built-in instructions. The fix skill proposes one when two turned-off marks are the same kind of mistake, and writes it after the user's yes. (Claude: the file's place, the two-marks trigger)
- **The judge is on the page** (user), as the card "judge rules": its first row counts the marks turned off as wrong since its rules were last edited, then the user's rules, then the built-in instructions. It has no good marks, so it is "Failing" or "No data". (Claude)
- **The plugin's own skills are on the page** (user), judged like any skill. The doctor keeps each text of them it has seen, so a step's marks survive a plugin update that didn't change it. (Claude)
- The page still has no "This mark is wrong" button: turning off happens in the conversation. (kept)

Still open from this:
- A rule for the judge that is too wide hides real failures, and the page can't show a failure the judge no longer reports.
- Which rule for the judge a wrong mark should have been stopped by isn't recorded.
- Marks made before a rule for the judge stay as they are; there is no "judge them again".
