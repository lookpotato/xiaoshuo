from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import novel_stage_pipeline as pipeline
from novel_reader_gate import narrative_sha256


class NovelStagePipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "book"
        (self.project / "chapters").mkdir(parents=True)
        (self.root / "novel_pipeline.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "enabled": True,
                    "recent_chapters_for_director": 5,
                    "recent_chapters_for_reviewer": 2,
                    "max_new_questions_per_chapter": 1,
                    "plan_directory": "chapter_plans",
                    "review_directory": "literary_reviews",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        for name in (
            "novel_config.md",
            "outline.md",
            "characters.md",
            "world.md",
            "style_guide.md",
            "chapter_state.json",
        ):
            (self.project / name).write_text(name, encoding="utf-8")
        (self.project / "chapters" / "0001-开门.md").write_text(
            "# 第 1 章 开门\n\n阿澄用肩抵住房门。母亲还在门外，他却听见追兵已经上楼。\n",
            encoding="utf-8",
        )
        self.chapter = self.project / "chapters" / "0002-留下谁.md"
        self.chapter.write_text(
            "# 第 2 章 留下谁\n\n"
            "母亲还在门外，阿澄却只能先挡住追兵。\n\n"
            "妹妹把钥匙塞进他手里，说她回去接人。\n\n"
            "阿澄没有答应。他锁门的手停了很久，最终把钥匙还给妹妹。\n\n"
            "门锁上了，妹妹留在外面。楼下随即传来母亲叫她乳名的声音。\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def valid_plan(self) -> dict:
        return {
            "schema_version": 1,
            "chapter_number": 2,
            "planning_context_sha256": pipeline.planning_context_sha256(
                self.root, self.project, 2
            ),
            "immediate_goal": "在追兵上楼前决定是否开门救母亲",
            "reader_concern": "兄妹谁承担留下的代价",
            "character_drives": [
                {"name": "阿澄", "want": "保住妹妹", "fear": "再失去家人", "pressure": "追兵正在上楼"},
                {"name": "妹妹", "want": "接回母亲", "fear": "母亲死在门外", "pressure": "门马上要锁"},
            ],
            "central_conflict": "兄妹都想让自己留下",
            "central_choice": "阿澄必须决定钥匙交给谁",
            "emotional_progression": {
                "opening": "急迫",
                "pressure": "母亲尚未回来",
                "peak": "阿澄交还钥匙",
                "aftermath": "妹妹被关在门外",
            },
            "promise": {"existing": "母亲能否回来", "treatment": "advance", "concrete_gain": "确认母亲已经到楼下"},
            "new_questions": ["妹妹能否带母亲上楼"],
            "irreversible_change": "兄妹被门分开",
            "scene_plan": [
                {"purpose": "争夺钥匙", "conflict": "两人都要出去", "turn": "钥匙交给妹妹"},
                {"purpose": "锁门", "conflict": "追兵抵达", "turn": "妹妹留在门外"},
            ],
            "tone_risks": ["家人可能死亡时不拿其处境开玩笑"],
            "forbidden_shortcuts": ["不得突然出现救兵"],
        }

    def write_plan(self, value: dict | None = None) -> None:
        path = pipeline.plan_path(self.root, self.project, 2)
        path.parent.mkdir()
        path.write_text(
            json.dumps(value or self.valid_plan(), ensure_ascii=False),
            encoding="utf-8",
        )

    def valid_review(self) -> dict:
        quotes = [
            "母亲还在门外，阿澄却只能先挡住追兵。",
            "妹妹把钥匙塞进他手里，说她回去接人。",
            "阿澄没有答应。他锁门的手停了很久，最终把钥匙还给妹妹。",
            "门锁上了，妹妹留在外面。楼下随即传来母亲叫她乳名的声音。",
        ]
        return {
            "schema_version": 1,
            "chapter_number": 2,
            "mode": pipeline.REVIEW_MODE,
            "narrative_sha256": narrative_sha256(self.chapter),
            "decision": "pass",
            "dimensions": {
                key: {
                    "verdict": "pass",
                    "assessment": f"{key} 有具体人物选择和后果。",
                    "evidence": [quotes[index % len(quotes)]],
                }
                for index, key in enumerate(pipeline.REVIEW_DIMENSIONS)
            },
            "blocking_issues": [],
            "most_fragile_passage": {
                "quote": "阿澄没有答应。他锁门的手停了很久，最终把钥匙还给妹妹。",
                "risk": "动作概括较快，可能削弱犹豫的重量",
                "why_acceptable": "前后选择和代价清楚，没有替人物解释主题",
            },
            "strengths_to_preserve": ["交还钥匙的选择直接改变兄妹处境"],
        }

    def write_review(self, value: dict) -> None:
        path = pipeline.review_path(self.root, self.project, 2)
        path.parent.mkdir()
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def valid_dialogue_review(self) -> dict:
        return {
            "schema_version": 1,
            "chapter_number": 2,
            "mode": pipeline.DIALOGUE_REVIEW_MODE,
            "narrative_sha256": narrative_sha256(self.chapter),
            "decision": "pass",
            "assessment": "本章没有直接对白，人物选择主要通过动作呈现。",
            "issues": [],
            "strengths_to_preserve": ["交还钥匙的动作保留了人物犹豫"],
        }

    def test_director_is_generic_and_reads_only_existing_book_sources(self) -> None:
        prompt = pipeline.director_prompt(self.root, self.project, 2, "作者约束")
        self.assertIn("outline.md", prompt)
        self.assertNotIn("resource_ledger.md", prompt)
        self.assertNotIn("404修理站", prompt)
        self.assertNotIn("道友你这天命与我有缘", prompt)
        self.assertIn("不得运行 git add、commit 或 push", prompt)
        self.assertIn("新增悬念超过", str(self._new_question_error()))

    def _new_question_error(self) -> Exception:
        plan = self.valid_plan()
        plan["new_questions"] = ["问题一", "问题二"]
        self.write_plan(plan)
        with self.assertRaises(pipeline.PipelineValidationError) as caught:
            pipeline.validate_plan(self.root, self.project, 2)
        return caught.exception

    def test_valid_plan_requires_emotion_characters_and_old_promise(self) -> None:
        self.write_plan()
        self.assertEqual(
            pipeline.validate_plan(self.root, self.project, 2)["chapter_number"], 2
        )

    def test_plan_becomes_stale_when_book_context_changes(self) -> None:
        self.write_plan()
        (self.project / "outline.md").write_text("新的故事方向", encoding="utf-8")
        with self.assertRaisesRegex(
            pipeline.PipelineValidationError, "作品资料已变化"
        ):
            pipeline.validate_plan(self.root, self.project, 2)

    def test_plan_survives_expected_archive_state_updates(self) -> None:
        self.write_plan()
        (self.project / "chapter_state.json").write_text(
            '{"last_completed_chapter": 2, "next_chapter_number": 3}',
            encoding="utf-8",
        )
        (self.project / "continuity_ledger.md").write_text(
            "第 2 章新增的连续性记录", encoding="utf-8"
        )
        (self.project / "resource_ledger.md").write_text(
            "第 2 章新增的资源变化", encoding="utf-8"
        )
        (self.project / "feedback_learning.json").write_text(
            '{"chapter": 2}', encoding="utf-8"
        )
        self.assertEqual(
            pipeline.validate_plan(self.root, self.project, 2)["chapter_number"], 2
        )

    def test_single_character_single_scene_chapter_is_supported(self) -> None:
        plan = self.valid_plan()
        plan["character_drives"] = plan["character_drives"][:1]
        plan["scene_plan"] = plan["scene_plan"][:1]
        self.write_plan(plan)
        self.assertEqual(
            pipeline.validate_plan(self.root, self.project, 2)["chapter_number"], 2
        )

    def test_independent_reviewer_cannot_see_plan_or_book_bible(self) -> None:
        self.write_plan()
        prompt = pipeline.reviewer_prompt(self.root, self.project, 2)
        self.assertIn("`chapter.md`", prompt)
        self.assertNotIn(str(self.chapter.resolve()), prompt)
        self.assertNotIn(str((self.project / "chapters" / "0001-开门.md").resolve()), prompt)
        self.assertNotIn("chapter_plans", prompt)
        self.assertNotIn("style_guide.md", prompt)
        self.assertNotIn("outline.md", prompt)
        self.assertIn("reader_orientation", prompt)
        self.assertIn("不得运行", prompt)
        self.assertIn("git add、commit 或 push", prompt)

    def test_valid_independent_review_passes(self) -> None:
        self.write_review(self.valid_review())
        self.assertEqual(
            pipeline.validate_literary_review(self.root, self.project, 2)["decision"],
            "pass",
        )
        self.assertEqual(pipeline.literary_review_errors(self.root, self.project, 2), [])

    def test_dialogue_reviewer_has_only_current_prose_and_reality_test(self) -> None:
        prompt = pipeline.dialogue_reviewer_prompt(self.project, 2)
        self.assertIn("`chapter.md`", prompt)
        self.assertIn("人物目的正确", prompt)
        self.assertIn("作者概括", prompt)
        self.assertNotIn("style_guide.md", prompt)
        self.assertNotIn(str(self.chapter.resolve()), prompt)

    def test_valid_dialogue_review_passes(self) -> None:
        path = pipeline.dialogue_review_path(self.project, 2)
        path.parent.mkdir()
        path.write_text(
            json.dumps(self.valid_dialogue_review(), ensure_ascii=False),
            encoding="utf-8",
        )
        self.assertEqual(
            pipeline.validate_dialogue_review(self.project, 2)["decision"], "pass"
        )
        self.assertEqual(pipeline.dialogue_review_errors(self.project, 2), [])

    def test_dialogue_review_requires_literal_problem_quote(self) -> None:
        review = self.valid_dialogue_review()
        review["decision"] = "revise"
        review["issues"] = [{
            "quote": "不存在的台词",
            "speaker_intent": "阻止对方",
            "diagnosis": "像作者概括",
            "natural_direction": "指向眼前动作",
        }]
        path = pipeline.dialogue_review_path(self.project, 2)
        path.parent.mkdir()
        path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(pipeline.PipelineValidationError, "引用不在正文"):
            pipeline.validate_dialogue_review(self.project, 2)

    def test_review_quotes_are_canonicalized_only_from_unique_body_text(self) -> None:
        self.chapter.write_text(
            "# 第 2 章 留下谁\n\n"
            "“我知道。你再试，它就不只是你的响应了。”\n\n"
            "“查谁碰过它。”\n\n“还有？”\n\n“什么时候碰的。”\n\n"
            "文件名只剩下半个字母：\n\n`L_`\n",
            encoding="utf-8",
        )
        review = self.valid_review()
        review["dimensions"]["dialogue_in_context"]["evidence"] = [
            "“你再试，它就不只是你的响应了。”"
        ]
        review["dimensions"]["narrative_progress"]["evidence"] = [
            "“查谁碰过它。”“还有？”“什么时候碰的。”"
        ]
        review["dimensions"]["next_chapter_pull"]["evidence"] = [
            "文件名只剩下半个字母：`L_`"
        ]
        body = pipeline.chapter_narrative_text(self.chapter)
        normalized = pipeline.canonicalize_literary_review_quotes(review, body)
        self.assertEqual(
            normalized["dimensions"]["dialogue_in_context"]["evidence"][0],
            "“我知道。你再试，它就不只是你的响应了。”",
        )
        self.assertEqual(
            normalized["dimensions"]["narrative_progress"]["evidence"][0],
            "“查谁碰过它。”\n\n“还有？”\n\n“什么时候碰的。”",
        )
        self.assertEqual(
            normalized["dimensions"]["next_chapter_pull"]["evidence"][0],
            "文件名只剩下半个字母：\n\n`L_`",
        )

    def test_review_quote_normalizer_does_not_guess_missing_prose(self) -> None:
        review = self.valid_review()
        review["dimensions"]["dialogue_in_context"]["evidence"] = [
            "正文里不存在的概括"
        ]
        body = pipeline.chapter_narrative_text(self.chapter)
        normalized = pipeline.canonicalize_literary_review_quotes(review, body)
        self.assertEqual(
            normalized["dimensions"]["dialogue_in_context"]["evidence"][0],
            "正文里不存在的概括",
        )

    def test_contextually_wrong_dialogue_can_block_even_when_causality_is_clear(self) -> None:
        review = self.valid_review()
        review["decision"] = "revise"
        review["dimensions"]["dialogue_in_context"]["verdict"] = "revise"
        review["blocking_issues"] = [
            {
                "dimension": "dialogue_in_context",
                "quote": "妹妹把钥匙塞进他手里，说她回去接人。",
                "diagnosis": "这句若被写成玩笑，会削弱母亲遇险时的恐惧与兄妹冲突",
                "repair_scope": "scene",
            }
        ]
        self.write_review(review)
        errors = pipeline.literary_review_errors(self.root, self.project, 2)
        self.assertTrue(any("削弱" in error for error in errors))

    def test_redesign_must_return_to_chapter_plan(self) -> None:
        review = self.valid_review()
        review["decision"] = "redesign"
        review["dimensions"]["narrative_progress"]["verdict"] = "revise"
        review["blocking_issues"] = [
            {
                "dimension": "narrative_progress",
                "quote": "母亲还在门外，阿澄却只能先挡住追兵。",
                "diagnosis": "整章重复上一章的守门局面",
                "repair_scope": "chapter",
            }
        ]
        self.write_review(review)
        with self.assertRaisesRegex(pipeline.PipelineValidationError, "未退回章节合同"):
            pipeline.validate_literary_review(self.root, self.project, 2)


if __name__ == "__main__":
    unittest.main()
