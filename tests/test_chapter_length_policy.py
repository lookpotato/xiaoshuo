import json
import tempfile
import unittest
from pathlib import Path

from chapter_length_policy import (
    chapter_length_instruction,
    chapter_length_limited,
    project_chapter_length_limited,
)


class ChapterLengthPolicyTests(unittest.TestCase):
    def test_book_can_disable_all_chapter_length_limits(self):
        book = {"word_count_limit_enabled": False}
        self.assertFalse(chapter_length_limited(book, 1))
        instruction = chapter_length_instruction(book, 1)
        self.assertIn("忽略本书 novel_config.md", instruction)
        self.assertIn("不豁免读者验收", instruction)

    def test_single_chapter_can_be_exempted(self):
        book = {"word_count_limit_enabled": True, "word_count_exempt_chapters": [4]}
        self.assertTrue(chapter_length_limited(book, 3))
        self.assertFalse(chapter_length_limited(book, 4))
        self.assertIn("第 4 章已关闭字数限制", chapter_length_instruction(book, 4))

    def test_existing_books_keep_limits_by_default(self):
        self.assertTrue(chapter_length_limited({}, 1))
        self.assertIn("继续执行本书 novel_config.md", chapter_length_instruction({}, 1))

    def test_project_setting_controls_short_upload_parser_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "book-one"
            project.mkdir()
            (root / "manager_config.json").write_text(
                json.dumps({
                    "books": [{
                        "id": "book-one",
                        "path": "book-one",
                        "word_count_limit_enabled": True,
                        "word_count_exempt_chapters": [7],
                    }]
                }),
                encoding="utf-8",
            )
            self.assertTrue(project_chapter_length_limited(project, 6, root))
            self.assertFalse(project_chapter_length_limited(project, 7, root))


if __name__ == "__main__":
    unittest.main()
