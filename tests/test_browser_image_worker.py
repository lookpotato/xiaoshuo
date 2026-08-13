from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from browser_image_worker import ROOT, load_config, validate_output_path


class BrowserImageWorkerTests(unittest.TestCase):
    def test_default_config_disables_codex_fallback(self) -> None:
        config = load_config()
        self.assertEqual(config.provider, "chatgpt-plus")
        self.assertEqual(config.url, "https://chatgpt.com/")
        self.assertFalse(config.allow_codex_imagegen_fallback)

    def test_output_must_be_inside_book_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, "管理系统目录内"):
                validate_output_path(Path(temp) / "outside.png")

    def test_output_inside_images_is_allowed(self) -> None:
        target = ROOT / "测试本小说" / "images" / "items" / "new-v99.png"
        self.assertEqual(validate_output_path(target), target.resolve())

    def test_existing_output_cannot_be_overwritten(self) -> None:
        target = ROOT / "测试本小说" / "images" / "items" / "existing-v99.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"existing")
        try:
            with self.assertRaisesRegex(ValueError, "禁止覆盖"):
                validate_output_path(target)
        finally:
            target.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
