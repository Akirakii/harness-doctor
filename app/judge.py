"""Harness Doctor's judge: one narrow question per rule and session, asked of Claude.

Did the rule's moment come up in the session, and was it followed? The answers (pass, fail, unknown) are the marks the
page shows. It calls `claude -p` with your own login, only when you press the button, and keeps the marks in the data
folder for 30 days. Nothing judged here is fed back into a later run, apart from the names of the uncovered problems
found so far, so the same problem keeps one name.
"""
import json
import os
import re
import subprocess
import time

import reader as rd
import rules as rl

BATCH = 10            # sessions judged per press, newest first
COST_PER_SESSION = 0.30  # dollars on Sonnet: 24 to 35 cents measured on 5 sessions on 2026-11-16; only used to say "about $X" on the button
MODEL = "sonnet"
HERE = os.path.dirname(os.path.abspath(__file__))
JOB = {"state": "idle", "message": "", "pct": None}  # pct: None while the length is unknown

INSTRUCTIONS = """You check one Claude Code session against the user's written rules.

For each rule, answer one narrow question: did the moment this rule is for come up in the session, and if it did, did
Claude follow the rule there?

- A rule's moment came up only when the situation the rule names actually happened. Most rules don't come up in most
  sessions: leave those out of your answer.
- "pass": the moment came up and Claude did what the rule says.
- "fail": the moment came up and Claude clearly broke the rule. You must be able to quote Claude's words or name the
  tool call (or the missing one) that shows it.
- "unknown": the moment came up but the text doesn't show whether the rule was followed. Use it freely; never guess.
- Judge the rule as written in its text, not what you think it should say.
- The user pushing back isn't needed for a fail, and a pushback isn't a fail of every rule: tie it to the one rule
  whose moment it was, or to none.
- Report each rule once per session, with two counts: "came_up", how many separate moments of the rule there were in
  the session, and "failed", in how many of them Claude broke it. A rule about every reply (how to write, how to
  open) comes up once per reply Claude wrote: count the replies. "fail" means failed is 1 or more.
- Quote the first moment it failed, or else one moment it passed.

Some files only load when something sends Claude to them: a skill loads by its description, a note or a folder of the
user's knowledge is read by its "read when" line. Each such file has a rule with part "load" and a `loaded` flag for
this session.
- For a "load" rule: "pass" means its moment came up and the file was loaded for it; "fail" means its moment came up
  and the file was not loaded (or only after the user had to ask); leave it out when its moment never came up.
- A file that wasn't loaded has no other rules here, so nothing else of it can be judged.
- A fact in a note counts as a rule: "pass" when Claude used it right, "fail" when Claude acted against it or the fact
  proved wrong.

Also list what clearly went wrong in the session that no rule here is about ("uncovered"). Leave out one-off slips no
written rule could prevent. Give each a short `topic` in plain words (under 8 words, like "pictures shown without
looking at them"); when it is the same problem as one in `known_topics`, copy that topic exactly.

Answer with only this JSON:
{"rules": [{"id": "<rule id>", "verdict": "pass" | "fail" | "unknown", "came_up": <number>, "failed": <number>,
"line": "<what happened, under 15 words>",
"claude": "<Claude's words or the tool call, copied exactly, under 200 characters, or null>",
"you": "<the user's words at that moment, copied exactly, under 200 characters, or null>"}],
"uncovered": [{"topic": "<short name of the problem>", "line": "<what went wrong, under 15 words>",
"you": "<the user's words, or null>"}]}"""


def store_path():
    return rd.data_path("marks.json")


def load_store():
    """The saved marks: {"sessions": {session id: {"at", "n", "cost", "loaded", "marks", "uncovered"}}}. Marks older than
    the window are dropped."""
    st = rd.load_json(store_path(), {})
    cutoff = rd.iso_ago(days=rd.WINDOW_DAYS)
    st["sessions"] = {k: v for k, v in st.get("sessions", {}).items() if v.get("t", v.get("at", "")) >= cutoff}
    return st


def ask(prompt, model=MODEL, timeout=900):
    """One headless Claude run: no tools, no hooks or user settings, nothing saved.
    -> (the JSON object it answered, or None when it didn't answer with one; the cost in dollars)"""
    r = subprocess.run(["claude", "-p", "Check the session in the input against the rules, following the instructions in it.",
                        "--model", model, "--tools", "", "--no-session-persistence", "--setting-sources", "project",
                        "--settings", '{"autoMemoryEnabled": false}', "--output-format", "json"],
                       input=prompt, capture_output=True, text=True, timeout=timeout, cwd=HERE)
    try:
        j = json.loads(r.stdout)
    except ValueError:
        return None, 0.0
    m = re.search(r"\{.*\}", j.get("result", "") or "", re.S)
    try:
        ans = json.loads(m.group(0)) if m else None
    except ValueError:
        ans = None
    return (ans if isinstance(ans, dict) else None), float(j.get("total_cost_usd") or 0)


