#!/usr/bin/env python3
"""Trial run of the per-rule judge (docs/design-2.0.md, "The judge").

Cuts CLAUDE.md, the output style, every skill and every indexed note into rules, takes a few recent sessions, and asks
Claude one narrow question per rule and session: did the rule's moment come up, and was it followed? Prints the marks
and what the run cost. Nothing here touches the page or its saved results.

A file that loads on a trigger (a skill, a note) is sent as its one loading line (the description, the "read when");
its body is only sent for a session that loaded it.

Run:  python3 proto/rule_judge.py --sessions 10            judge 10 recent sessions (calls Claude, one run each)
      python3 proto/rule_judge.py --sessions 10 --dry      only show what would be sent, and how big it is
      python3 proto/rule_judge.py --report                 print the last run again
      python3 proto/real_page.py                           put the last run's marks into a copy of the page mock
"""
import argparse
import concurrent.futures
import glob
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))
import harness_doctor as hd  # noqa: E402  the reader the page already uses

OUT = os.path.join(hd.DATA, "proto-rule-judge.json")
KNOW = os.path.join(hd.CLAUDE, "domain-knowledge")
ITEM = re.compile(r"^(?:\d+\.|[-*])\s+(.*)")
HEAD = re.compile(r"^(#{1,4})\s+(.+)")
MAX_RULES, MAX_TEXT = 30, 1200


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def title_of(first):
    """A rule's title: the bold text that starts it, or else its first sentence."""
    m = re.match(r"\*\*(.+?)\*\*", first)
    t = m.group(1) if m else re.split(r"(?<=[.:!?])\s", first, maxsplit=1)[0]
    return hd.one_line(t, 80).rstrip(".:,")


def cut_rules(text):
    """A file's rules, section by section (a section is the text under one heading).
    A section with list items at the left edge: each item is a rule, up to the next item.
    A section with only prose: the section is one rule, named by its heading (not the file's title section).
    -> [{"title", "group" (the heading above it), "text"}]"""
    text = re.sub(r"^---\n.*?\n---\n", "", text or "", count=1, flags=re.S)
    sections, cur, fence = [], {"level": 0, "head": "", "lines": []}, False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            fence = not fence
        h = None if fence else HEAD.match(line)
        if h:
            sections.append(cur)
            cur = {"level": len(h.group(1)), "head": h.group(2).strip(), "lines": []}
        else:
            cur["lines"].append((line, fence))
    sections.append(cur)
    rules = []
    for sec in sections:
        items, prose, item = [], [], None
        for line, fence in sec["lines"]:
            m = None if fence else ITEM.match(line)
            if m:
                item = {"title": title_of(m.group(1)), "group": sec["head"] if sec["level"] > 1 else "", "text": line.strip()}
                items.append(item)
            elif item is not None:
                if line.strip():
                    item["text"] += "\n" + line.strip()
            elif line.strip():
                prose.append(line.strip())
        if items:
            rules += items
        elif prose and sec["level"] > 1:
            rules.append({"title": sec["head"].rstrip(".:"), "group": "", "text": "\n".join(prose)})
    for r in rules:
        r["text"] = r["text"][:MAX_TEXT]
    return rules[:MAX_RULES]


def description(text):
    fm = re.match(r"---\s*\n(.*?)\n---", text or "", re.S)
    m = re.search(r"^description:\s*(.+)$", fm.group(1), re.M) if fm else None
    return m.group(1).strip() if m else ""


SHORT = 8000  # a note up to this many characters is cut into facts; a longer doc is judged only on being read
GENERAL = ("", "Shared", "Every project")


