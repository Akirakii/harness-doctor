---
name: fix
description: Fix what Harness Doctor marked as failing - go through each regression, failing rule and uncovered problem, worst first and one at a time, find why the rule, skill or note failed in those sessions, and propose the exact edit. Use when the user pastes "/harness-doctor:fix", or asks "why does X keep failing", "fix my failing rules", "what's wrong with skill X", "is my CLAUDE.md working", "diagnose X", "which of my rules are failing".
allowed-tools: Bash(python3 *)
---

# Fix what the doctor marked, one rule at a time

Harness Doctor judged the user's sessions against every rule and fact they wrote, and marked each rule's moments good or bad.
The marks are a model's judgment, so they can be wrong: the user is the check, here in the conversation.
This skill never writes to the doctor's own data, and it changes a file only after the user says yes.

## Steps
1. **Get the report.** Run this, adding `--project <name>` when the user named a project (the word after `/harness-doctor:fix`):
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/app/harness_doctor.py" --report --data "${CLAUDE_PLUGIN_DATA}"
   ```
   It lists, worst first: regressions (a rule that was clean before its last edit), failing rules, then problems nothing written covers.
   If it says no sessions are judged, tell the user to open the page (`/harness-doctor:open`) and press the judge button.
2. **Say what's there in one line,** like "1 regression, 4 failing rules, 2 not covered", then start with the first one.
   Take them one at a time; never propose edits for several at once.
3. **Check the marks before believing them.** Read the rule as written, then each bad moment.
   When the one line and the quotes aren't enough, open the session log the report names and read around the quote.
   Tell the user plainly when a mark looks wrong ("this one didn't break the rule, because …") and drop it; a rule isn't changed for a wrong mark.
4. **Find why it failed,** from the moments that are right:
   - *Didn't load* (a skill's description, a note's "read when"): the user's words in those moments aren't in the loading line. The fix goes in that line, using the words the user actually said.
   - *Bad use of a rule*: it doesn't name this case, it's vague about when it applies, another rule pulls the other way (name that rule), or it's buried among others.
   - *Regression*: read the last edit the report shows. Say what the edit removed or changed that the bad moments needed, and whether putting it back fixes it.
   - *A fact in a note*: the fact is wrong or out of date. The fix is the right fact, checked, not a new instruction.
5. **Propose one edit, ready to apply:** the exact text to replace and the new text, with one sentence on why.
   Write it as the general rule the failure breaks, with no project or topic words from the sessions; one of the moments can follow as a short example.
   Wait for the user's yes, apply it, and go to the next item.
   If the user has their own skill for saving rules or knowledge, use it to place the edit.
6. **For a problem nothing covers,** decide with the user where the fix belongs: an instruction goes to CLAUDE.md, the output style or a skill; a fact goes to a note; something that must happen every time without thinking goes to a hook.

## After
The edit shows on the page as a new version of that rule, with its marks starting empty.
Later sessions, once judged, show whether it helped; if the rule turns red again, the page says "Regression".

## Output
Short. For each item: what failed and why in two lines, then the edit. No recap at the end.