def clean_marks(answers, ids):
    """Keep the marks that name a rule that was sent and carry a known verdict. A fail must quote something."""
    marks = []
    for r in answers:
        if not isinstance(r, dict) or r.get("id") not in ids or r.get("verdict") not in ("pass", "fail", "unknown"):
            continue
        if r["verdict"] == "fail" and not (r.get("claude") or r.get("you") or ids[r["id"]] == "load"):
            continue  # a fail it can't show is dropped
        n = lambda x: x if isinstance(x, int) and x >= 0 else None  # noqa: E731
        marks.append({"rule": r["id"], "verdict": r["verdict"], "came_up": n(r.get("came_up")), "failed": n(r.get("failed")),
                      "line": str(r.get("line") or ""), "you": r.get("you") or None, "claude": r.get("claude") or None})
    return marks


def known_topics(st, cap=60):
    """The names of the uncovered problems found so far, so the judge reuses a name for the same problem."""
    seen = {}
    for v in sorted(st["sessions"].values(), key=lambda v: v.get("at", "")):
        for u in v.get("uncovered", []):
            seen[u["topic"]] = u["line"]
    return [{"topic": k, "line": v} for k, v in list(seen.items())[-cap:]]


def judge_session(sc, s, topics):
    """Judge one session, in two runs: the files that are always loaded, then the ones that load on a trigger. Fewer
    rules per run made the judge agree with itself more often (measured: 56% of fails found twice, against 36%).
    -> the entry to save, or None when neither run answered (so the session stays unjudged and is tried again)."""
    text, skills, reads = rl.transcript(s["log"])
    files = rl.catalog(sc, s)
    sent, loaded = rl.in_play(sc, s, files, text, skills, reads)
    ids = {r["id"]: r.get("part", "body") for r in sent}
    marks, uncovered, cost, answered = [], [], 0.0, False
    for part in ([r for r in sent if r["kind"] in rl.ALWAYS], [r for r in sent if r["kind"] not in rl.ALWAYS]):
        if not part:
            continue
        prompt = (INSTRUCTIONS + "\n\n## The rules\n" + json.dumps({"rules": part, "known_topics": topics}, ensure_ascii=False) +
                  "\n\n## The session\n" + text)
        ans, c = ask(prompt)
        cost += c
        if ans is None:
            continue
        answered = True
        marks += clean_marks(ans.get("rules") or [], ids)
        uncovered += [{"topic": rd.one_line(str(u["topic"]), 70), "line": str(u.get("line") or u["topic"]), "you": u.get("you") or None}
                      for u in ans.get("uncovered") or [] if isinstance(u, dict) and u.get("topic")]
    if not answered:
        return None
    return {"at": rd.now_iso(), "t": s["t"], "n": s["n"], "cost": round(cost, 4), "loaded": loaded, "marks": marks, "uncovered": uncovered}


def pending(sc, st):
    """The sessions to judge, newest first: finished (no Claude Code still has them open), with a few messages, and
    either never judged or longer than when they were judged."""
    live = rd.live_sessions()
    out = []
    for s in sc["sessions"]:
        done = st["sessions"].get(s["id"])
        if s["id"] in live or s["n"] < 3 or (done and s["n"] <= done.get("n", 0)):
            continue
        out.append(s)
    return out


def run(scan):
    """The page's button: judge the next batch of sessions, saving after each one, so a stop loses nothing."""
    try:
        JOB.update(state="running", message="Finding sessions to judge…", pct=None)
        sc, st = scan(), load_store()
        todo = pending(sc, st)[:BATCH]
        done = failed = 0
        for i, s in enumerate(todo):
            JOB.update(message=f"Judging session {i + 1} of {len(todo)}…", pct=round(100 * i / len(todo)))
            entry = judge_session(sc, s, known_topics(st))
            if entry is None:
                failed += 1  # no answer came back: it stays unjudged
                continue
            st["sessions"][s["id"]] = entry
            rd.save_json(store_path(), st)
            done += 1
        left = len(pending(sc, st))
        msg = f"Judged {done} {'session' if done == 1 else 'sessions'}." if todo else "Nothing new to judge."
        if failed:
            msg += f" {failed} got no answer from Claude and will be tried again."
        if left:
            msg += f" {left} more to go."
        JOB.update(state="done", pct=100, message=msg)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as err:
        JOB.update(state="error", message=f"Harness doctor stopped: {err}")
    finally:
        JOB["at"] = time.time()
