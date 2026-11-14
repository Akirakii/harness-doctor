#!/usr/bin/env python3
"""Harness Doctor: which of your Claude Code instructions help, and which cause trouble.

One card per CLAUDE.md, skill and output style. Each session that used a file is judged a good use (✓) or a bad
use (✕) by Claude itself, from your own messages. Working out a fix takes a conversation, so the page only copies a
prompt to paste into Claude Code.

Run:   python3 harness_doctor.py                 starts the page on http://127.0.0.1:8737 and opens your browser
       python3 harness_doctor.py --background    the same, detached; if it's already running, just opens the page
       python3 harness_doctor.py --stop          stops a background server
Flags: --port N, --data DIR (where it keeps its results), --no-open, --build (print a summary, no server),
       --dev (restart itself when this file changes)

It reads what Claude Code already keeps: session logs (~/.claude/projects), file-history snapshots
(~/.claude/file-history) and, when present, /insights scores (~/.claude/usage-data/facets). Those formats aren't
documented, so a Claude Code update can break it. Its own results go to the data folder and drop off after 30 days.
"""
import argparse
import bisect
import datetime
import difflib
import glob
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOME = os.path.expanduser("~")
CLAUDE = os.path.join(HOME, ".claude")
HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("HARNESS_DOCTOR_PORT", "8737"))
# Where results live. The plugin passes ${CLAUDE_PLUGIN_DATA}; run by hand it defaults to ~/.local/share.
DATA = os.environ.get("HARNESS_DOCTOR_DATA") or os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.join(HOME, ".local", "share", "harness-doctor")


def data_path(name):
    os.makedirs(DATA, exist_ok=True)
    return os.path.join(DATA, name)
WINDOW_DAYS = 30
TS = re.compile(r'"timestamp"\s*:\s*"([^"]+)"')
CWD = re.compile(r'"cwd"\s*:\s*"([^"]+)"')
REDIRECT = re.compile(r"(?<![0-9&])(>>?)\s*([^\s;&|<>]+)")
NOISE = ("<", "Caveat:", "[Request interrupted", "Base directory for this skill", "This session is being continued")


def one_line(text, n=220):
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def row_of(path):
    """(scope, kind, name) when the path is a CLAUDE.md, a SKILL.md or an output style, else None.
    scope is "global" for ~/.claude, else the folder that holds the project's CLAUDE.md / .claude."""
    if not path or not path.startswith("/") or path.startswith("/tmp/"):
        return None
    g = CLAUDE + "/"
    if path == g + "CLAUDE.md":
        return "global", "rules", "CLAUDE.md"
    m = re.fullmatch(re.escape(g) + r"skills/([^/]+)/SKILL\.md", path)
    if m:
        return "global", "skill", m.group(1)
    m = re.fullmatch(re.escape(g) + r"output-styles/([^/]+)\.md", path)
    if m:
        return "global", "output style", m.group(1)
    if path.startswith(g):
        return None
    m = re.fullmatch(r"(.+)/\.claude/skills/([^/]+)/SKILL\.md", path)
    if m:
        return m.group(1), "skill", m.group(2)
    m = re.fullmatch(r"(.+)/\.claude/output-styles/([^/]+)\.md", path)
    if m:
        return m.group(1), "output style", m.group(2)
    m = re.fullmatch(r"(.+)/CLAUDE\.md", path)
    if m and "/." not in m.group(1):
        return m.group(1), "rules", "CLAUDE.md"
    return None


def resolve(token, cwd):
    token = token.strip("'\"").replace("$HOME", HOME)
    if token.startswith("~"):
        token = HOME + token[1:]
    if not token.startswith("/"):
        if not cwd:
            return None
        token = os.path.join(cwd, token)
    return os.path.normpath(token)


def shell_targets(cmd, cwd):
    """Row files a shell command writes to (a rough guess: cd, > and >>, mv/cp/rm/touch/tee/ln, sed -i).
    Heredoc bodies and scripts run by python are not followed."""
    out = []
    for seg in re.split(r"&&|\|\||;|\n|\|", cmd.split("<<", 1)[0]):
        seg = seg.strip()
        try:
            toks = shlex.split(seg)
        except ValueError:
            toks = seg.split()
        if not toks:
            continue
        if toks[0] == "cd" and len(toks) > 1:
            cwd = resolve(toks[1], cwd)
            continue
        redirects = REDIRECT.findall(seg)
        whole = {t for op, t in redirects if op == ">"}  # "> file" writes the whole file
        targets = [t for _, t in redirects]
        args = [t for t in toks[1:] if not t.startswith("-")]
        if toks[0] in ("mv", "rm", "touch", "tee", "ln"):
            targets += args
        elif toks[0] == "cp" and args:
            targets.append(args[-1])
        elif toks[0] == "sed" and any(t.startswith("-i") for t in toks):
            targets += args[1:]
        for t in targets:
            p = resolve(t, cwd)
            if p and row_of(p):
                out.append((p, one_line(seg), t in whole))
    return out


HEREDOC = re.compile(r"""cat\s+(?:>\s*([^\s<>;&|]+)\s*<<(-?)\s*(['"]?)(\w+)\3|<<(-?)\s*(['"]?)(\w+)\6\s*>\s*([^\s<>;&|]+))[^\n]*\n""")


