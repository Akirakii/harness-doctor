"""Tests for the parts of Harness Doctor that don't need Claude or real session logs."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))
import harness_doctor as doc  # noqa: E402
import judge as jd  # noqa: E402
import reader as hd  # noqa: E402  the tests below were written against the reader when it lived in harness_doctor.py
import rules as rl  # noqa: E402

rd_read = hd.read


class RowOf(unittest.TestCase):
    def test_global_files(self):
        c = hd.CLAUDE
        self.assertEqual(hd.row_of(c + "/CLAUDE.md"), ("global", "rules", "CLAUDE.md"))
        self.assertEqual(hd.row_of(c + "/skills/remember/SKILL.md"), ("global", "skill", "remember"))
        self.assertEqual(hd.row_of(c + "/output-styles/terse.md"), ("global", "output style", "terse"))

    def test_project_files(self):
        self.assertEqual(hd.row_of("/work/app/CLAUDE.md"), ("/work/app", "rules", "CLAUDE.md"))
        self.assertEqual(hd.row_of("/work/app/.claude/skills/deploy/SKILL.md"), ("/work/app", "skill", "deploy"))

    def test_not_harness_files(self):
        self.assertIsNone(hd.row_of(hd.CLAUDE + "/settings.json"))
        self.assertIsNone(hd.row_of("/tmp/x/CLAUDE.md"))
        self.assertIsNone(hd.row_of("/work/app/README.md"))


class ShellTargets(unittest.TestCase):
    def test_redirect_and_cd(self):
        hits = hd.shell_targets("cd ~/.claude && cat > skills/x/SKILL.md", None)
        self.assertEqual([(p, whole) for p, _, whole in hits], [(hd.CLAUDE + "/skills/x/SKILL.md", True)])

    def test_append_is_not_a_whole_file(self):
        hits = hd.shell_targets("echo hi >> ~/.claude/CLAUDE.md", None)
        self.assertEqual([whole for _, _, whole in hits], [False])

    def test_reads_are_ignored(self):
        self.assertEqual(hd.shell_targets("grep foo ~/.claude/CLAUDE.md", None), [])


class Edits(unittest.TestCase):
    def test_apply_forward_and_back(self):
        edits = [("old line", "new line", False)]
        self.assertEqual(hd.apply("a\nold line\n", edits, True), "a\nnew line\n")
        self.assertEqual(hd.apply("a\nnew line\n", edits, False), "a\nold line\n")
        self.assertIsNone(hd.apply("nothing here", edits, True))

    def test_udiff_has_no_header(self):
        d = hd.udiff("a\nb\n", "a\nc\n")
        self.assertNotIn("---", d.splitlines()[0])
        self.assertIn("-b", d)
        self.assertIn("+c", d)
        self.assertIsNone(hd.udiff(None, "x"))


class OutsideChange(unittest.TestCase):
    def test_a_change_after_the_logs_is_a_version(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md") as f:
            ev = hd.outside_change(f.name, [], "2000-01-01")
            self.assertEqual(ev["kind"], "outside")
            self.assertTrue(ev["t"].endswith("Z"))

    def test_the_logged_change_itself_is_not(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md") as f:
            done = hd.now_iso()
            self.assertIsNone(hd.outside_change(f.name, [{"t": done, "done": done}], "2000-01-01"))

    def test_older_than_the_window_is_ignored(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md") as f:
            self.assertIsNone(hd.outside_change(f.name, [], "9999"))


class Heredocs(unittest.TestCase):
    def test_quoted_heredoc_after_cd(self):
        cmd = "cd ~/.claude/skills && cat > x/SKILL.md <<'EOF'\n---\nname: x\n$NOT_EXPANDED\nEOF\necho done"
        self.assertEqual(hd.heredoc_writes(cmd, None), [(hd.CLAUDE + "/skills/x/SKILL.md", "---\nname: x\n$NOT_EXPANDED\n")])

    def test_two_heredocs_in_one_command(self):
        cmd = "cat > ~/.claude/CLAUDE.md <<'A'\none\nA\ncat <<'B' > ~/.claude/skills/y/SKILL.md\ntwo\nB\n"
        self.assertEqual([t for _, t in hd.heredoc_writes(cmd, None)], ["one\n", "two\n"])

    def test_unquoted_heredoc_with_expansion_is_skipped(self):
        self.assertEqual(hd.heredoc_writes("cat > ~/.claude/CLAUDE.md <<EOF\n$HOME\nEOF\n", None), [])


class RuleTitles(unittest.TestCase):
    def test_version_at(self):
        row = {"id": "/nope", "base": "v0", "versions": [{"t": "2026-11-10", "text": "v1"}, {"t": "2026-11-12", "text": "v2"}]}
        self.assertEqual(hd.version_at(row, "2026-11-09"), (-1, "v0"))
        self.assertEqual(hd.version_at(row, "2026-11-11"), (0, "v1"))
        self.assertEqual(hd.version_at(row, "2026-11-13"), (1, "v2"))


class LiveSessions(unittest.TestCase):
    def test_only_running_processes_count(self):
        old = hd.CLAUDE
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "sessions"))
            for pid, sid in [(os.getpid(), "alive"), (2 ** 22 + 7, "gone")]:
                with open(os.path.join(d, "sessions", f"{pid}.json"), "w") as f:
                    json.dump({"pid": pid, "sessionId": sid}, f)
            hd.CLAUDE = d
            try:
                self.assertEqual(hd.live_sessions(), {"alive"})
            finally:
                hd.CLAUDE = old


class CutRules(unittest.TestCase):
    TEXT = """---
