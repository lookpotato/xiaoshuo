from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import manual_edit_learning_worker as worker
import reader_feedback_service as service


class ManualEditLearningWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "book"
        (self.project / "chapters").mkdir(parents=True)
        (self.root / "manager_config.json").write_text(json.dumps({
            "books": [{"id": "demo", "title": "测试书", "path": "book"}],
        }, ensure_ascii=False), encoding="utf-8")
        (self.project / "style_guide.md").write_text("# 原有文风\n", encoding="utf-8")
        chapter = self.project / "chapters" / "0001-试读.md"
        chapter.write_text("# 第 1 章 试读\n\n甲把门推开。\n" + "这是正文。" * 130, encoding="utf-8")
        current = service.chapter_versions(self.root, "demo", 1)["current"]
        saved = service.save_manual_revision(
            self.root, "demo", 1,
            current["content"].replace("甲把门推开。", "甲轻轻把门推开。"),
            current["sha256"],
        )
        self.edit_id = saved["receipt"]["edit_id"]

    def test_analysis_is_saved_for_exact_edit_without_promoting_rule(self) -> None:
        def fake_run_codex(command, *, cwd, prompt, output, label):
            self.assertIn("before.md", prompt)
            self.assertIn("after.md", prompt)
            self.assertIn("diff.md", prompt)
            output.write_text(json.dumps({
                "summary": "开门动作加了轻缓的节奏。",
                "changes": [{"before": "甲把门推开。", "after": "甲轻轻把门推开。",
                             "effect": "人物此刻在试探，动作放慢。"}],
                "tradeoffs": [],
                "learning_candidates": [{
                    "principle": "用动作速度表达试探，不替人物解释心情。",
                    "applies_when": "人物面对不确定回应时", "avoid": "日常开门不必套用",
                    "rationale": "改前动作太直，改后让试探可见。",
                    "recommended_scope": "shared_language", "confidence": "medium", "tags": ["动作"],
                }],
            }, ensure_ascii=False), encoding="utf-8")

        with patch.object(worker, "ROOT", self.root), patch.object(
            worker, "resolve_codex", return_value="codex"
        ), patch.object(worker.novel_codex_model, "exec_prefix", return_value=["codex", "exec"]), patch.object(
            worker, "run_codex", side_effect=fake_run_codex
        ):
            worker.run("demo", 1, self.edit_id)
        record = service.manual_edit_record(self.root, "demo", 1, self.edit_id)
        self.assertEqual(record["status"], "completed")
        self.assertIn("日常开门不必套用", record["analysis"])
        self.assertEqual(record["learning_candidates"][0]["recommended_scope"], "shared_language")
        self.assertEqual((self.project / "style_guide.md").read_text(encoding="utf-8"), "# 原有文风\n")
        promoted = service.promote_manual_edit_learning(self.root, "demo", 1, self.edit_id, 0)
        self.assertEqual(promoted["promotion"]["scope"], "shared_language")
        self.assertTrue((self.root / "shared" / "chinese_dialogue_feedback.jsonl").is_file())

    def test_failed_analysis_keeps_edit_and_shows_error(self) -> None:
        with patch.object(worker, "ROOT", self.root), patch.object(
            worker, "resolve_codex", return_value="codex"
        ), patch.object(worker.novel_codex_model, "exec_prefix", return_value=["codex", "exec"]), patch.object(
            worker, "run_codex", side_effect=RuntimeError("连接失败")
        ):
            with self.assertRaisesRegex(RuntimeError, "连接失败"):
                worker.run("demo", 1, self.edit_id)
        record = service.manual_edit_record(self.root, "demo", 1, self.edit_id)
        self.assertEqual(record["status"], "failed")
        self.assertIn("连接失败", record["error"])
        self.assertIn("甲轻轻把门推开", (self.project / "chapters" / "0001-试读.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
