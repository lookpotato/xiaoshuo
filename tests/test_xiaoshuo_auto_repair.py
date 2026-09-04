from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import xiaoshuo_on_demand as worker


class AutomaticRepairTests(unittest.TestCase):
    def test_repair_prompt_contains_machine_errors_and_safety_boundary(self) -> None:
        prompt = worker.local_repair_prompt(
            "cosmic-404",
            {"id": "job-12345678"},
            24,
            ["第 24 章正文哈希不一致", "第 24 章因果证据顺序错误"],
            1,
        )
        self.assertIn("正文哈希不一致", prompt)
        self.assertIn("依据→行动原理→结果代价", prompt)
        self.assertIn("不要另写下一章", prompt)
        self.assertIn("不上传番茄", prompt)
        self.assertIn("不运行 Git", prompt)

    def test_next_chapter_gate_errors_are_recoverable(self) -> None:
        with TemporaryDirectory() as temp:
            project = Path(temp)
            (project / "chapters").mkdir()
            (project / "chapters" / "0024-test.md").write_text("draft", encoding="utf-8")
            (project / "chapter_state.json").write_text(
                json.dumps({"last_completed_chapter": 23, "next_chapter_number": 24}),
                encoding="utf-8",
            )
            errors = [
                "第 24 章无大纲读者验收正文哈希不一致",
                "归档最高章节为 24，但 last_completed_chapter=23",
            ]
            self.assertTrue(worker.recoverable_draft_errors(project, errors))

    def test_other_chapter_or_structural_errors_are_not_auto_repaired(self) -> None:
        with TemporaryDirectory() as temp:
            project = Path(temp)
            (project / "chapters").mkdir()
            (project / "chapters" / "0024-test.md").write_text("draft", encoding="utf-8")
            (project / "chapter_state.json").write_text(
                json.dumps({"last_completed_chapter": 23, "next_chapter_number": 24}),
                encoding="utf-8",
            )
            self.assertFalse(
                worker.recoverable_draft_errors(project, ["第 21 章正文哈希不一致"])
            )
            self.assertFalse(
                worker.recoverable_draft_errors(project, ["缺少 novel_config.md"])
            )

    def test_write_one_sends_gate_failure_back_to_codex_and_rechecks(self) -> None:
        with TemporaryDirectory() as temp:
            project = Path(temp)
            (project / "chapters").mkdir()
            (project / "drafts").mkdir()
            state_path = project / "chapter_state.json"
            original = {"last_completed_chapter": 6, "next_chapter_number": 7}
            state_path.write_text(json.dumps(original), encoding="utf-8")
            book = {
                "id": "cosmic-404",
                "mode": "write_only",
                "reader_gate_from_chapter": 1,
            }

            def fake_codex(*_args, **_kwargs):
                (project / "chapters" / "0007-test.md").write_text(
                    "# 第 7 章 test", encoding="utf-8"
                )
                worker.manager.write_json(
                    state_path,
                    {"last_completed_chapter": 7, "next_chapter_number": 8},
                )
                return subprocess.CompletedProcess([], 0)

            with (
                patch.object(worker, "resolve_codex", return_value="codex"),
                patch.object(worker, "project_for", return_value=project),
                patch.object(worker, "local_write_prompt", return_value="initial prompt"),
                patch.object(worker.manager, "JOB_DIR", project),
                patch.object(worker.manager, "config", return_value={}),
                patch.object(worker.manager, "find_book", return_value=book),
                patch.object(worker.manager, "validate_parallel_character_threads", return_value=[]),
                patch.object(
                    worker.manager,
                    "validate_reader_checks",
                    side_effect=[["第 7 章正文哈希不一致"], []],
                ),
                patch.object(worker.manager, "validate_book", return_value=[]),
                patch.object(worker.subprocess, "run", side_effect=fake_codex) as run_codex,
            ):
                worker.write_one("cosmic-404", {"id": "job-12345678"})

            self.assertEqual(run_codex.call_count, 2)
            self.assertEqual(
                worker.manager.read_json(state_path)["last_completed_chapter"], 7
            )
            repair_input = run_codex.call_args_list[1].kwargs["input"]
            self.assertIn("正文哈希不一致", repair_input)
            self.assertIn("自动发起", repair_input)

    def test_connection_failures_do_not_consume_content_repair_quota(self) -> None:
        with TemporaryDirectory() as temp:
            project = Path(temp)
            (project / "chapters").mkdir()
            (project / "drafts").mkdir()
            state_path = project / "chapter_state.json"
            original = {"last_completed_chapter": 39, "next_chapter_number": 40}
            state_path.write_text(json.dumps(original), encoding="utf-8")
            (project / "chapters" / "0040-test.md").write_text(
                "# 第 40 章 test", encoding="utf-8"
            )
            book = {
                "id": "cosmic-404",
                "mode": "write_only",
                "reader_gate_from_chapter": 1,
            }
            with (
                patch.object(worker, "resolve_codex", return_value="codex"),
                patch.object(worker, "project_for", return_value=project),
                patch.object(worker.manager, "JOB_DIR", project),
                patch.object(worker.manager, "config", return_value={}),
                patch.object(worker.manager, "find_book", return_value=book),
                patch.object(
                    worker,
                    "enforce_local_archive_gates",
                    side_effect=worker.ArchiveGateFailure(["第 40 章正文哈希不一致"]),
                ),
                patch.object(
                    worker.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 1),
                ) as run_codex,
            ):
                with self.assertRaisesRegex(RuntimeError, "未消耗内容修复次数"):
                    worker.write_one("cosmic-404", {"id": "job-12345678"})
            self.assertEqual(run_codex.call_count, worker.MAX_CODEX_PROCESS_RETRIES + 1)

    def test_successful_failed_gate_still_consumes_repair_quota(self) -> None:
        with TemporaryDirectory() as temp:
            project = Path(temp)
            (project / "chapters").mkdir()
            (project / "drafts").mkdir()
            state_path = project / "chapter_state.json"
            state_path.write_text(
                json.dumps({"last_completed_chapter": 6, "next_chapter_number": 7}),
                encoding="utf-8",
            )
            (project / "chapters" / "0007-test.md").write_text(
                "# 第 7 章 test", encoding="utf-8"
            )
            book = {"id": "cosmic-404", "mode": "write_only", "reader_gate_from_chapter": 1}

            def fake_codex(*_args, **_kwargs):
                fake_codex.calls += 1
                if fake_codex.calls == 2:
                    worker.manager.write_json(
                        state_path, {"last_completed_chapter": 7, "next_chapter_number": 8}
                    )
                return subprocess.CompletedProcess([], 0)

            fake_codex.calls = 0
            with (
                patch.object(worker, "resolve_codex", return_value="codex"),
                patch.object(worker, "project_for", return_value=project),
                patch.object(worker.manager, "JOB_DIR", project),
                patch.object(worker.manager, "config", return_value={}),
                patch.object(worker.manager, "find_book", return_value=book),
                patch.object(
                    worker,
                    "enforce_local_archive_gates",
                    side_effect=[
                        worker.ArchiveGateFailure(["第 7 章正文哈希不一致"]),
                        None,
                    ],
                ),
                patch.object(worker.subprocess, "run", side_effect=fake_codex) as run_codex,
            ):
                worker.write_one("cosmic-404", {"id": "job-12345678"})
            self.assertEqual(run_codex.call_count, 2)

    def test_start_job_uses_repair_claim_for_recoverable_draft(self) -> None:
        job = {
            "id": "job-12345678",
            "book_id": "cosmic-404",
            "status": "queued",
            "completed_chapters": [],
            "events": [],
        }
        book = {"id": "cosmic-404"}
        with (
            patch.object(worker, "queued_job", return_value=job),
            patch.object(worker, "project_for", return_value=Path("project")),
            patch.object(worker.manager, "find_book", return_value=book),
            patch.object(worker.manager, "validate_book", return_value=["current error"]),
            patch.object(worker, "recoverable_draft_errors", return_value=True),
            patch.object(worker, "claim_repair_job", return_value=0) as repair_claim,
            patch.object(worker.manager, "cmd_claim") as ordinary_claim,
            patch.object(worker.manager, "read_json", return_value={}),
            patch.object(worker.manager, "write_json"),
        ):
            worker.start_job({}, "cosmic-404", 1, None, False, False)
        repair_claim.assert_called_once_with({}, "cosmic-404")
        ordinary_claim.assert_not_called()


if __name__ == "__main__":
    unittest.main()