def indexed_items():
    """Everything an index line points at, a file or a folder:
    [{"path", "display", "kind": "note" | "folder" | "project", "scope", "domain", "when"}].
    Scope: "" (everything), "Shared", "Every project", or a project's name. A heading that names a folder (`game-patterns/`)
    sets where the rows under it live."""
    out, seen = [], set()
    for idx in sorted(glob.glob(os.path.join(KNOW, "**", "INDEX.md"), recursive=True)):
        folder = os.path.dirname(idx)
        rel = os.path.relpath(folder, KNOW)
        base = folder
        for line in (hd.read(idx) or "").splitlines():
            h = HEAD.match(line)
            if h:
                sub = [t for t in re.findall(r"`([^`]+)`", h.group(2)) if t.endswith("/") and os.path.isdir(os.path.join(folder, t))]
                base = os.path.join(folder, sub[0].rstrip("/")) if sub else folder
                continue
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            names = re.findall(r"`([^`]+)`", cells[0]) if cells else []
            if len(cells) < 2 or not names or names[0].startswith("."):
                continue
            path = os.path.normpath(os.path.join(base, names[0]))
            isdir = os.path.isdir(path)
            if path in seen or not (isdir or os.path.isfile(path)) or os.path.basename(path) == "INDEX.md":
                continue
            seen.add(path)
            if rel == ".":
                scope = ""
            elif rel == "shared":
                scope = "Shared"
            elif rel == "projects":
                scope = os.path.basename(path) if isdir else "Every project"
            else:
                scope = rel.split(os.sep)[1] if rel.startswith("projects" + os.sep) else rel
            kind = "project" if isdir and rel == "projects" else "folder" if isdir else "note"
            out.append({"path": path, "display": os.path.basename(path) + ("/" if isdir else ""), "kind": kind, "scope": scope,
                        "domain": cells[1] if rel == "." and len(cells) == 4 else "", "when": cells[-1]})
    return out


def born(path):
    """When a file or folder was made, as an ISO time ("" when the disk doesn't say). A move keeps it; a copy resets it."""
    try:
        t = int(subprocess.run(["stat", "-c", "%W", path], capture_output=True, text=True).stdout.strip() or 0)
    except (OSError, ValueError):
        t = 0
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) if t > 0 else ""


def projects_touched(log_text, projects):
    """The projects a session worked in: it made 2 or more file calls (read, edit, write) on a path with /<project>/ or
    /<project>-… in it. A project that is only named in a listing doesn't count."""
    paths = re.findall(r'"file_path"\s*:\s*"([^"]+)"', log_text)
    return {p for p in projects if sum(1 for x in paths if re.search(r"/" + re.escape(p) + r"[/-]", x)) >= 2}


def catalog(d, s):
    """Every file the doctor judges for session s, each cut into rules. A rule's id is its file and its title, so it
    survives edits. Files that load on a trigger get a "load" rule first.
    -> [{"id", "name", "kind", "scope", "path", "trigger": bool, "rules": [{"id", "part", "group", "title", "text"}]}]"""
    rows, files = {r["id"]: r for r in d["rows"]}, []

    def add(path, name, kind, scope, text, load=None, load_group=None, **more):
        fid = slug(kind + "-" + (scope or "") + "-" + name)
        rules = []
        if load is not None:
            rules.append({"id": fid + "#load", "part": "load", "group": load_group, "title": hd.one_line(load, 110), "text": load})
        for r in cut_rules(text):
            rules.append({"id": fid + "#" + slug(r["title"]), "part": "body", "group": r["group"] or ("What it says" if load is not None else ""), "title": r["title"], "text": r["text"]})
        files.append({"id": fid, "name": name, "kind": kind, "scope": scope, "path": path.replace(hd.HOME, "~", 1), "trigger": load is not None, "rules": rules, **more})

    def then(path):  # the file's text when the session started, where the logs can tell; else today's
        row = rows.get(path)
        if row and row.get("created") and row["created"] > s["t"]:
            return None  # written after this session
        return hd.version_at(row, s["t"])[1] if row else hd.read(path)

    text = then(os.path.join(hd.CLAUDE, "CLAUDE.md"))
    if text:
        add(os.path.join(hd.CLAUDE, "CLAUDE.md"), "CLAUDE.md", "rules", "", text)
    for p in sorted(glob.glob(os.path.join(hd.CLAUDE, "output-styles", "*.md"))):
        text = then(p)
        if text:
            add(p, os.path.basename(p)[:-3], "output style", "", text)
    for p in sorted(glob.glob(os.path.join(hd.CLAUDE, "skills", "*", "SKILL.md"))):
        text = then(p)
        if text and description(text):
            add(p, os.path.basename(os.path.dirname(p)), "skill", "", text, load=description(text), load_group="When it loads")
    for it in indexed_items():
        if born(it["path"]) > s["t"]:
            continue  # made after this session, so the session can't be judged against it
        isdir = it["kind"] != "note"
        text = None if isdir else hd.read(it["path"])
        short = bool(text) and len(text) <= SHORT and it["path"].endswith(".md")  # only a short note is cut into facts
        label = "domain knowledge" + (" · " + it["domain"] if it["domain"] not in ("", "everything") else "")  # one name for a note, a doc or a folder
        add(it["path"], re.sub(r"\.md$", "", it["display"].rstrip("/")) + ("-dir" if isdir else ""), "note", it["scope"], text if short else "",
            load="Read when: " + it["when"], load_group="When it's read", display=it["display"], label=label, isdir=isdir)
    return files


