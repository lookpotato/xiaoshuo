from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import novel_v2
import xiaoshuo_on_demand as on_demand


class UnifiedNovelPipelineTests(unittest.TestCase):
    def test_production_prompt_uses_bound_author_and_book_learning(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "book"
            project.mkdir()
            (project / "feedback_learning.json").write_text(
                json.dumps({"schema_version": 1, "rules": []}), encoding="utf-8"
            )
            home = root / "novel_engine_v2"
            (home / "authors").mkdir(parents=True)
            (home / "system.json").write_text(json.dumps({
                "schema_version": 2,
                "books": {"demo": {"author": "owner"}},
            }), encoding="utf-8")
            (home / "authors" / "owner.json").write_text(json.dumps({
                "schema_version": 1,
                "id": "owner",
                "name": "主作者",
                "creative_identity": ["人物选择优先。"],
                "reader_contract": ["正文独立可懂。"],
                "language_principles": ["对白符合关系。"],
            }, ensure_ascii=False), encoding="utf-8")
            with patch.object(on_demand, "ROOT", root):
                context = on_demand.author_prompt_context("demo", project)
            self.assertIn("主作者（owner）", context)
            self.assertIn("feedback_learning.json", context)
            self.assertIn("不得调用 `novel_v2.py`", context)

    def test_former_v2_run_forwards_to_canonical_production(self) -> None:
        command = novel_v2.production_command("run", "demo")
        self.assertEqual(command[0], sys.executable)
        self.assertTrue(command[1].endswith("xiaoshuo.py"))
        self.assertIn("--no-publish-fanqie", command)
        self.assertIn("--no-sync-git", command)

    def test_former_v2_prepare_is_only_a_dry_run(self) -> None:
        self.assertIn("--dry-run", novel_v2.production_command("prepare", "demo"))

    def test_former_v2_validate_uses_production_doctor(self) -> None:
        command = novel_v2.production_command("validate", "demo")
        self.assertTrue(command[1].endswith("fanqie_novel_manager.py"))
        self.assertEqual(command[2:4], ["doctor", "--book"])

    def test_compatibility_main_runs_forwarded_command(self) -> None:
        completed = type("Completed", (), {"returncode": 0})()
        with (
            patch.object(sys, "argv", ["novel_v2", "run", "--book", "demo"]),
            patch.object(novel_v2.subprocess, "run", return_value=completed) as run,
        ):
            self.assertEqual(novel_v2.main(), 0)
        self.assertEqual(run.call_args.args[0], novel_v2.production_command("run", "demo"))


if __name__ == "__main__":
    unittest.main()
