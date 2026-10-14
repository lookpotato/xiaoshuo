from __future__ import annotations

import json
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path

import xiaoshuo_on_demand
from fanqie_novel_manager import _readable_interaction_map, validate_parallel_character_threads
from xiaoshuo_on_demand import (
    _codex_result_detail,
    enforce_local_archive_gates,
    ensure_unique_chapter_title,
    expected_regular_slot,
    normalize_schedule_entry,
    local_write_only_prompt,
    pending_chapter,
    record_upload,
    should_publish_immediately,
    uploaded_today_count,
)
from fanqie_browser_worker import Chapter


class ParallelCharacterThreadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        self.thread_dir = self.project / "character_threads" / "0007"
        self.thread_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_valid_threads(self) -> None:
        (self.thread_dir / "00-cast.md").write_text(
            "# 本章人物\n- 甲：目标\n- 乙：阻力\n", encoding="utf-8"
        )
        (self.thread_dir / "01-甲.md").write_text("甲的独立动向\n", encoding="utf-8")
        (self.thread_dir / "02-乙.md").write_text("乙的独立动向\n", encoding="utf-8")
        (self.thread_dir / "interaction_map.md").write_text(
            "|场景|人物行动|相互影响|结果|\n"
            "|---|---|---|---|\n"
            "|门口|甲守门，乙转移物资|乙拿走工具后甲改变守门办法|两人守住入口但耗尽工具|\n"
            "|后院|乙引开追兵，甲关闭侧门|甲的关门动作给乙留下撤退窗口|追兵改道，两人分别受伤|\n",
            encoding="utf-8",
        )
        (self.thread_dir / "state_update.md").write_text(
            "甲：下一状态\n乙：下一状态\n", encoding="utf-8"
        )

    def test_valid_parallel_threads_pass(self) -> None:
        self.write_valid_threads()
        self.assertEqual(validate_parallel_character_threads(self.project, 7), [])

    def test_missing_character_private_line_is_rejected(self) -> None:
        self.write_valid_threads()
        (self.thread_dir / "02-乙.md").unlink()
        errors = validate_parallel_character_threads(self.project, 7)
        self.assertTrue(any("人物 乙 缺少独立人物线" in error for error in errors))

    def test_cast_metadata_is_not_treated_as_characters(self) -> None:
        self.write_valid_threads()
        (self.thread_dir / "00-cast.md").write_text(
            "# 本章人物线总表\n"
            "\n"
            "- 主要视角：甲。\n"
            "- 出场人物：甲、乙。\n"
            "- 当章目标：守住门。\n"
            "- 当章小胜负：门没倒。\n"
            "- 主要钩子：门外还有人。\n",
            encoding="utf-8",
        )
        errors = validate_parallel_character_threads(self.project, 7)
        self.assertTrue(any("未列出人物名单" in error for error in errors))
        self.assertFalse(any("人物 主要视角" in error for error in errors))
        self.assertFalse(any("人物 当章目标" in error for error in errors))

    def test_numbered_interaction_log_is_readable(self) -> None:
        self.assertTrue(_readable_interaction_map(
            "1. 甲改变路线，乙因此失去掩护。\n"
            "2. 丙的行动迫使甲承担代价，结果队伍撤离。\n" * 4,
            ["甲", "乙", "丙"],
        ))

    def test_interaction_map_does_not_depend_on_fixed_keywords(self) -> None:
        text = (
            "段野顶住院门，魏平向门外报出站内资产，苏青禾当场截住凭证，"
            "门外机甲暂时不能进院，魏平却靠近了旧收货台。\n"
            "白露拆下蓄热板救人，老崔少了稳住热管的材料，只能拆门片补管夹，"
            "伤员保住清醒，院门结构随即变得更脆。\n"
        )
        self.assertTrue(_readable_interaction_map(
            text, ["段野", "魏平", "苏青禾", "白露", "老崔"]
        ))

    def test_interaction_map_rejects_single_character_filler(self) -> None:
        text = (
            "段野站在门口反复检查设备，记录天气、温度和门锁状态，随后继续检查。\n"
            "段野又检查一遍设备，记录相同项目，没有其他人物参与，也没有交织内容。\n"
        )
        self.assertFalse(_readable_interaction_map(text, ["段野", "林小满"]))

    def test_reader_gate_failure_rolls_back_state_before_success(self) -> None:
        self.write_valid_threads()
        state_path = self.project / "chapter_state.json"
        state_path.write_text(
            json.dumps({"last_completed_chapter": 7, "next_chapter_number": 8}),
            encoding="utf-8",
        )
        original = {"last_completed_chapter": 6, "next_chapter_number": 7}
        with patch.object(
            xiaoshuo_on_demand.manager,
            "validate_reader_checks",
            return_value=["第 7 章正文哈希不一致"],
        ):
            with self.assertRaisesRegex(RuntimeError, "正文哈希不一致"):
                enforce_local_archive_gates(self.project, 7, 1, original)
        self.assertEqual(
            json.loads(state_path.read_text(encoding="utf-8")), original
        )