def cwd_after(text, cwd):
    """The folder after the cd commands in a piece of shell."""
    for seg in re.split(r"&&|\|\||;|\n|\|", text):
        toks = seg.strip().split()
        if len(toks) > 1 and toks[0] == "cd":
            cwd = resolve(toks[1], cwd)
    return cwd


def heredoc_writes(cmd, cwd):
    """Row files written whole with cat > file <<'EOF' … EOF, with the text written: [(path, text)].
    An unquoted EOF only counts when its text has nothing the shell would expand."""
    out, pos, prefix = [], 0, ""
    for m in HEREDOC.finditer(cmd):
        if m.start() < pos:
            continue  # inside an earlier heredoc's text
        if m.group(1):
            target, dash, quote, word = m.group(1), m.group(2), m.group(3), m.group(4)
        else:
            target, dash, quote, word = m.group(8), m.group(5), m.group(6), m.group(7)
        end = re.compile(r"^" + (r"\t*" if dash else "") + re.escape(word) + r"[ \t]*$", re.M).search(cmd, m.end())
        if not end:
            continue
        body = cmd[m.end():end.start()]
        prefix += cmd[pos:m.start()]
        pos = end.end()
        if dash:
            body = re.sub(r"^\t+", "", body, flags=re.M)
        p = resolve(target, cwd_after(prefix, cwd))
        if p and row_of(p) and (quote or not re.search(r"[$`\\]", body)):
            out.append((p, body))
    return out


def user_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def parse_log(path):
    """One session log -> (session or None, harness events, file-history snapshots)."""
    sid = os.path.basename(path)[:-6]
    start = end = cwd = title = None
    n_user = 0
    events, snaps, pending, uses, turns = [], [], {}, [], []
    for line in open(path, errors="ignore"):
        m = TS.search(line)
        if m:
            start = start or m.group(1)
            end = m.group(1)
        if cwd is None:
            c = CWD.search(line)
            if c:
                cwd = c.group(1)
        snap = '"file-history-snapshot"' in line
        if not (snap or '"type":"user"' in line or '"tool_use"' in line):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if snap:
            for p, b in ((d.get("snapshot") or {}).get("trackedFileBackups") or {}).items():
                if row_of(p) and b.get("backupTime"):
                    snaps.append({"path": p, "t": b["backupTime"], "file": b.get("backupFileName"), "session": sid})
            continue
        content = (d.get("message") or {}).get("content")
        if d.get("type") == "user":
            if isinstance(content, list):  # drop edits whose tool call failed
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in pending:
                        for e in pending[b["tool_use_id"]]:
                            e["done"] = d.get("timestamp")
                        res = b.get("content")
                        res = user_text(res) if not isinstance(res, str) else res
                        if b.get("is_error") or "<tool_use_error>" in (res or ""):
                            for e in pending[b["tool_use_id"]]:
                                e["failed"] = True
            if d.get("isMeta"):
                continue
            text = user_text(content).strip()
            cmd = re.search(r"<command-name>(.*?)</command-name>", text)
            if cmd:
                text = cmd.group(1)
                if d.get("timestamp"):  # a skill you typed as /name
                    uses.append({"name": text.lstrip("/"), "t": d["timestamp"], "by": "you"})
            elif not text or text.startswith(NOISE):
                continue
            n_user += 1
            title = title or one_line(text, 140)
            if d.get("timestamp"):
                turns.append({"who": "You", "text": text[:4000], "t": d["timestamp"]})
        elif d.get("type") == "assistant" and isinstance(content, list):
            t = d.get("timestamp")
            said = " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text").strip()
            if said and t:
                turns.append({"who": "Claude", "text": said[:4000], "t": t})
            for b in content:
                if not isinstance(b, dict) or b.get("type") != "tool_use" or not t:
                    continue
                name, inp, made = b.get("name"), b.get("input") or {}, []
                if name == "Skill" and inp.get("skill"):
                    uses.append({"name": str(inp["skill"]).lstrip("/"), "t": t, "by": "claude"})
                if name in ("Edit", "Write", "MultiEdit"):
                    p = str(inp.get("file_path", ""))
                    if row_of(p):
                        if name == "Write":
                            made.append({"path": p, "t": t, "kind": "rewrite", "content": inp.get("content", "")})
                        else:
                            edits = [inp] if name == "Edit" else inp.get("edits") or []
                            made.append({"path": p, "t": t, "kind": "edit", "edits": [
                                (e.get("old_string", ""), e.get("new_string", ""), bool(e.get("replace_all"))) for e in edits]})
                elif name == "Bash":
                    shell = str(inp.get("command", ""))
                    written = dict(heredoc_writes(shell, cwd))  # cat > file <<'EOF': the whole new text is in the command
                    for p, body in written.items():
                        made.append({"path": p, "t": t, "kind": "rewrite", "content": body})
                    for p, seg, whole in shell_targets(shell, cwd):
                        if p not in written:
                            made.append({"path": p, "t": t, "kind": "shell", "cmd": seg, "whole": whole})
                for e in made:
                    e["session"] = sid
                events += made
                if made:
                    pending[b.get("id")] = made
    session = None
    if start and n_user and cwd and not cwd.startswith("/tmp/"):
        session = {"id": sid, "t": start, "end": end, "cwd": cwd, "title": title, "n": n_user, "skills": uses, "turns": turns}
    return session, [e for e in events if not e.get("failed")], snaps


