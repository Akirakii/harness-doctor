---
name: diagnose
description: Diagnose how a CLAUDE.md, a skill or an output style is doing - judge each session that used it as a good or bad use, find the moment each issue happened, and say why the file fails and the exact edit to make. Use when the user asks "why does X keep failing", "diagnose X", "what's wrong with skill X", "is my CLAUDE.md working", or "is my output style helping". The Harness Doctor page also runs it.
---

# Harness Doctor: is this file doing its job?

A session can go wrong for many reasons, and nothing in Claude Code says which instruction file was at fault.
This skill judges each use of a file: a good use (it did its job, or at least caused no issue) or a bad use (it caused or allowed an issue).
It never edits the file; the user decides.

## Input
- **From the Harness Doctor page:** a JSON document with `file` (name, kind, text at that version), `history` (the up to 3 versions before it: when, how many edits, the diff, and what the triage found in sessions on each: issue lines and how many worked) and `sessions`: each with `issues` counted by type (may be empty), the /insights `note` and `summary`, the `outcome`, `you` (all of the user's messages, with time) and `messages` (both sides of the conversation near the issues and near where the file was used: who, time, text).
- **In chat:** gather the same yourself: the file; the messages from `~/.claude/projects/*/<session_id>.jsonl`; and, where it exists, the /insights score in `~/.claude/usage-data/facets/<session_id>.json` (`friction_counts`, `friction_detail`, `brief_summary`, `outcome`).
  Use only sessions that loaded the skill (or, for CLAUDE.md and the output style, any session after that version).

## Steps
1. **Judge each session for this file:**
   - `worked`: the file was used and did its job (say what went right in one line, and quote in `you` the user's request it served, copied exactly, under 200 characters);
   - `failed`: the file caused or allowed at least one issue (a line in it was wrong, or a needed line was missing);
   - `not involved`: only for CLAUDE.md and output styles, which load in every session: the session never touched what the file covers (say where its issues belong in one line, e.g. "mouse lock: game launch rules in CLAUDE.md").
   A skill only loads when it's used, so every session that loaded it is `worked` or `failed`, never `not involved`: a skill that loaded and caused no issue `worked`.
2. **For a failed session, split its issues.** Only the issues this file caused; each gets its type and one plain line under 12 words.
3. **Find each moment.** Quote the user's words and Claude's words where the issue happened, copied exactly and cut to the key part (under 200 characters each).
   Leave a side out (null) if it said nothing at that point; never invent a quote.
   In each quote, mark the 1–3 words or short phrases that show the issue (the pushback, the wrong claim, the confusion), copied exactly from the quote.
4. **Read the history.** If an issue began after an earlier edit, name that edit and suggest putting back what it removed; if the same issue also happened before, say it's older than this version.
5. **Write the diagnosis:**
   - `why`: two sentences on the pattern across the failed sessions, naming the part of the file at fault (or saying it isn't failing);
   - `try`: two or three edits to the file, each ready to apply: `why` (one sentence), `find` (exact text now in the file to replace or cut, or null to add), `text` (the new text, or null to cut) and `after` (for an add: the exact line it goes after); none if nothing needs changing.
     Copy `find` and `after` exactly from the file text, so the edit can be applied as is.

## Triage mode (the page's "Run harness doctor" button)

A cheap first pass over many sessions at once, from the user's own messages (no file text).
Input: `files` (id, name, kind, one line on what it's for) and `sessions` (each with the ids of the `files` it touched, and `you`: the user's messages, each with its time and `after_claude`, the end of the Claude message it answered).
Where /insights scored the session, it also has `issues` counted by type, `note`, `summary` and `outcome`; use them as extra evidence.
Where it didn't, judge from `you` alone: pushback, corrections and confusion ("no", "i meant", "why did you") show an issue; type each one as `misunderstood_request`, `wrong_approach`, `buggy_code`, `excessive_changes` or a short snake_case name.
For each session, judge each file it touched with the steps above, but skip the diagnosis (every skill it loaded is `worked` or `failed`).
For every verdict and every issue, quote in `you` the user's words for it (the request a worked file served, or the words where the issue showed), copied exactly from `you`, under 200 characters; mark 1–3 words that show an issue in `you_marks`.
Leave out files that weren't involved.
Answer with only this JSON: `{"sessions": [{"session": "<id>", "files": [{"file": "<id>", "verdict": "worked" | "failed", "line": "...", "you": "..." or null, "issues": [{"type": "<type>", "line": "...", "you": "..." or null, "you_marks": ["..."]}]}]}]}`

## Output
- **Page:** only this JSON, no other text:
  `{"why": "...", "try": [{"why": "...", "find": "..." or null, "text": "..." or null, "after": "..." or null}], "sessions": [{"session": "<id>", "verdict": "worked" | "failed" | "not involved", "line": "...", "you": "..." or null, "issues": [{"type": "<type>", "line": "...", "you": "..." or null, "you_marks": ["..."], "claude": "..." or null, "claude_marks": ["..."]}]}]}`
  (`issues` is empty unless the verdict is `failed`.)
- **Chat:** a short answer: how many sessions were good and bad uses, why in two lines, then the edits as a short list. Offer to apply them; don't apply them.
