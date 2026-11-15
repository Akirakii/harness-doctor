"""Harness Doctor's rules: every file the doctor judges, cut into the parts that get their own verdicts.

A rule is one part of a file: a numbered or bulleted item, or a section of prose. A file that only loads when something
sends Claude to it (a skill by its description, a note or a folder of the domain knowledge by its "read when" line in an
index) gets one more rule first, its loading line.
"""
import glob
import os
import re

import reader as rd

KNOW = os.path.join(rd.CLAUDE, "domain-knowledge")
ITEM = re.compile(r"^(?:\d+\.|[-*])\s+(.*)")
HEAD = re.compile(r"^(#{1,4})\s+(.+)")
MAX_RULES, MAX_TEXT = 30, 1200
SHORT = 8000  # a note up to this many characters is cut into facts; a longer doc is judged only on being read
GENERAL = ("", "Shared", "Every project")  # the scopes that hold in every project
IN_USE_DAYS = 7
ALWAYS = ("rules", "output style")  # the kinds that load in every session


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def title_of(first):
    """A rule's title: the bold text that starts it, or else its first sentence."""
    m = re.match(r"\*\*(.+?)\*\*", first)
    t = m.group(1) if m else re.split(r"(?<=[.:!?])\s", first, maxsplit=1)[0]
    return rd.one_line(t, 80).rstrip(".:,")


def cut_rules(text):
    """A file's rules, section by section (a section is the text under one heading).
    A section with list items at the left edge: each item is a rule, up to the next item.
    A section with only prose: the section is one rule, named by its heading (not the section under the file's title).
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


def indexed_items(know=None, text_of=None):
    """Everything an index line of the domain knowledge points at, a file or a folder:
    [{"path", "display", "kind": "note" | "folder" | "project", "scope", "domain", "when", "index": the index file}].
    Scope: "" (everything), "Shared", "Every project", or a project's name. A heading that names a folder
    (`game-patterns/`) sets where the rows under it live. text_of(index file) gives an index's text at another
    time than now (None: it wasn't there)."""
    know = know or KNOW
    out, seen = [], set()
    for idx in sorted(glob.glob(os.path.join(know, "**", "INDEX.md"), recursive=True)):
        folder = os.path.dirname(idx)
        rel = os.path.relpath(folder, know)
        base = folder
        for line in ((text_of or rd.read)(idx) or "").splitlines():
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
                        "domain": cells[1] if rel == "." and len(cells) == 4 else "", "when": cells[-1], "index": idx})
    return out


def when_in(index, text, path, know=None):
    """The "read when" an index's text gives for one note or folder, None when that text doesn't list it."""
    return next((it["when"] for it in indexed_items(know, lambda idx: text if idx == index else None) if it["path"] == path), None)


born = rd.born


def project_dirs(sc):
    """The folders Claude Code was started in, each a project by its folder name: {name: folder}."""
    return {os.path.basename(s["cwd"].rstrip("/")): s["cwd"] for s in sc["sessions"]}


def catalog(sc, s=None):
    """Every file the doctor judges, each cut into rules: as it was when session s started, or as it is now (s None).
    A rule's id is its file and its title, so it survives edits. Files that load on a trigger get a "load" rule first.
    A project's own CLAUDE.md, skills and output styles are listed for the project Claude Code was started in.
    -> [{"id", "name", "display", "label", "kind", "scope", "path", "trigger", "isdir",
         "rules": [{"id", "part": "load" | "body", "group", "title", "text"}]}]"""
    files = []

    def add(path, name, kind, scope, text, load=None, load_group=None, display=None, label=None, isdir=False, index=None):
        fid = slug(kind + "-" + (scope or "") + "-" + name)
        rules = []
        if load is not None:
            rules.append({"id": fid + "#load", "part": "load", "group": load_group, "title": rd.one_line(load, 110), "text": load})
        seen = set()
        for r in cut_rules(text):
            rid = fid + "#" + slug(r["title"])
            if rid in seen:
                continue  # two rules with one title: the first keeps the name
            seen.add(rid)
            rules.append({"id": rid, "part": "body", "group": r["group"] or ("What it says" if load is not None else ""), "title": r["title"], "text": r["text"]})
        files.append({"id": fid, "name": name, "display": display or (name if name.endswith(".md") else name + ".md"), "label": label or kind, "kind": kind,
                      "scope": scope, "path": path.replace(rd.HOME, "~", 1), "trigger": load is not None, "isdir": isdir, "index": index, "rules": rules})

    def then(path):  # the file's text when the session started, where the logs can tell; else today's
        row = rd.row(sc, path)
        if s and rd.since(row, born(path)) > s["t"]:
            return None  # written after this session started, or its text back then isn't known
        return rd.version_at(row, s["t"])[1] if (row and s) else rd.read(path)

    def own_files(folder, scope):  # the CLAUDE.md, output styles and skills of ~/.claude or of one project
        base = folder if scope == "" else os.path.join(folder, ".claude")
        md = os.path.join(folder, "CLAUDE.md")
        text = then(md) if os.path.isfile(md) else None
        if text:
            add(md, "CLAUDE.md", "rules", scope, text)
        for p in sorted(glob.glob(os.path.join(base, "output-styles", "*.md"))):
            text = then(p)
            if text:
                add(p, os.path.basename(p)[:-3], "output style", scope, text)
        for p in sorted(glob.glob(os.path.join(base, "skills", "*", "SKILL.md"))):
            text = then(p)
            if text and description(text):
                add(p, os.path.basename(os.path.dirname(p)), "skill", scope, text, load=description(text), load_group="When it loads")

    own_files(rd.CLAUDE, "")
    for name, folder in sorted(project_dirs(sc).items()):
        if os.path.isdir(folder) and (s is None or s["cwd"] == folder or s["cwd"].startswith(folder + "/")):
            own_files(folder, name)
    for it in indexed_items(text_of=then if s else None):  # a "read when" line is judged as its index had it back then
        if s and born(it["path"]) > s["t"]:
            continue  # made after this session, so the session can't be judged against it
        isdir = it["kind"] != "note"
        text = None if isdir else rd.read(it["path"])
        short = bool(text) and len(text) <= SHORT and it["path"].endswith(".md")  # only a short note is cut into facts
        label = "domain knowledge" + (" · " + it["domain"] if it["domain"] not in ("", "everything") else "")
        add(it["path"], re.sub(r"\.md$", "", it["display"].rstrip("/")) + ("-dir" if isdir else ""), "note", it["scope"], text if short else "",
            load="Read when: " + it["when"], load_group="When it's read", display=it["display"], label=label, isdir=isdir, index=it["index"])
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
            if os.path.dirname(r) in (rd.CLAUDE, os.path.join(rd.CLAUDE, "notes")):
                return True
        elif "/" + f["scope"] + "/" in r:
            return True
    return False


