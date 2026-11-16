from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import overview_handoff_service as service


class OverviewHandoffTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "novel_engine_v3").mkdir()
        (self.root / "novel_engine_v3" / "system.json").write_text(
            json.dumps({"schema_version": 3, "books": {"book": {"project": "book"}}}), encoding="utf-8")
        self.project = self.root / "book"
        (self.project / "chapter_plans").mkdir(parents=True)
        (self.project / "chapter_state.json").write_text(
            json.dumps({"next_chapter_number": 3}), encoding="utf-8")
        (self.project / "chapter_plans" / "0003.json").write_text(
            json.dumps({"chapter_number": 3, "immediate_goal": "开门"}, ensure_ascii=False), encoding="utf-8")
        self.run = self.root / ".novel_runs_v3" / "book" / "0003-test"
        self.run.mkdir(parents=True)
        (self.run / "manifest.json").write_text(json.dumps({
            "book_id": "book", "chapter": 3, "project": str(self.project),
        }), encoding="utf-8")
        overview = "# 第三章\n" + "人物先确认门后的声音。" * 12
        (self.run / "overview.md").write_text(overview, encoding="utf-8")
        service._write_meta(self.run, {"status": "pending", "plan_revision": service._hash(
            (self.project / "chapter_plans" / "0003.json").read_text(encoding="utf-8")), "questions": []})

    def test_save_and_expand_use_author_revision(self):
        initial = service.workspace(self.root, "book", 3)
        revised = initial["overview"] + "\n最后由她亲自开门。"
        saved = service.save(self.root, "book", 3, revised, initial["revision"])
        self.assertEqual(saved["change_summary"]["count"], 1)
        self.assertEqual(saved["change_summary"]["changes"][0]["kind"], "新增")
        self.assertIn("亲自开门", saved["change_summary"]["changes"][0]["after"])
        self.assertEqual(service.workspace(self.root, "book", 3)["change_summary"], saved["change_summary"])
        with self.assertRaisesRegex(ValueError, "别处更新"):
            service.save(self.root, "book", 3, initial["overview"], initial["revision"])
        (self.run / "reader_review.json").write_text('{"blocking":[]}', encoding="utf-8")

        def fake_execute(prompt, output, run, **kwargs):
            if prompt.name == "draft.md":
                self.assertEqual((run / "approved_overview.md").read_text(encoding="utf-8"), revised)
                self.assertFalse((run / "reader_review.json").exists())

        with patch.object(service, "sync_overview_context"), patch.object(service, "execute", side_effect=fake_execute), patch.object(
            service, "review_and_archive", return_value=self.project / "chapters" / "0003.md"
        ):
            service.expand(self.root, "book", 3, saved["revision"])
        self.assertEqual(service.workspace(self.root, "book", 3)["status"], "archived")
        next_page = service.workspace(self.root, "book", 4)
        self.assertEqual(next_page["last_report"]["chapter"], 3)
        self.assertEqual(next_page["last_report"]["change_summary"], saved["change_summary"])

    def test_recovered_legacy_comparison_is_labelled_against_initial_plan(self):
        initial = "初始总览" * 20
        (self.run / "overview.original.md").write_text(initial, encoding="utf-8")
        result = service.workspace(self.root, "book", 3)
        self.assertIn("初始规划", result["change_summary"]["baseline"])

    def test_comparison_preserves_added_deleted_and_replaced_text(self):
        report = service.compare_overviews("删掉的承诺\n保持\n旧路线", "保持\n新路线\n")
        self.assertEqual([item["kind"] for item in report["changes"]], ["删除", "修改"])
        self.assertEqual(report["changes"][0]["before"], "删掉的承诺")
        self.assertEqual(report["changes"][1]["after"], "新路线")

    def test_question_is_read_only_and_saved_without_changing_overview(self):
        initial = service.workspace(self.root, "book", 3)

        def fake_execute(prompt, output, run, **kwargs):
            self.assertTrue(kwargs["read_only"])
            self.assertIn("钥匙", prompt.read_text(encoding="utf-8"))
            self.assertEqual(service.workspace(self.root, "book", 3)["questions"][-1]["status"], "running")
            output.write_text("钥匙先交给她，动机才成立。", encoding="utf-8")

        with patch.object(service, "execute", side_effect=fake_execute):
            answer = service.ask(self.root, "book", 3, "他为什么交钥匙？", initial["revision"])
        self.assertIn("动机", answer)
        self.assertEqual(service.workspace(self.root, "book", 3)["revision"], initial["revision"])
        self.assertEqual(len(service.workspace(self.root, "book", 3)["questions"]), 1)
        self.assertEqual(service.workspace(self.root, "book", 3)["questions"][0]["status"], "answered")

    def test_failed_question_is_visible_for_retry(self):
        revision = service.workspace(self.root, "book", 3)["revision"]
        with patch.object(service, "execute", side_effect=RuntimeError("模型失败")):
            with self.assertRaisesRegex(RuntimeError, "模型失败"):
                service.ask(self.root, "book", 3, "钥匙呢？", revision)
        self.assertEqual(service.workspace(self.root, "book", 3)["questions"][0]["status"], "failed")

    def test_failed_expansion_reuses_candidate_and_reviews_it_again(self):
        revision = service.workspace(self.root, "book", 3)["revision"]
        (self.run / "candidate.md").write_text("# 第三章\n候选正文\n", encoding="utf-8")
        (self.run / "reader_review.json").write_text('{"blocking": ["旧问题"]}', encoding="utf-8")
        (self.run / "continuity_review.json").write_text('{"blocking": ["旧问题"]}', encoding="utf-8")
        meta = json.loads((self.run / "overview_handoff.json").read_text(encoding="utf-8"))
        meta["candidate_revision"] = revision
        service._write_meta(self.run, meta)

        def review(run, project, chapter):
            self.assertFalse((run / "reader_review.json").exists())
            self.assertFalse((run / "continuity_review.json").exists())
            self.assertEqual((run / "candidate.md").read_text(encoding="utf-8"), "# 第三章\n候选正文\n")
            return project / "chapters" / "0003.md"

        with patch.object(service, "sync_overview_context"), patch.object(service, "execute") as execute, patch.object(
            service, "review_and_archive", side_effect=review
        ):
            service.expand(self.root, "book", 3, revision)
        execute.assert_not_called()

    def test_overview_rebuilds_plan_and_removes_old_character_inputs(self):
        revision = service.workspace(self.root, "book", 3)["revision"]
        (self.run / "approved_overview.md").write_text("住户已经回家，不借窗口。", encoding="utf-8")
        threads = self.project / "character_threads" / "0003"
        threads.mkdir(parents=True)
        (threads / "旧邻楼观察者.md").write_text("借窗取证", encoding="utf-8")
        meta = json.loads((self.run / "overview_handoff.json").read_text(encoding="utf-8"))
        stages = []

        def execute(prompt, output, run, **kwargs):
            stages.append(prompt.name)
            progress = service.workspace(self.root, "book", 3)["progress"]
            self.assertTrue(progress["stage"].startswith("正在"))
            if prompt.name == "overview_sync.md":
                self.assertTrue(kwargs["read_only"])
                output.write_text(json.dumps({"chapter_number": 3, "immediate_goal": "街对面复测"}), encoding="utf-8")
            elif prompt.name == "characters.overview.md":
                self.assertFalse(threads.exists())
                threads.mkdir()
                (threads / "新人物.md").write_text("街对面复测", encoding="utf-8")

        with patch.object(service, "execute", side_effect=execute), \
             patch.object(service, "planning_context_sha256", return_value="current"), \
             patch.object(service, "validate_plan"), patch.object(service, "validate_characters"), \
             patch.object(service, "validate_viewpoint_contract"), patch.object(service, "validate_scene_seed"):
            service.sync_overview_context(self.root, self.project, self.run, 3, revision, meta)
            service.sync_overview_context(self.root, self.project, self.run, 3, revision, meta)
        self.assertEqual(stages, ["overview_sync.md", "characters.overview.md", "author_room.overview.md", "scene_seed.overview.md"])
        self.assertEqual(meta["context_revision"], revision)
        self.assertFalse(service.workspace(self.root, "book", 3)["plan_stale"])
        self.assertEqual(len(list(self.run.glob("overview-characters-*/0003/旧邻楼观察者.md"))), 1)

    def test_invalid_synced_plan_restores_original_and_keeps_context_pending(self):
        plan = self.project / "chapter_plans" / "0003.json"
        original = plan.read_bytes()
        meta = json.loads((self.run / "overview_handoff.json").read_text(encoding="utf-8"))

        def execute(prompt, output, run, **kwargs):
            output.write_text('{"chapter_number":3}', encoding="utf-8")

        with patch.object(service, "execute", side_effect=execute), \
             patch.object(service, "planning_context_sha256", return_value="current"), \
             patch.object(service, "validate_plan", side_effect=ValueError("合同不完整")):
            with self.assertRaisesRegex(ValueError, "合同不完整"):
                service.sync_overview_context(self.root, self.project, self.run, 3, "revision", meta)
        self.assertEqual(plan.read_bytes(), original)
        self.assertNotIn("context_revision", meta)

    def test_sync_failure_keeps_chapter_pending_and_never_starts_prose(self):
        revision = service.workspace(self.root, "book", 3)["revision"]
        with patch.object(service, "sync_overview_context", side_effect=ValueError("同步失败")), \
             patch.object(service, "execute") as execute, patch.object(service, "review_and_archive") as archive:
            with self.assertRaisesRegex(ValueError, "同步失败"):
                service.expand(self.root, "book", 3, revision)
        execute.assert_not_called()
        archive.assert_not_called()
        self.assertEqual(service.workspace(self.root, "book", 3)["status"], "pending")
        self.assertIn("任务失败", service.workspace(self.root, "book", 3)["progress"]["stage"])
        self.assertEqual(json.loads((self.project / "chapter_state.json").read_text())["next_chapter_number"], 3)

    def test_changed_plan_blocks_expansion(self):
        initial = service.workspace(self.root, "book", 3)
        (self.project / "chapter_plans" / "0003.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "规划已变化"):
            service.validate_pending(self.root, "book", 3, "expand", initial["revision"])


if __name__ == "__main__":
    unittest.main()