STOP = set("that this with from have were they them then than into about after before while when what which would "
           "could should their there because just also only some more most very user claude session asked wanted "
           "said made make first later again instead".split())


def stem(w):
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def words(text):
    return {stem(w) for w in re.findall(r"[a-z][a-z0-9'-]{3,}", (text or "").lower()) if w not in STOP}


def clip(text, size, from_end=False):
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()
    if len(text) <= size:
        return text
    if from_end:
        return "… " + text[-size:].split(" ", 1)[-1]
    return text[:size].rsplit(" ", 1)[0] + " …"


def apply(text, edits, forward):
    """Replay Edit tool calls on a text, forward (old -> new) or backward (new -> old). None if it can't."""
    seq = edits if forward else [(new, old, a) for old, new, a in reversed(edits)]
    for a, b, everywhere in seq:
        if not a or a not in text:
            return None
        text = text.replace(a, b) if everywhere else text.replace(a, b, 1)
    return text


def read(path):
    try:
        with open(path, errors="ignore") as fh:
            return fh.read()
    except OSError:
        return None


def udiff(a, b, cap=9000):
    """A unified diff without its ---/+++ header, or None when either text is unknown."""
    if a is None or b is None:
        return None
    d = "\n".join(difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=1))
    return "\n".join(d.splitlines()[2:])[:cap]


def outside_change(path, evs, cutoff):
    """A change the logs don't show (a script run from the shell, an editor): the file was modified after the last
    change in the logs finished. Only the latest one shows; sessions before it ran on an older text."""
    try:
        t = datetime.datetime.fromtimestamp(os.stat(path).st_mtime, datetime.timezone.utc)
    except OSError:
        return None
    iso = t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"
    if iso < cutoff:
        return None
    last = max((e.get("done") or e["t"] for e in evs), default=None)
    if last and (t - datetime.datetime.fromisoformat(last.replace("Z", "+00:00"))).total_seconds() < 5:
        return None  # that's the logged change itself
    return {"path": path, "t": iso, "kind": "outside", "session": "outside:" + iso}


def build_row(path, evs, snaps):
    """Versions of one file: what changed when, and its text after each change when it can be rebuilt."""
    scope, kind, name = row_of(path)
    evs = sorted(evs, key=lambda e: e["t"])
    times = [e["t"] for e in evs]
    n = len(evs)
    A = [None] * (n + 1)  # A[i] = text after change i; A[0] = text before the first change in the logs
    A[n] = read(path)
    for i, e in enumerate(evs, 1):
        if e["kind"] == "rewrite":
            A[i] = e["content"]
            if e.get("before") is not None and A[i - 1] is None:
                A[i - 1] = e["before"]
    created = None
    for s in sorted(snaps, key=lambda s: s["t"]):
        k = bisect.bisect_left(times, s["t"])  # number of changes before this snapshot
        if s["file"] is None:  # the file didn't exist yet
            text = ""
            if k < n:
                created = times[k]
        else:
            text = read(os.path.join(CLAUDE, "file-history", s["session"], s["file"]))
        if text is not None and A[k] is None:
            A[k] = text
    changed = True
    while changed:
        changed = False
        for i, e in enumerate(evs, 1):
            if e["kind"] != "edit":
                continue
            if A[i] is None and A[i - 1] is not None:
                A[i] = apply(A[i - 1], e["edits"], True)
                changed |= A[i] is not None
            if A[i - 1] is None and A[i] is not None:
                A[i - 1] = apply(A[i], e["edits"], False)
                changed |= A[i - 1] is not None
    if created is None and evs and (evs[0]["kind"] == "rewrite" or evs[0].get("whole")) and A[0] is None:
        created = times[0]  # first seen as a full write: assume it was created then
    versions = []
    for i, e in enumerate(evs, 1):
        if e["kind"] == "edit":
            what = one_line(" … ".join(new for _, new, _ in e["edits"]))
        elif e["kind"] == "rewrite":
            what = e.get("what") or "Rewrote the whole file."
        elif e["kind"] == "outside":
            what = "Changed by a script or an editor, so what changed can't be shown."
        else:
            what = "Shell: " + e["cmd"]
        versions.append({"t": e["t"], "kind": e["kind"], "session": e["session"], "what": what, "diff": udiff(A[i - 1], A[i]), "text": A[i]})
    # A "version" for people is one session's batch of edits: consecutive edits made in the same session.
    clusters = []
    for i, e in enumerate(evs, 1):
        if clusters and clusters[-1]["session"] == e["session"]:
            clusters[-1].update(end=e["t"], edits=clusters[-1]["edits"] + 1, after=i)
        else:
            clusters.append({"session": e["session"], "start": e["t"], "end": e["t"], "edits": 1, "before": i - 1, "after": i})
    for c in clusters:
        before, after = A[c.pop("before")], A[c.pop("after")]
        c.update(diff=udiff(before, after), new=before == "" or (created is not None and c["start"] == created), text=after, what=versions[evs.index(next(x for x in evs if x["t"] == c["end"]))]["what"])
    return {"id": path, "path": path.replace(HOME, "~", 1), "scope": scope, "kind": kind, "name": name,
            "created": created, "base": A[0], "versions": versions, "clusters": clusters}


