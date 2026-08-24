#!/usr/bin/env python3
"""Generate novel reference images through a user-signed-in Chrome web app."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
import winreg
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

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
GENERATION_ATTEMPTS = 2

BLOCK_PATTERNS = (
    ("验证码或真人验证", re.compile(
        r"验证码|captcha|verify (?:that )?you are human|"
        r"验证(?:您|你)?是(?:否)?真人|确认(?:您|你)?是真人|Cloudflare",
        re.I,
    )),
    ("异常流量", re.compile(r"异常流量|unusual traffic|automated requests", re.I)),
    ("账号受限", re.compile(
        r"账号.{0,8}(?:受限|停用|封禁)|"
        r"account.{0,12}(?:restricted|deactivated|suspended)",
        re.I,
    )),
    ("明确的政策拦截", re.compile(
        r"(?:违反|不符合).{0,20}(?:内容|使用)?政策|"
        r"policy violation|violates?.{0,12}(?:content )?policy",
        re.I,
    )),
    ("使用额度或请求限制", re.compile(
        r"(?:已|达到|超出).{0,12}(?:使用|图片|生成|请求).{0,8}(?:上限|限制)|"
        r"rate limit|too many requests",
        re.I,
    )),
)
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
    submit_selectors: tuple[str, ...]
    submit_names: tuple[str, ...]
    generated_image_selectors: tuple[str, ...]
    download_names: tuple[str, ...]
    timeout_seconds: int
    allow_codex_imagegen_fallback: bool
    proxy_mode: str
    proxy_server: str | None


def normalize_proxy_server(value: str) -> str:
    server = value.strip()
    if not server:
        raise ValueError("代理地址不能为空")
    if "://" not in server:
        server = f"http://{server}"
    return server


def parse_windows_proxy(value: str) -> str | None:
    entries = [entry.strip() for entry in value.split(";") if entry.strip()]
    if not entries:
        return None
    keyed: dict[str, str] = {}
    direct: list[str] = []
    for entry in entries:
        if "=" in entry:
            scheme, server = entry.split("=", 1)
            keyed[scheme.strip().lower()] = server.strip()
        else:
            direct.append(entry)
    selected = keyed.get("https") or keyed.get("http")
    if selected is None and direct:
        selected = direct[0]
    return normalize_proxy_server(selected) if selected else None


def windows_user_proxy() -> str | None:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled = int(winreg.QueryValueEx(key, "ProxyEnable")[0])
            raw = str(winreg.QueryValueEx(key, "ProxyServer")[0])
    except (FileNotFoundError, OSError, TypeError, ValueError):
        return None
    return parse_windows_proxy(raw) if enabled else None


def effective_proxy(config: WebImageConfig) -> str | None:
    if config.proxy_mode == "direct":
        return None
    if config.proxy_mode == "manual":
        if not config.proxy_server:
            raise ValueError("proxy_mode=manual 时必须填写 proxy_server")
        return normalize_proxy_server(config.proxy_server)
    if config.proxy_mode == "system":
        return windows_user_proxy()
    raise ValueError("proxy_mode 只能是 system、manual 或 direct")


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
        submit_selectors=tuple(
            web.get(
                "submit_selectors",
                ["[data-testid='send-button']", "button[aria-label*='发送']"],
            )
        ),
        submit_names=tuple(
            web.get(
                "submit_names",
                ["发送提示", "发送", "Send prompt", "Send message", "Send"],
            )
        ),
        generated_image_selectors=tuple(
            web.get(
                "generated_image_selectors",
                [
                    "img[alt^='已生成图片']",
                    "img[alt^='Generated image']",
                    "img[src*='/backend-api/estuary/content?id=file_']",
                ],
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
        proxy_mode=str(web.get("proxy_mode", "system")),
        proxy_server=(
            str(web["proxy_server"]) if web.get("proxy_server") else None
        ),
    )


def launch(playwright, config: WebImageConfig):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    options = {
        "user_data_dir": str(PROFILE_DIR),
        "executable_path": str(chrome_path()),
        "headless": False,
        "viewport": {"width": 1440, "height": 960},
        "locale": "zh-CN",
        "accept_downloads": True,
    }
    proxy = effective_proxy(config)
    if proxy:
        options["proxy"] = {"server": proxy, "bypass": "localhost;127.0.0.1"}
    try:
        return playwright.chromium.launch_persistent_context(**options)
    except PlaywrightError as exc:
        message = str(exc)
        if "Target page, context or browser has been closed" in message:
            raise ImageRetryable(
                "图片专用 Chrome 配置正在被另一个窗口或遗留进程占用；"
                "请只关闭图片专用 Chrome 后重试，不要关闭日常 Chrome。"
            ) from exc
        raise


def navigate(page: Page, config: WebImageConfig, attempts: int = 3) -> None:
    last_error = "未知网络错误"
    for attempt in range(1, attempts + 1):
        try:
            page.goto(config.url, wait_until="domcontentloaded", timeout=60_000)
            return
        except PlaywrightError as exc:
            last_error = str(exc).splitlines()[0]
            if attempt < attempts:
                page.wait_for_timeout(attempt * 2000)
    proxy = effective_proxy(config)
    route = f"代理 {proxy}" if proxy else "直连"
    raise ImageRetryable(
        f"连续 {attempts} 次无法通过{route}打开 {config.url}；"
        "请确认代理程序正在运行后重试。最后错误: "
        f"{last_error}"
    )


def page_text(page: Page) -> str:
    try:
        return page.locator("body").inner_text(timeout=10_000)[:30_000]
    except PlaywrightError:
        return ""


def detect_blocker(text: str) -> str | None:
    for label, pattern in BLOCK_PATTERNS:
        if pattern.search(text):
            return label
    return None


def assert_safe(page: Page) -> None:
    blocker = detect_blocker(page_text(page))
    if blocker:
        raise ImageBlocked(f"图片网站出现{blocker}，已安全停止")


def visible_prompt(page: Page, config: WebImageConfig):
    for selector in config.prompt_selectors:
        try:
            matches = [
                item
                for item in page.locator(selector).all()
                if item.is_visible() and item.is_enabled()
            ]
        except PlaywrightError:
            continue
        if matches:
            return matches[-1]
    return None


def click_selector(page: Page, selectors: tuple[str, ...]) -> bool:
    for selector in selectors:
        try:
            matches = [
                item
                for item in page.locator(selector).all()
                if item.is_visible() and item.is_enabled()
            ]
            if matches:
                matches[-1].click()
                return True
        except PlaywrightError:
            continue
    return False


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


def generated_image_sources(page: Page, config: WebImageConfig) -> list[str]:
    sources: list[str] = []
    seen: set[str] = set()
    for selector in config.generated_image_selectors:
        try:
            images = page.locator(selector).all()
        except PlaywrightError:
            continue
        for item in images:
            try:
                source = item.get_attribute("src") or ""
                if item.is_visible() and source.startswith("http") and source not in seen:
                    sources.append(source)
                    seen.add(source)
            except PlaywrightError:
                continue
    return sources


def download_image_in_browser(page: Page, source: str) -> Path:
    """Fall back to Chrome's own network stack for an authenticated image URL."""
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    try:
        with page.expect_download(timeout=60_000) as download_info:
            page.evaluate(
                """source => {
                    const link = document.createElement('a');
                    link.href = source;
                    link.download = 'chatgpt-image.png';
                    link.style.display = 'none';
                    document.body.appendChild(link);
                    link.click();
                    link.remove();
                }""",
                source,
            )
        download = download_info.value
        suffix = Path(download.suggested_filename).suffix.lower() or ".png"
        target = DOWNLOAD_DIR / f"web-image-{time.time_ns()}{suffix}"
        download.save_as(target)
        return target
    except PlaywrightError as exc:
        raise ImageRetryable(
            f"ChatGPT 图片原图下载连续失败：{str(exc).splitlines()[0]}"
        ) from exc


