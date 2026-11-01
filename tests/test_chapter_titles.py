import tempfile
import unittest
from pathlib import Path

from chapter_titles import duplicate_title_paths, title_key


class ChapterTitleTests(unittest.TestCase):
    def test_title_key_ignores_spacing_punctuation_and_width(self):
        self.assertEqual(title_key("墙 脚！"), title_key("墙脚"))
        self.assertEqual(title_key("ＡＢＣ：认路"), title_key("abc认路"))
        self.assertNotEqual(title_key("墙角"), title_key("墙脚"))

    def test_scans_headings_and_falls_back_to_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            chapters = project / "chapters"
            chapters.mkdir()
            first = chapters / "0001-旧文件名.md"
            first.write_text("# 第 1 章 墙脚\n", encoding="utf-8")
            second = chapters / "0002-认路.md"
            second.write_text("", encoding="utf-8")
            self.assertEqual(duplicate_title_paths(project, "墙 脚！"), [first])
            self.assertEqual(duplicate_title_paths(project, "认路"), [second])
            self.assertEqual(duplicate_title_paths(project, "墙脚", exclude=first), [])


if __name__ == "__main__":
    unittest.main()
