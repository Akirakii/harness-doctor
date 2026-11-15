# How others judge and fix instructions from real sessions

Researched 2026-11-16 for Harness Doctor 2.0.
Tags: [read] = I opened the page; [source] = from a search summary of the page, not opened; [opinion] = Claude's own.

The problem in general terms: given logs of a model's past conversations and the written instructions and notes it had, find which instruction or note failed in each bad moment, and prove an edit fixes it without breaking what worked.

## Patterns

### 1. Read the logs by hand first
- A person reads traces and writes free notes, groups the notes into named failure types, then counts each type. [read] ([Husain & Shankar FAQ](https://hamel.dev/blog/posts/evals-faq/why-is-error-analysis-so-important-in-llm-evals-and-how-is-it-performed.html))
- At least 30 traces by the person before any agent helps; about 100 in the working pool; stop when new traces show no new failure type. [read]
- Note the first failure in a trace, since later ones often follow from it. [read]
- A model can help group and search, and the person accepts or rejects each suggestion. [read]
- Fails when: nobody has the time to read.

### 2. A pass/fail judge, checked against a person's labels
- One question per check, answered pass or fail with a written reason; no 1–5 scores. [read] ([Husain, LLM judge guide](https://hamel.dev/blog/posts/llm-judge/))
- The person's labelled examples go into the judge's prompt as examples (10–20% of them); the rest are held back to measure the judge. [read]
- Measure two numbers apart: of the real fails, how many the judge catches; of the real passes, how many it leaves alone. Plain agreement misleads when fails are rare. [read]
- Under 60 labelled examples the measurement is too loose to trust; aim for 30–50 of each label. [read]
- Re-check after any change of model or prompt. [read]
- People change their own criteria while labelling ("criteria drift"), so the criteria can't be fixed up front. [source] ([Shankar et al., UIST 2024](https://arxiv.org/abs/2404.12272))
- Give the judge an "unknown" answer so it doesn't invent a verdict. [read] ([Anthropic, Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents))
- Fails when: nobody labels, or the labels are too few to measure.

### 3. Every failure becomes a saved case that is replayed
- Loop: save the failed trace, label its failure type, store it as a case, write a check for it, run the check on every change. [read] ([Braintrust](https://www.braintrust.dev/articles/turn-llm-production-failures-into-regression-tests))
- Keep one case per group of similar failures, and add cases that went well so a fix doesn't break them. [read]
- 20–50 cases from real failures is enough to start. [read] (Anthropic)
- Test both sides: where the behaviour should happen and where it shouldn't. [read] (Anthropic)
- Grade what was produced, not the exact steps taken. [read] (Anthropic)
- Claude Code ships this as `claude plugin eval` (checked that the command exists on this machine; not tried on `~/.claude` files).
- Fails when: every failure is added as its own case (the suite overfits), or the judge on the cases is never re-checked.

### 4. A counter on every line of the instructions
- ACE keeps the instructions as a list of items, each with an id and a "helped" and a "harmed" counter; a second model updates the counters after each run and a third adds, edits or removes single items, never rewriting the whole text. [read: abstract] ([ACE, arXiv 2510.04618](https://arxiv.org/abs/2510.04618)) [source: counters and the three roles] ([repo](https://github.com/ace-agent/ace))
- It names two failures of rewriting: detail gets dropped for short summaries, and repeated rewrites wear the text down. [read]
- Reported gains: +10.6% on agent benchmarks. [read]
- Fails when: a wrong lesson gets written in ("playbook poisoning"); in the open code the counters don't remove a harmful item by themselves. [source] ([review](https://zby.github.io/commonplace/agent-memory-systems/reviews/ace/))

### 5. Asking a model which step caused the failure
- The Who&When paper tested models at naming the agent and the step that caused a failed run: best method 53.5% on the agent, 14.2% on the step. [read: abstract] ([arXiv 2505.00212](https://arxiv.org/abs/2505.00212))
- Later methods reach 29–46% on the step. [source] ([FALAT](https://arxiv.org/pdf/2606.00765))
- Different task from ours (many agents, not one instruction file), so the numbers are a warning, not a measurement of our case. [opinion]

### 6. Telling a missing fact from a broken behaviour
- In retrieval systems: give the model the right context by hand and run again. If the answer turns right, the fact was missing; if it stays wrong, the behaviour is at fault. [source] ([Latitude](https://www.latitude.so/blog/debug-rag-pipeline-wrong-answers), [Kili](https://kili-technology.com/blog/rag-evaluation-guide-measuring-retrieval-and-generation-as-separate-problems))

## Tools that already exist
- `claude-doctor` (millionco): reads Claude Code transcripts, flags fixed anti-patterns (edit thrashing, error loops) and user frustration by word lists, and writes suggested rules. No per-rule marks or versions in the listings. [source, repo not opened] ([listing](https://www.sourcepulse.org/projects/28128163))
- `/insights` (built into Claude Code): per-session friction scores, which Harness Doctor already reads.
- Community "reflection" commands: point Claude at the transcripts and ask which rules were ignored. [source]

## What this changes in the 2.0 design [opinion]
- Ask the judge, for each rule, "did its moment come up, and was it followed?" (pass, fail, didn't come up, unknown), not "which rule is to blame for this failure". Pattern 5 says blame is the weak spot; pattern 2 says a narrow pass/fail question is the strong one.
- "This mark is wrong" is the labelling in pattern 2, so it must be one click, and the page should show the judge's two numbers against the user's labels.
- Each bad use becomes a saved case (pattern 3), with a few good uses beside it, replayed on an edit.
- Harness or knowledge is settled by pattern 6: replay the moment with the note given.

## Decided by the user (2026-11-16)
- The doctor marks verdicts, the page copies a prompt, and the fixing happens in a Claude Code conversation.
- Nothing feeds back: the conversation writes no verdicts into the doctor's data, and the judge gets no examples from past marks. The user wants no circular dependency.
- So "This mark is wrong" is not on the page, and the judge is not measured against labels. Its instructions change only when someone edits them by hand.
- Still open from the list above: the narrow per-rule question with "unknown", and saved cases replayed on an edit.
