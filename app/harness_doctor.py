#!/usr/bin/env python3
"""Harness Doctor: which of your written rules and facts hold up in real sessions, and which fail.

One card per file: CLAUDE.md, the output style, each skill, and each note or folder of the domain knowledge. Every rule
in a file gets its own marks, from Claude reading your sessions and answering one narrow question per rule: did its
moment come up, and was it followed? Fixing takes a conversation, so the page only copies a prompt for Claude Code.

Run:   python3 harness_doctor.py                 starts the page on http://127.0.0.1:8737 and opens your browser
       python3 harness_doctor.py --background    the same, detached; if it's already running, just opens the page
       python3 harness_doctor.py --stop          stops a background server
       python3 harness_doctor.py --report        prints what is failing, worst first (the fix skill reads this)
Flags: --port N, --data DIR (where it keeps its marks), --no-open, --project NAME (with --report), --dev (restart
       itself when its code changes)

app/reader.py reads the logs, app/rules.py cuts the files into rules, app/judge.py asks Claude.
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import judge as jd  # noqa: E402
import reader as rd  # noqa: E402
import rules as rl  # noqa: E402

PORT = int(os.environ.get("HARNESS_DOCTOR_PORT", "8737"))
STATUS = {"worse": "Regression", "bad": "Failing", "good": "Healthy", "none": "No data"}  # worst first


def day(iso):
    """2026-11-13T… -> "Nov 13"."""
    try:
        return time.strftime("%b %-d", time.strptime(iso[:10], "%Y-%m-%d"))
    except (ValueError, TypeError):
        return ""


def versions_of(sc, f):
    """A file's versions, oldest first: the text before its first edit in the logs (unless it was created in the window),
    then one per session that edited it. A folder has one version.
    -> [{"v", "when", "t" (in force from), "base": bool, "diff", "text"}]"""
    row = None if f["isdir"] else rd.row(sc, os.path.expanduser(f["path"]))
    cl = row["clusters"] if row else []
    out = []
    if not cl or not (row.get("created") and cl[0]["start"] <= row["created"]):
        out.append({"t": "", "when": "", "base": True, "diff": None, "text": row["base"] if (row and cl) else rd.read(os.path.expanduser(f["path"]))})
    for c in cl:
        out.append({"t": c["end"], "when": day(c["end"]), "base": False, "diff": c["diff"], "text": c["text"]})
    for i, v in enumerate(out, 1):
        v["v"] = i
    return out


def version_at(versions, t):
    """The version in force at time t (the oldest one when t is before them all)."""
    v = versions[0]["v"]
    for x in versions:
        if x["t"] and x["t"] <= t:
            v = x["v"]
    return v


def rule_texts(f, text):
    """{rule id: its text} for one version of a file, cut the same way the catalog cuts it."""
    out = {}
    for r in f["rules"]:
        if r["part"] == "load":
            out[r["id"]] = rl.description(text) if f["kind"] == "skill" else r["text"]
    for r in rl.cut_rules(text):
        out.setdefault(f["id"] + "#" + rl.slug(r["title"]), r["text"])
    return out


def rule_epochs(f):
    """A rule's own history: an epoch is a stretch of file versions in which the rule's text stayed the same, so an
    edit elsewhere in the file doesn't reset the rule's marks.
    -> {rule id: [the epoch number of each file version, oldest first]}, {rule id: [{"e", "when": when it began}]}"""
    texts = [rule_texts(f, v["text"]) if v["text"] is not None else None for v in f["versions"]]
    for i in range(len(texts) - 2, -1, -1):  # a version whose text couldn't be rebuilt counts as unchanged: from the next one,
        texts[i] = texts[i] if texts[i] is not None else texts[i + 1]
    for i in range(len(texts)):  # or, with nothing known after it, from the one before
        texts[i] = texts[i] if texts[i] is not None else (texts[i - 1] if i else {})
    of, spans = {}, {}
    for r in f["rules"]:
        e, of[r["id"]], spans[r["id"]] = 1, [], [{"e": 1, "when": f["versions"][0]["when"]}]
        for i in range(len(texts)):
            if i and texts[i].get(r["id"]) != texts[i - 1].get(r["id"]):
                e += 1
                spans[r["id"]].append({"e": e, "when": f["versions"][i]["when"]})
            of[r["id"]].append(e)
    return of, spans


def status(moments, latest):
    """One rule's status from its marks: on the rule as it is written now (epoch `latest`) and on the newest older
    wording that has any. regression: a bad use now, clean before. failing: a bad use now. healthy: used now, no bad
    use. no data: no use now."""
    now = [m for m in moments if m["e"] == latest]
    if not now:
        return "none"
    if all(m["verdict"] == "ok" for m in now):
        return "good"
    older = sorted({m["e"] for m in moments if m["e"] < latest})
    before = [m for m in moments if older and m["e"] == older[-1]]
    return "worse" if before and all(m["verdict"] == "ok" for m in before) else "bad"


TEXTS = {}  # (file id, version) -> that version's text, for "Show source file"; filled by page_data


def page_data(sc, st):
    """Everything the page shows, in one object: the files with their rules, versions and statuses, the marks, the
    problems nothing covers, the projects in use, and what the judge button would do."""
    files = [f for f in rl.catalog(sc) if f["rules"]]
    by_id = {s["id"]: s for s in sc["sessions"]}
    rule_file = {r["id"]: f for f in files for r in f["rules"]}
    part = {r["id"]: r["part"] for f in files for r in f["rules"]}
    epoch_of = {}
    for f in files:
        f["versions"] = versions_of(sc, f)
        of, spans = rule_epochs(f)
        epoch_of.update(of)
        for r in f["rules"]:
            r["epochs"] = spans[r["id"]]
    moments, topics, n = [], defaultdict(dict), 0
    for sid, e in st["sessions"].items():
        s = by_id.get(sid)
        if not s:
            continue  # its log is gone
        project = os.path.basename(s["cwd"].rstrip("/"))
        for m in e.get("marks", []):
            f = rule_file.get(m["rule"])
            if not f or m["verdict"] == "unknown":
                continue  # the rule was since renamed or removed, or the judge couldn't tell
            n += 1
            v = version_at(f["versions"], s["t"])
            moments.append({"id": f"m{n}", "sid": sid, "file": f["id"], "rule": m["rule"], "v": v, "e": epoch_of[m["rule"]][v - 1],
                            "verdict": "ok" if m["verdict"] == "pass" else "miss" if part[m["rule"]] == "load" else "x",
                            "when": day(s["t"]), "t": s["t"], "project": project, "line": m["line"], "you": m.get("you"), "claude": m.get("claude"),
                            "came_up": m.get("came_up"), "failed": m.get("failed")})
        for u in e.get("uncovered", []):
            n += 1
            topics[u["topic"]].setdefault(sid, {"id": f"g{n}", "sid": sid, "verdict": "x", "when": day(s["t"]), "t": s["t"], "project": project,
                                               "line": u["line"], "you": u.get("you"), "claude": None})
    by_rule = defaultdict(list)
    for m in moments:
        by_rule[m["rule"]].append(m)
    for f in files:
        for r in f["rules"]:
            r["status"] = status(by_rule[r["id"]], r["epochs"][-1]["e"])
        for v in f["versions"]:
            TEXTS[(f["id"], v["v"])] = v.pop("text")  # sent only when asked for
    gaps = [{"topic": t, "moments": sorted(ms.values(), key=lambda m: m["t"], reverse=True)} for t, ms in topics.items() if len(ms) >= 2]
    todo = jd.pending(sc, st)
    return {"files": files, "moments": sorted(moments, key=lambda m: m["t"], reverse=True), "gaps": sorted(gaps, key=lambda g: -len(g["moments"])),
            "active": rl.active_projects(sc), "judged": len([k for k in st["sessions"] if k in by_id]),
            "pending": {"count": len(todo), "batch": min(len(todo), jd.BATCH), "cost": round(min(len(todo), jd.BATCH) * jd.COST_PER_SESSION, 2)}}


def report(project=""):
    """What is failing, worst first, as text for a Claude Code conversation: regressions, then failing rules, then the
    problems nothing covers. `project` adds that project's group to the general ones."""
    sc = rd.scan()
    d = page_data(sc, jd.load_store())
    logs = {s["id"]: s["log"] for s in sc["sessions"]}
    show = lambda scope: scope in rl.GENERAL or scope == project  # noqa: E731
    out = [f"# Harness Doctor report ({d['judged']} sessions judged)", ""]
    rows = []
    for f in d["files"]:
        if not show(f["scope"]):
            continue
        for r in f["rules"]:
            if r["status"] in ("worse", "bad"):
                ms = [m for m in d["moments"] if m["rule"] == r["id"]]
                bad = [m for m in ms if m["e"] == r["epochs"][-1]["e"] and m["verdict"] != "ok"]
                rows.append((0 if r["status"] == "worse" else 1, -len(bad), f, r, ms, bad))
    for _, _, f, r, ms, bad in sorted(rows, key=lambda x: x[:2]):
        good = sum(1 for m in ms if m["e"] == r["epochs"][-1]["e"] and m["verdict"] == "ok")
        out += [f"## {STATUS[r['status']]}: {r['title']}", f"File: {f['path']} ({f['label']}" + (f", {f['scope']}" if f["scope"] else "") + ")",
                f"On the rule as it is written now: {len(bad)} bad, {good} good." + (" It was clean before its last edit." if r["status"] == "worse" else ""),
                "", "The rule as written:", *("> " + line for line in r["text"].splitlines()), ""]
        if r["status"] == "worse" and f["versions"][-1]["diff"]:
            out += ["The last edit (" + f["versions"][-1]["when"] + "):", "```diff", f["versions"][-1]["diff"], "```", ""]
        for m in bad:
            kind = "didn't load" if m["verdict"] == "miss" else "bad use"
            count = f" (broke it in {m['failed']} of {m['came_up']} moments)" if (m.get("came_up") or 0) > 1 and m.get("failed") else ""
            out += [f"- {m['when']}, {m['project']}, {kind}: {m['line']}{count}"]
            if m.get("you"):
                out += [f"  - you: {m['you']}"]
            if m.get("claude"):
                out += [f"  - Claude: {m['claude']}"]
            out += [f"  - session log: {logs.get(m['sid'], '?')}"]
        out += [""]
    gaps = [g for g in d["gaps"] if show(next(iter({m["project"] for m in g["moments"]})) if len({m["project"] for m in g["moments"]}) == 1 else "")]
    for g in gaps:
        out += [f"## Not covered yet: {g['topic']}", f"Seen in {len(g['moments'])} sessions; nothing written is about it."]
        for m in g["moments"]:
            out += [f"- {m['when']}, {m['project']}: {m['line']}"] + ([f"  - you: {m['you']}"] if m.get("you") else []) + [f"  - session log: {logs.get(m['sid'], '?')}"]
        out += [""]
    if not rows and not gaps:
        out += ["Nothing is failing in the sessions judged so far." if d["judged"] else "No sessions are judged yet. Open the page and press the judge button first."]
    return "\n".join(out)


