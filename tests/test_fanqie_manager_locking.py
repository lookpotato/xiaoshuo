from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import fanqie_novel_manager as manager


class ManagerLockingTests(unittest.TestCase):
    def test_write_json_ignores_stale_fixed_temp_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / ".manager.lock"
            stale = target.with_suffix(target.suffix + ".tmp")
            stale.write_text("stale", encoding="utf-8")

            manager.write_json(target, {"book_id": "free-sky"})

            self.assertEqual(manager.read_json(target)["book_id"], "free-sky")
            self.assertEqual(stale.read_text(encoding="utf-8"), "stale")

    def test_live_lock_recovers_dead_half_claim_for_queued_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = root / ".manager.lock"
            jobs = root / ".manager_jobs"
            jobs.mkdir()
            claimed_at = "2026-08-12T09:32:10+08:00"
            lock.write_text(
                json.dumps({
                    "book_id": "free-sky",
                    "claimed_at": claimed_at,
                    "pid": 26636,
                }),
                encoding="utf-8",
            )
            (jobs / "job.json").write_text(
                json.dumps({
                    "book_id": "free-sky",
                    "created_at": "2026-08-12T09:32:09+08:00",
                    "status": "queued",
                }),
                encoding="utf-8",
            )

            with (
                patch.object(manager, "LOCK", lock),
                patch.object(manager, "JOB_DIR", jobs),
                patch.object(manager, "process_is_running", return_value=False),
            ):
                active = manager.live_lock(
                    {"global_lock_minutes": 180},
                    datetime(2026, 8, 12, 9, 33, tzinfo=timezone.utc),
                )

            self.assertIsNone(active)
            self.assertFalse(lock.exists())


if __name__ == "__main__":
    unittest.main()
