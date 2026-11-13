"""Tests for the parts of harness_doctor.py that don't need Claude or real session logs."""
import importlib.util
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
        return {"cwd": "/x", "n": n, "t": t, "skills": [{"name": "s", "t": t}]}

    def test_unused_version_folds_into_the_next(self):
        r = self.row()
        hd.merge_unused(r, [])
        self.assertEqual(len(r["clusters"]), 1)
        self.assertEqual(r["clusters"][0]["edits"], 3)
        self.assertEqual(r["clusters"][0]["start"], "2026-01-01T00:00")

    def test_used_version_stays(self):
        r = self.row()
        hd.merge_unused(r, [self.session("2026-01-01T12:00")])
        self.assertEqual(len(r["clusters"]), 2)

    def test_short_sessions_dont_count(self):
        r = self.row()
        hd.merge_unused(r, [self.session("2026-01-01T12:00", n=1)])
        self.assertEqual(len(r["clusters"]), 1)


class ApplyEdits(unittest.TestCase):
    def test_replace(self):
        self.assertEqual(hd.edit_text("a\nold\nb\n", {"find": "old", "text": "new"}), "a\nnew\nb\n")

    def test_cut_takes_its_line_break(self):
        self.assertEqual(hd.edit_text("a\nold\nb\n", {"find": "old", "text": None}), "a\nb\n")

    def test_add_after_a_line(self):
        self.assertEqual(hd.edit_text("a line\nb\n", {"find": None, "text": "new\n", "after": "a line"}), "a line\nnew\nb\n")

    def test_add_at_the_end(self):
        self.assertEqual(hd.edit_text("a\n", {"find": None, "text": "new"}), "a\n\nnew\n")

    def test_missing_or_repeated_text_is_refused(self):
        self.assertIsNone(hd.edit_text("a\n", {"find": "zzz", "text": "x"}))
        self.assertIsNone(hd.edit_text("x\nx\n", {"find": "x", "text": "y"}))
        self.assertIsNone(hd.edit_text("a\n", {"find": None, "text": "n", "after": "zzz"}))

    def test_write_keeps_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = os.path.join(tmp, "SKILL.md")
            open(f, "w").write("old")
            os.chmod(f, 0o640)
            hd.write_file(f, "new")
            self.assertEqual(open(f).read(), "new")
            self.assertEqual(os.stat(f).st_mode & 0o777, 0o640)


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


if __name__ == "__main__":
    unittest.main()