def download_image_source(page: Page, source: str, attempts: int = 3) -> Path:
    """Download a generated image, tolerating transient TLS/proxy interruptions."""
    last_error = "未知网络错误"
    for attempt in range(1, attempts + 1):
        try:
            response = page.context.request.get(source, timeout=60_000)
            if not response.ok:
                last_error = f"HTTP {response.status}"
            else:
                content_type = (response.headers.get("content-type") or "").lower()
                suffix = ".png"
                if "jpeg" in content_type or "jpg" in content_type:
                    suffix = ".jpg"
                elif "webp" in content_type:
                    suffix = ".webp"
                DOWNLOAD_DIR.mkdir(exist_ok=True)
                target = DOWNLOAD_DIR / f"web-image-{time.time_ns()}{suffix}"
                target.write_bytes(response.body())
                return target
        except PlaywrightError as exc:
            last_error = str(exc).splitlines()[0]
        if attempt < attempts:
            page.wait_for_timeout(attempt * 1500)

    try:
        return download_image_in_browser(page, source)
    except ImageRetryable as exc:
        raise ImageRetryable(
            f"ChatGPT 图片资源下载失败（直连重试 {attempts} 次，最后错误："
            f"{last_error}；浏览器兜底也失败：{exc}）"
        ) from exc


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


