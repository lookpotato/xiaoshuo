from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from playwright.sync_api import Error as PlaywrightError

from browser_image_worker import (
    ROOT,
    detect_blocker,
    download_image_source,
    load_config,
    navigate,
    normalize_proxy_server,
    parse_windows_proxy,
    validate_output_path,
)


class BrowserImageWorkerTests(unittest.TestCase):
    def test_default_config_disables_codex_fallback(self) -> None:
        config = load_config()
        self.assertEqual(config.provider, "chatgpt-plus")
        self.assertEqual(config.url, "https://chatgpt.com/")
        self.assertFalse(config.allow_codex_imagegen_fallback)
        self.assertEqual(config.proxy_mode, "system")
        self.assertIn("[data-testid='send-button']", config.submit_selectors)
        self.assertIn("img[alt^='已生成图片']", config.generated_image_selectors)

    def test_plain_windows_proxy_is_normalized(self) -> None:
        self.assertEqual(
            parse_windows_proxy("127.0.0.1:7890"),
            "http://127.0.0.1:7890",
        )

    def test_https_proxy_wins_for_scheme_mapping(self) -> None:
        self.assertEqual(
            parse_windows_proxy("http=127.0.0.1:8080;https=127.0.0.1:7890"),
            "http://127.0.0.1:7890",
        )

    def test_explicit_proxy_scheme_is_preserved(self) -> None:
        self.assertEqual(
            normalize_proxy_server("socks5://127.0.0.1:7891"),
            "socks5://127.0.0.1:7891",
        )

    def test_privacy_policy_footer_is_not_a_blocker(self) -> None:
        self.assertIsNone(detect_blocker("使用条款 · 隐私政策 · Cookie 设置"))

    def test_explicit_policy_violation_is_a_blocker(self) -> None:
        self.assertEqual(
            detect_blocker("此请求可能违反我们的内容政策"),
            "明确的政策拦截",
        )

    def test_human_verification_is_a_blocker(self) -> None:
        self.assertEqual(detect_blocker("Verify you are human"), "验证码或真人验证")

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

    def test_image_source_retries_then_uses_browser_download(self) -> None:
        page = Mock()
        page.context.request.get.return_value = Mock(ok=False, status=503)
        fallback = Path("browser-fallback.png")

        with patch(
            "browser_image_worker.download_image_in_browser",
            return_value=fallback,
        ) as browser_download:
            result = download_image_source(page, "https://chatgpt.com/image", attempts=3)

        self.assertEqual(result, fallback)
        self.assertEqual(page.context.request.get.call_count, 3)
        self.assertEqual(page.wait_for_timeout.call_count, 2)
        browser_download.assert_called_once_with(page, "https://chatgpt.com/image")

    def test_navigation_retries_transient_proxy_failure(self) -> None:
        config = load_config()
        page = Mock()
        page.goto.side_effect = [
            PlaywrightError("proxy closed"),
            PlaywrightError("tls interrupted"),
            None,
        ]

        navigate(page, config, attempts=3)

        self.assertEqual(page.goto.call_count, 3)
        self.assertEqual(page.wait_for_timeout.call_count, 2)


if __name__ == "__main__":
    unittest.main()
