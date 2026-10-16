from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import batch_rewrite
from novel_engine_v3 import V3ValidationError


class FakeEngine:
    def __init__(self, root: Path, project: Path):
        self.root = root
        self.project = project

    def book(self, _book_id):
        return self.project, {}


class BatchRewriteTests(unittest.TestCase):
    def make_book(self, root: Path) -> Path:
        project = root / "book"
        (project / "chapters").mkdir(parents=True)
        for number in (1, 2, 3):
            (project / "chapters" / f"{number:04d}-原章.md").write_text(f"# 第 {number} 章 原章\n\n旧正文{number}", encoding="utf-8")
        (project / "chapter_state.json").write_text(json.dumps({"last_completed_chapter": 3,
            "next_chapter_number": 4, "last_uploaded_chapter": 0}), encoding="utf-8")
        (project / "continuity_ledger.md").write_text("## 第 1 章后\n旧一\n## 第 2—3 章后\n旧二三\n", encoding="utf-8")
        return project

    def test_prepare_preserves_originals_and_clears_tail_in_work_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.make_book(root)
            with patch.object(batch_rewrite, "WORK_ROOT", root / ".batch_rewrites"):
                work, stage = batch_rewrite.prepare_workspace(FakeEngine(root, project), "book", 2, 3)
            self.assertTrue(batch_rewrite.chapter_path(stage, 1).is_file())
            self.assertEqual(len(list((work / "originals").glob("*.md"))), 2)
            self.assertEqual(len(list((stage / "chapters").glob("*.md"))), 1)
            self.assertEqual(len(list((project / "chapters").glob("*.md"))), 3)
            self.assertNotIn("旧二三", (stage / "continuity_ledger.md").read_text(encoding="utf-8"))

    def test_install_backs_up_and_replaces_tail_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.make_book(root)
            with patch.object(batch_rewrite, "WORK_ROOT", root / ".batch_rewrites"):
                work, stage = batch_rewrite.prepare_workspace(FakeEngine(root, project), "book", 2, 3)
            for number in (2, 3):
                (stage / "chapters" / f"{number:04d}-新章.md").write_text("# 新章\n\n新正文", encoding="utf-8")
            batch_rewrite.install(project, stage, work, 2, 3)
            self.assertEqual(len(list((project / "chapters").glob("0002-*.md"))), 1)
            self.assertIn("新正文", batch_rewrite.chapter_path(project, 3).read_text(encoding="utf-8"))
            self.assertIn("旧正文2", (work / "before" / "chapters" / "0002-原章.md").read_text(encoding="utf-8"))
            self.assertIn("旧正文1", batch_rewrite.chapter_path(project, 1).read_text(encoding="utf-8"))

    def test_uploaded_tail_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            project = self.make_book(Path(directory))
            state = json.loads((project / "chapter_state.json").read_text(encoding="utf-8"))
            state["last_uploaded_chapter"] = 2
            (project / "chapter_state.json").write_text(json.dumps(state), encoding="utf-8")
            with self.assertRaises(V3ValidationError):
                batch_rewrite.validate_request(project, 2)

    def test_night_floor_checkpoint_resolves_before_chapter_three(self):
        project = batch_rewrite.ROOT / "我家旧楼每晚多一层"
        point = batch_rewrite.baseline(batch_rewrite.ROOT, project, "night-floor", 3)
        self.assertEqual(point, {"last_chapter": 2, "day": 2, "floor": 8, "after_2347": True})

    def test_continuity_blocker_repairs_candidate_and_rechecks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "project"
            run = root / "run"
            (stage / "character_threads" / "0003").mkdir(parents=True)
            run.mkdir()
            (stage / "character_threads" / "0003" / "00-cast.md").write_text(
                "## 角色名单\n- 现场民警：维持秩序\n", encoding="utf-8"
            )
            (run / "continuity.md").write_text("回写人物状态。", encoding="utf-8")
            (run / "candidate.md").write_text("# 第三章\n\n昨天。", encoding="utf-8")
            (run / "reader_review.json").write_text('{"blocking": []}', encoding="utf-8")
            (run / "continuity_review.json").write_text(
                '{"blocking": ["时间倒退"]}', encoding="utf-8"
            )
            with patch.object(batch_rewrite, "review_and_archive", side_effect=[
                V3ValidationError("时间线未通过"), stage / "chapters" / "0003-新章.md"
            ]) as review, patch.object(batch_rewrite, "execute") as execute:
                result = batch_rewrite.review_with_repair(run, stage, 3)
            self.assertEqual(result.name, "0003-新章.md")
            self.assertEqual(review.call_count, 2)
            self.assertEqual(execute.call_count, 1)
            self.assertFalse((run / "reader_review.json").exists())
            self.assertIn("现场民警", (run / "continuity.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
