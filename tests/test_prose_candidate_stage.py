from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import xiaoshuo_on_demand as demand


class ProseCandidateStageTests(unittest.TestCase):
    def test_isolated_prose_is_reused_only_for_matching_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            plan = demand.stage_pipeline.plan_path(demand.ROOT, project, 4)
            plan.parent.mkdir(parents=True)
            plan.write_text('{"chapter_number": 4}', encoding="utf-8")
            calls = []

            def fake_run(command, **kwargs):
                calls.append((command, kwargs))
                result = Path(command[command.index("--output-last-message") + 1])
                result.write_text(
                    "# 第 4 章 门开了\n\n" + "刘桂芳推开门，看见灯亮着。" * 70,
                    encoding="utf-8",
                )
                return SimpleNamespace(returncode=0)

            with patch.object(demand.novel_codex_model, "exec_prefix", return_value=["codex", "exec"]), patch.object(
                demand.subprocess, "run", side_effect=fake_run
            ):
                demand.ensure_prose_candidate("codex", "night-floor", project, 4)
                candidate, metadata = demand._prose_candidate_paths(project, 4)
                self.assertTrue(candidate.is_file())
                self.assertEqual(json.loads(metadata.read_text(encoding="utf-8"))["chapter_number"], 4)
                self.assertIn("read-only", calls[0][0])
                self.assertNotEqual(Path(calls[0][1]["cwd"]), project)
                self.assertIn("本章提纲", calls[0][1]["input"])
                self.assertIn("chapter_number", calls[0][1]["input"])
                self.assertIn("不逐项复述提纲", calls[0][1]["input"])
                demand.ensure_prose_candidate("codex", "night-floor", project, 4)
                self.assertEqual(len(calls), 1)
                plan.write_text('{"chapter_number": 4, "changed": true}', encoding="utf-8")
                demand.ensure_prose_candidate("codex", "night-floor", project, 4)
                self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
