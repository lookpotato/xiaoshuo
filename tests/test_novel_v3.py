from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from novel_v3 import accept_deep_edit, archive_chapter, review_and_archive, validate_scene_seed
from novel_engine_v3 import V3ValidationError


class DeepEditAcceptanceTests(unittest.TestCase):
    def test_scene_seed_must_be_prose_before_expansion(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run / "scene_seed.md").write_text("- 目标：开门", encoding="utf-8")
            with self.assertRaises(V3ValidationError):
                validate_scene_seed(run)
            (run / "scene_seed.md").write_text("他推开门，先看见灯。" * 40, encoding="utf-8")
            validate_scene_seed(run)

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

    def test_unified_archive_uses_final_continuity_hook(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            project = root / "book"
            run.mkdir()
            project.mkdir()
            (run / "manifest.json").write_text('{"workflow":["director"]}', encoding="utf-8")
            (run / "candidate.md").write_text("# 第 2 章 新楼层\n\n新楼层出现。", encoding="utf-8")
            (run / "state_delta.json").write_text('{"next_hook":"错误的旧时间"}', encoding="utf-8")
            (run / "continuity_review.json").write_text(
                '{"next_hook":"新楼层已出现","unresolved":["尚未勘验"]}', encoding="utf-8"
            )
            (project / "chapter_state.json").write_text(
                '{"last_completed_chapter":1,"next_chapter_number":2}', encoding="utf-8"
            )
            with patch("novel_v3.validate_unified_run", return_value=None) as gate:
                archive_chapter(run, project, 2)
            gate.assert_called_once()
            state = json.loads((project / "chapter_state.json").read_text(encoding="utf-8"))
            self.assertIn("新楼层已出现", state["notes_for_next_chapter"])
            self.assertNotIn("错误的旧时间", state["notes_for_next_chapter"])

    def test_review_and_archive_accepts_utf8_bom_from_powershell(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            project = root / "book"
            run.mkdir()
            project.mkdir()
            (run / "candidate.md").write_text("# 第 2 章 原地等候\n\n正文。", encoding="utf-8")
            (run / "reader_review.json").write_text('{"blocking": []}', encoding="utf-8-sig")
            (run / "state_delta.json").write_text('{"next_hook": "继续找人"}', encoding="utf-8-sig")
            (project / "chapter_state.json").write_text('{"last_completed_chapter": 1, "next_chapter_number": 2}', encoding="utf-8")
            archived = review_and_archive(run, project, 2)
            self.assertTrue(archived.is_file())
            self.assertIn("继续找人", (project / "chapter_state.json").read_text(encoding="utf-8"))

    def test_second_repair_can_pass_without_manual_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            project = root / "book"
            run.mkdir()
            project.mkdir()
            (run / "candidate.md").write_text("# 第 2 章 原地等候\n\n正文。", encoding="utf-8")
            review_path = run / "reader_review.json"
            review_path.write_text('{"blocking": [{"quote": "第一处"}]}', encoding="utf-8")
            (run / "reader.md").write_text("复读", encoding="utf-8")
            (project / "chapter_state.json").write_text(
                '{"last_completed_chapter": 1, "next_chapter_number": 2}', encoding="utf-8"
            )
            reviews = iter([
                {"blocking": [{"quote": "第二处"}]},
                {"blocking": []},
            ])

            def fake_execute(prompt, output, run_dir, **kwargs):
                if prompt.name == "reader.md":
                    review_path.write_text(json.dumps(next(reviews)), encoding="utf-8")

            with patch("novel_v3.execute", side_effect=fake_execute) as execute:
                archived = review_and_archive(run, project, 2)
            self.assertTrue(archived.is_file())
            self.assertEqual(execute.call_count, 4)
            self.assertTrue((run / "reader_review.before_repair.2.json").is_file())

    def test_unresolved_reader_blocker_does_not_archive_or_advance_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            project = root / "book"
            run.mkdir()
            project.mkdir()
            (run / "candidate.md").write_text("# 第 2 章 原地等候\n\n正文。", encoding="utf-8")
            (run / "reader_review.json").write_text(
                '{"blocking": [{"quote": "正文。", "issue": "对白失真"}]}',
                encoding="utf-8",
            )
            (run / "reader.md").write_text("复读", encoding="utf-8")
            state = project / "chapter_state.json"
            original_state = '{"last_completed_chapter": 1, "next_chapter_number": 2}'
            state.write_text(original_state, encoding="utf-8")
            def fake_execute(prompt, output, run_dir, **kwargs):
                if prompt.name == "reader.md":
                    (run / "reader_review.json").write_text(
                        '{"blocking": [{"quote": "正文。", "issue": "对白失真"}]}',
                        encoding="utf-8",
                    )

            with patch("novel_v3.execute", side_effect=fake_execute) as execute:
                with self.assertRaisesRegex(V3ValidationError, "自动返修 3 次后仍有 1 项阻塞.*正文"):
                    review_and_archive(run, project, 2)
            self.assertEqual(execute.call_count, 6)
            self.assertEqual(state.read_text(encoding="utf-8"), original_state)
            self.assertFalse((project / "chapters").exists())

    def test_missing_fresh_review_never_uses_old_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run"
            project = root / "book"
            run.mkdir()
            project.mkdir()
            (run / "candidate.md").write_text("# 第 2 章 原地等候\n\n正文。", encoding="utf-8")
            (run / "reader_review.json").write_text('{"blocking": [{"quote": "待修"}]}', encoding="utf-8")
            (run / "reader.md").write_text("复读", encoding="utf-8")
            with patch("novel_v3.execute"):
                with self.assertRaisesRegex(V3ValidationError, "读者检查结果缺失或无效"):
                    review_and_archive(run, project, 2)
            self.assertFalse((project / "chapters").exists())


if __name__ == "__main__":
    unittest.main()
