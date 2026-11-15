#!/usr/bin/env python3
"""Put the last trial run's marks (proto/rule_judge.py) into a copy of the page mock: mock/real.html.

The page is mock/harness-history.html with its sample data swapped for the real files, rules and marks.
A file with no mark in the run is left out, and the page says how many were.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))
sys.path.insert(0, HERE)
import harness_doctor as hd  # noqa: E402
import rule_judge as rj  # noqa: E402

MOCK = os.path.join(HERE, "..", "mock")


def main():
    res = hd.load_json(os.path.join(hd.DATA, "proto-rule-judge.json"), {"sessions": []})
    if not res["sessions"]:
        sys.exit("No trial run found. Run proto/rule_judge.py first.")
    # The files and rules as the newest session saw them; older sessions' marks land on them by rule id.
    files = {}
    for s in res["sessions"]:
        for f in s["files"]:
            files.setdefault(f["id"], f)
    rule_file = {r["id"]: fid for fid, f in files.items() for r in f["rules"]}
    part = {r["id"]: r["part"] for f in files.values() for r in f["rules"]}
    made = {fid: rj.born(os.path.expanduser(f["path"])) if f["kind"] == "note" else "" for fid, f in files.items()}
    moments, gaps, lost, late, n = [], [], 0, 0, 0
    for s in res["sessions"]:
        a = s.get("answer") or {}
        proj, when = os.path.basename(s["cwd"]), s["date"][5:]
        for r in a.get("rules", []):
            if r.get("verdict") not in ("pass", "fail"):
                continue
            if r.get("id") not in rule_file:
                lost += 1
                continue
            if made[rule_file[r["id"]]][:10] > s["date"]:
                late += 1  # the note or folder was made after this session: the mark means nothing
                continue
            n += 1
            verdict = "ok" if r["verdict"] == "pass" else "miss" if part[r["id"]] == "load" else "x"
            moments.append({"id": f"m{n}", "sid": s["id"], "file": rule_file[r["id"]], "rule": r["id"], "v": 1, "verdict": verdict,
                            "when": when, "project": proj, "line": r.get("line") or "", "you": r.get("you"), "marks": [],
                            "claude": r.get("claude"), "cmarks": []})
        for u in a.get("uncovered", []):
            n += 1
            gaps.append({"topic": u.get("line") or "", "moments": [{"id": f"g{n}", "sid": s["id"], "verdict": "x", "when": when, "project": proj,
                                                                    "line": u.get("line") or "", "you": u.get("you"), "marks": [], "claude": None, "cmarks": []}]})
    marked = {m["file"] for m in moments}
    out = []
    for fid, f in files.items():
        rules = []
        for r in f["rules"]:
            title = r["title"]
            if r["part"] == "load" and f["kind"] == "skill":
                title = "Description"
            rules.append({"id": r["id"], "group": re.sub(r"\s*\(rules.*\)", "", r["group"] or ""), "title": title, "text": r["text"]})
        out.append({"id": fid, "name": f["name"], "kind": f["kind"], "scope": f["scope"], "path": f["path"], "rules": rules,
                    "display": f.get("display"), "label": ("domain knowledge" + ((f.get("label") or "").partition(" · ")[1] + (f.get("label") or "").partition(" · ")[2]).replace(" · everything", "")) if f["kind"] == "note" else None,
                    "versions": [{"v": 1, "when": "the trial run", "diff": "The trial run kept no versions: every session was judged against the file as it was then."}]})
    hidden = len(files) - len(marked)

    s = hd.read(os.path.join(MOCK, "harness-history.html"))
    a, b = s.index("  // ---------- sample data"), s.index("  // ---------- helpers")
    data = ("  // ---------- real marks from the trial run (proto/rule_judge.py) ----------\n"
            f"  const FILES = {json.dumps(out, ensure_ascii=False)};\n  const MOMENTS = {json.dumps(moments, ensure_ascii=False)};\n"
            f"  const GAPS = {json.dumps(gaps, ensure_ascii=False)};\n  const CHECKS = [];\n  const ACTIVE = {json.dumps(res.get('active', []))};\n\n").replace("</", "<\\/")
    s = s[:a] + data + s[b:]
    note = (f"Real marks: the trial judge on {len(res['sessions'])} of your sessions, against CLAUDE.md, the output style, your skills and your notes. "
            f"No versions yet. Pick a project on top to see its group.")
    s = re.sub(r'<div class="note">.*?</div>', lambda m: f'<div class="note">{note}</div>', s, count=1, flags=re.S)
    for old, new in [
        ("Judge 6 new sessions", "Judge new sessions"),
        ("went wrong in 2 or more sessions, and nothing you wrote is about it", "went wrong, and nothing you wrote is about it (each seen in one session so far)"),
        ('g.moments.length + " sessions"', 'g.moments.length + (g.moments.length === 1 ? " session" : " sessions")'),
        (".rtext{", ".rtext{white-space:pre-wrap;"),
    ]:
        assert old in s, old
        s = s.replace(old, new)
    with open(os.path.join(MOCK, "real.html"), "w") as fh:
        fh.write(s)
    print(f"{len(out)} files ({len(marked)} with marks), {len(moments)} marks, {len(gaps)} not covered, "
          f"{late} marks dropped (the note is newer than the session), {lost} on rules that no longer exist")
    print("in use:", res.get("active"))
    print("with marks:", ", ".join(sorted(f"{files[i]['scope'] or 'everything'}/{files[i].get('display') or files[i]['name']}" for i in marked)))


if __name__ == "__main__":
    main()
