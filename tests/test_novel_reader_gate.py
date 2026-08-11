from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from novel_reader_gate import narrative_sha256, validate_reader_checks


class ReaderGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        (self.project / "chapters").mkdir()
        (self.project / "reader_checks").mkdir()
        self.chapter = self.project / "chapters" / "0010-test.md"
        self.chapter.write_text(
            "# 第 10 章 测试\n\n"
            "上一章留下的闸门仍在漏水，所以众人必须在天亮前堵住缺口。\n\n"
            "水位每分钟上涨一寸，再拖延就会淹没伤员所在的底舱。\n\n"
            "林舟看见左侧铆钉先冒气泡，据此判断裂缝藏在铁板背面。\n\n"
            "他先关闭支管降低压力，再把木楔钉进裂缝；压力变小后，木楔才不会被水流顶飞。\n\n"
            "漏水停了，但关闭支管也让上层失去供暖，众人只争取到半个时辰。\n\n"
            "就在他们准备转移伤员时，木楔背后传来了第二次敲击。\n\n"
            "---\n\n## Metadata\n\n- upload_status: not_uploaded\n",
            encoding="utf-8",
        )
        sentences = [
            "上一章留下的闸门仍在漏水，所以众人必须在天亮前堵住缺口。",
            "水位每分钟上涨一寸，再拖延就会淹没伤员所在的底舱。",
            "林舟看见左侧铆钉先冒气泡，据此判断裂缝藏在铁板背面。",
            "他先关闭支管降低压力，再把木楔钉进裂缝；压力变小后，木楔才不会被水流顶飞。",
            "漏水停了，但关闭支管也让上层失去供暖，众人只争取到半个时辰。",
            "就在他们准备转移伤员时，木楔背后传来了第二次敲击。",
        ]
        keys = [
            "previous_context",
            "immediate_problem",
            "decision_basis",
            "action_logic",
            "result_and_cost",
            "reader_hook",
        ]
        self.check = {
            "schema_version": 1,
            "chapter_number": 10,
            "mode": "outline-blind-first-reader",
            "outline_blind": True,
            "reviewer": "codex-reader-gate",
            "status": "passed",
            "reviewed_at": "2026-08-11T18:00:00+08:00",
            "revision_rounds": 1,
            "narrative_sha256": narrative_sha256(self.chapter),
            "questions": {
                key: {"answer": f"能够从正文直接解释{key}的前因后果。", "evidence": [sentence]}
                for key, sentence in zip(keys, sentences)
            },
            "new_terms": [],
            "unexplained_terms": [],
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_check(self) -> None:
        (self.project / "reader_checks" / "0010.json").write_text(
            json.dumps(self.check, ensure_ascii=False), encoding="utf-8"
        )

    def test_valid_outline_blind_review_passes(self) -> None:
        self.write_check()
        self.assertEqual(validate_reader_checks(self.project, 10), [])

    def test_missing_review_blocks_archived_chapter(self) -> None:
        errors = validate_reader_checks(self.project, 10)
        self.assertTrue(any("验收缺失" in error for error in errors))

    def test_evidence_must_exist_in_final_prose(self) -> None:
        self.check["questions"]["decision_basis"]["evidence"] = ["大纲里才有的依据"]
        self.write_check()
        errors = validate_reader_checks(self.project, 10)
        self.assertTrue(any("逐字存在于正文" in error for error in errors))

    def test_text_change_requires_new_review(self) -> None:
        self.write_check()
        self.chapter.write_text(
            self.chapter.read_text(encoding="utf-8").replace("半个时辰", "一刻钟"),
            encoding="utf-8",
        )
        errors = validate_reader_checks(self.project, 10)
        self.assertTrue(any("正文哈希不一致" in error for error in errors))

    def test_unexplained_terms_block_pass(self) -> None:
        self.check["unexplained_terms"] = ["回压匣"]
        self.write_check()
        errors = validate_reader_checks(self.project, 10)
        self.assertTrue(any("未解释名词" in error for error in errors))

    def test_causal_evidence_must_follow_reader_order(self) -> None:
        questions = self.check["questions"]
        questions["decision_basis"]["evidence"], questions["result_and_cost"][
            "evidence"
        ] = (
            questions["result_and_cost"]["evidence"],
            questions["decision_basis"]["evidence"],
        )
        self.write_check()
        errors = validate_reader_checks(self.project, 10)
        self.assertTrue(any("因果证据顺序错误" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
