---
name: fix
description: Fix what Harness Doctor marked as failing - go through each regression, failing rule and uncovered problem, worst first and one at a time, find why the rule, skill or note failed in those sessions, and propose the exact edit; then check why the unused rules never came up and whether to remove them. Use when the user pastes "/harness-doctor:fix", or asks "why does X keep failing", "fix my failing rules", "what's wrong with skill X", "is my CLAUDE.md working", "diagnose X", "which of my rules are failing", "which rules are unused", "should I remove this rule".
allowed-tools: Bash(python3 *)
---

# Fix what the doctor marked, one rule at a time

Harness Doctor judged the user's sessions against every rule and fact they wrote, and marked each rule's moments good or bad.
The marks are a model's judgment, so they can be wrong: the user is the check, here in the conversation.
It changes a file, turns a mark off, or adds a rule for the judge only after the user says yes.

## Steps
1. **Get the report.** Run this, adding `--project <name>` when the user named a project (the word after `/harness-doctor:fix`):
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/app/harness_doctor.py" --report --data "${CLAUDE_PLUGIN_DATA}"
   ```
   It lists, worst first: regressions (a rule that was clean before its last edit), failing rules, then problems nothing written covers.
   Last comes the unused list: the rules with no mark at all, one line per file.
   The report ends with "The judge": the user's rules for the judge, and the marks turned off as wrong since those rules were last edited.
   If it says no sessions are judged, tell the user to open the page (`/harness-doctor:open`) and press the judge button.
2. **Say what's there in one line,** like "1 regression, 4 failing rules, 2 not covered, 30 unused", then start with the first one.
   Take them one at a time; never propose edits for several at once.
3. **Check the marks before believing them.** Read the rule as written, then each bad moment.
   When the one line and the quotes aren't enough, open the session log the report names and read around the quote.
   Tell the user plainly when a mark looks wrong ("this one didn't break the rule, because …"); a rule isn't changed for a wrong mark.
   Ask to turn the wrong mark off, and on the user's yes (one yes can cover several marks) run, once per mark:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/app/harness_doctor.py" --turn-off <session> "<rule id>" --why "<why it is wrong, in one line>" --data "${CLAUDE_PLUGIN_DATA}"
   ```
   `<session>` is the 8 characters the report prints beside the mark, and the rule id is on the rule's "Rule id:" line; for a problem nothing covers, give its name in place of the rule id.
   A mark that is turned off stays on the page, struck through, with the reason; it no longer counts against its rule, and it counts as a bad mark on the judge.
   The same command with `--turn-on <session> "<rule id>"` in place of `--turn-off … --why …` undoes it.
   A problem nothing covers needs 2 sessions, so it leaves the page when turning one off leaves it with fewer.
   These marks are Claude's own, so never turn one off without the yes, and never to make a rule look healthy: only when the session shows the rule wasn't broken.
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
7. **When the judge keeps making the same wrong mark, give the judge a rule.**
   The report's last part, "The judge", lists the marks turned off since the judge's rules were last edited.
   When two or more of them are the same kind of mistake, propose one rule for the judge: a bullet that starts with a bold sentence, saying how to read that kind of moment, in general words.
   On the user's yes, add it to the judge's rules file, whose path the report prints under "The judge", with the Edit or Write tool (make the file when it isn't there), so the change shows on the page as a new version of the judge's rules.
   Only when the user's own rule is clear and the judge misread the moment; when the user's rule is unclear about that moment, reword the rule instead (step 4).
   A rule for the judge never says "don't mark rule X": that would hide real failures.
8. **Last, the unused rules: find why each never came up, and whether it should go.**
   A rule that is never used still costs room in every session and hides the rules around it; but no mark doesn't prove it's dead.
   Take one file at a time, the files Claude always loads first (CLAUDE.md, the output style), and skip a file the user says to leave.
   For each rule, read it as written and pick the one reason that fits, checking it instead of guessing:
   - *Too early to tell*: the rule is newer than most of the judged sessions, or few sessions are judged. Keep it, and say when to look again.
   - *Its moment doesn't happen any more*: search the session logs for the words of its moment; none in weeks of work means the tool, project or habit it was written for is gone. Propose removing it.
   - *Its moment happens, but the judge can't see it*: the logs do show the moment, yet the rule names no moment the judge can recognise (it's vague, or about every reply and nothing in particular). Propose rewording it so it says when it applies, or folding it into the rule it belongs to.
   - *Another rule says the same*: the marks land on that rule. Propose merging the two.
   - *Rare, but costly when missed* (a destructive action, money, something that can't be undone): keep it as it is, however rarely it comes up.
   - *The whole file never loaded* (a skill or a note with "none of its rules came up"): its loading line doesn't match what the user says, or the work it's for has stopped. Fix the loading line or propose removing the file, never rule by rule.
   Show the result per file as a short list, each rule with keep, reword, merge or remove and the reason in a few words, and wait for the user's yes before removing anything.

## After
The edit shows on the page as a new version of that rule, with its marks starting empty.
The judge has a card too ("judge rules"): the marks turned off as wrong are its bad marks, and they start again at each edit of its rules.
This skill is on the page as well, judged like any other skill, so its own failing steps come up in the report.
Later sessions, once judged, show whether it helped; if the rule turns red again, the page says "Regression".

## Output
Short. For each item: what failed and why in two lines, then the edit. No recap at the end.
