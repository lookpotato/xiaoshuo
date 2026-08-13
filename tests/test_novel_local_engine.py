import tempfile
import unittest
from pathlib import Path

from novel_local_engine import build_bundle, local_check, prepare


class NovelLocalEngineTests(unittest.TestCase):
    def make_book(self, root: Path) -> Path:
        book = root / "book"
        (book / "chapters").mkdir(parents=True)
        for name in ("novel_config.md", "outline.md", "characters.md"):
            (book / name).write_text(name, encoding="utf-8")
        for number in range(1, 5):
            body = (f"第{number}章正文。" * 400)
            (book / "chapters" / f"{number:04d}-标题.md").write_text(
                f"# 第{number}章 标题\n\n{body}\n\n---\n\n## Metadata\n",
                encoding="utf-8",
            )
        return book

    def test_bundle_only_includes_latest_chapters(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = build_bundle(self.make_book(Path(tmp)), recent=2)
            self.assertEqual(bundle["recent_chapters"], [3, 4])

    def test_prepare_reuses_unchanged_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = self.make_book(Path(tmp))
            _, first_reused = prepare(book)
            _, second_reused = prepare(book)
            self.assertFalse(first_reused)
            self.assertTrue(second_reused)

    def test_local_check_detects_short_or_placeholder_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "0001-x.md"
            path.write_text("# 第1章 x\n\nTODO\n", encoding="utf-8")
            issues = local_check(path).issues
            self.assertIn("缺少 Metadata", issues)
            self.assertIn("正文少于 2200 字符", issues)
            self.assertIn("正文含占位或元创作词", issues)


if __name__ == "__main__":
    unittest.main()
