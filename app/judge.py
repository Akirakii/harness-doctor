"""Harness Doctor's judge: one narrow question per rule and session, asked of Claude.

Did the rule's moment come up in the session, and was it followed? The answers (pass, fail, unknown) are the marks the
page shows. It calls `claude -p` with your own login, only when you press the button, and keeps the marks in the data
folder for 30 days. Nothing judged here is fed back into a later run, apart from the names of the uncovered problems
found so far, so the same problem keeps one name. The sessions of one press are judged at the same time, so a last
small run gives the problems they found the same name where they are the same problem.
"""
import json
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import reader as rd
import rules as rl

BATCH = 10            # sessions judged per press, newest first
PARALLEL = 10         # how many of them are judged at the same time
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


YOURS = """

## The user's rules for you
The user wrote these after finding marks of yours that were wrong. Each says how to read one kind of moment. Where one
of them applies, follow it; everywhere else the instructions above hold, and a rule is still judged as it is written.

"""


def user_rules():
    """The user's own rules for the judge (judge-rules.md in the data folder), as text; "" when there are none."""
    text = (rd.read(rd.judge_rules_path()) or "").strip()
    return text if any(rl.ITEM.match(line) for line in text.splitlines()) else ""


def instructions():
    """What every judge run is told: the built-in instructions, then the user's rules when there are any."""
    mine = user_rules()
    return INSTRUCTIONS + (YOURS + mine if mine else "")


def off_path():
    return rd.data_path("turned-off.json")


def load_off():
    """The marks turned off as wrong: {"<session id> <rule id or gap:topic>": {"at": when that session was judged,
    "quote": Claude's words the mark quoted, "why", "t": when it was turned off}}. An entry holds for the judging it
    was made on, and for a later judging of the same session that quotes the same words (see is_off)."""
    off = rd.load_json(off_path(), {})
    return off if isinstance(off, dict) else {}


def words_of(m):
    """The words a mark is known by when its session is judged again: Claude's, else the user's, else its one line."""
    return m.get("claude") or m.get("you") or m.get("line") or None


def is_off(off, sid, key, entry, quote=None):
    """Is this mark turned off? Yes when the session wasn't judged again since, or was and the mark quotes the same words."""
    o = off.get(sid + " " + key)
    if o and (o.get("at") == entry.get("at") or (quote and o.get("quote") == quote)):
        return o
    return None


def find_mark(st, files, session, rule):
    """The one bad mark a turn-off names: `session` is the start of a session id, `rule` a rule's id or its title, or
    the name of a problem nothing covers. -> (session id, the mark's key, the saved entry, the words it quoted, None)
    or (None, None, None, None, why it wasn't found)"""
    sids = [k for k in st["sessions"] if k.startswith(session)]
    if len(sids) != 1:
        return None, None, None, None, f"{'No' if not sids else 'More than one'} judged session starts with {session!r}."
    entry = st["sessions"][sids[0]]
    low = rule.strip().lower()
    ids = {r["id"] for f in files for r in f["rules"] if r["id"].lower() == low or r["title"].lower().rstrip(".") == low.rstrip(".")}
    hits = [m for m in entry.get("marks", []) if m["rule"] in ids and m["verdict"] == "fail"]
    if not hits:
        gap = [u for u in entry.get("uncovered", []) if u["topic"].lower() == low]
        if gap:
            return sids[0], "gap:" + gap[0]["topic"], entry, gap[0]["topic"], None  # a problem is known by its name
        return None, None, None, None, f"That session has no bad mark on a rule, and no uncovered problem, called {rule!r}."
    if len({m["rule"] for m in hits}) != 1:
        return None, None, None, None, f"More than one rule is called {rule!r}; use the rule id from the report."
    return sids[0], hits[0]["rule"], entry, words_of(hits[0]), None


