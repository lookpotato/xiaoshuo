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
        with self.assertRaisesRegex(ValueError, "别处更新"):
            service.save(self.root, "book", 3, initial["overview"], initial["revision"])

        def fake_execute(prompt, output, run, **kwargs):
            if prompt.name == "draft.md":
                self.assertEqual((run / "approved_overview.md").read_text(encoding="utf-8"), revised)

        with patch.object(service, "execute", side_effect=fake_execute), patch.object(
            service, "review_and_archive", return_value=self.project / "chapters" / "0003.md"
        ):
            service.expand(self.root, "book", 3, saved["revision"])
        self.assertEqual(service.workspace(self.root, "book", 3)["status"], "archived")

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

    def test_changed_plan_blocks_expansion(self):
        initial = service.workspace(self.root, "book", 3)
        (self.project / "chapter_plans" / "0003.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "规划已变化"):
            service.validate_pending(self.root, "book", 3, "expand", initial["revision"])


if __name__ == "__main__":
    unittest.main()
