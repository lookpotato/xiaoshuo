from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fanqie_browser_worker import (
    FanqieBlocked,
    PublishConfig,
    chapter_manage_url,
    submit_publish_confirmation,
)
from xiaoshuo_reward import (
    build_new_reward_plan,
    default_reward_time,
    reward_job_crosses_published_floor,
    record_reward,
    reward_candidates,
    validate_time,
)


CN = timezone(timedelta(hours=8))


class RewardTimeTests(unittest.TestCase):
    def test_default_time_adds_lead_and_rounds_up(self) -> None:
        now = datetime(2026, 8, 1, 15, 3, tzinfo=CN)
        self.assertEqual(default_reward_time(now), "15:50")

    def test_explicit_time_must_be_in_future(self) -> None:
        now = datetime(2026, 8, 1, 15, 3, tzinfo=CN)
        self.assertEqual(validate_time("15:50", now), "15:50")
        with self.assertRaises(ValueError):
            validate_time("15:30", now)


class BrowserRouteTests(unittest.TestCase):
    def test_reuses_verified_chapter_manage_url_from_local_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            expected = (
                "https://fanqienovel.com/main/writer/chapter-manage/123&book?type=1"
            )
            (project / "batch_schedule_0001_0020.json").write_text(
                json.dumps({"entries": [{"fanqie_url": expected}]}),
                encoding="utf-8",
            )
            config = PublishConfig(
                writer_url="https://fanqienovel.com/main/writer/123/publish/456",
                book_id="123",
                submit_publish=True,
            )
            self.assertEqual(chapter_manage_url(config, project), expected)

    def test_surfaces_business_rejection_from_publish_api(self) -> None:
        class Response:
            def json(self):
                return {"code": -1019, "message": "提交字数超出每日上限"}

        class ResponseInfo:
            value = Response()

        class ExpectResponse:
            def __enter__(self):
                return ResponseInfo()

            def __exit__(self, *_args):
                return False

        class Page:
            def expect_response(self, _predicate, timeout):
                self.timeout = timeout
                return ExpectResponse()

        class Confirm:
            def evaluate(self, _script):
                return None

        with self.assertRaisesRegex(FanqieBlocked, "提交字数超出每日上限"):
            submit_publish_confirmation(Page(), Confirm())


class RewardScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        (self.project / "chapters").mkdir()
        (self.project / "logs").mkdir()
        (self.project / "chapter_state.json").write_text("{}", encoding="utf-8")
        for number in range(28, 32):
            (self.project / "chapters" / f"{number:04d}-test.md").write_text(
                f"# 第{number}章 测试标题{number}\n\n" + "正文" * 600,
                encoding="utf-8",
            )
        self.schedule = self.project / "batch_schedule_0028_0047.json"
        self.schedule.write_text(
            json.dumps(
                {
                    "entries": [
                        {
                            "chapter": 28,
                            "date": "2026-08-01",
                            "time": "18:00",
                            "status": "published",
                        },
                        {
                            "chapter": 29,
                            "date": "2026-08-02",
                            "time": "12:00",
                            "status": "submitted_pending_review",
                        },
                        {
                            "chapter": 30,
                            "date": "2026-08-03",
                            "time": "12:00",
                            "status": "scheduled",
                        },
                        {
                            "chapter": 31,
                            "date": "2026-08-04",
                            "time": "12:00",
                            "status": "not_uploaded",
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_selects_only_earliest_platform_submitted_future_chapters(self) -> None:
        now = datetime(2026, 8, 1, 15, 3, tzinfo=CN)
        selected = reward_candidates(self.project, 2, now, "15:40")
        self.assertEqual([item[1]["chapter"] for item in selected], [29, 30])

    def test_published_floor_excludes_old_scheduled_chapters(self) -> None:
        (self.project / "chapter_state.json").write_text(
            json.dumps({"latest_published_chapter": 29}),
            encoding="utf-8",
        )
        now = datetime(2026, 8, 1, 15, 3, tzinfo=CN)
        plan = build_new_reward_plan(self.project, 1, now, "15:50")
        self.assertEqual(plan["bonus_chapters"], [30])
        self.assertTrue(
            reward_job_crosses_published_floor(
                {"bonus_chapters": [23], "moves": [{"chapter": 25}]},
                29,
            )
        )

    def test_reward_plan_refills_all_later_schedule_slots(self) -> None:
        now = datetime(2026, 8, 1, 15, 3, tzinfo=CN)
        plan = build_new_reward_plan(self.project, 1, now, "15:50")
        self.assertEqual(plan["bonus_chapters"], [29])
        self.assertEqual(
            [
                (move["chapter"], move["kind"], move["to"])
                for move in plan["moves"]
            ],
            [
                (
                    29,
                    "reward_bonus",
                    {"date": "2026-08-01", "time": "15:50"},
                ),
                (
                    30,
                    "reward_reflow",
                    {"date": "2026-08-02", "time": "12:00"},
                ),
            ],
        )

    def test_record_preserves_previous_schedule_in_history(self) -> None:
        data = {"timezone": "Asia/Shanghai"}
        entry = json.loads(self.schedule.read_text(encoding="utf-8"))["entries"][1]
        changed = record_reward(
            data,
            self.project,
            self.schedule,
            entry,
            "2026-08-01",
            "15:40",
            {"status": "审核中", "url": "https://example.test/chapter-manage"},
        )
        updated = json.loads(self.schedule.read_text(encoding="utf-8"))["entries"][1]
        self.assertEqual((updated["date"], updated["time"]), ("2026-08-01", "15:40"))
        self.assertEqual(
            updated["reward_history"][0]["from"],
            {"date": "2026-08-02", "time": "12:00"},
        )
        self.assertIn(self.schedule, changed)
        self.assertIn(self.project / "chapter_state.json", changed)
        self.assertEqual(len([path for path in changed if path.parent.name == "logs"]), 1)


if __name__ == "__main__":
    unittest.main()
