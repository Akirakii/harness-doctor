"""Tests for the parts of harness_doctor.py that don't need Claude or real session logs."""
import importlib.util
import json
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("harness_doctor", os.path.join(HERE, "..", "app", "harness_doctor.py"))
hd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hd)


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


class Versions(unittest.TestCase):
    def row(self):
        return {"scope": "global", "kind": "skill", "name": "s", "base": "v0", "clusters": [
            {"session": "a", "start": "2026-01-01T00:00", "end": "2026-01-01T00:10", "edits": 1, "new": False, "text": "v1", "diff": "", "what": ""},
            {"session": "b", "start": "2026-01-02T00:00", "end": "2026-01-02T00:10", "edits": 2, "new": False, "text": "v2", "diff": "", "what": ""},
        ]}

    def session(self, t, n=5):
        return {"id": "s1", "cwd": "/x", "n": n, "t": t, "skills": [{"name": "s", "t": t}]}

    def test_unused_version_folds_into_the_next(self):
        r = self.row()
        hd.merge_unused(r, [], {})
        self.assertEqual(len(r["clusters"]), 1)
        self.assertEqual(r["clusters"][0]["edits"], 3)
        self.assertEqual(r["clusters"][0]["start"], "2026-01-01T00:00")

    def test_marked_version_stays(self):  # a skill loaded in a judged session gets a mark
        r = self.row()
        hd.merge_unused(r, [self.session("2026-01-01T12:00")], {"s1": {"files": []}})
        self.assertEqual(len(r["clusters"]), 2)

    def test_used_but_unjudged_version_folds(self):
        r = self.row()
        hd.merge_unused(r, [self.session("2026-01-01T12:00")], {})
        self.assertEqual(len(r["clusters"]), 1)

    def test_short_sessions_dont_count(self):
        r = self.row()
        hd.merge_unused(r, [self.session("2026-01-01T12:00", n=1)], {"s1": {"files": []}})
        self.assertEqual(len(r["clusters"]), 1)


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
    def test_headings_and_bold_rule_titles(self):
        text = "# How Claude works\n## Output\n21. **Replies are short.**\n   detail\n- **Never pad.** more\nplain line\n"
        self.assertEqual(hd.rule_titles(text), "How Claude works · Output · Replies are short · Never pad")

    def test_about_uses_the_version_text(self):
        row = {"id": "/nope/CLAUDE.md", "scope": "global", "kind": "rules"}
        self.assertEqual(hd.about(row, "## Replies\n"), "rules loaded in every session: Replies")
        self.assertEqual(hd.about(row, "---\ndescription: Short replies.\n---\n# X"), "Short replies.")

    def test_version_at(self):
        row = {"id": "/nope", "base": "v0", "versions": [{"t": "2026-11-10", "text": "v1"}, {"t": "2026-11-12", "text": "v2"}]}
        self.assertEqual(hd.version_at(row, "2026-11-09"), (-1, "v0"))
        self.assertEqual(hd.version_at(row, "2026-11-11"), (0, "v1"))
        self.assertEqual(hd.version_at(row, "2026-11-13"), (1, "v2"))