def projects_touched(log_text, projects):
    """The projects a session worked in: it made 2 or more file calls (read, edit, write) on a path with /<project>/ or
    /<project>-… in it. A project that is only named in a listing doesn't count."""
    paths = re.findall(r'"file_path"\s*:\s*"([^"]+)"', log_text)
    return {p for p in projects if sum(1 for x in paths if re.search(r"/" + re.escape(p) + r"[/-]", x)) >= 2}


def all_projects(sc):
    return ({it["scope"] for it in indexed_items()} - set(GENERAL)) | set(project_dirs(sc))


def active_projects(sc):
    """The projects in use: a session worked in them, or was started in them, in the last 7 days. (30 days, the
    doctor's window, still showed every project the user tried and dropped that month.)"""
    projects, since, out = all_projects(sc), rd.iso_ago(days=IN_USE_DAYS), set()
    dirs = project_dirs(sc)
    for s in sc["sessions"]:
        if s["end"] < since:
            continue
        out |= projects_touched(open(s["log"], errors="ignore").read(), projects - out)
        out |= {n for n, folder in dirs.items() if s["cwd"] == folder}
    return sorted(out)


def brief(inp):
    """The one argument of a tool call that says what it did."""
    for k in ("skill", "command", "file_path", "query", "url", "pattern", "description", "prompt"):
        if inp.get(k):
            return rd.one_line(str(inp[k]), 90)
    return ""


def transcript(path, per_msg=1500, cap=70000):
    """The session as text: your messages, Claude's replies, and one line per tool call (a rule about what Claude does,
    not what it says, can only be judged from those). Long sessions keep their start and end.
    Also what it loaded: the skill names, and the paths of the files it read.
    -> (text, {skill names}, {file paths read})"""
    import json
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
            text = rd.user_text(content).strip()
            cmd = re.search(r"<command-name>(.*?)</command-name>", text)
            if cmd:
                text = cmd.group(1)
                skills.add(text.lstrip("/"))
            elif not text or text.startswith(rd.NOISE):
                continue
            out.append("USER: " + rd.clip(text, per_msg))
        elif d.get("type") == "assistant" and isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and b.get("text", "").strip():
                    out.append("CLAUDE: " + rd.clip(b["text"], per_msg))
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


def in_play(sc, s, files, text, skills, reads):
    """What to send the judge for session s: every loading line, and the body only of the files that were loaded.
    A project's domain knowledge only counts in a session that worked in that project.
    -> ([{"id", "file", "kind", "part" | "group", "loaded", "text"}], [names of the trigger files that loaded])"""
    touched = projects_touched(open(s["log"], errors="ignore").read(), {f["scope"] for f in files} - set(GENERAL))
    sent, loaded = [], []
    for f in files:
        if f["kind"] == "note" and f["scope"] not in GENERAL and f["scope"] not in touched:
            continue
        on = not f["trigger"] or (f["name"] in skills if f["kind"] == "skill" else was_read(f, reads))
        if f["trigger"] and on:
            loaded.append(f["display"])
        for r in f["rules"]:
            if r["part"] == "load":
                sent.append({"id": r["id"], "file": f["display"], "kind": f["kind"], "part": "load", "loaded": on, "text": r["text"]})
            elif on:
                sent.append({"id": r["id"], "file": f["display"], "kind": f["kind"], "group": r["group"], "text": r["text"]})
    return sent, loaded
