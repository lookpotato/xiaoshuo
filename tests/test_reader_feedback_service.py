from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import reader_feedback_service as service
from reader_feedback_worker import (
    _evidence_in_text,
    parse_blind_reader_result,
    parse_chapter_interview_result,
    parse_follow_up_result,
    parse_result,
)


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
        self.assertEqual(item["review_mode"], "combined")
        self.assertEqual(item["review_mode_label"], "双重审稿")
        self.assertEqual(len(service.list_feedback(self.root, "demo", 1)), 1)

    def test_feedback_accepts_explicit_review_mode(self) -> None:
        item = service.create_feedback(self.root, {
            "book_id": "demo", "chapter": 1, "category": "confusing",
            "comment": "我只想先确认陌生读者能看到什么。",
            "review_mode": "blind",
        })
        self.assertEqual(item["review_mode"], "blind")
        self.assertEqual(item["review_mode_label"], "陌生读者试读")
        self.assertIn("陌生读者试读", item["status_message"])

    def test_chapter_interview_is_whole_chapter_author_question_mode(self) -> None:
        item = service.create_feedback(self.root, {
            "book_id": "demo", "chapter": 1,
            "review_mode": "chapter_interview",
        })
        self.assertEqual(item["review_mode_label"], "整章写法审校")
        self.assertIn("完整章", item["comment"])

    def test_chapter_interview_result_requires_foundation_level_and_evidence(self) -> None:
        parsed = parse_chapter_interview_result(json.dumps({
            "chapter_promise": "甲会在这一章做出选择。",
            "reading_summary": "选择看得见，但代价不清楚。",
            "questions": [{
                "id": "Q1", "level": "foundation",
                "question": "如果甲不这样做，眼前会失去什么？",
                "why_it_matters": "否则冲突只是作者安排。",
                "evidence": ["甲把门推开。"],
            }],
            "foundation_risks": [{
                "risk": "中心冲突可能缺少即时代价。",
                "severity": "high",
                "question": "作者是否能说明失败的具体后果？",
                "evidence": "甲把门推开。",
            }],
        }, ensure_ascii=False))
        self.assertEqual(parsed["questions"][0]["level"], "foundation")
        self.assertEqual(parsed["foundation_risks"][0]["severity"], "high")

    def test_chapter_interview_answers_are_saved_against_question_ids(self) -> None:
        item = service.create_feedback(self.root, {
            "book_id": "demo", "chapter": 1,
            "review_mode": "chapter_interview",
        })
        folder = self.project / "reader_feedback" / item["id"]
        service.atomic_json(folder / "chapter_interview.json", {
            "questions": [{"id": "Q1", "question": "为什么？"}],
        })
        saved = service.save_chapter_interview_answers(
            self.root, "demo", item["id"], {"Q1": "因为人物必须做这个选择。"}
        )
        self.assertEqual(saved["answers"]["Q1"], "因为人物必须做这个选择。")
        self.assertEqual(
            service.feedback_item(self.root, "demo", item["id"])["chapter_interview_answers"]["Q1"],
            "因为人物必须做这个选择。",
        )

    def test_chapter_interview_evidence_allows_punctuation_and_speaker_prefix(self) -> None:
        self.assertTrue(_evidence_in_text(
            "孟阿婆把门一关，说：‘行。不播放，不翻书。’",
            "孟阿婆：行，不播放，不翻书",
        ))

    def test_feedback_rejects_unknown_review_mode(self) -> None:
        with self.assertRaisesRegex(ValueError, "审稿方式无效"):
            service.create_feedback(self.root, {
                "book_id": "demo", "chapter": 1, "category": "confusing",
                "comment": "测试无效审稿方式。", "review_mode": "pretend",
            })

    def test_author_dialogue_keeps_correction_in_same_feedback_record(self) -> None:
        item = service.create_feedback(self.root, {
            "book_id": "demo", "chapter": 1, "category": "character_voice",
            "comment": "这句不像真人说话。",
        })
        folder = self.project / "reader_feedback" / item["id"]
        service.atomic_json(folder / "analysis.json", {
            "decision": "partial", "revision_scope": "wording",
            "scope_rationale": "局部对白问题。", "author_judgment": "略显生硬。",
            "valid_observations": ["措辞生硬"], "misdiagnoses": [],
            "revision_strategy": ["改台词"], "learning_candidate": None,
        })
        queued = service.append_author_dialogue_message(
            self.root, "demo", item["id"], "不是略显生硬，是正常人根本不会这样开口。"
        )
        refreshed = service.feedback_item(self.root, "demo", item["id"])
        self.assertEqual(queued["dialogue"]["status"], "queued")
        self.assertEqual(refreshed["author_dialogue_status"], "queued")
        self.assertEqual(refreshed["author_dialogue"][0]["role"], "user")

    def test_author_dialogue_rejects_second_pending_message(self) -> None:
        item = service.create_feedback(self.root, {
            "book_id": "demo", "chapter": 1, "category": "character_voice",
            "comment": "这句不像真人说话。",
        })
        folder = self.project / "reader_feedback" / item["id"]
        service.atomic_json(folder / "analysis.json", {"decision": "reject"})
        service.append_author_dialogue_message(self.root, "demo", item["id"], "第一条")
        with self.assertRaisesRegex(ValueError, "正在回复"):
            service.append_author_dialogue_message(self.root, "demo", item["id"], "第二条")

    def test_feedback_rejects_quote_not_in_chapter(self) -> None:
        with self.assertRaisesRegex(ValueError, "不属于当前章节"):
            service.create_feedback(self.root, {
                "book_id": "demo", "chapter": 1, "category": "other",
                "quote": "根本不存在的原文", "comment": "不舒服",
            })

    def test_feedback_exposes_blind_reader_report_separately_from_author(self) -> None:
        item = service.create_feedback(self.root, {
            "book_id": "demo", "chapter": 1, "category": "confusing",
            "quote": "甲把门推开。", "comment": "我不知道他为什么来。",
        })
        folder = self.project / "reader_feedback" / item["id"]
        service.atomic_json(folder / "blind_reader_analysis.json", {
            "recommended_scope": "scene", "reader_experience": "进入场景没有原因。",
        })
        service.atomic_json(folder / "analysis.json", {
            "decision": "partial", "revision_scope": "scene",
            "author_judgment": "需要补场景。",
        })
        result = service.feedback_item(self.root, "demo", item["id"])
        self.assertEqual(result["blind_reader"]["recommended_scope"], "scene")
        self.assertEqual(result["analysis"]["revision_scope"], "scene")

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
        revised = self.chapter.read_text(encoding="utf-8").replace(
            "甲把门推开。", "甲停了一下，再把门推开。"
        ) + "\n---\n\n## Metadata\n\n- word_count: 9999\n"
        service.atomic_text(folder / "proposed_revision.md", revised)
        result = service.apply_revision(self.root, "demo", item["id"])
        self.assertEqual(result["item"]["status"], "applied")
        self.assertEqual(result["receipt"]["chapter"], 1)
        self.assertIn("甲停了一下", result["current"]["content"])
        self.assertTrue((folder / "original_before_apply.md").is_file())
        self.assertIn("甲停了一下", self.chapter.read_text(encoding="utf-8"))

    def test_chapter_versions_keeps_generated_draft_candidate_and_backup(self) -> None:
        (self.project / "drafts").mkdir()
        draft = self.project / "drafts" / "2026-09-17-chapter-0001.md"
        draft.write_text(self.chapter.read_text(encoding="utf-8"), encoding="utf-8")
        item = service.create_feedback(self.root, {
            "book_id": "demo", "chapter": 1, "category": "pacing",
            "quote": "甲把门推开。", "comment": "这里太快。",
        })
        folder = self.project / "reader_feedback" / item["id"]
        service.atomic_json(folder / "analysis.json", {
            "decision": "partial", "author_judgment": "问题成立。",
            "valid_observations": ["动作缺反应"], "misdiagnoses": [],
            "revision_strategy": ["补一个反应"],
        })
        revised = self.chapter.read_text(encoding="utf-8").replace(
            "甲把门推开。", "甲停了一下，再把门推开。"
        ) + "\n---\n\n## Metadata\n\n- word_count: 9999\n"
        service.atomic_text(folder / "proposed_revision.md", revised)
        service.apply_revision(self.root, "demo", item["id"])

        result = service.chapter_versions(self.root, "demo", 1)
        kinds = {version["kind"] for version in result["versions"]}
        self.assertEqual(kinds, {"generated_draft", "proposal", "before_apply"})
        self.assertIn("甲停了一下", result["current"]["content"])

    def test_worker_result_requires_revision_only_when_author_accepts(self) -> None:
        parsed = parse_result(json.dumps({
            "decision": "reject", "author_judgment": "人物有意沉默。",
            "revision_scope": "wording", "scope_rationale": "只有一句口吻需要判断。",
            "valid_observations": ["读者感到疏远"],
            "misdiagnoses": ["疏远正是本场目的"],
            "revision_strategy": [], "proposed_revision": None,
        }, ensure_ascii=False))
        self.assertIsNone(parsed["revision"])

    def test_revision_rejects_underlength_configured_chapter(self) -> None:
        (self.project / "novel_config.md").write_text(
            "# 配置\n\n- 常规章：1900—2200字。\n", encoding="utf-8"
        )
        item = service.create_feedback(self.root, {
            "book_id": "demo", "chapter": 1, "category": "pacing",
            "quote": "甲把门推开。", "comment": "局部太快。",
        })
        folder = self.project / "reader_feedback" / item["id"]
        service.atomic_json(folder / "analysis.json", {
            "decision": "partial", "author_judgment": "问题成立。",
            "valid_observations": ["动作太快"], "misdiagnoses": [],
            "revision_strategy": ["局部补反应"],
        })
        service.atomic_text(
            folder / "proposed_revision.md",
            "# 第 1 章 试读\n\n" + "正文。" * 300
            + "\n\n---\n\n## Metadata\n\n- word_count: 2000\n",
        )
        with self.assertRaisesRegex(ValueError, "不符合本书"):
            service.apply_revision(self.root, "demo", item["id"])

    def test_revision_rejects_changes_far_outside_selected_quote(self) -> None:
        item = service.create_feedback(self.root, {
            "book_id": "demo", "chapter": 1, "category": "pacing",
            "quote": "甲把门推开。", "comment": "只修改这里。",
        })
        folder = self.project / "reader_feedback" / item["id"]
        service.atomic_json(folder / "analysis.json", {
            "decision": "partial", "author_judgment": "问题成立。",
            "valid_observations": ["动作太快"], "misdiagnoses": [],
            "revision_strategy": ["局部补反应"],
        })
        service.atomic_text(
            folder / "proposed_revision.md",
            "# 第 1 章 试读\n\n甲停了一下，再把门推开。" + "完全改写。" * 180
            + "\n\n---\n\n## Metadata\n\n- word_count: 900\n",
        )
        with self.assertRaisesRegex(ValueError, "超出允许范围"):
            service.apply_revision(self.root, "demo", item["id"])

    def test_revision_refreshes_word_count_and_invalidates_reader_check(self) -> None:
        item = service.create_feedback(self.root, {
            "book_id": "demo", "chapter": 1, "category": "pacing",
            "quote": "甲把门推开。", "comment": "这里太快。",
        })
        folder = self.project / "reader_feedback" / item["id"]
        service.atomic_json(folder / "analysis.json", {
            "decision": "partial", "author_judgment": "问题成立。",
            "valid_observations": ["动作缺反应"], "misdiagnoses": [],
            "revision_strategy": ["补一个反应"],
        })
        original = self.chapter.read_text(encoding="utf-8")
        revised = original.replace("甲把门推开。", "甲停了一下，再把门推开。")
        revised += "\n---\n\n## Metadata\n\n- word_count: 9999\n"
        service.atomic_text(folder / "proposed_revision.md", revised)
        check = self.project / "reader_checks" / "0001.json"
        service.atomic_json(check, {"narrative_sha256": "old"})
        literary = self.project / "literary_reviews" / "0001.json"
        report = self.project / "module_reports" / "0001.json"
        service.atomic_json(literary, {"narrative_sha256": "old"})
        service.atomic_json(report, {"narrative_sha256": "old"})
        result = service.apply_revision(self.root, "demo", item["id"])
        expected = len(service._compact(service._body_text(revised)))
        self.assertIn(
            f"- word_count: {expected}", self.chapter.read_text(encoding="utf-8")
        )
        self.assertFalse(check.exists())
        self.assertTrue((folder / "reader_check_before_apply.json").is_file())
        self.assertFalse(literary.exists())
        self.assertFalse(report.exists())
        self.assertTrue((folder / "literary_review_before_apply.json").is_file())
        self.assertTrue((folder / "module_report_before_apply.json").is_file())
        self.assertEqual(
            result["receipt"]["invalidated_checks"],
            ["读者验收", "文学终审", "创作能力报告"],
        )

    def test_chapter_scope_allows_structural_rewrite_beyond_selected_quote(self) -> None:
        item = service.create_feedback(self.root, {
            "book_id": "demo", "chapter": 1, "category": "pacing",
            "quote": "甲把门推开。", "comment": "整章缺少进入场景的背景。",
        })
        folder = self.project / "reader_feedback" / item["id"]
        service.atomic_json(folder / "analysis.json", {
            "decision": "accept", "revision_scope": "chapter",
            "scope_rationale": "问题影响整章的信息顺序。",
            "author_judgment": "需要重做结构。", "valid_observations": ["缺少参照"],
            "misdiagnoses": [], "revision_strategy": ["重排整章"],
        })
        service.atomic_text(
            folder / "proposed_revision.md",
            "# 第 1 章 试读\n\n" + "新的完整场景。" * 220
            + "\n\n---\n\n## Metadata\n\n- word_count: 9999\n",
        )
        result = service.apply_revision(self.root, "demo", item["id"])
        self.assertEqual(result["receipt"]["revision_scope"], "chapter")
        self.assertIn("新的完整场景", self.chapter.read_text(encoding="utf-8"))

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
            "revision_scope": "scene", "scope_rationale": "读感涉及完整场景，但作者决定保留。",
            "valid_observations": [], "misdiagnoses": ["不是长期问题"],
            "revision_strategy": [], "learning_candidate": None,
            "proposed_revision": None,
        }, ensure_ascii=False))
        self.assertIsNone(parsed["analysis"]["learning_candidate"])

    def test_follow_up_result_can_replace_author_judgment(self) -> None:
        parsed = parse_follow_up_result(json.dumps({
            "reply": "你说得对，原判断拿人物目的替台词自然度辩护了。",
            "changed_judgment": True,
            "analysis": {
                "decision": "partial", "revision_scope": "wording",
                "scope_rationale": "局部台词失真。",
                "author_judgment": "这句话不像现场中的真人表达。",
                "valid_observations": ["抽象概括被塞进人物嘴里"],
                "misdiagnoses": [], "revision_strategy": ["改为眼前动作"],
                "learning_candidate": None,
            },
            "proposed_revision": "# 第 1 章 试读\n\n正文\n\n---\n\n## Metadata\n\n- word_count: 500",
        }, ensure_ascii=False))
        self.assertTrue(parsed["changed_judgment"])
        self.assertEqual(parsed["analysis"]["decision"], "partial")

    def test_blind_reader_result_requires_explicit_scope(self) -> None:
        parsed = parse_blind_reader_result(json.dumps({
            "reader_experience": "读者不知道人物为什么此刻进门。",
            "visible_facts": ["有人推门"],
            "missing_or_late_information": ["来访目的"],
            "recommended_scope": "scene",
            "scope_rationale": "需要补全整个进门场景的行动原因。",
            "evidence": ["甲把门推开。"],
        }, ensure_ascii=False))
        self.assertEqual(parsed["recommended_scope"], "scene")


if __name__ == "__main__":
    unittest.main()
