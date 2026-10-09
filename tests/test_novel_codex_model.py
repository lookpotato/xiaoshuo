from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

import novel_codex_model
import xiaoshuo_on_demand
from novel_engine_v2 import runner


class NovelCodexModelTest(TestCase):
    def test_main_chapter_command_pins_model_and_effort(self):
        command = xiaoshuo_on_demand._stage_command("codex", Path("result.md"))
        self.assertEqual(command[:6], [
            "codex", "exec", "--model", "gpt-6-sol",
            "--config", 'model_reasoning_effort="medium"',
        ])
        self.assertIn("--output-last-message", command)

    def test_v2_stages_use_the_same_pin(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            prompt = root / "prompt.md"
            prompt.write_text("测试", encoding="utf-8")
            with mock.patch.object(runner, "resolve_codex", return_value="codex"), mock.patch.object(
                runner.subprocess, "run", return_value=mock.Mock(returncode=0)
            ) as process:
                runner.execute_prompt(root, prompt, root / "result.md")
        command = process.call_args.args[0]
        self.assertEqual(command[:6], novel_codex_model.exec_prefix("codex"))