def fill_and_submit_prompt(
    page: Page,
    config: WebImageConfig,
    prompt: str,
    timeout_seconds: int,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "输入框尚未出现"
    while time.monotonic() < deadline:
        assert_safe(page)
        prompt_box = visible_prompt(page, config)
        if prompt_box is None:
            page.wait_for_timeout(500)
            continue
        try:
            prompt_box.scroll_into_view_if_needed()
            prompt_box.click()
            prompt_box.fill(prompt)
            page.wait_for_timeout(500)
            if not click_selector(page, config.submit_selectors):
                if not click_named(page, config.submit_names):
                    prompt_box.press("Enter")
            return
        except PlaywrightError as exc:
            last_error = str(exc).splitlines()[0]
            page.wait_for_timeout(750)
    raise ImageRetryable(
        f"ChatGPT 输入框在页面重绘后仍不可用：{last_error}"
    )


def wait_ready(
    page: Page,
    config: WebImageConfig,
    timeout_seconds: int,
    allow_manual_challenge: bool = False,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    reported_blocker: str | None = None
    while time.monotonic() < deadline:
        blocker = detect_blocker(page_text(page))
        manual_challenge = allow_manual_challenge and blocker == "验证码或真人验证"
        if blocker and not manual_challenge:
            raise ImageBlocked(f"图片网站出现{blocker}，已安全停止")
        if manual_challenge and blocker != reported_blocker:
            print(
                f"检测到{blocker}，请在打开的 Chrome 中由你本人处理；"
                "程序不会自动绕过。",
                file=sys.stderr,
                flush=True,
            )
            reported_blocker = blocker
        text = page_text(page)
        if visible_prompt(page, config) and not LOGIN_TEXT.search(text):
            return
        page.wait_for_timeout(1000)
    text = page_text(page)
    if LOGIN_TEXT.search(text):
        raise ImageBlocked("图片专用 Chrome 尚未登录；请先运行 --setup")
    raise ImageRetryable("未找到图片网站提示词输入框；网页可能尚未加载或控件配置已过期")


def setup(timeout_seconds: int) -> dict:
    config = load_config()
    with sync_playwright() as playwright:
        context = launch(playwright, config)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            navigate(page, config)
            wait_ready(
                page,
                config,
                timeout_seconds,
                allow_manual_challenge=True,
            )
            READY_FILE.write_text(json.dumps({"provider": config.provider, "url": config.url}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return {"ready": True, "provider": config.provider, "profile": str(PROFILE_DIR), "url": page.url}
        finally:
            context.close()


def download_image(page: Page, config: WebImageConfig, prompt: str, timeout_seconds: int) -> Path:
    before_sources = set(generated_image_sources(page, config))
    before_downloads = visible_named_count(page, config.download_names)
    fill_and_submit_prompt(page, config, prompt, min(timeout_seconds, 60))

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        assert_safe(page)
        current_sources = generated_image_sources(page, config)
        new_sources = [source for source in current_sources if source not in before_sources]
        if new_sources:
            return download_image_source(page, new_sources[-1])
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


def recover_image(
    conversation_url: str,
    output: Path,
    ratio: str,
    timeout_seconds: int,
) -> dict:
    parsed = urlparse(conversation_url)
    if parsed.scheme != "https" or parsed.netloc != "chatgpt.com" or not parsed.path.startswith("/c/"):
        raise ValueError("--recover-url 必须是 https://chatgpt.com/c/... 会话地址")
    config = load_config()
    output = validate_output_path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = launch(playwright, config)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(conversation_url, wait_until="domcontentloaded", timeout=60_000)
            deadline = time.monotonic() + timeout_seconds
            downloaded: Path | None = None
            while time.monotonic() < deadline:
                assert_safe(page)
                sources = generated_image_sources(page, config)
                if sources:
                    downloaded = download_image_source(page, sources[-1])
                    break
                page.wait_for_timeout(1000)
            if downloaded is None:
                raise ImageRetryable("指定 ChatGPT 会话中未找到已生成图片")
        finally:
            context.close()
    return finalize_download(downloaded, output, ratio, config.provider)


def finalize_download(
    downloaded: Path,
    output: Path,
    ratio: str,
    provider: str,
) -> dict:
    shutil.copy2(downloaded, output)
    downloaded.unlink(missing_ok=True)
    if not _valid_image_signature(output):
        output.unlink(missing_ok=True)
        raise ImageRetryable("下载结果不是有效的 PNG/JPEG/WebP 图片")
    dimensions = _image_dimensions(output)
    if dimensions is None or not _matches_aspect_ratio(*dimensions, ratio):
        raise ImageRetryable(f"下载图片尺寸 {dimensions} 不符合目标画幅 {ratio}；保留文件供人工检查")
    return {
        "status": "downloaded_ready_for_catalog",
        "provider": provider,
        "generated_with": "chrome-web",
        "path": str(output),
        "sha256": sha256_file(output),
        "dimensions": list(dimensions),
        "ratio": ratio,
    }


def generate_once(
    config: WebImageConfig,
    web_prompt: str,
    output: Path,
    ratio: str,
    timeout_seconds: int,
) -> dict:
    """Generate once in a fresh browser context; always close it before returning."""
    with sync_playwright() as playwright:
        context = launch(playwright, config)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            navigate(page, config)
            wait_ready(page, config, timeout_seconds)
            downloaded = download_image(page, config, web_prompt, timeout_seconds)
        finally:
            context.close()
    return finalize_download(downloaded, output, ratio, config.provider)


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
    attempt_timeout = timeout_seconds or config.timeout_seconds
    failures: list[str] = []
    for attempt in range(1, GENERATION_ATTEMPTS + 1):
        try:
            return generate_once(config, web_prompt, output, ratio, attempt_timeout)
        except ImageBlocked:
            raise
        except (ImageRetryable, PlaywrightError, PlaywrightTimeoutError, OSError) as exc:
            failures.append(f"第{attempt}次：{str(exc).splitlines()[0]}")
            if attempt < GENERATION_ATTEMPTS:
                continue
            raise ImageRetryable(
                "图片生成已独立重启浏览器并尝试两次，仍失败；已关闭图片专用 Chrome。"
                + "；".join(failures)
            ) from exc


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
    parser.add_argument("--recover-url", help="从已经完成的 ChatGPT 会话恢复图片")
    parser.add_argument("--timeout", type=int)
    args = parser.parse_args()
    try:
        if args.setup or args.check:
            emit(setup(args.timeout or 600))
            return 0
        if args.recover_url:
            if not args.output or not args.ratio:
                parser.error("恢复图片需要 --output 和 --ratio")
            emit(recover_image(
                args.recover_url,
                args.output,
                args.ratio,
                args.timeout or 120,
            ))
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
