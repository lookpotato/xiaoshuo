from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path

from xiaoshuo_on_demand import (
    _codex_result_detail,
    expected_regular_slot,
    normalize_schedule_entry,
    should_publish_immediately,
    uploaded_today_count,
)


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


if __name__ == "__main__":
    unittest.main()
