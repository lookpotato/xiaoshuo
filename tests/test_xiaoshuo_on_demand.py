from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from xiaoshuo_on_demand import expected_regular_slot, normalize_schedule_entry


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


if __name__ == "__main__":
    unittest.main()
