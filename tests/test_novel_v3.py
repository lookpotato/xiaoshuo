from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from novel_v3 import accept_deep_edit
from novel_engine_v3 import V3ValidationError


class DeepEditAcceptanceTests(unittest.TestCase):
    def test_complete_revision_replaces_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            candidate = run / "candidate.md"
            output = run / "deep_edit.result.md"
            candidate.write_text("# 原标题\n\n" + "原文" * 100, encoding="utf-8")
            output.write_text("# 新标题\n\n" + "修改后的正文" * 90, encoding="utf-8")
            accept_deep_edit(run, output)
            self.assertTrue(candidate.read_text(encoding="utf-8").startswith("# 新标题"))

    def test_incomplete_revision_keeps_original(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            candidate = run / "candidate.md"
            output = run / "deep_edit.result.md"
            original = "# 原标题\n\n" + "原文" * 100
            candidate.write_text(original, encoding="utf-8")
            output.write_text("已完成修改。", encoding="utf-8")
            with self.assertRaises(V3ValidationError):
                accept_deep_edit(run, output)
            self.assertEqual(candidate.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