def ran_on(row, s, start, stop):
    """Did session s use this file between start and stop? (Sessions under 3 messages don't count.)"""
    if s["n"] < 3 or not (row["scope"] == "global" or s["cwd"] == row["scope"] or s["cwd"].startswith(row["scope"] + "/")):
        return False
    if row["kind"] == "skill":
        return any(u["name"] == row["name"] and start <= u["t"] < stop for u in s.get("skills", []))
    return start <= s["t"] < stop


def has_marks(row, s, tri):
    """Does session s give this file a mark on the page? A skill: loaded and judged. CLAUDE.md or a style: a verdict on it."""
    v = tri.get(s["id"])
    if not v:
        return False
    return row["kind"] == "skill" or any(f.get("file") == row["id"] for f in v.get("files", []))


def merge_unused(row, sessions, tri=None):
    """Fold a version with no marks (no session ran on it, or none the doctor gave a mark) into the next one:
    one diff from before it to after the next, edits added up. The latest version always stays."""
    tri = load_triage()["sessions"] if tri is None else tri
    cl, out = row["clusters"], []
    for k, c in enumerate(cl):
        if out and out[-1].get("_fold"):
            prev = out.pop()
            before = out[-1]["text"] if out else row["base"]
            c = dict(c, start=prev["start"], edits=prev["edits"] + c["edits"], new=prev["new"], diff=udiff(before, c["text"]) or c["diff"])
        last = k == len(cl) - 1
        c["_fold"] = not last and not any(ran_on(row, s, c["end"], cl[k + 1]["start"]) and has_marks(row, s, tri) for s in sessions)
        out.append(c)
    for c in out:
        c.pop("_fold", None)
    row["clusters"] = out


def build():
    sessions, events, snaps = [], defaultdict(list), defaultdict(list)
    for log in glob.glob(os.path.join(CLAUDE, "projects", "*", "*.jsonl")):
        s, evs, sn = parse_log(log)
        if s:
            sessions.append(s)
        for e in evs:
            events[e["path"]].append(e)
        for x in sn:
            snaps[x["path"]].append(x)

    scores = {}
    for f in glob.glob(os.path.join(CLAUDE, "usage-data", "facets", "*.json")):
        try:
            d = json.load(open(f))
        except ValueError:
            continue
        scores[d.get("session_id")] = d

    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = (now - datetime.timedelta(days=WINDOW_DAYS)).isoformat()
    sessions = [s for s in sessions if s["t"] >= cutoff]
    for s in sessions:
        sc = scores.get(s["id"])
        s["scored"] = bool(sc)
        if sc:
            s["friction"] = sc.get("friction_counts") or {}
            s["detail"] = sc.get("friction_detail") or ""
            s["outcome"] = (sc.get("outcome") or "").replace("_", " ")
            s["summary"] = sc.get("brief_summary") or ""
        s.pop("turns", None)  # messages stay on disk; the page doesn't need them

    paths = set(glob.glob(os.path.join(CLAUDE, "CLAUDE.md")))
    paths |= set(glob.glob(os.path.join(CLAUDE, "skills", "*", "SKILL.md")))
    paths |= set(glob.glob(os.path.join(CLAUDE, "output-styles", "*.md")))
    for cwd in {s["cwd"] for s in sessions}:
        paths |= set(glob.glob(os.path.join(cwd, "CLAUDE.md")))
        paths |= set(glob.glob(os.path.join(cwd, ".claude", "skills", "*", "SKILL.md")))
        paths |= set(glob.glob(os.path.join(cwd, ".claude", "output-styles", "*.md")))
    paths |= {p for p in events if os.path.isfile(p)}
    rows, tri = [], load_triage()["sessions"]
    for p in sorted(paths):
        if os.path.isfile(p) and row_of(p):
            evs = [e for e in events[p] if e["t"] >= cutoff]
            later = outside_change(p, evs, cutoff)
            row = build_row(p, evs + ([later] if later else []), snaps[p])
            merge_unused(row, sessions, tri)
            rows.append(row)

    counts = defaultdict(int)
    for s in sessions:
        counts[s["cwd"]] += 1
    scopes = [{"key": "global", "label": "Global", "sessions": len(sessions)}]
    for cwd, c in sorted(counts.items(), key=lambda kv: -kv[1]):
        scopes.append({"key": cwd, "label": cwd.replace(HOME, "~", 1), "sessions": c})

    return {"from": int((now - datetime.timedelta(days=WINDOW_DAYS)).timestamp() * 1000),
            "to": int(now.timestamp() * 1000),
            "scopes": scopes, "sessions": sessions, "rows": rows,
            "triage": {k: v["files"] for k, v in load_triage()["sessions"].items()},
            "gaps": {k: v["gaps"] for k, v in load_triage()["sessions"].items() if v.get("gaps")},
            "pending": {sc["key"]: pending({"rows": rows, "sessions": sessions}, sc["key"]) for sc in scopes},
            "last_judged": max((v["at"] for v in load_triage()["sessions"].values()), default=None)}


# ---------- asking Claude: the page's judging and re-check runs follow the diagnose skill ----------
SKILL = os.path.normpath(os.path.join(HERE, "..", "skills", "diagnose", "SKILL.md"))


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def iso_ago(**delta):
    return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(**delta)).isoformat()