class SettleGaps(unittest.TestCase):
    def setUp(self):
        self.rules = {"id": "/g/CLAUDE.md", "scope": "global", "kind": "rules", "name": "CLAUDE.md", "created": None}
        self.style = {"id": "/g/coworker.md", "scope": "global", "kind": "output style", "name": "coworker", "created": "2026-11-12T15:00"}
        self.proj = {"id": "/p/a/CLAUDE.md", "scope": "/p/a", "kind": "rules", "name": "CLAUDE.md", "created": None}
        self.d = {"rows": [self.rules, self.style, self.proj], "sessions": [
            {"id": "old", "t": "2026-11-11T10:00", "cwd": "/p/b", "skills": []},
            {"id": "new", "t": "2026-11-13T10:00", "cwd": "/p/b", "skills": []}]}

    def gap(self, topic="overlong_replies"):
        return {"topic": topic, "type": "verbose", "line": "Reply too long.", "you": "too long", "you_marks": ["too long"]}

    def test_file_there_then_becomes_a_fail(self):
        t = {"sessions": {"new": {"files": [{"file": "/g/coworker.md", "verdict": "worked", "line": "ok"}], "gaps": [self.gap()]}}}
        self.assertEqual(hd.settle_gaps(t, self.d, {"overlong_replies": "/g/coworker.md"}), 1)
        f = t["sessions"]["new"]["files"][0]
        self.assertEqual((f["verdict"], f["line"], len(f["issues"])), ("failed", "Reply too long.", 1))
        self.assertEqual(t["sessions"]["new"]["gaps"], [])

    def test_file_written_later_deletes_the_gap(self):
        t = {"sessions": {"old": {"files": [], "gaps": [self.gap()]}}}
        self.assertEqual(hd.settle_gaps(t, self.d, {"overlong_replies": "/g/coworker.md"}), 1)
        self.assertEqual(t["sessions"]["old"], {"files": [], "gaps": []})

    def test_skill_there_but_not_loaded_is_a_miss(self):
        skill = {"id": "/g/skills/remember/SKILL.md", "scope": "global", "kind": "skill", "name": "remember", "created": "2026-11-12T15:00"}
        self.d["rows"].append(skill)
        t = {"sessions": {"new": {"files": [], "gaps": [self.gap()]}, "old": {"files": [], "gaps": [self.gap()]}}}
        self.assertEqual(hd.settle_gaps(t, self.d, {"overlong_replies": skill["id"]}), 2)
        f = t["sessions"]["new"]["files"][0]
        self.assertEqual((f["file"], f["verdict"], len(f["issues"])), (skill["id"], "missed", 1))
        self.assertEqual(t["sessions"]["old"], {"files": [], "gaps": []})  # the skill came later: deleted

    def test_other_projects_file_and_uncovered_gaps_stay(self):
        t = {"sessions": {"new": {"files": [], "gaps": [self.gap(), self.gap("other")]}}}
        self.assertEqual(hd.settle_gaps(t, self.d, {"overlong_replies": "/p/a/CLAUDE.md"}), 0)
        self.assertEqual(len(t["sessions"]["new"]["gaps"]), 2)


class StaleResults(unittest.TestCase):
    def test_rules_are_the_triage_and_recheck_parts(self):
        text = hd.rules_text()
        self.assertTrue(text.startswith("## Triage mode"))
        self.assertIn("## Re-check mode", text)
        self.assertNotIn("## Output", text)

    def test_older_rules_are_stale_but_new_messages_come_first(self):
        s = {"id": "s", "n": 5, "cwd": "/p", "t": "2026-11-10T10:00", "end": "2026-11-10T11:00", "skills": []}
        t = {"sessions": {"s": {"v": 3, "rules": "old", "n": 5, "files": [], "gaps": []}}}
        self.assertTrue(hd.stale(s, "global", t, "new"))
        self.assertFalse(hd.stale(s, "global", t, "old"))
        t["sessions"]["s"]["n"] = 3  # it grew: the main button judges it, so it isn't counted as stale
        self.assertFalse(hd.stale(s, "global", t, "new"))