def turn_off(st, files, session, rule, why, restore=False):
    """Turn one bad mark off as wrong, with the reason, or turn it back on. -> one line saying what happened."""
    off, low = load_off(), rule.strip().lower()
    if restore:  # a turn-off whose mark is gone (the session was judged again) can still be taken out
        old = [k for k in off if k.startswith(session) and k.split(" ", 1)[1].lower() in (low, "gap:" + low)]
        if len(old) == 1:
            del off[old[0]]
            rd.save_json(off_path(), off)
            return f"Turned back on: {old[0].split(' ', 1)[1]} in session {old[0][:8]}."
    sid, rid, entry, quote, err = find_mark(st, files, session, rule)
    if err:
        return err
    key = sid + " " + rid
    if restore:
        if key not in off:
            return "That mark wasn't turned off."
        del off[key]
        rd.save_json(off_path(), off)
        return f"Turned back on: {rid} in session {sid[:8]}."
    if not (why or "").strip():
        return "Give the reason with --why: it shows on the page beside the mark."
    off[key] = {"at": entry["at"], "quote": quote, "why": rd.one_line(why, 300), "t": rd.now_iso()}
    rd.save_json(off_path(), off)
    return f"Turned off: {rid} in session {sid[:8]}. The page shows it struck through, with the reason."


def load_store():
    """The saved marks: {"sessions": {session id: {"at", "n", "cost", "loaded", "marks", "uncovered"}}}. Marks older than
    the window are dropped."""
    st = rd.load_json(store_path(), {})
    cutoff = rd.iso_ago(days=rd.WINDOW_DAYS)
    st["sessions"] = {k: v for k, v in st.get("sessions", {}).items() if v.get("t", v.get("at", "")) >= cutoff}
    return st


# The user settings that say how Claude Code logs in and which server it talks to (a company gateway, Bedrock, Vertex,
# a key helper). The judge skips the user's settings to leave out their hooks and rules, so these are handed over by name.
LOGIN_KEYS = ("env", "apiKeyHelper", "awsAuthRefresh", "awsCredentialExport", "forceLoginMethod", "forceLoginOrgUUID")
WHY = {"text": ""}  # why the last run got no answer, in Claude Code's own words


def run_settings():
    """The settings for a judge run: no auto memory, plus the login settings from the user's own settings files."""
    out = {"autoMemoryEnabled": False}
    for name in ("settings.json", "settings.local.json"):
        user = rd.load_json(os.path.join(rd.CLAUDE, name), {})
        for k in LOGIN_KEYS:
            if k not in user:
                continue
            if k == "env" and isinstance(user[k], dict):
                out["env"] = {**out.get("env", {}), **user[k]}
            else:
                out[k] = user[k]
    return json.dumps(out)


CHECK = "Check the session in the input against the rules, following the instructions in it."


