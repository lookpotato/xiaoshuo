from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import reader_feedback_service as service
from reader_feedback_worker import parse_result


class ReaderFeedbackServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "book"
        (self.project / "chapters").mkdir(parents=True)
        self.chapter = self.project / "chapters" / "0001-试读.md"
        self.chapter.write_text(
            "# 第 1 章 试读\n\n甲把门推开。乙没回答，只把碗往里面挪了挪。\n"
            + "这是正文。" * 180,
            encoding="utf-8",
        )
        (self.root / "manager_config.json").write_text(json.dumps({
            "books": [{"id": "demo", "title": "测试书", "path": "book"}],
        }, ensure_ascii=False), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_feedback_keeps_reader_observation_structured(self) -> None:
        item = service.create_feedback(self.root, {
            "book_id": "demo",
            "chapter": 1,
            "category": "character_voice",
            "quote": "乙没回答，只把碗往里面挪了挪。",
            "comment": "这里让我觉得人物突然疏远了。",
        })
        self.assertEqual(item["status"], "queued")
        self.assertEqual(item["category_label"], "人物不像本人")
        self.assertEqual(len(service.list_feedback(self.root, "demo", 1)), 1)

    def test_feedback_rejects_quote_not_in_chapter(self) -> None:
        with self.assertRaisesRegex(ValueError, "不属于当前章节"):
            service.create_feedback(self.root, {
                "book_id": "demo", "chapter": 1, "category": "other",
                "quote": "根本不存在的原文", "comment": "不舒服",
            })

    def test_revision_requires_author_acceptance_and_preserves_backup(self) -> None:
        item = service.create_feedback(self.root, {
            "book_id": "demo", "chapter": 1, "category": "pacing",
            "quote": "甲把门推开。", "comment": "这里太快。",
        })
        folder = self.project / "reader_feedback" / item["id"]
        service.atomic_json(folder / "analysis.json", {
            "decision": "partial", "author_judgment": "感受成立，病因只对一半。",
            "valid_observations": ["动作缺反应"], "misdiagnoses": [],
            "revision_strategy": ["补一个人物反应"],
        })
        revised = "# 第 1 章 试读\n\n" + "甲停了一下，再把门推开。" * 80
        service.atomic_text(folder / "proposed_revision.md", revised)
        result = service.apply_revision(self.root, "demo", item["id"])
        self.assertEqual(result["item"]["status"], "applied")
        self.assertTrue((folder / "original_before_apply.md").is_file())
        self.assertIn("甲停了一下", self.chapter.read_text(encoding="utf-8"))

    def test_worker_result_requires_revision_only_when_author_accepts(self) -> None:
        parsed = parse_result(json.dumps({
            "decision": "reject", "author_judgment": "人物有意沉默。",
            "valid_observations": ["读者感到疏远"],
            "misdiagnoses": ["疏远正是本场目的"],
            "revision_strategy": [], "proposed_revision": None,
        }, ensure_ascii=False))
        self.assertIsNone(parsed["revision"])


if __name__ == "__main__":
    unittest.main()