name: x
description: Do the thing. Use when asked.
---
# Title

Intro prose under the title is not a rule.

## How to think
1. **Short tags.** Keep labels short.
   A second line of the same rule.
2. **Find the problem first,** then fit it.
- A plain bullet with no bold start. More words here.

## Asking
Ask only when the wording is ambiguous.

```
- not a rule, it's inside a code block
```
"""

    def test_items_and_prose_sections(self):
        rules = rl.cut_rules(self.TEXT)
        self.assertEqual([r["title"] for r in rules], ["Short tags", "Find the problem first", "A plain bullet with no bold start", "Asking"])
        self.assertEqual(rules[0]["group"], "How to think")
        self.assertIn("A second line", rules[0]["text"])
        self.assertEqual(rules[3]["group"], "")  # a prose section is its own rule, named by its heading

    def test_description_and_slug(self):
        self.assertEqual(rl.description(self.TEXT), "Do the thing. Use when asked.")
        self.assertEqual(rl.slug("Don't open with \"Got it\""), "don-t-open-with-got-it")


class IndexedItems(unittest.TestCase):
    def setUp(self):
        self.k = tempfile.mkdtemp()
        for d in ("shared/game-patterns", "projects/moon/redo-race"):
            os.makedirs(os.path.join(self.k, d))
        w = lambda p, text: open(os.path.join(self.k, p), "w").write(text)  # noqa: E731
        w("note.md", "- a fact\n")
        w("shared/game-patterns/feel.md", "long doc")
        w("projects/projects.md", "x")
        w("projects/moon/design.md", "x")
        w("projects/moon/redo-race/ideas.md", "x")
        w("INDEX.md", "| Note | Domain | What | Read when |\n|---|---|---|---|\n| `note.md` | games | a note | doing games |\n| `gone.md` | games | missing | never |\n")
        w("shared/INDEX.md", "## `game-patterns/`: patterns\n| Doc | What | Read when |\n|---|---|---|\n| `feel.md` | feel | adding juice |\n")
        w("projects/INDEX.md", "| Note or folder | What | Read when |\n|---|---|---|\n| `projects.md` | where | starting |\n| `moon/` | the game | any work on Moon |\n")
        w("projects/moon/INDEX.md", "| Doc | What | Read when |\n|---|---|---|\n| `design.md` | rules | old versions |\n| `redo-race/` | now | any design work |\n")

    def test_files_and_folders_with_their_scope(self):
        items = {i["display"]: i for i in rl.indexed_items(self.k)}
        self.assertEqual(set(items), {"note.md", "feel.md", "projects.md", "moon/", "design.md", "redo-race/"})  # a missing file is skipped
        self.assertEqual((items["note.md"]["scope"], items["note.md"]["domain"], items["note.md"]["when"]), ("", "games", "doing games"))
        self.assertEqual(items["feel.md"]["scope"], "Shared")  # found through the heading that names its folder
        self.assertEqual(items["projects.md"]["scope"], "Every project")
        self.assertEqual((items["moon/"]["scope"], items["moon/"]["kind"]), ("moon", "project"))
        self.assertEqual((items["redo-race/"]["scope"], items["redo-race/"]["kind"]), ("moon", "folder"))
        self.assertEqual((items["design.md"]["scope"], items["design.md"]["kind"]), ("moon", "note"))


    def test_the_read_when_an_older_index_text_gave(self):
        idx, note = os.path.join(self.k, "INDEX.md"), os.path.join(self.k, "note.md")
        self.assertEqual(rl.when_in(idx, "| `note.md` | games | a note | old words |\n", note, self.k), "old words")
        self.assertIsNone(rl.when_in(idx, "| `other.md` | games | a note | x |\n", note, self.k))  # not listed back then

    def test_a_changed_read_when_is_a_new_version_of_the_note(self):
        idx, note = os.path.join(self.k, "INDEX.md"), os.path.join(self.k, "note.md")
        old = "| `note.md` | games | a note | old words |\n"
        f = {"id": "n", "path": note, "isdir": False, "index": idx, "kind": "note", "rules": [{"id": "n#load", "part": "load", "text": "Read when: doing games"}]}
        row = {"created": None, "base": "nothing listed\n", "clusters": [
            {"start": "2026-11-02T10:00", "end": "2026-11-02T10:00", "text": old, "diff": ""},
            {"start": "2026-11-03T10:00", "end": "2026-11-03T10:00", "text": old + "more\n", "diff": ""},  # another row changed
            {"start": "2026-11-05T10:00", "end": "2026-11-05T10:00", "text": rd_read(idx), "diff": ""}]}
        sc, know, rl.KNOW = {"rows": {idx: row, note: None}}, rl.KNOW, self.k
        try:
            f["versions"] = doc.versions_of(sc, f)
        finally:
            rl.KNOW = know
        self.assertEqual([v["load"] for v in f["versions"]], [None, "Read when: old words", "Read when: doing games"])
        of, spans = doc.rule_epochs(f)
        self.assertEqual(of["n#load"], [1, 2, 3])  # a mark from before the line was written isn't on today's wording


class KnownSince(unittest.TestCase):
    def test_since(self):
        edit = {"t": "2026-11-10T10:00:00.000Z", "kind": "edit"}
        self.assertEqual(hd.since({"created": "2026-11-09T00:00:00.000Z", "versions": [edit]}, "2026-11-12T00:00:00"), "2026-11-09T00:00:00.000Z")
        self.assertEqual(hd.since({"versions": []}, "2026-11-12T00:00:00"), "2026-11-12T00:00:00")  # made outside the logs
        self.assertEqual(hd.since({"versions": [{"t": "2026-11-12T00:00:00.100Z", "kind": "outside"}]}, "2026-11-12T00:00:00"), "2026-11-12T00:00:00")
        self.assertEqual(hd.since({"versions": [edit]}, "2026-11-08T00:00:00"), "2026-11-08T00:00:00")  # made by a script, edited later
        self.assertEqual(hd.since({"versions": [edit]}, "2026-11-10T10:00:01"), "")  # the disk only knows the last save
        self.assertEqual(hd.since(None, ""), "")


class WasRead(unittest.TestCase):
    def test_a_folder_is_read_when_a_file_in_it_is(self):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "task"))
        open(os.path.join(d, "task", "notes.md"), "w").write("x")
        f = {"path": os.path.join(d, "task"), "isdir": True, "scope": "moon"}
        self.assertTrue(rl.was_read(f, {os.path.join(d, "task", "notes.md")}))
        self.assertFalse(rl.was_read(f, {os.path.join(d, "other.md")}))

    def test_a_file_by_its_path_only(self):
        f = {"path": "/k/projects/moon/design.md", "isdir": False, "scope": "moon"}
        self.assertTrue(rl.was_read(f, {"/k/projects/moon/design.md"}))
        self.assertTrue(rl.was_read(f, {"/home/u/Playground/moon/docs/design.md"}))  # where it lived before the notes moved
        self.assertFalse(rl.was_read(f, {"/home/u/Playground/other/docs/design.md"}))  # another project's file of the same name

    def test_a_message_sent_while_claude_worked_is_in_the_transcript(self):
        log = os.path.join(tempfile.mkdtemp(), "s.jsonl")
        with open(log, "w") as fh:
            fh.write(json.dumps({"type": "user", "message": {"content": "drop on click"}}, separators=(",", ":")) + "\n")  # as Claude Code writes it, with no spaces
            fh.write(json.dumps({"type": "attachment", "attachment": {"type": "queued_command", "prompt": "also add stealing", "origin": {"kind": "human"}}}, separators=(",", ":")) + "\n")
            fh.write(json.dumps({"type": "attachment", "attachment": {"type": "queued_command", "prompt": "a task finished", "origin": {"kind": "task"}}}, separators=(",", ":")) + "\n")
        self.assertEqual(rl.transcript(log)[0], "USER: drop on click\nUSER: also add stealing")

    def test_projects_touched_needs_two_file_calls(self):
        log = '{"file_path":"/p/moon/a.gd"} {"file_path": "/p/moon-hands/b.gd"} {"file_path":"/p/sun/a.gd"} "ls /p/star/"'
        self.assertEqual(rl.projects_touched(log, {"moon", "sun", "star"}), {"moon"})


class Statuses(unittest.TestCase):
    M = staticmethod(lambda e, verdict: {"e": e, "verdict": verdict})

    def test_the_four_statuses(self):
        M = self.M
        self.assertEqual(doc.status([], 2), "none")
        self.assertEqual(doc.status([M(1, "x")], 2), "none")  # only marks on an older wording
        self.assertEqual(doc.status([M(2, "ok"), M(1, "x")], 2), "good")  # fixed reads as healthy
        self.assertEqual(doc.status([M(2, "x")], 2), "bad")
        self.assertEqual(doc.status([M(2, "x"), M(1, "x"), M(1, "ok")], 2), "bad")
        self.assertEqual(doc.status([M(2, "miss"), M(2, "ok"), M(1, "ok")], 2), "worse")  # clean before, a bad use now

    def test_a_rule_keeps_its_marks_when_another_rule_is_edited(self):
        f = {"id": "f", "kind": "rules", "rules": [{"id": "f#a", "part": "body", "text": ""}, {"id": "f#b", "part": "body", "text": ""}],
             "versions": [{"v": 1, "when": "", "text": "- **A.** one\n- **B.** one\n"}, {"v": 2, "when": "Nov 2", "text": "- **A.** one\n- **B.** two\n"},
                          {"v": 3, "when": "Nov 3", "text": None}]}  # a version whose text is unknown counts as unchanged
        of, spans = doc.rule_epochs(f)
        self.assertEqual(of["f#a"], [1, 1, 1])
        self.assertEqual(of["f#b"], [1, 2, 2])
        self.assertEqual(spans["f#b"], [{"e": 1, "when": ""}, {"e": 2, "when": "Nov 2"}])

    def test_version_at(self):
        vs = [{"v": 1, "t": ""}, {"v": 2, "t": "2026-11-02T10:00"}, {"v": 3, "t": "2026-11-05T10:00"}]
        self.assertEqual([doc.version_at(vs, t) for t in ("2026-11-01T00:00", "2026-11-03T00:00", "2026-11-09T00:00")], [1, 2, 3])


class JudgeAnswers(unittest.TestCase):
    def test_only_sent_rules_and_known_verdicts_are_kept(self):
        ids = {"f#a": "body", "f#load": "load"}
        out = jd.clean_marks([
            {"id": "f#a", "verdict": "pass", "came_up": 3, "failed": 0, "line": "did it"},
            {"id": "f#zzz", "verdict": "fail", "claude": "x"},          # a rule that wasn't sent
            {"id": "f#a", "verdict": "maybe"},                          # not a verdict
            {"id": "f#a", "verdict": "fail", "line": "no quote"},       # a fail it can't show
            {"id": "f#load", "verdict": "fail", "line": "never loaded"},  # a missed load has nothing to quote
            "junk"], ids)
        self.assertEqual([(m["rule"], m["verdict"]) for m in out], [("f#a", "pass"), ("f#load", "fail")])
        self.assertEqual(out[0]["came_up"], 3)

    def test_pending_is_new_or_grown_and_not_open(self):
        sc = {"sessions": [{"id": "new", "n": 5}, {"id": "short", "n": 2}, {"id": "same", "n": 5}, {"id": "grown", "n": 9}]}
        st = {"sessions": {"same": {"n": 5}, "grown": {"n": 5}}}
        live, hd.live_sessions = hd.live_sessions, lambda: set()
        try:
            self.assertEqual([s["id"] for s in jd.pending(sc, st)], ["new", "grown"])
        finally:
            hd.live_sessions = live

    def test_new_names_take_an_existing_name_and_chains_end_at_the_last(self):
        to = jd.renames([{"new": "b", "use": "a"}, {"new": "c", "use": "b"}, {"new": "a", "use": "c"},   # a is known: never renamed
                         {"new": "d", "use": "nowhere"}, {"new": "e", "use": "f"}, {"new": "f", "use": "e"}, "junk"],
                        {"b", "c", "d", "e", "f"}, {"a"})
        self.assertEqual(to, {"b": "a", "c": "a"})
        entries = [{"uncovered": [{"topic": "b", "line": "1"}, {"topic": "c", "line": "2"}, {"topic": "d", "line": "3"}]}, {}]
        jd.rename(entries, to)
        self.assertEqual([(u["topic"], u["line"]) for u in entries[0]["uncovered"]], [("a", "1"), ("d", "3")])

    def test_a_press_judges_every_session_and_tries_a_failed_one_again(self):
        sessions = [{"id": f"s{i}", "n": 5} for i in range(4)]
        calls, saved = [], []
        def judge(sc, s, topics):
            calls.append(s["id"])
            if s["id"] == "s1" and calls.count("s1") == 1:
                return None  # no answer the first time
            if s["id"] == "s3":
                return None  # never answers
            return {"at": "2026-11-19T10:00", "n": 5, "marks": [], "uncovered": [{"topic": "new " + s["id"], "line": "x"}]}
        def ask(prompt, **kw):
            return {"same": [{"new": "new s2", "use": "new s0"}]}, 0.0
        keep = (jd.judge_session, jd.ask, jd.load_store, hd.save_json, hd.live_sessions)
        jd.judge_session, jd.ask, jd.load_store, hd.live_sessions = judge, ask, lambda: {"sessions": {}}, lambda: set()
        hd.save_json = lambda path, st: saved.append(sorted(st["sessions"]))
        try:
            jd.run(lambda: {"sessions": sessions})
        finally:
            jd.judge_session, jd.ask, jd.load_store, hd.save_json, hd.live_sessions = keep
        self.assertEqual(jd.JOB["state"], "done", jd.JOB["message"])
        self.assertEqual(sorted(calls), ["s0", "s1", "s1", "s2", "s3", "s3"])
        self.assertEqual(saved[-1], ["s0", "s1", "s2"])
        self.assertIn("Judged 3 sessions. 1 got no answer", jd.JOB["message"])


class TurnedOff(unittest.TestCase):
    """A wrong mark can be turned off, with a reason, and turned back on; the judge reads the user's own rules."""

    def setUp(self):
        self.keep, self.tmp = hd.DATA, tempfile.TemporaryDirectory()
        hd.DATA = self.tmp.name
        self.files = [{"rules": [{"id": "f#a", "title": "Stay short"}, {"id": "g#a", "title": "Steps"}, {"id": "h#a", "title": "Steps"}]}]
        self.st = {"sessions": {"abc123": {"at": "T1", "marks": [{"rule": "f#a", "verdict": "fail", "claude": "a long reply"},
                                                                  {"rule": "g#a", "verdict": "fail", "claude": "x"}, {"rule": "h#a", "verdict": "fail", "claude": "y"}],
                                           "uncovered": [{"topic": "pictures not looked at", "line": "l"}]}}}

    def tearDown(self):
        hd.DATA = self.keep
        self.tmp.cleanup()

    def test_off_needs_a_reason_and_on_undoes_it(self):
        self.assertIn("--why", jd.turn_off(self.st, self.files, "abc", "stay short", ""))
        self.assertEqual(jd.load_off(), {})
        self.assertIn("Turned off: f#a", jd.turn_off(self.st, self.files, "abc", "Stay short", "the user asked for detail"))
        self.assertEqual(jd.load_off()["abc123 f#a"]["why"], "the user asked for detail")
        self.assertIn("Turned back on", jd.turn_off(self.st, self.files, "abc", "f#a", "", restore=True))
        self.assertEqual(jd.load_off(), {})

    def test_what_cant_be_found_says_why(self):
        self.assertIn("No judged session", jd.turn_off(self.st, self.files, "zzz", "f#a", "w"))
        self.assertIn("More than one rule", jd.turn_off(self.st, self.files, "abc", "Steps", "w"))  # two rules share the title
        self.assertIn("no bad mark", jd.turn_off(self.st, self.files, "abc", "nothing", "w"))

    def test_a_problem_nothing_covers_can_be_turned_off_too(self):
        self.assertIn("gap:pictures not looked at", jd.turn_off(self.st, self.files, "abc", "Pictures not looked at", "one-off"))

    def test_it_holds_for_the_same_judging_or_the_same_words(self):
        jd.turn_off(self.st, self.files, "abc", "f#a", "w")
        self.st["sessions"]["abc123"]["marks"] = []  # judged again without that mark: the old turn-off can still be taken out
        self.assertIn("Turned back on", jd.turn_off(self.st, self.files, "abc", "f#a", "", restore=True))
        self.st["sessions"]["abc123"]["marks"] = [{"rule": "f#a", "verdict": "fail", "claude": "a long reply"}]
        jd.turn_off(self.st, self.files, "abc", "f#a", "w")
        off = jd.load_off()
        self.assertTrue(jd.is_off(off, "abc123", "f#a", {"at": "T1"}, "other words"))
        self.assertTrue(jd.is_off(off, "abc123", "f#a", {"at": "T2"}, "a long reply"))   # judged again, the same words quoted
        self.assertFalse(jd.is_off(off, "abc123", "f#a", {"at": "T2"}, "a new reply"))   # judged again, a new moment

    def test_the_judge_is_told_the_users_rules(self):
        self.assertEqual(jd.instructions(), jd.INSTRUCTIONS)  # no file, nothing added
        with open(hd.judge_rules_path(), "w") as fh:
            fh.write("# Rules for the judge\n\nNothing yet.\n")
        self.assertEqual(jd.instructions(), jd.INSTRUCTIONS)  # a file with no rule in it adds nothing
        with open(hd.judge_rules_path(), "w") as fh:
            fh.write("- **A setup line names its file.** That isn't detail.\n")
        self.assertIn("A setup line names its file", jd.instructions())
        self.assertEqual(hd.row_of(hd.judge_rules_path()), ("global", "judge", "judge rules"))

    def test_the_judge_card_and_its_count_starting_again(self):
        card = doc.judge_card()
        self.assertEqual(card["rules"][0]["id"], doc.WRONG)
        self.assertIn("Built into the plugin", {r["group"] for r in card["rules"]})
        card["versions"] = [{"v": 1, "when": "", "text": ""}, {"v": 2, "when": "Nov 19", "text": "- **A.** one\n"}]
        of, _ = doc.rule_epochs(card)
        self.assertEqual(of[doc.WRONG], [1, 2])  # an edit of the judge's rules starts the count of wrong marks again


