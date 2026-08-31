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
from fanqie_novel_manager import validate_parallel_character_threads
from xiaoshuo_on_demand import (
    _codex_result_detail,
    ensure_unique_chapter_title,
    expected_regular_slot,
    normalize_schedule_entry,
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
            "|人物|交织|\n|---|---|\n|甲|乙|\n", encoding="utf-8"
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

    def test_codex_result_detail_exposes_image_gate_failure(self) -> None:
        result = self.project / "result.md"
        result.write_text(
            "已调用 ImageGen，但道具孔位数量未通过视觉核验，因此只保留草稿。",
            encoding="utf-8",
        )
        self.assertIn("孔位数量未通过", _codex_result_detail(result))

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
