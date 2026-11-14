---
name: diagnose
description: Diagnose how a CLAUDE.md, a skill or an output style is doing - judge each session that used it as a good or bad use, find the moment each issue happened, and say why the file fails and the exact edit to make. Use when the user asks "why does X keep failing", "diagnose X", "what's wrong with skill X", "is my CLAUDE.md working", or "is my output style helping". The Harness Doctor page uses its triage and re-check modes.
---

# Harness Doctor: is this file doing its job?

A session can go wrong for many reasons, and nothing in Claude Code says which instruction file was at fault.
This skill judges each use of a file: a good use (it did its job, or at least caused no issue) or a bad use (it caused or allowed an issue).
It never edits the file; the user decides.

## Input
Gather it yourself: the file; the messages from `~/.claude/projects/*/<session_id>.jsonl`; and, where it exists, the /insights score in `~/.claude/usage-data/facets/<session_id>.json` (`friction_counts`, `friction_detail`, `brief_summary`, `outcome`).
  Use the sessions that loaded the skill (or, for CLAUDE.md and the output style, any session after that version).
  For a skill, also search the other sessions since it existed for moments its job came up but it never loaded.

## Steps
1. **Judge each session for this file:**
   - `worked`: the file was used and did its job (say what went right in one line, and quote in `you` the user's request it served, copied exactly, under 200 characters);
   - `failed`: the file caused or allowed at least one issue (a line in it was wrong, or a needed line was missing);
   - `not involved`: only for CLAUDE.md and output styles, which load in every session: the session never touched what the file covers (say where its issues belong in one line, e.g. "mouse lock: game launch rules in CLAUDE.md").
   - `missed`: only for a skill: its job came up in the session but it never loaded (say what the user asked for in one line, and quote it in `you`).
   A skill only loads when it's used, so every session that loaded it is `worked` or `failed`, never `not involved`: a skill that loaded and caused no issue `worked`.
   A missed load is the description's fault, not the body's: the fix goes in the description, using the words the user actually said.
   Each ✓ or ✕ is one usage: CLAUDE.md and an output style are used once per session, a skill once per load.
   A session that loaded the skill more than once has `uses` (the time of each load): give one verdict per load, each with `use` (1 for the first), judged from the messages after that load.
2. **For a failed session, split its issues.** Only the issues this file caused; each gets its type and one plain line under 12 words.
3. **Find each moment.** Quote the user's words and Claude's words where the issue happened, copied exactly and cut to the key part (under 200 characters each).
   Leave a side out (null) if it said nothing at that point; never invent a quote.
   In each quote, mark the 1–3 words or short phrases that show the issue (the pushback, the wrong claim, the confusion), copied exactly from the quote.
4. **Read the history.** If an issue began after an earlier edit, name that edit and suggest putting back what it removed; if the same issue also happened before, say it's older than this version.
5. **Write the diagnosis:**
   - `why`: two sentences on the pattern across the failed sessions, naming the part of the file at fault (or saying it isn't failing);
   - `try`: two or three edits to the file, each ready to apply: `why` (one sentence), `find` (exact text now in the file to replace or cut, or null to add), `text` (the new text, or null to cut) and `after` (for an add: the exact line it goes after); none if nothing needs changing.
     Copy `find` and `after` exactly from the file text, so the edit can be applied as is.
     Write each edit as the general rule the failure breaks, with no project, genre or topic words from the sessions; a case can follow only as a short example.

## Triage mode (the Harness Doctor page's "Judge new sessions" and "Judge them again" buttons)

A cheap first pass over many sessions at once, from the user's own messages (no file text).
Input: `files` (one per file version: id, name, kind, and `about`: its description, or else its headings and rule titles at that version, so a CLAUDE.md shows what it covered back then) and `sessions` (each with the ids of the `files` it touched, and `you`: the user's messages, each with its time and `after_claude`, the end of the Claude message it answered).
Where /insights scored the session, it also has `issues` counted by type, `note`, `summary` and `outcome`; use them as extra evidence.
Where it didn't, judge from `you` alone: pushback, corrections and confusion ("no", "i meant", "why did you") show an issue; type each one as `misunderstood_request`, `wrong_approach`, `buggy_code`, `excessive_changes` or a short snake_case name.
For each session, judge each file it touched with the steps above, but skip the diagnosis (every skill it loaded is `worked` or `failed`).
A session's `uses` lists, for each skill it loaded more than once, the time of each load: give that skill one verdict per load, each with `use` (1 for the first).
For every verdict and every issue, quote in `you` the user's words for it (the request a worked file served, or the words where the issue showed), copied exactly from `you`, under 200 characters; mark 1–3 words that show an issue in `you_marks`.
Leave out files that weren't involved.
Also list the session's `gaps`: issues that none of its `files` is about, so a new skill, rule or hook would be their home.
Leave out one-off slips no instruction could prevent.
Give each gap a `topic`, a short snake_case name for the problem (e.g. `unverified_visuals`); reuse a topic from `known_gaps` (topics found before, each with one line) when it's the same problem, so repeats group together.
Answer with only this JSON: `{"sessions": [{"session": "<id>", "files": [{"file": "<id>", "use": 1 (only with `uses`), "verdict": "worked" | "failed", "line": "...", "you": "..." or null, "issues": [{"type": "<type>", "line": "...", "you": "..." or null, "you_marks": ["..."]}]}], "gaps": [{"topic": "<snake_case>", "type": "<type>", "line": "...", "you": "..." or null, "you_marks": ["..."]}]}]}`

## Re-check mode (right after triage, on the same button)
Gaps were judged against the files each session loaded, so a file may cover them: one written or changed since, or a skill that existed but didn't load.
Input: `files` (every file today: id, name, kind, `scope` (global or a project) and `about`) and `gaps` (each `topic` with its `moments`: date, project, one line).
For each gap, name the one file whose job today is to prevent that problem, judged from its `about`; a skill whose job was the moment itself (the user asked for what it does) covers it too.
A file covers a gap only when its `about` names that problem or the behaviour that prevents it; a file on a nearby subject doesn't.
A project file covers only that project's moments.
Leave a gap out when no file covers it.
Answer with only this JSON: `{"covered": [{"topic": "<topic>", "file": "<id>"}]}`

## Output
A short answer: how many sessions were good uses, bad uses and missed loads, why in two lines, then the edits as a short list. Offer to apply them; don't apply them.