class OwnSkills(unittest.TestCase):
    def test_the_doctors_own_skill_keeps_each_text_it_has_seen(self):
        keep, tmp = hd.DATA, tempfile.TemporaryDirectory()
        hd.DATA = tmp.name
        try:
            p = os.path.join(tmp.name, "SKILL.md")
            with open(p, "w") as fh:
                fh.write("one")
            self.assertEqual(hd.own_skill_row(p, "fix")["versions"], [])
            with open(p, "w") as fh:
                fh.write("two")
            row = hd.own_skill_row(p, "fix")
            self.assertEqual((row["base"], [v["text"] for v in row["versions"]]), ("one", ["two"]))
            self.assertEqual(len(hd.own_skill_row(p, "fix")["versions"]), 1)  # seen again unchanged: no new version
            hd.READ_ONLY = True
            with open(p, "w") as fh:
                fh.write("three")
            hd.own_skill_row(p, "fix")  # a report reads it and records nothing
            hd.READ_ONLY = False
            with open(p, "w") as fh:
                fh.write("two")
            self.assertEqual(len(hd.own_skill_row(p, "fix")["versions"]), 1)
            self.assertEqual(hd.own_skill(os.path.join(hd.PLUGIN, "skills", "fix", "SKILL.md")), "fix")
            self.assertIsNone(hd.own_skill(hd.CLAUDE + "/skills/fix/SKILL.md"))
        finally:
            hd.DATA = keep
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