INSIDE = {}


def was_read(f, reads):
    """Was this note or folder read in the session? A file: by its path today, or by its name under where it lived
    before the notes moved (~/.claude and ~/.claude/notes for the general ones, the project's own folders for a
    project's). A folder: any file in it was read, by path or, for the old place, by name inside the project."""
    path = os.path.expanduser(f["path"])
    if f.get("isdir"):
        if path not in INSIDE:
            INSIDE[path] = {n for _, _, names in os.walk(path) for n in names if n != "INDEX.md"}
        for r in reads:
            if r.startswith(path + os.sep):
                return True
            if "/" + f["scope"] in r and KNOW not in r and os.path.basename(r) in INSIDE[path] and os.path.basename(r) not in ("CLAUDE.md", "README.md"):
                return True
        return False
    base = os.path.basename(path)
    for r in reads:
        if r == path:
            return True
        if os.path.basename(r) != base:
            continue
        if f["scope"] in GENERAL:
            if os.path.dirname(r) in (hd.CLAUDE, os.path.join(hd.CLAUDE, "notes")):
                return True
        elif "/" + f["scope"] + "/" in r:
            return True
    return False


def brief(inp):
    """The one argument of a tool call that says what it did."""
    for k in ("skill", "command", "file_path", "query", "url", "pattern", "description", "prompt"):
        if inp.get(k):
            return hd.one_line(str(inp[k]), 90)
    return ""