CACHE = {"at": 0.0, "body": None}
LOCK = threading.Lock()


def data_json(fresh=False):
    with LOCK:
        if fresh or CACHE["body"] is None or time.time() - CACHE["at"] > 20:
            CACHE["body"] = json.dumps(page_data(rd.scan(), jd.load_store()), ensure_ascii=False).encode()
            CACHE["at"] = time.time()
        return CACHE["body"]


def judge_job():
    jd.run(rd.scan)
    CACHE["body"] = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the browser left before the answer was ready

    def local(self):  # refuse requests that didn't come to this address (DNS rebinding)
        return self.headers.get("Host") in (f"127.0.0.1:{PORT}", f"localhost:{PORT}")

    def do_GET(self):
        if not self.local():
            return self.reply(403, b"forbidden", "text/plain")
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as fh:
                return self.reply(200, fh.read(), "text/html; charset=utf-8")
        if path.startswith("/fonts/") and path.endswith(".woff2") and "/" not in path[7:] and ".." not in path:
            try:
                with open(os.path.join(HERE, "fonts", path[7:]), "rb") as fh:
                    return self.reply(200, fh.read(), "font/woff2")
            except OSError:
                return self.reply(404, b"not found", "text/plain")
        if path == "/api/text":  # one version of one file, for "Show source file"
            q = dict(x.split("=", 1) for x in self.path.partition("?")[2].split("&") if "=" in x)
            data_json()
            text = TEXTS.get((q.get("file", ""), int(q["v"]) if q.get("v", "").isdigit() else 0))
            return self.reply(200 if text is not None else 404, (text if text is not None else "This version's text couldn't be rebuilt from the logs.").encode(), "text/plain; charset=utf-8")
        if path == "/api/data":
            return self.reply(200, data_json(), "application/json")
        if path == "/api/status":
            return self.reply(200, json.dumps(jd.JOB).encode(), "application/json")
        self.reply(404, b"not found", "text/plain")

    def do_POST(self):
        # The custom header can't be sent cross-site without a CORS preflight, which this server never allows.
        if not self.local() or self.path != "/api/doctor" or self.headers.get("X-Harness-Doctor") != "1":
            return self.reply(403, b'{"ok":false,"message":"forbidden"}', "application/json")
        if jd.JOB["state"] == "running":
            return self.reply(200, b'{"ok":false,"message":"Already running."}', "application/json")
        jd.JOB.update(state="running", message="Starting…", pct=None)
        threading.Thread(target=judge_job, daemon=True).start()
        self.reply(200, b'{"ok":true}', "application/json")


