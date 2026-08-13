#!/usr/bin/env python3
"""Generate novel reference images through a user-signed-in Chrome web app."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from fanqie_browser_worker import chrome_path
from novel_image_system import _image_dimensions, _matches_aspect_ratio, _valid_image_signature, sha256_file


ROOT = Path(__file__).resolve().parent
PROFILE_DIR = (
    Path.home()
    / "AppData"
    / "Local"
    / "xiaoshuo"
    / "image-chatgpt-chrome-profile-v1"
)
READY_FILE = PROFILE_DIR.parent / "image-chatgpt-chrome-profile-v1.ready.json"
DOWNLOAD_DIR = ROOT / ".manager_image_downloads"
CONFIG_FILE = ROOT / "image_browser_config.json"

STOP_TEXT = re.compile(r"验证码|验证身份|异常流量|稍后再试|账号受限|政策|违反|captcha", re.I)
LOGIN_TEXT = re.compile(r"登录|注册|Log in|Sign in|Sign up", re.I)


class ImageBlocked(RuntimeError):
    pass


class ImageRetryable(RuntimeError):
    pass


@dataclass(frozen=True)
class WebImageConfig:
    provider: str
    url: str
    prompt_selectors: tuple[str, ...]
    submit_names: tuple[str, ...]
    download_names: tuple[str, ...]
    timeout_seconds: int
    allow_codex_imagegen_fallback: bool


def load_config() -> WebImageConfig:
    web = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    if web.get("generator") != "chrome-web":
        raise ValueError("image_browser_config.json 的 generator 必须为 chrome-web")
    if web.get("allow_codex_imagegen_fallback") is not False:
        raise ValueError("质量策略禁止自动降级到 Codex imagegen")
    return WebImageConfig(
        provider=str(web.get("provider", "chatgpt-plus")),
        url=str(web.get("url", "https://chatgpt.com/")),
        prompt_selectors=tuple(web.get("prompt_selectors", [
            "#prompt-textarea",
            "[data-testid='prompt-textarea']",
            "textarea",
            "[contenteditable='true'][role='textbox']",
            "div[contenteditable='true']",
        ])),
        submit_names=tuple(
            web.get(
                "submit_names",
                ["发送提示", "发送", "Send prompt", "Send message", "Send"],
            )
        ),
        download_names=tuple(
            web.get(
                "download_names",
                [
                    "下载此图片",
                    "下载图片",
                    "保存",
                    "Download this image",
                    "Download image",
                    "Download",
                    "Save",
                ],
            )
        ),
        timeout_seconds=int(web.get("timeout_seconds", 240)),
        allow_codex_imagegen_fallback=bool(
            web.get("allow_codex_imagegen_fallback", False)
        ),
    )


def launch(playwright):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return playwright.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        executable_path=str(chrome_path()),
        headless=False,
        viewport={"width": 1440, "height": 960},
        locale="zh-CN",
        accept_downloads=True,
    )


def page_text(page: Page) -> str:
    try:
        return page.locator("body").inner_text(timeout=10_000)[:30_000]
    except PlaywrightError:
        return ""


def assert_safe(page: Page) -> None:
    text = page_text(page)
    if STOP_TEXT.search(text):
        raise ImageBlocked("图片网站出现验证码、风控或政策提示，已安全停止")


def visible_prompt(page: Page, config: WebImageConfig):
    for selector in config.prompt_selectors:
        matches = [item for item in page.locator(selector).all() if item.is_visible() and item.is_enabled()]
        if matches:
            return matches[-1]
    return None


def click_named(page: Page, names: tuple[str, ...]) -> bool:
    for name in names:
        for locator in (page.get_by_role("button", name=name, exact=False), page.get_by_text(name, exact=False)):
            matches = [item for item in locator.all() if item.is_visible() and item.is_enabled()]
            if matches:
                matches[-1].click()
                return True
    return False


def visible_named_count(page: Page, names: tuple[str, ...]) -> int:
    seen: set[int] = set()
    for name in names:
        for locator in (
            page.get_by_role("button", name=name, exact=False),
            page.get_by_text(name, exact=False),
        ):
            for item in locator.all():
                if item.is_visible() and item.is_enabled():
                    seen.add(id(item))
    return len(seen)


def validate_output_path(output: Path) -> Path:
    resolved = output.resolve()
    if ROOT not in resolved.parents:
        raise ValueError("输出文件必须位于小说管理系统目录内")
    relative = resolved.relative_to(ROOT)
    if "images" not in relative.parts[:-1]:
        raise ValueError("输出文件必须位于某本小说的 images/ 目录内")
    if resolved.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise ValueError("输出文件扩展名必须是 PNG、JPEG 或 WebP")
    if resolved.exists():
        raise ValueError("输出文件已存在；请使用 -v2、-v3 等新文件名，禁止覆盖旧图")
    return resolved


def wait_ready(page: Page, config: WebImageConfig, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        assert_safe(page)
        if visible_prompt(page, config):
            return
        page.wait_for_timeout(1000)
    text = page_text(page)
    if LOGIN_TEXT.search(text):
        raise ImageBlocked("图片专用 Chrome 尚未登录；请先运行 --setup")
    raise ImageRetryable("未找到图片网站提示词输入框；网页可能尚未加载或控件配置已过期")


def setup(timeout_seconds: int) -> dict:
    config = load_config()
    with sync_playwright() as playwright:
        context = launch(playwright)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(config.url, wait_until="domcontentloaded", timeout=60_000)
            wait_ready(page, config, timeout_seconds)
            READY_FILE.write_text(json.dumps({"provider": config.provider, "url": config.url}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return {"ready": True, "provider": config.provider, "profile": str(PROFILE_DIR), "url": page.url}
        finally:
            context.close()


def download_image(page: Page, config: WebImageConfig, prompt: str, timeout_seconds: int) -> Path:
    prompt_box = visible_prompt(page, config)
    if prompt_box is None:
        raise ImageRetryable("提示词输入框不可用")
    prompt_box.click()
    prompt_box.fill(prompt)
    before_images = page.locator("img").count()
    before_downloads = visible_named_count(page, config.download_names)
    if not click_named(page, config.submit_names):
        prompt_box.press("Enter")

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        assert_safe(page)
        new_download_available = visible_named_count(page, config.download_names) > before_downloads
        if new_download_available:
            break
        page.wait_for_timeout(1500)
    else:
        raise ImageRetryable("等待网页生成图片超时")

    DOWNLOAD_DIR.mkdir(exist_ok=True)
    with page.expect_download(timeout=30_000) as download_info:
        if not click_named(page, config.download_names):
            raise ImageRetryable("图片已出现，但未找到下载按钮；请更新 download_names 配置")
    download = download_info.value
    suffix = Path(download.suggested_filename).suffix.lower() or ".png"
    target = DOWNLOAD_DIR / f"web-image-{int(time.time())}{suffix}"
    download.save_as(target)
    return target


def generate(prompt_file: Path, output: Path, ratio: str, timeout_seconds: int | None) -> dict:
    config = load_config()
    prompt = prompt_file.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError("提示词文件为空")
    if not READY_FILE.is_file():
        raise ImageBlocked("尚未完成图片浏览器配置；请先运行 --setup")
    output = validate_output_path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    web_prompt = (
        f"{prompt}\n\n硬性输出要求：请直接生成一张图片；画幅必须为 {ratio}；"
        "无文字、无水印、不得新增提示词之外的设定。"
    )
    with sync_playwright() as playwright:
        context = launch(playwright)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(config.url, wait_until="domcontentloaded", timeout=60_000)
            wait_ready(page, config, timeout_seconds or config.timeout_seconds)
            downloaded = download_image(
                page,
                config,
                web_prompt,
                timeout_seconds or config.timeout_seconds,
            )
        finally:
            context.close()
    shutil.copy2(downloaded, output)
    downloaded.unlink(missing_ok=True)
    if not _valid_image_signature(output):
        output.unlink(missing_ok=True)
        raise ImageRetryable("下载结果不是有效的 PNG/JPEG/WebP 图片")
    dimensions = _image_dimensions(output)
    if dimensions is None or not _matches_aspect_ratio(*dimensions, ratio):
        raise ImageRetryable(f"下载图片尺寸 {dimensions} 不符合目标画幅 {ratio}；保留文件供人工检查")
    return {
        "status": "downloaded_pending_visual_review",
        "provider": config.provider,
        "generated_with": "chrome-web",
        "path": str(output),
        "sha256": sha256_file(output),
        "dimensions": list(dimensions),
        "ratio": ratio,
    }


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="通过已登录 Chrome 网页生成小说配图")
    parser.add_argument("--setup", action="store_true", help="首次打开图片网站并等待用户登录")
    parser.add_argument("--check", action="store_true", help="检查登录和页面控件")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--ratio", choices=["16:9", "9:16", "1:1", "2:3", "3:2"])
    parser.add_argument("--timeout", type=int)
    args = parser.parse_args()
    try:
        if args.setup or args.check:
            emit(setup(args.timeout or 600))
            return 0
        if not args.prompt_file or not args.output or not args.ratio:
            parser.error("生成图片需要 --prompt-file、--output 和 --ratio")
        emit(generate(args.prompt_file, args.output, args.ratio, args.timeout))
        return 0
    except ImageBlocked as exc:
        emit({"status": "blocked_manual", "message": str(exc)})
        return 4
    except (ImageRetryable, PlaywrightError, PlaywrightTimeoutError, OSError, ValueError) as exc:
        emit({"status": "failed_retryable", "message": str(exc)})
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