def transcript(path, per_msg=1500, cap=70000):
    """The session as text: your messages, Claude's replies, and one line per tool call (a rule about what Claude does,
    not what it says, can only be judged from those). Long sessions keep their start and end.
    Also what it loaded: the skill names, and the paths of the files it read.
    -> (text, {skill names}, {file paths read})"""
    out, skills, reads = [], set(), set()
    for line in open(path, errors="ignore"):
        if '"type":"user"' not in line and '"type":"assistant"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        content = (d.get("message") or {}).get("content")
        if d.get("type") == "user" and not d.get("isMeta"):
            text = hd.user_text(content).strip()
            cmd = re.search(r"<command-name>(.*?)</command-name>", text)
            if cmd:
                text = cmd.group(1)
                skills.add(text.lstrip("/"))
            elif not text or text.startswith(hd.NOISE):
                continue
            out.append("USER: " + hd.clip(text, per_msg))
        elif d.get("type") == "assistant" and isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and b.get("text", "").strip():
                    out.append("CLAUDE: " + hd.clip(b["text"], per_msg))
                elif b.get("type") == "tool_use":
                    inp = b.get("input") or {}
                    if b.get("name") == "Skill" and inp.get("skill"):
                        skills.add(str(inp["skill"]).lstrip("/"))
                    if b.get("name") == "Read" and inp.get("file_path"):
                        reads.add(os.path.normpath(os.path.expanduser(str(inp["file_path"]))))
                    out.append(f"  [tool {b.get('name')}: {brief(inp)}]")
    text = "\n".join(out)
    if len(text) > cap:
        text = text[: cap // 2] + "\n… (the middle of the session is cut) …\n" + text[-cap // 2:]
    return text, skills, reads


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
- Report each rule at most once per session: the first moment it failed, or else one moment it passed.

Some files only load when something sends Claude to them: a skill loads by its description, a note is read by its
"read when" line. Each such file has a rule with part "load" and a `loaded` flag for this session.
- For a "load" rule: "pass" means its moment came up and the file was loaded for it; "fail" means its moment came up
  and the file was not loaded (or only after the user had to ask); leave it out when its moment never came up.
- A file that wasn't loaded has no other rules here, so nothing else of it can be judged.
- A fact in a note counts as a rule: "pass" when Claude used it right, "fail" when Claude acted against it or the fact
  proved wrong.

Answer with only this JSON:
{"rules": [{"id": "<rule id>", "verdict": "pass" | "fail" | "unknown", "line": "<what happened, under 15 words>",
"claude": "<Claude's words or the tool call, copied exactly, under 200 characters, or null>",
"you": "<the user's words at that moment, copied exactly, under 200 characters, or null>"}],
"uncovered": [{"line": "<something that clearly went wrong and that no rule is about, under 15 words>",
"you": "<the user's words, or null>"}]}"""


def ask(prompt, model):
    """One headless Claude run with no tools. -> (the JSON it answered or None, cost in dollars, usage, seconds)"""
    t0 = time.time()
    r = subprocess.run(["claude", "-p", "Check the session in the input against the rules, following the instructions in it.",
                        "--model", model, "--tools", "", "--no-session-persistence", "--setting-sources", "project",
                        "--settings", '{"autoMemoryEnabled": false}', "--output-format", "json"],
                       input=prompt, capture_output=True, text=True, timeout=900, cwd=HERE)
    try:
        j = json.loads(r.stdout)
    except ValueError:
        return None, 0.0, {}, time.time() - t0
    m = re.search(r"\{.*\}", j.get("result", "") or "", re.S)
    try:
        ans = json.loads(m.group(0)) if m else None
    except ValueError:
        ans = None
    return ans, float(j.get("total_cost_usd") or 0), j.get("usage") or {}, time.time() - t0


IN_USE_DAYS = 7


def active_projects(d):
    """The projects in use: a session worked in them in the last 7 days. (30 days, the doctor's window, still showed
    every project the user tried and dropped that month.)"""
    projects = {it["scope"] for it in indexed_items()} - set(GENERAL)
    since = hd.iso_ago(days=IN_USE_DAYS)
    out = set()
    for s in d["sessions"]:
        if s["end"] < since:
            continue
        for log in glob.glob(os.path.join(hd.CLAUDE, "projects", "*", s["id"] + ".jsonl")):
            out |= projects_touched(open(log, errors="ignore").read(), projects - out)
    return sorted(out)


def pick_sessions(d, n):
    """Recent finished sessions with enough back-and-forth to judge, and not huge."""
    live = hd.live_sessions()
    ok = [s for s in d["sessions"] if s["id"] not in live and 5 <= s["n"] <= 80]
    return sorted(ok, key=lambda s: s["t"], reverse=True)[:n]


def report(res):
    marks = {"pass": "✓", "fail": "✕", "unknown": "?"}
    per_rule, cost, secs = {}, 0.0, 0.0
    for s in res["sessions"]:
        cost += s["cost"]
        secs += s["seconds"]
        rules = {r["id"]: (f, r) for f in s["files"] for r in f["rules"]}
        print(f"\n── {s['date']}  {os.path.basename(s['cwd'])}  ({s['n']} messages, {s['chars'] // 1000}k chars sent, ${s['cost']:.3f})  {s['title'][:70]}")
        print(f"   loaded: {', '.join(sorted(s['loaded'])) or 'nothing on a trigger'}")
        if s["answer"] is None:
            print("   (no answer came back)")
            continue
        for r in s["answer"].get("rules", []):
            if r.get("id") not in rules:
                continue
            f, rule = rules[r["id"]]
            name = f"{f['name']}: {'(load) ' if rule['part'] == 'load' else ''}{rule['title']}"
            per_rule.setdefault(r["id"], {"name": name, "pass": 0, "fail": 0, "unknown": 0})[r.get("verdict") if r.get("verdict") in marks else "unknown"] += 1
            print(f"   {marks.get(r.get('verdict'), '?')} {name[:64]:64}  {r.get('line', '')}")
            if r.get("verdict") == "fail":
                if r.get("you"):
                    print(f"       you:    {hd.one_line(r['you'], 150)}")
                if r.get("claude"):
                    print(f"       claude: {hd.one_line(r['claude'], 150)}")
        for u in s["answer"].get("uncovered", []):
            print(f"   ○ not covered: {u.get('line', '')}")
    print("\n── per rule, over these sessions")
    for rid, c in sorted(per_rule.items(), key=lambda kv: (-kv[1]["fail"], -kv[1]["pass"])):
        print(f"   {c['fail']} ✕  {c['pass']} ✓  {c['unknown']} ?   {c['name'][:90]}")
    print(f"\n{len(per_rule)} rules came up at least once in {len(res['sessions'])} sessions.")
    print(f"Cost: ${cost:.2f} in all, ${cost / max(1, len(res['sessions'])):.3f} per session, {secs:.0f} seconds of Claude time.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sessions", type=int, default=10)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--dry", action="store_true", help="show what would be sent, call nothing")
    ap.add_argument("--report", action="store_true", help="print the last run again")
    args = ap.parse_args()
    if args.report:
        return report(hd.load_json(OUT, {"sessions": []}))

    d = hd.build()
    jobs = []
    for s in pick_sessions(d, args.sessions):
        logs = glob.glob(os.path.join(hd.CLAUDE, "projects", "*", s["id"] + ".jsonl"))
        if not logs:
            continue
        text, skills, reads = transcript(logs[0])
        files, sent, loaded = catalog(d, s), [], []
        touched = projects_touched(open(logs[0], errors="ignore").read(), {f["scope"] for f in files} - set(GENERAL))
        for f in files:
            if f["scope"] not in GENERAL and f["scope"] not in touched:
                continue  # a project's notes and folders only count in a session that touched that project
            on = not f["trigger"] or (f["name"] in skills if f["kind"] == "skill" else was_read(f, reads))
            if f["trigger"] and on:
                loaded.append(f.get("display") or f["name"])
            for r in f["rules"]:
                if r["part"] == "load":
                    sent.append({"id": r["id"], "file": f["name"], "kind": f["kind"], "part": "load", "loaded": on, "text": r["text"]})
                elif on:
                    sent.append({"id": r["id"], "file": f["name"], "kind": f["kind"], "group": r["group"], "text": r["text"]})
        prompt = INSTRUCTIONS + "\n\n## The rules\n" + json.dumps({"rules": sent}, ensure_ascii=False) + "\n\n## The session\n" + text
        jobs.append((s, files, loaded, len(sent), prompt, sorted(touched)))
    if args.dry:
        for s, files, loaded, n, prompt, touched in jobs:
            print(f"{s['t'][:10]}  {s['n']:3} messages  {n} rules sent  {len(prompt) // 1000}k chars  projects: {', '.join(touched) or '-'}  loaded: {', '.join(loaded) or '-'}")
        print(f"{len(jobs)} sessions, {sum(len(j[4]) for j in jobs) // 1000}k chars in all (about {sum(len(j[4]) for j in jobs) // 4000}k tokens)")
        return

    def run(job):
        s, files, loaded, n, prompt, touched = job
        ans, cost, usage, secs = ask(prompt, args.model)
        print(f"  judged {s['t'][:10]} {os.path.basename(s['cwd'])} (${cost:.3f})", flush=True)
        return {"id": s["id"], "date": s["t"][:10], "cwd": s["cwd"], "n": s["n"], "title": s["title"], "chars": len(prompt),
                "files": files, "loaded": loaded, "touched": touched, "answer": ans, "cost": cost, "usage": usage, "seconds": secs}

    with concurrent.futures.ThreadPoolExecutor(3) as pool:
        res = {"model": args.model, "active": active_projects(d), "sessions": list(pool.map(run, jobs))}
    hd.save_json(OUT, res)
    report(res)


if __name__ == "__main__":
    main()
