from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from novel_v3 import accept_deep_edit, archive_chapter
from novel_engine_v3 import V3ValidationError


class DeepEditAcceptanceTests(unittest.TestCase):
    def test_complete_revision_replaces_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            candidate = run / "candidate.md"
            output = run / "deep_edit.result.md"
            candidate.write_text("# 原标题\n\n" + "原文" * 100, encoding="utf-8")
            output.write_text("# 新标题\n\n" + "修改后的正文" * 90, encoding="utf-8")
            accept_deep_edit(run, output)
            self.assertTrue(candidate.read_text(encoding="utf-8").startswith("# 新标题"))

    def test_incomplete_revision_keeps_original(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            candidate = run / "candidate.md"
            output = run / "deep_edit.result.md"
            original = "# 原标题\n\n" + "原文" * 100
            candidate.write_text(original, encoding="utf-8")
            output.write_text("已完成修改。", encoding="utf-8")
            with self.assertRaises(V3ValidationError):
                accept_deep_edit(run, output)
            self.assertEqual(candidate.read_text(encoding="utf-8"), original)

    def test_archive_removes_model_supplied_chapter_number_and_keeps_hook(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            project = root / "book"
            run.mkdir()
            project.mkdir()
            (run / "candidate.md").write_text("# 第二章 不要替她播放\n\n正文。", encoding="utf-8")
            (run / "state_delta.json").write_text('{"next_hook":"查借阅记录","unresolved":["声音来源"]}', encoding="utf-8")
            (project / "chapter_state.json").write_text('{"last_completed_chapter":1,"next_chapter_number":2}', encoding="utf-8")
            archived = archive_chapter(run, project, 2)
            self.assertEqual(archived.name, "0002-不要替她播放.md")
            self.assertTrue(archived.read_text(encoding="utf-8").startswith("# 第 2 章 不要替她播放"))
            self.assertIn("声音来源", (project / "chapter_state.json").read_text(encoding="utf-8"))
            with self.assertRaises(V3ValidationError):
                archive_chapter(run, project, 2)


if __name__ == "__main__":
    unittest.main()