def ask(prompt, model=None, timeout=900, task=CHECK):
    """One headless Claude run: no tools, no hooks or user rules, nothing saved; only the user's login settings.
    -> (the JSON object it answered, or None when it didn't answer with one; the cost in dollars)"""
    model = model or os.environ.get("HARNESS_DOCTOR_MODEL") or MODEL
    try:
        r = subprocess.run(["claude", "-p", task,
                            "--model", model, "--tools", "", "--no-session-persistence", "--setting-sources", "project",
                            "--settings", run_settings(), "--output-format", "json"],
                           input=prompt, capture_output=True, text=True, timeout=timeout, cwd=HERE)
    except subprocess.TimeoutExpired:  # one slow run doesn't stop the others
        WHY["text"] = f"claude gave no answer in {timeout // 60} minutes"
        return None, 0.0
    try:
        j = json.loads(r.stdout)
    except ValueError:
        WHY["text"] = rd.one_line((r.stderr or r.stdout or f"claude exited with code {r.returncode}").strip(), 200)
        return None, 0.0
    if not isinstance(j, dict):
        WHY["text"] = "claude answered in a format Harness Doctor doesn't know"
        return None, 0.0
    if j.get("is_error"):  # not logged in, no access to the model, the server can't be reached
        WHY["text"] = rd.one_line(str(j.get("result") or "claude reported an error"), 200)
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
        prompt = (instructions() + "\n\n## The rules\n" + json.dumps({"rules": part, "known_topics": topics}, ensure_ascii=False) +
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


SAME = """Below are short names of problems found in Claude Code sessions. `known` are the names already in use, `new` are
names that were just given, each with one line saying what went wrong.

For each new name that is the same problem as a known name, or as another new name, say which name it takes. The same
problem means the same thing went wrong, not only the same subject. When you aren't sure, leave it out. Take a known
name when one fits; between two new names, take the clearer one.

Answer with only this JSON, copying the names exactly:
{"same": [{"new": "<a new name>", "use": "<the name it takes>"}]}"""


def renames(same, new, known):
    """Clean the judge's answer -> {new name: the name it takes}. Only new names are renamed, only to a name that
    exists, and a chain (a takes b, b takes c) ends at its last name."""
    to = {}
    for x in same if isinstance(same, list) else []:
        if isinstance(x, dict) and x.get("new") in new and x.get("use") in new | known and x["new"] != x["use"]:
            to.setdefault(x["new"], x["use"])
    out = {}
    for a in to:
        b, seen = to[a], {a}
        while b in to and b not in seen:
            seen.add(b)
            b = to[b]
        if b not in seen:  # a circle of names is left alone
            out[a] = b
    return out


def rename(entries, to):
    """Give the uncovered problems of these sessions their new names, and keep one per name in a session."""
    for e in entries:
        kept = {}
        for u in e.get("uncovered", []):
            u["topic"] = to.get(u["topic"], u["topic"])
            kept.setdefault(u["topic"], u)
        e["uncovered"] = list(kept.values())


def same_names(entries, before):
    """The sessions of one press were judged at the same time, so none saw the names the others gave. One small run
    over the names alone finds the ones that are the same problem. `before`: the names known before this press."""
    known = {t["topic"] for t in before}
    new = {}
    for e in entries:
        for u in e.get("uncovered", []):
            if u["topic"] not in known:
                new.setdefault(u["topic"], u["line"])
    if not new or len(new) + len(known) < 2:
        return
    ans, _ = ask(SAME + "\n\n" + json.dumps({"known": before, "new": [{"topic": k, "line": v} for k, v in new.items()]}, ensure_ascii=False),
                 timeout=180, task="Find the names in the input that are the same problem, following the instructions in it.")
    if ans:
        rename(entries, renames(ans.get("same"), set(new), known))


def run(scan):
    """The page's button: judge the next batch of sessions, all at the same time, saving each one as it comes back, so
    a stop loses nothing. The ones that got no answer are tried once more, one at a time (a rate limit hits them all
    together), and last the problems the batch found get the same name where they are the same problem."""
    try:
        JOB.update(state="running", message="Finding sessions to judge…", pct=None)
        WHY["text"] = ""
        sc, st = scan(), load_store()
        todo = pending(sc, st)[:BATCH]
        before, got, again = known_topics(st), [], []

        def keep(s, entry):
            if entry is None:
                return False
            st["sessions"][s["id"]] = entry
            got.append(entry)
            rd.save_json(store_path(), st)
            return True

        if todo:
            JOB.update(message=f"Judging {len(todo)} {'session' if len(todo) == 1 else 'sessions'} at the same time…", pct=0)
            with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
                runs = {pool.submit(judge_session, sc, s, before): s for s in todo}
                for i, f in enumerate(as_completed(runs)):  # saved here, in this one thread
                    if not keep(runs[f], f.result()):
                        again.append(runs[f])
                    JOB.update(message=f"Judged {i + 1} of {len(todo)} sessions…", pct=round(100 * (i + 1) / (len(todo) + 1)))
        failed = 0
        for i, s in enumerate(again):
            JOB.update(message=f"Trying again the {len(again)} that got no answer: {i + 1} of {len(again)}…")
            failed += not keep(s, judge_session(sc, s, known_topics(st)))
        done = len(got)
        if got:
            JOB.update(message="Giving the same problem the same name…")
            same_names(got, before)
            rd.save_json(store_path(), st)
        left = len(pending(sc, st))
        msg = f"Judged {done} {'session' if done == 1 else 'sessions'}." if todo else "Nothing new to judge."
        if failed:
            msg += f" {failed} got no answer from Claude and will be tried again."
            if WHY["text"]:
                msg += f" Claude Code said: {WHY['text']}"
        if left:
            msg += f" {left} more to go."
        JOB.update(state="done", pct=100, message=msg)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as err:
        JOB.update(state="error", message=f"Harness doctor stopped: {err}")
    finally:
        JOB["at"] = time.time()
