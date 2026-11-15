"""Harness Doctor's reader: what Claude Code already keeps on disk, turned into sessions and file versions.

It reads the session logs (~/.claude/projects) and the file-history snapshots (~/.claude/file-history). Those formats
aren't documented, so a Claude Code update can break it. Nothing here calls Claude or writes outside the data folder.
"""
import bisect
import datetime
import difflib
import glob
import json
import os
import re
import shlex
import subprocess
import time
from collections import defaultdict

HOME = os.path.expanduser("~")
CLAUDE = os.path.join(HOME, ".claude")
HERE = os.path.dirname(os.path.abspath(__file__))
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
    """(scope, kind, name) when the path is a CLAUDE.md, a SKILL.md, an output style or a note in the domain knowledge, else None.
    scope is "global" for ~/.claude, else the folder that holds the project's CLAUDE.md / .claude."""
    if not path or not path.startswith("/") or path.startswith("/tmp/"):
        return None
    g = CLAUDE + "/"
    if path == g + "CLAUDE.md":
        return "global", "rules", "CLAUDE.md"
    if path.startswith(g + "domain-knowledge/") and path.endswith(".md"):  # an index is followed for its "read when" lines
        return "global", "index" if path.endswith("/INDEX.md") else "note", path[len(g) + len("domain-knowledge/"):]
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
        if not (snap or '"type":"user"' in line or '"type":"assistant"' in line):  # Claude's text-only replies count too
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


def version_at(row, t):
    """The text of a file in place at time t: (the index of the last edit before t, or -1 for the text before the logs,
    its text). Per edit, not per page version, since versions with no marks get folded into the next one."""
    k, text = -1, row.get("base")
    for i, v in enumerate(row.get("versions", [])):
        if v["t"] <= t:
            k, text = i, v["text"]
    return k, (text if text is not None else read(row["id"]))


def born(path):
    """When a file or folder was made, as an ISO time ("" when the disk doesn't say). A move keeps it; a copy, or an
    edit that saves by replacing the file, resets it."""
    try:
        t = int(subprocess.run(["stat", "-c", "%W", path], capture_output=True, text=True).stdout.strip() or 0)
    except (OSError, ValueError):
        t = 0
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) if t > 0 else ""


def since(row, birth):
    """From when a file's text is known, as an ISO time: a session older than this can't be judged against the file.
    "" when the file was there before everything the logs show. `birth` is born(path).
    The logs saw it made: then. No change in the logs, or the disk says it was made clearly before the first one
    (a script made it, a later session edited it): the disk's time. Else the disk's time is only the last save."""
    if row and row.get("created"):
        return row["created"]
    first = next((v["t"] for v in (row or {}).get("versions", []) if v["kind"] != "outside"), None)
    if not birth or first is None:
        return birth
    gap = datetime.datetime.fromisoformat(first[:19]) - datetime.datetime.fromisoformat(birth)
    return birth if gap.total_seconds() > 2 else ""


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


PARSED = {}  # log path -> ((modified time, size), what parse_log made of it); lives as long as the server


def scan():
    """Read every session log once. -> {"sessions": the ones in the window, newest first (each with "log", its file),
    "events": {path: changes to that file in the window}, "snaps": {path: snapshots}, "cutoff": the window's start}"""
    sessions, events, snaps = [], defaultdict(list), defaultdict(list)
    for log in glob.glob(os.path.join(CLAUDE, "projects", "*", "*.jsonl")):
        try:
            stamp = (os.path.getmtime(log), os.path.getsize(log))
        except OSError:
            continue
        if PARSED.get(log, (None,))[0] != stamp:  # only a log that changed is read again
            PARSED[log] = (stamp, parse_log(log))
        s, evs, sn = PARSED[log][1]
        if s:
            s = dict(s, log=log)
            s.pop("turns", None)
            sessions.append(s)
        for e in evs:
            events[e["path"]].append(e)
        for x in sn:
            snaps[x["path"]].append(x)
    cutoff = iso_ago(days=WINDOW_DAYS)
    return {"sessions": sorted((s for s in sessions if s["t"] >= cutoff), key=lambda s: s["t"], reverse=True),
            "events": {p: [e for e in evs if e["t"] >= cutoff] for p, evs in events.items()}, "snaps": snaps, "cutoff": cutoff, "rows": {}}


def row(sc, path):
    """The versions of one file (see build_row), built once per scan. None when the file isn't one the reader follows."""
    if path not in sc["rows"]:
        if not (os.path.isfile(path) and row_of(path)):
            sc["rows"][path] = None
        else:
            evs = sc["events"].get(path, [])
            later = outside_change(path, evs, sc["cutoff"])
            sc["rows"][path] = build_row(path, evs + ([later] if later else []), sc["snaps"].get(path, []))
    return sc["rows"][path]