def load_json(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    with open(path + ".tmp", "w") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(path + ".tmp", path)


def ask_claude(prompt, instruction, model, effort=None, timeout=900):
    """One headless Claude run: no tools, no hooks or user settings, nothing saved. Returns the JSON object it answers."""
    r = subprocess.run(["claude", "-p", instruction, "--model", model, *(["--effort", effort] if effort else []),
                        "--tools", "", "--no-session-persistence",
                        "--setting-sources", "project", "--settings", '{"autoMemoryEnabled": false}', "--output-format", "json"],
                       input=prompt, capture_output=True, text=True, timeout=timeout, cwd=HERE)
    text = json.loads(r.stdout).get("result", "")
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else None


def skill_body():
    return re.sub(r"^---.*?---\s*", "", read(SKILL) or "", flags=re.S)


# ---------- triage: one cheap run that sorts every new session's issues onto the files ----------
TRIAGE_V = 3  # bump to re-sort every session (2: verdicts quote the user's words; 3: a skill loaded twice gets a verdict per load)
TRIAGE_OK = (2, 3)  # versions still read; a v2 session where a skill loaded twice gets judged again
JOB = {"state": "idle", "message": "", "pct": None}  # pct: None while the length is unknown


def load_triage():
    t = load_json(data_path("triage.json"), {})
    cutoff = iso_ago(days=WINDOW_DAYS)
    t["sessions"] = {k: v for k, v in t.get("sessions", {}).items() if v.get("at", "") >= cutoff and v.get("v") in TRIAGE_OK}
    return t


def about(row, text=None):
    """What a file is for: its description, or else its headings and rule titles, or else what kind of file it is.
    Pass the text of an older version to say what that version covered."""
    text = (read(row["id"]) if text is None else text) or ""
    fm = re.match(r"---\s*\n(.*?)\n---", text, re.S)
    desc = re.search(r"^description:\s*(.+)$", fm.group(1), re.M) if fm else None
    if desc:
        return one_line(desc.group(1), 240)
    where = "every session" if row["scope"] == "global" else "sessions in " + os.path.basename(row["scope"])
    titles = rule_titles(text + "".join("\n" + (read(p) or "") for p in imports(row["id"], text)))
    return f"{row['kind']} loaded in {where}" + (": " + titles if titles else "")


def imports(path, text):
    """Files a CLAUDE.md pulls in with @path lines (one level), as they are today; a file that's gone is skipped."""
    out = []
    for m in re.finditer(r"^@(\S+)\s*$", text or "", re.M):
        p = os.path.expanduser(m.group(1))
        out.append(p if os.path.isabs(p) else os.path.join(os.path.dirname(path), p))
    return out


def rule_titles(text, cap=900):
    """What a CLAUDE.md covers, without its full text: its headings and the bold title that starts each rule."""
    out = []
    for line in (text or "").splitlines():
        m = re.match(r"\s*#{1,4}\s+(.+)", line) or re.match(r"\s*(?:\d+\.|[-*])\s+\*\*(.+?)\*\*", line)
        if m:
            out.append(m.group(1).strip().rstrip(".:,"))
    return one_line(" · ".join(out), cap) if out else ""


def version_at(row, t):
    """The text of a file in place at time t: (the index of the last edit before t, or -1 for the text before the logs,
    its text). Per edit, not per page version, since versions with no marks get folded into the next one."""
    k, text = -1, row.get("base")
    for i, v in enumerate(row.get("versions", [])):
        if v["t"] <= t:
            k, text = i, v["text"]
    return k, (text if text is not None else read(row["id"]))


def touched_files(s, rows):
    out = []
    for r in rows:
        if r.get("created") and r["created"] > s["t"]:
            continue
        here = r["scope"] == "global" or s["cwd"] == r["scope"] or s["cwd"].startswith(r["scope"] + "/")
        if not here:
            continue
        if r["kind"] != "skill" or any(u["name"] == r["name"] for u in s.get("skills", [])):
            out.append(r["id"])
    return out


def your_words(sid, cap=40):
    """The user's own messages in a session (time, text, and the end of the Claude message it answered), clipped."""
    logs = glob.glob(os.path.join(CLAUDE, "projects", "*", sid + ".jsonl"))
    turns, out, before = ((parse_log(logs[0])[0] or {}).get("turns", []) if logs else []), [], ""
    for m in turns:
        if m["who"] == "Claude":
            before = m["text"]
        else:
            out.append({"time": m["t"], "text": clip(m["text"], 240), "after_claude": clip(before, 200, from_end=True) if before else None})
            before = ""
    return out if len(out) <= cap else out[: cap // 2] + out[-cap // 2:]  # keep the start and the end of long sessions


def run_triage(d, todo):
    rows = {r["id"]: r for r in d["rows"]}
    files, real, items = {}, {}, []  # files: one entry per file version, so each session sees what its version covered
    for s in todo:
        keys = []
        for i in touched_files(s, d["rows"]):
            k, text = version_at(rows[i], s["t"])
            key = f"{i}#v{k + 1}"
            if key not in files:
                files[key] = {"id": key, "name": rows[i]["name"], "kind": rows[i]["kind"], "about": about(rows[i], text)}
                real[key] = i
            keys.append(key)
        twice = loads_by_skill(s, more_than_once=True)
        uses = {k: twice[rows[real[k]]["name"]] for k in keys if rows[real[k]]["kind"] == "skill" and rows[real[k]]["name"] in twice}
        items.append({"session": s["id"], "date": s["t"][:10], "issues": s.get("friction", {}), "note": s.get("detail", ""),
                      "summary": s.get("summary", ""), "outcome": s.get("outcome", ""), "files": keys, "you": your_words(s["id"]),
                      **({"uses": uses} if uses else {})})
    doc = {"files": list(files.values()), "known_gaps": known_gaps(), "sessions": items}
    prompt = ("Act as the harness-doctor skill below, in triage mode: answer with the JSON only.\n\n" + skill_body() +
              "\n\n## The input\n" + json.dumps(doc, ensure_ascii=False))
    res = ask_claude(prompt, "Triage the sessions in the input, following the instructions in it.", "sonnet") or {}
    out = {s["id"]: {"at": now_iso(), "v": TRIAGE_V, "n": s["n"], "files": [], "gaps": []} for s in todo}  # judged, even when nothing was found; n: your messages then
    for x in res.get("sessions", []):
        if isinstance(x, dict) and x.get("session") in out:
            out[x["session"]]["files"] = [clean_use(dict(f, file=real[f["file"]])) for f in x.get("files", []) if isinstance(f, dict) and f.get("file") in files]
            out[x["session"]]["gaps"] = [g for g in x.get("gaps", []) if isinstance(g, dict) and g.get("topic") and g.get("line")]
    return out


def clean_use(f):
    """A verdict's `use` (which load of a skill, 1 for the first) as a whole number, or none (one usage per session)."""
    u = f.pop("use", None)
    if isinstance(u, int) and u >= 1:
        f["use"] = u
    return f


# ---------- re-check: a gap a file covers today leaves the list ----------
def recheck_key(t, d):
    """Changes when the open gaps or any file's versions change; the re-check only runs then."""
    gaps = sorted((sid, g["topic"]) for sid, v in t["sessions"].items() for g in v.get("gaps", []))
    files = sorted((r["id"], r["clusters"][-1]["end"] if r.get("clusters") else "") for r in d["rows"])
    return hashlib.sha1(json.dumps([gaps, files]).encode()).hexdigest()[:16]


def recheck_gaps(d, t):
    """Ask once which open gaps a file covers today, then settle them. Returns how many gaps left the list."""
    key = recheck_key(t, d)
    by_id = {s["id"]: s for s in d["sessions"]}
    topics = defaultdict(list)
    for sid, v in t["sessions"].items():
        for g in v.get("gaps", []):
            if sid in by_id:
                topics[g["topic"]].append({"date": by_id[sid]["t"][:10], "project": os.path.basename(by_id[sid]["cwd"]), "line": g["line"]})
    if not topics or t.get("recheck") == key:
        return 0
    files = [{"id": r["id"], "name": r["name"], "kind": r["kind"],
              "scope": "global" if r["scope"] == "global" else os.path.basename(r["scope"]), "about": about(r)} for r in d["rows"]]
    doc = {"files": files, "gaps": [{"topic": k, "moments": v} for k, v in topics.items()]}
    prompt = ("Act as the harness-doctor skill below, in re-check mode: answer with the JSON only.\n\n" + skill_body() +
              "\n\n## The input\n" + json.dumps(doc, ensure_ascii=False))
    res = ask_claude(prompt, "Re-check the gaps in the input, following the instructions in it.", "sonnet") or {}
    ids = {r["id"] for r in d["rows"]}
    covered = {x["topic"]: x["file"] for x in res.get("covered", []) if isinstance(x, dict) and x.get("file") in ids}
    n = settle_gaps(t, d, covered)
    t["recheck"] = recheck_key(t, d)  # after settling, so a later run with nothing new skips the call
    return n


def settle_gaps(t, d, covered):
    """covered: {topic: file id that covers it today}. For each session with that gap:
    the file was there and in use then → the gap becomes a ✕ on that file in that session (its version then lacked it);
    the file came later, or a skill wasn't loaded → the gap is deleted;
    the file belongs to another project → the gap stays."""
    rows, by_id, n = {r["id"]: r for r in d["rows"]}, {s["id"]: s for s in d["sessions"]}, 0
    for sid, v in t["sessions"].items():
        s, keep = by_id.get(sid), []
        for g in v.get("gaps", []):
            fid = covered.get(g["topic"])
            row = rows.get(fid)
            if not (s and row) or (row["scope"] != "global" and not in_scope(s, row["scope"])):
                keep.append(g)  # nothing covers it, or only another project's file does
                continue
            n += 1
            if fid not in touched_files(s, d["rows"]):
                continue  # deleted: the fix came after this session
            issue = {"type": g.get("type") or "issue", "line": g["line"], "you": g.get("you"), "you_marks": g.get("you_marks", []), "gap": g["topic"]}
            f = next((f for f in v["files"] if f.get("file") == fid), None)
            if f:
                if f.get("verdict") != "failed":
                    f.update(verdict="failed", line=g["line"])  # its "what went right" line no longer fits
                f["issues"] = f.get("issues", []) + [issue]
            else:
                v["files"].append({"file": fid, "verdict": "failed", "line": g["line"], "you": g.get("you"), "issues": [issue]})
        v["gaps"] = keep
    return n


# ---------- problems: where the fix for a gap belongs ----------
def known_gaps(cap=60):
    """Gap topics found so far (the latest line for each), so the triage reuses a topic for the same problem."""
    seen = {}
    for v in sorted(load_triage()["sessions"].values(), key=lambda v: v.get("at", "")):
        for g in v.get("gaps", []):
            seen[g["topic"]] = g["line"]
    return [{"topic": k, "line": v} for k, v in list(seen.items())[-cap:]]


def in_scope(s, scope):
    return scope == "global" or s["cwd"] == scope or s["cwd"].startswith(scope + "/")


def rows_in(d, scope):
    """Global: the global files. A project: that project's own files."""
    if scope == "global":
        return [r for r in d["rows"] if r["scope"] == "global"]
    return [r for r in d["rows"] if r["scope"] != "global" and (scope == r["scope"] or scope.startswith(r["scope"] + "/"))]


def pending(d, scope="global"):
    """What "Run harness doctor" would do now for a scope: finished and ongoing sessions to judge (for the first time, or
    again because they have new messages), and whether the problems need a re-check."""
    t, live = load_triage(), live_sessions()
    todo = [s for s in d["sessions"] if to_judge(s, scope, t)]
    ongoing = sum(1 for s in todo if s["id"] in live)
    recheck = any(v.get("gaps") for v in t["sessions"].values()) and t.get("recheck") != recheck_key(t, d)
    return {"has_data": bool(t["sessions"]), "judge": len(todo) - ongoing, "ongoing": ongoing, "recheck": recheck}


def live_sessions():
    """Session ids a running Claude Code still has open (ongoing): each process writes ~/.claude/sessions/<pid>.json."""
    ids = set()
    for f in glob.glob(os.path.join(CLAUDE, "sessions", "*.json")):
        j = load_json(f, {})
        pid, sid = j.get("pid"), j.get("sessionId")
        if not (pid and sid):
            continue
        try:
            os.kill(int(pid), 0)  # signal 0 only checks the process exists
        except ProcessLookupError:
            continue  # a stale file from a process that's gone
        except (PermissionError, OSError, ValueError):
            pass
        ids.add(sid)
    return ids


def to_judge(s, scope, t):
    """Why session s needs judging now, open or closed: "new" (never judged, or judged before gaps or one verdict per
    skill load existed), "again" (it has new messages since it was judged; the newest verdict replaces the old one),
    or None. Every session counts, not only ones that used a file: an issue no file covers is a gap."""
    if s["n"] < 3 or not in_scope(s, scope):
        return None
    done = t["sessions"].get(s["id"])
    if not done or "gaps" not in done or (done.get("v", 0) < 3 and loads_by_skill(s, more_than_once=True)):
        return "new"
    return "again" if grew(s, done) else None


def needs_judging(s, d, scope, t):
    return to_judge(s, scope, t) is not None


def grew(s, done):
    """Has the session had new messages from you since it was judged? (Older results don't keep the count: compare times.)"""
    if "n" in done:
        return s["n"] > done["n"]
    def when(x):
        d = datetime.datetime.fromisoformat(x.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)
    try:
        return when(s["end"]) > when(done["at"])
    except (KeyError, ValueError, TypeError):
        return False


def loads_by_skill(s, more_than_once=False):
    """{skill name: the times it loaded in session s, in order}; each load is one usage with its own verdict."""
    out = defaultdict(list)
    for u in s.get("skills", []):
        out[u["name"]].append(u["t"])
    return {k: sorted(v) for k, v in out.items() if len(v) > 1 or not more_than_once}


def doctor_job(scope="global"):
    """The page's button, for the chosen scope: judge every session it hasn't judged or that has new messages (one cheap run per 15,
    from your messages, plus the /insights note where there is one), then re-check the problems no file covered."""
    try:
        JOB.update(state="running", message="Finding new sessions…", pct=None)
        d, t = json.loads(data_json(True)), load_triage()
        todo = [s for s in d["sessions"] if needs_judging(s, d, scope, t)]
        for i in range(0, len(todo), 15):
            JOB.update(message=f"Judging sessions {i + 1}–{min(i + 15, len(todo))} of {len(todo)}…", pct=round(90 * i / len(todo)))
            for sid, v in run_triage(d, todo[i:i + 15]).items():
                old = t["sessions"].get(sid)
                if old and "gaps" not in old:
                    v["files"] = old["files"]  # judged before gaps existed: keep its verdicts, add the gaps
                t["sessions"][sid] = v
            save_json(data_path("triage.json"), t)
        # Then re-check the open gaps against today's files (one cheap run, only when the gaps or files changed).
        JOB.update(message="Checking the problems no file covered against your files…", pct=90)
        try:
            settled = recheck_gaps(d, t)
        except (OSError, subprocess.SubprocessError, ValueError) as err:
            settled, t = 0, load_triage()  # leave the gaps as they were
            print(f"Re-check didn't come back: {err}", file=sys.stderr)
        save_json(data_path("triage.json"), t)
        CACHE["body"] = None
        msg = (f"Done: judged {len(todo)} new {'session' if len(todo) == 1 else 'sessions'}" +
               (f", and {settled} {'problem is' if settled == 1 else 'problems are'} now covered by a file." if settled else "."))
        JOB.update(state="done", pct=100, message=msg)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as err:
        JOB.update(state="error", message=f"Harness doctor stopped: {err}")


CACHE = {"at": 0.0, "body": None}
LOCK = threading.Lock()


def data_json(fresh):
    with LOCK:
        if fresh or CACHE["body"] is None or time.time() - CACHE["at"] > 60:
            CACHE["body"] = json.dumps(build(), ensure_ascii=False).encode()
            CACHE["at"] = time.time()
        return CACHE["body"]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def local(self):  # refuse requests that didn't come to this address (DNS rebinding)
        return self.headers.get("Host") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def do_GET(self):
        if not self.local():
            return self.reply(403, b"forbidden", "text/plain")
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as fh:
                return self.reply(200, fh.read(), "text/html; charset=utf-8")
        if path == "/api/data":
            return self.reply(200, data_json("fresh=1" in self.path), "application/json")
        if path == "/api/status":
            return self.reply(200, json.dumps(JOB).encode(), "application/json")
        self.reply(404, b"not found", "text/plain")

    def do_POST(self):
        # The custom header can't be sent cross-site without a CORS preflight, which this server never allows.
        if not self.local() or self.path != "/api/doctor" or self.headers.get("X-Harness-Doctor") != "1":
            return self.reply(403, b'{"ok":false,"message":"forbidden"}', "application/json")
        if JOB["state"] == "running":
            return self.reply(200, b'{"ok":false,"message":"Already running."}', "application/json")
        try:
            scope = str(json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}").get("scope") or "global")
        except ValueError:
            scope = "global"
        JOB.update(state="running", message="Starting…", pct=None)
        threading.Thread(target=doctor_job, args=(scope,), daemon=True).start()
        self.reply(200, b'{"ok":true}', "application/json")


def reload_on_change():
    """Restart this server when its own code changes. The page itself is read from disk on every load."""
    me = os.path.abspath(__file__)
    seen = os.path.getmtime(me)
    while True:
        time.sleep(1)
        try:
            now = os.path.getmtime(me)
        except OSError:
            continue
        if now == seen:
            continue
        seen = now
        try:
            with open(me) as fh:
                compile(fh.read(), me, "exec")
        except SyntaxError as err:
            print(f"Code changed but doesn't compile, keeping the old version: {err}", flush=True)
            continue
        print("Code changed, restarting…", flush=True)
        os.execv(sys.executable, [sys.executable] + sys.orig_argv[1:])


def url():
    return f"http://127.0.0.1:{PORT}"


def running():
    """Is a Harness Doctor server already answering on this port?"""
    try:
        req = urllib.request.Request(url() + "/api/status", headers={"Host": f"127.0.0.1:{PORT}"})
        with urllib.request.urlopen(req, timeout=2) as r:
            return "state" in json.loads(r.read())
    except (OSError, ValueError):
        return False


def open_page(args):
    if not args.no_open:
        webbrowser.open(url())
    print(f"Harness Doctor: {url()}", flush=True)


def main():
    global PORT, DATA
    ap = argparse.ArgumentParser(description="Harness Doctor: which of your Claude Code instructions help, and which cause trouble.")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--data", default=None, help="folder for its results (default: %(default)s)")
    ap.add_argument("--no-open", action="store_true", help="don't open the browser")
    ap.add_argument("--background", action="store_true", help="run detached; if one is already running, just open the page")
    ap.add_argument("--stop", action="store_true", help="stop a background server")
    ap.add_argument("--build", action="store_true", help="print a summary of what it reads, no server")
    ap.add_argument("--dev", action="store_true", help="restart when this file changes")
    args = ap.parse_args()
    PORT = args.port
    if args.data:
        DATA = os.path.expanduser(args.data)
    os.environ["HARNESS_DOCTOR_DATA"] = DATA  # a background copy uses the same folder
    pid_file = data_path("server.pid")

    if args.stop:
        try:
            os.kill(int(open(pid_file).read()), 15)
            os.remove(pid_file)
            print("Stopped.")
        except (OSError, ValueError):
            print("No background server to stop.")
        return
    if args.build:
        d = build()
        print(f"{len(d['sessions'])} sessions, {len(d['rows'])} files, {len(d['scopes']) - 1} projects")
        for r in d["rows"]:
            print(f"  {r['kind']:12} {r['path']:60} {len(r['clusters'])} versions")
        return
    if running():
        return open_page(args)
    if args.background:
        with open(data_path("server.log"), "a") as log:
            child = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--port", str(PORT), "--no-open"],
                                     stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
        with open(pid_file, "w") as fh:
            fh.write(str(child.pid))
        for _ in range(50):  # up to 10 seconds
            if running():
                return open_page(args)
            time.sleep(0.2)
        print(f"It didn't start; see {data_path('server.log')}")
        sys.exit(1)

    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Harness Doctor: {url()}   (Ctrl+C to stop)", flush=True)
    if not args.no_open:
        threading.Timer(0.5, webbrowser.open, [url()]).start()
    if args.dev:
        threading.Thread(target=reload_on_change, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