def reload_on_change():
    """Restart this server when its own code changes. The page itself is read from disk on every load."""
    mine = [os.path.join(HERE, n) for n in ("harness_doctor.py", "reader.py", "rules.py", "judge.py")]
    stamp = lambda: [os.path.getmtime(m) if os.path.exists(m) else 0 for m in mine]  # noqa: E731
    seen = stamp()
    while True:
        time.sleep(1)
        if stamp() == seen:
            continue
        seen = stamp()
        try:
            for me in mine:
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
    global PORT
    ap = argparse.ArgumentParser(description="Harness Doctor: which of your written rules and facts hold up in real sessions, and which fail.")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--data", default=None, help="folder for its results (default: %(default)s)")
    ap.add_argument("--no-open", action="store_true", help="don't open the browser")
    ap.add_argument("--background", action="store_true", help="run detached; if one is already running, just open the page")
    ap.add_argument("--stop", action="store_true", help="stop a background server")
    ap.add_argument("--report", action="store_true", help="print what is failing, worst first, no server")
    ap.add_argument("--project", default="", help="with --report: also include this project's group")
    ap.add_argument("--dev", action="store_true", help="restart when this file changes")
    args = ap.parse_args()
    PORT = args.port
    if args.data:
        rd.DATA = os.path.expanduser(args.data)
    os.environ["HARNESS_DOCTOR_DATA"] = rd.DATA  # a background copy uses the same folder
    pid_file = rd.data_path("server.pid")

    if args.stop:
        try:
            os.kill(int(open(pid_file).read()), 15)
            os.remove(pid_file)
            print("Stopped.")
        except (OSError, ValueError):
            print("No background server to stop.")
        return
    if args.report:
        print(report(args.project))
        return
    if running():
        return open_page(args)
    if args.background:
        with open(rd.data_path("server.log"), "a") as log:
            child = subprocess.Popen([sys.executable, os.path.abspath(__file__), "--port", str(PORT), "--no-open"] + (["--dev"] if args.dev else []),
                                     stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
        with open(pid_file, "w") as fh:
            fh.write(str(child.pid))
        for _ in range(50):  # up to 10 seconds
            if running():
                return open_page(args)
            time.sleep(0.2)
        print(f"It didn't start; see {rd.data_path('server.log')}")
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
