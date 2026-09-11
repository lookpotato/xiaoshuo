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
        (self.project / "style_guide.md").write_text("# 文风指南\n", encoding="utf-8")
        engine_home = self.root / "novel_engine_v2"
        (engine_home / "authors").mkdir(parents=True)
        (engine_home / "system.json").write_text(json.dumps({
            "schema_version": 2,
            "principle": "测试",
            "limits": {
                "max_writer_modules": 2, "max_reader_modules": 2,
                "recent_chapters": 1, "max_author_context_chars": 8000,
            },
            "legacy_adapter": {"book_sources": [], "excluded_from_writer": []},
            "books": {"demo": {
                "title": "测试书", "project": "book", "author": "owner",
            }},
        }, ensure_ascii=False), encoding="utf-8")
        (engine_home / "modules.json").write_text(json.dumps({
            "schema_version": 2, "modules": {},
        }), encoding="utf-8")
        (engine_home / "authors" / "owner.json").write_text(json.dumps({
            "schema_version": 1, "id": "owner", "name": "测试作者",
            "creative_identity": ["写清人物选择。"],
            "reader_contract": ["读者理解当前行动。"],
            "language_principles": ["对白符合关系。"],
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

    def _feedback_with_learning(self, category: str = "character_voice") -> dict:
        item = service.create_feedback(self.root, {
            "book_id": "demo", "chapter": 1, "category": category,
            "quote": "甲把门推开。", "comment": "这句不像人物在现场会说的话。",
        })
        folder = self.project / "reader_feedback" / item["id"]
        service.atomic_json(folder / "analysis.json", {
            "decision": "partial", "author_judgment": "问题成立。",
            "valid_observations": ["对白书面"], "misdiagnoses": [],
            "revision_strategy": ["改为现场说法"],
            "learning_candidate": {
                "principle": "人物先回应眼前的人和事，再补必要背景。",
                "applies_when": "熟人正在共同处理紧急事件时",
                "avoid": "不能删除读者理解行动所必需的信息",
                "recommended_scope": "author", "confidence": "high",
                "rationale": "可以避免跨章节反复出现的说明腔。",
            },
        })
        return item

    def test_book_learning_promotion_updates_structured_store_and_style(self) -> None:
        item = self._feedback_with_learning()
        result = service.promote_learning(self.root, "demo", item["id"], "book")
        registry = json.loads((self.project / "feedback_learning.json").read_text(encoding="utf-8"))
        self.assertEqual(len(registry["rules"]), 1)
        self.assertIn("人物先回应眼前的人和事", (self.project / "style_guide.md").read_text(encoding="utf-8"))
        self.assertEqual(result["promotion"]["scope"], "book")
        repeated = service.promote_learning(self.root, "demo", item["id"], "book")
        self.assertEqual(repeated["promotion"]["rule_id"], result["promotion"]["rule_id"])

    def test_same_book_rule_accumulates_independent_feedback_evidence(self) -> None:
        first = self._feedback_with_learning()
        service.promote_learning(self.root, "demo", first["id"], "book")
        second = self._feedback_with_learning()
        result = service.promote_learning(self.root, "demo", second["id"], "book")
        registry = json.loads((self.project / "feedback_learning.json").read_text(encoding="utf-8"))
        self.assertEqual(len(registry["rules"]), 1)
        self.assertEqual(registry["rules"][0]["evidence_count"], 2)
        self.assertEqual(result["promotion"]["evidence_count"], 2)

    def test_author_learning_promotion_updates_profile_and_bound_style(self) -> None:
        item = self._feedback_with_learning()
        service.promote_learning(self.root, "demo", item["id"], "author")
        profile = json.loads(
            (self.root / "novel_engine_v2" / "authors" / "owner.json").read_text(encoding="utf-8")
        )
        self.assertEqual(profile["feedback_learning"][0]["evidence_count"], 1)
        self.assertTrue(any("人物先回应眼前的人和事" in rule for rule in profile["language_principles"]))
        self.assertIn("人物先回应眼前的人和事", (self.project / "style_guide.md").read_text(encoding="utf-8"))

    def test_worker_accepts_bounded_learning_candidate(self) -> None:
        parsed = parse_result(json.dumps({
            "decision": "reject", "author_judgment": "本章有意这样处理。",
            "valid_observations": [], "misdiagnoses": ["不是长期问题"],
            "revision_strategy": [], "learning_candidate": None,
            "proposed_revision": None,
        }, ensure_ascii=False))
        self.assertIsNone(parsed["analysis"]["learning_candidate"])


if __name__ == "__main__":
    unittest.main()
