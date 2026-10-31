from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import xiaoshuo
import xiaoshuo_auto_repair


class AutoHandoffTests(unittest.TestCase):
    def test_handoff_reads_failure_log_and_leaves_retry_to_supervisor(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "failure.log"
            log.write_text("V3 错误：章节合同 JSON 无效", encoding="utf-8")

            def fake_run(args, **kwargs):
                self.assertEqual(kwargs["env"]["XIAOSHUO_AUTO_REPAIR_ACTIVE"], "1")
                self.assertIn(str(log.resolve()), kwargs["input"])
                self.assertIn("不要再次执行原命令", kwargs["input"])
                self.assertIn("workspace-write", args)
                return type("Result", (), {"returncode": 0})()

            with patch.object(xiaoshuo_auto_repair, "REPAIR_ROOT", root / "repairs"), \
                 patch("xiaoshuo_on_demand.resolve_codex", return_value="codex"), \
                 patch.object(xiaoshuo_auto_repair.novel_codex_model, "exec_prefix", return_value=["codex", "exec"]), \
                 patch.object(xiaoshuo_auto_repair.subprocess, "run", side_effect=fake_run):
                self.assertTrue(xiaoshuo_auto_repair.repair_failed_book("night-floor", ["generate"], log))

    def test_failed_book_is_repaired_and_retried_once(self) -> None:
        books = [({"id": "night-floor", "title": "旧楼"}, ["generate"])]
        with patch.object(xiaoshuo, "run_book", side_effect=[
            (1, Path("failure.log")), (0, Path("retry.log")),
        ]) as run, patch.object(
            xiaoshuo.xiaoshuo_auto_repair, "repair_failed_book", return_value=True,
        ) as repair:
            self.assertEqual(xiaoshuo.run_commands(books, auto_repair=True), 0)
        self.assertEqual(run.call_count, 2)
        repair.assert_called_once_with("night-floor", ["generate"], Path("failure.log"))

    def test_failed_repair_does_not_block_remaining_books(self) -> None:
        books = [
            ({"id": "night-floor", "title": "旧楼"}, ["first"]),
            ({"id": "next", "title": "下一本"}, ["second"]),
        ]
        with patch.object(xiaoshuo, "run_book", side_effect=[
            (1, Path("failure.log")), (0, Path("next.log")),
        ]) as run, patch.object(
            xiaoshuo.xiaoshuo_auto_repair, "repair_failed_book", return_value=False,
        ):
            self.assertEqual(xiaoshuo.run_commands(books, auto_repair=True), 1)
        self.assertEqual(run.call_count, 2)
