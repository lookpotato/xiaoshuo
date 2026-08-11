from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fanqie_browser_worker import parse_chapter


class FanqieImageMarkdownTests(unittest.TestCase):
    def test_local_image_is_not_uploaded_as_plain_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            chapter = Path(directory) / "0001-test.md"
            prose = "这是用于测试上传正文的句子。" * 100
            chapter.write_text(
                "# 第1章 测试章节\n\n"
                + prose
                + "\n\n![锁魂钉](../images/items/soul-lock-v1.png)\n\n"
                + prose
                + "\n\n---\n\n## Metadata\n\n- upload_status: not_uploaded\n",
                encoding="utf-8",
            )
            parsed = parse_chapter(chapter)
            self.assertNotIn("![", parsed.body)
            self.assertNotIn("../images/", parsed.body)
            self.assertEqual(parsed.body.count(prose), 2)


if __name__ == "__main__":
    unittest.main()