class LocalWritingPromptTests(unittest.TestCase):
    def test_write_only_prompt_requires_shared_chinese_dialogue_base(self) -> None:
        prompt = local_write_only_prompt("cosmic-404", {"id": "test-job"})
        self.assertIn("shared/chinese_dialogue_foundation.md", prompt)
        self.assertIn("shared/chinese_dialogue_feedback.jsonl", prompt)
        self.assertIn("shared/narrative_prose_foundation.md", prompt)
        self.assertIn("narrative_style_pack.md", prompt)
        self.assertIn("人物的关系", prompt)
        self.assertIn("口语逆翻译", prompt)
        self.assertIn("不能全书固定替换", prompt)

class RegularScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        self.schedule = self.project / "batch_schedule_0048_0067.json"
        self.data = {
            "timezone": "Asia/Shanghai",
            "books": [
                {
                    "id": "cosmic-404",
                    "path": str(self.project),
                    "daily_chapter_target": 2,
                    "default_publish_times": ["12:00"],
                    "schedule": {
                        "days": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                        "time": "12:00",
                    },
                }
            ],
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_entries(self, entries: list[dict]) -> None:
        self.schedule.write_text(
            json.dumps({"entries": entries}, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_duplicate_chapter_title_is_rejected_before_upload(self) -> None:
        chapters = self.project / "chapters"
        chapters.mkdir()
        body = "正文 " * 300
        existing_path = (chapters / "0001-先修热管.md").resolve()
        existing_path.write_text("", encoding="utf-8")
        existing = Chapter(
            number=1, title="先修热管", body=body, path=existing_path
        )
        current = Chapter(
            number=2,
            title="先修热管！",
            body=body,
            path=(chapters / "0002-先修热管二.md").resolve(),
        )
        with patch.object(xiaoshuo_on_demand, "parse_chapter", return_value=existing):
            with self.assertRaisesRegex(RuntimeError, "0001-先修热管.md"):
                ensure_unique_chapter_title(self.project, current)

    def test_second_chapter_reuses_same_date_when_time_is_shared(self) -> None:
        self.write_entries(
            [
                {"chapter": 48, "date": "2026-08-13", "time": "12:00"},
                {"chapter": 49, "date": "2026-08-14", "time": "12:00"},
                {"chapter": 50, "date": "2026-08-15", "time": "12:00"},
            ]
        )
        self.assertEqual(
            expected_regular_slot(self.data, "cosmic-404", self.project, 50),
            ("2026-08-14", "12:00"),
        )

    def test_full_day_advances_to_next_allowed_day(self) -> None:
        self.write_entries(
            [
                {"chapter": 48, "date": "2026-08-13", "time": "12:00"},
                {"chapter": 49, "date": "2026-08-13", "time": "12:00"},
                {"chapter": 50, "date": "2026-08-14", "time": "12:00"},
            ]
        )
        self.assertEqual(
            expected_regular_slot(self.data, "cosmic-404", self.project, 50),
            ("2026-08-14", "12:00"),
        )

    def test_second_chapter_uses_second_distinct_time(self) -> None:
        self.data["books"][0]["default_publish_times"] = ["18:30", "20:30"]
        self.write_entries(
            [
                {"chapter": 12, "date": "2026-08-13", "time": "18:30"},
                {"chapter": 13, "date": "2026-08-13", "time": "20:30"},
                {"chapter": 14, "date": "2026-08-14", "time": "18:30"},
            ]
        )
        self.assertEqual(
            expected_regular_slot(self.data, "cosmic-404", self.project, 15),
            ("2026-08-14", "20:30"),
        )

    def test_normalize_repairs_new_entry_before_upload(self) -> None:
        self.write_entries(
            [
                {"chapter": 48, "date": "2026-08-13", "time": "12:00"},
                {"chapter": 49, "date": "2026-08-14", "time": "12:00"},
                {"chapter": 50, "date": "2026-08-15", "time": "12:00"},
            ]
        )
        changed = normalize_schedule_entry(
            self.data, "cosmic-404", self.project, 50
        )
        self.assertEqual(changed, self.schedule)
        entry = json.loads(self.schedule.read_text(encoding="utf-8"))["entries"][2]
        self.assertEqual((entry["date"], entry["time"]), ("2026-08-14", "12:00"))
        self.assertIn("schedule_normalized_at", entry)

    def test_unfilled_daily_quota_uses_immediate_publish(self) -> None:
        self.data["books"][0]["daily_chapter_target"] = 2
        today = "2026-08-20"
        self.write_entries(
            [
                {
                    "chapter": 48,
                    "date": today,
                    "time": "18:30",
                    "status": "submitted_pending_review",
                },
                {
                    "chapter": 49,
                    "date": today,
                    "time": "20:30",
                    "status": "local_archived",
                },
            ]
        )
        now = datetime(2026, 8, 20, 22, 26, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(uploaded_today_count(self.data, self.project, now), 1)
        self.assertTrue(
            should_publish_immediately(self.data, "cosmic-404", self.project, now)
        )

    def test_full_daily_quota_uses_next_schedule_slot(self) -> None:
        self.data["books"][0]["default_publish_times"] = ["18:30", "20:30"]
        self.data["books"][0]["daily_chapter_target"] = 2
        today = "2026-08-20"
        self.write_entries(
            [
                {
                    "chapter": 48,
                    "date": today,
                    "time": "18:30",
                    "status": "submitted_pending_review",
                },
                {
                    "chapter": 49,
                    "date": today,
                    "time": "20:30",
                    "status": "submitted_pending_review",
                },
                {
                    "chapter": 50,
                    "date": today,
                    "time": "20:30",
                    "status": "local_archived",
                },
            ]
        )
        now = datetime(2026, 8, 20, 22, 26, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(uploaded_today_count(self.data, self.project, now), 2)
        self.assertFalse(
            should_publish_immediately(self.data, "cosmic-404", self.project, now)
        )
        self.assertEqual(
            expected_regular_slot(self.data, "cosmic-404", self.project, 50),
            ("2026-08-21", "18:30"),
        )

    def test_codex_result_detail_does_not_copy_codex_chapter_text(self) -> None:
        result = self.project / "result.md"
        result.write_text(
            "已调用 ImageGen，但道具孔位数量未通过视觉核验，因此只保留草稿。",
            encoding="utf-8",
        )
        detail = _codex_result_detail(result)
        self.assertIn("未发现章节归档", detail)
        self.assertNotIn("孔位数量", detail)

    def test_explicit_local_not_uploaded_status_overrides_stale_schedule(self) -> None:
        chapters = self.project / "chapters"
        chapters.mkdir()
        chapter_path = chapters / "0098-漏传.md"
        chapter_path.write_text(
            "# 第98章 漏传\n\n" + ("正文 " * 250) +
            "\n\n---\n\n## Metadata\n\n- upload_status: not_uploaded\n",
            encoding="utf-8",
        )
        self.write_entries([
            {
                "chapter": 98,
                "date": "2026-08-20",
                "time": "12:00",
                "status": "submitted_pending_review",
            }
        ])
        pending = pending_chapter(self.project)
        self.assertIsNotNone(pending)
        self.assertEqual(pending[2], chapter_path)

    def test_backfill_does_not_regress_last_uploaded_chapter(self) -> None:
        self.write_entries([
            {
                "chapter": 98,
                "date": "2026-08-20",
                "time": "12:00",
                "status": "local_archived",
            }
        ])
        chapter_path = self.project / "chapters" / "0098-漏传.md"
        chapter_path.parent.mkdir(exist_ok=True)
        (self.project / "logs").mkdir(exist_ok=True)
        chapter_path.write_text(
            "# 第98章 漏传\n\n正文\n\n---\n\n## Metadata\n\n"
            "- upload_status: not_uploaded\n",
            encoding="utf-8",
        )
        state_path = self.project / "chapter_state.json"
        state_path.write_text(
            json.dumps({
                "last_uploaded_chapter": 99,
                "last_uploaded_status": "submitted_pending_review",
                "last_uploaded_at": "2026-08-20T12:00:00+08:00",
            }),
            encoding="utf-8",
        )
        result = {
            "status": "审核中",
            "url": "https://fanqienovel.com/main/writer/chapter-manage/test",
        }
        record_upload(
            self.data,
            "cosmic-404",
            self.project,
            self.schedule,
            {"chapter": 98, "date": "2026-08-20", "time": "12:00"},
            chapter_path,
            result,
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["last_uploaded_chapter"], 99)


if __name__ == "__main__":
    unittest.main()