class MergeUnmarked(unittest.TestCase):
    def test_versions_without_marks_fold_into_the_next(self):
        row = {"id": "/g/CLAUDE.md", "kind": "rules", "scope": "global", "base": "a", "clusters": [
            {"session": "e1", "start": "2026-11-01", "end": "2026-11-01", "edits": 1, "new": False, "text": "b", "diff": "", "what": ""},
            {"session": "e2", "start": "2026-11-03", "end": "2026-11-03", "edits": 1, "new": False, "text": "c", "diff": "", "what": ""},
            {"session": "e3", "start": "2026-11-05", "end": "2026-11-05", "edits": 1, "new": False, "text": "d", "diff": "", "what": ""}]}
        sessions = [{"id": "s1", "t": "2026-11-02", "n": 5, "cwd": "/p", "skills": []},   # ran on v1, no verdict on this file
                    {"id": "s2", "t": "2026-11-04", "n": 5, "cwd": "/p", "skills": []}]   # ran on v2, judged it
        tri = {"s1": {"files": [{"file": "/other", "verdict": "worked"}]}, "s2": {"files": [{"file": "/g/CLAUDE.md", "verdict": "failed"}]}}
        hd.merge_unused(row, sessions, tri)
        self.assertEqual([(c["start"], c["end"], c["edits"]) for c in row["clusters"]],
                         [("2026-11-01", "2026-11-03", 2), ("2026-11-05", "2026-11-05", 1)])


class Usages(unittest.TestCase):
    def session(self):
        return {"id": "s", "n": 5, "cwd": "/p", "t": "2026-11-10T10:00", "end": "2026-11-10T11:00",
                "skills": [{"name": "remember", "t": "2026-11-10T10:40"}, {"name": "remember", "t": "2026-11-10T10:05"}, {"name": "spot-gap", "t": "2026-11-10T10:20"}]}

    def test_loads_in_order_and_only_repeats(self):
        s = self.session()
        self.assertEqual(hd.loads_by_skill(s)["remember"], ["2026-11-10T10:05", "2026-11-10T10:40"])
        self.assertEqual(list(hd.loads_by_skill(s, more_than_once=True)), ["remember"])

    def test_old_verdicts_with_a_repeated_skill_get_judged_again(self):
        s = self.session()
        t = {"sessions": {"s": {"v": 2, "files": [], "gaps": []}}}
        self.assertTrue(hd.needs_judging(s, {}, "global", t))
        t["sessions"]["s"].update(v=3, at="2026-11-10T12:00:00+00:00")
        self.assertFalse(hd.needs_judging(s, {}, "global", t))

    def test_new_messages_mean_judging_again(self):
        s = self.session()
        t = {"sessions": {"s": {"v": 3, "n": 5, "files": [], "gaps": []}}}
        self.assertIsNone(hd.to_judge(s, "global", t))
        s["n"] = 7
        self.assertEqual(hd.to_judge(s, "global", t), "again")
        del t["sessions"]["s"]["n"]  # an older result: compare the session's last message with when it was judged
        t["sessions"]["s"]["at"] = "2026-11-10T10:30:00+00:00"
        self.assertEqual(hd.to_judge(s, "global", t), "again")

    def test_use_must_be_a_whole_number(self):
        self.assertEqual(hd.clean_use({"use": 2})["use"], 2)
        self.assertNotIn("use", hd.clean_use({"use": "2"}))
        self.assertNotIn("use", hd.clean_use({"use": 0}))

    def test_a_quiet_break_needs_claudes_words(self):
        f = hd.clean_use({"verdict": "failed", "issues": [
            {"type": "quiet_break", "quiet": True, "rule": "One idea", "line": "Packed three ideas.", "claude": "First… second… third…"},
            {"type": "quiet_break", "quiet": True, "rule": "One idea", "line": "No quote."},
            {"type": "wrong_approach", "quiet": False, "line": "Pushed back.", "you": "no"}]})
        self.assertEqual([i["line"] for i in f["issues"]], ["Packed three ideas.", "Pushed back."])
        self.assertNotIn("quiet", f["issues"][1])

    def test_claudes_words_keep_the_start_and_end(self):
        turns = [{"who": "Claude" if i % 2 else "You", "text": "x" * 500, "t": str(i)} for i in range(100)]
        out = hd.claude_words("s", cap=10, turns=turns)
        self.assertEqual(len(out), 10)
        self.assertEqual((out[0]["time"], out[-1]["time"]), ("1", "99"))
        self.assertLessEqual(len(out[0]["text"]), 325)


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

if __name__ == "__main__":
    unittest.main()
