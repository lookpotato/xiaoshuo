#!/usr/bin/env python3
"""按需启动专用 Chrome，向番茄上传并排期单个章节。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

try:
    from playwright.sync_api import (
        BrowserContext,
        Error as PlaywrightError,
        Locator,
        Page,
        Playwright,
        TimeoutError as PlaywrightTimeoutError,
        sync_playwright,
    )
except ImportError:
    print(
        "缺少 Playwright。请先运行：python -m pip install -r requirements.txt",
        file=sys.stderr,
    )
    raise SystemExit(2)


ROOT = Path(__file__).resolve().parent
PROFILE_DIR = (
    Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    / "xiaoshuo"
    / "fanqie-chrome-profile-v2"
)
PROFILE_READY = PROFILE_DIR.parent / "fanqie-chrome-profile-v2.ready.json"
CHROME_CANDIDATES = (
    Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
    / "Google"
    / "Chrome"
    / "Application"
    / "chrome.exe",
    Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
    / "Google"
    / "Chrome"
    / "Application"
    / "chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Google"
    / "Chrome"
    / "Application"
    / "chrome.exe",
)
STOP_PATTERNS = re.compile(
    r"验证码|滑块验证|安全验证|手机验证|扫码登录|二维码|风险控制|账号异常|"
    r"政策警告|违规风险|身份验证",
)
SUCCESS_STATUSES = ("待发布", "审核中", "已发布")
MIN_PLAYWRIGHT_VERSION = (1, 61, 0)


class FanqieBlocked(RuntimeError):
    """需要用户人工处理的安全停止。"""


class FanqieRetryable(RuntimeError):
    """页面或网络临时失败，可安全重试。"""


@dataclass(frozen=True)
class PublishConfig:
    writer_url: str
    book_id: str
    submit_publish: bool


@dataclass(frozen=True)
class Chapter:
    number: int
    title: str
    body: str
    path: Path


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)


def chrome_path() -> Path:
    for candidate in CHROME_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise FanqieBlocked("找不到 Google Chrome，无法启动专用浏览器。")


def playwright_version() -> str:
    try:
        return version("playwright")
    except PackageNotFoundError:
        return "0"


def ensure_playwright_version() -> None:
    numbers = tuple(
        int(item) for item in re.findall(r"\d+", playwright_version())[:3]
    )
    if numbers < MIN_PLAYWRIGHT_VERSION:
        required = ".".join(str(item) for item in MIN_PLAYWRIGHT_VERSION)
        raise FanqieRetryable(
            f"Playwright 版本过旧（当前 {playwright_version()}，需要至少 "
            f"{required}）；请运行 `python -m pip install -r requirements.txt`"
        )


def parse_publish_config(project: Path) -> PublishConfig:
    text = (project / "publish_config.md").read_text(encoding="utf-8")

    def field(name: str) -> str:
        match = re.search(rf"^\s*{re.escape(name)}\s*:\s*(\S+)\s*$", text, re.M)
        if not match:
            raise ValueError(f"publish_config.md 缺少 {name}")
        return match.group(1)

    return PublishConfig(
        writer_url=field("fanqie_writer_url"),
        book_id=field("book_id"),
        submit_publish=field("submit_publish").lower() == "true",
    )


def parse_chapter(path: Path) -> Chapter:
    text = path.read_text(encoding="utf-8")
    title_match = re.match(
        r"^\s*#\s*第\s*(\d+)\s*章\s+(.+?)\s*$", text, re.M
    )
    if not title_match:
        raise ValueError(f"章节标题格式错误：{path}")
    metadata = re.search(r"\n---\s*\n+\s*##\s+Metadata\b", text, re.I)
    body_start = title_match.end()
    body_end = metadata.start() if metadata else len(text)
    body = text[body_start:body_end].strip()
    if len(body) < 1000:
        raise ValueError(f"章节正文过短：{path}")
    return Chapter(
        number=int(title_match.group(1)),
        title=title_match.group(2).strip(),
        body=body,
        path=path.resolve(),
    )


def launch_context(playwright: Playwright) -> BrowserContext:
    ensure_playwright_version()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        return playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            executable_path=str(chrome_path()),
            headless=False,
            viewport={"width": 1440, "height": 960},
            locale="zh-CN",
        )
    except PlaywrightError as exc:
        raise FanqieRetryable(
            "专用 Chrome 启动失败；请先关闭残留的番茄专用 Chrome，"
            "再重新运行同一条命令"
        ) from exc


def active_page(context: BrowserContext) -> Page:
    page = context.pages[0] if context.pages else context.new_page()
    page.set_default_timeout(15_000)
    return page


def page_text(page: Page) -> str:
    return page.locator("body").inner_text(timeout=10_000)[:20_000]


def assert_safe_page(page: Page, config: PublishConfig) -> None:
    text = page_text(page)
    if STOP_PATTERNS.search(text):
        raise FanqieBlocked("番茄出现验证、风控或政策提示，已安全停止。")
    if "登录" in page.url.lower() or (
        "登录" in text and "作家专区" not in page.title()
    ):
        raise FanqieBlocked(
            "专用 Chrome 尚未登录番茄。请运行 `python xiaoshuo --setup-browser`。"
        )
    if config.book_id not in page.url and "404修理站" not in text:
        raise FanqieBlocked("当前页面无法确认目标作品，禁止继续。")


def unique(locator: Locator, description: str) -> Locator:
    count = locator.count()
    if count != 1:
        raise FanqieRetryable(f"{description}匹配到 {count} 个控件")
    return locator


def wait_editor(page: Page, config: PublishConfig) -> None:
    try:
        page.goto(config.writer_url, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_selector(
            ".syl-editor-container .ProseMirror[contenteditable='true']",
            state="visible",
            timeout=45_000,
        )
    except PlaywrightTimeoutError as exc:
        raise FanqieRetryable("番茄章节编辑页加载超时") from exc
    assert_safe_page(page, config)


def type_controlled_input(
    page: Page,
    field: Locator,
    value: str,
    description: str,
) -> None:
    """像人工操作一样点击、键入并失焦，触发 React 的受控输入事件。"""
    field.scroll_into_view_if_needed()
    field.click()
    field.press("Control+A")
    field.press("Backspace")
    field.type(value, delay=60)
    field.press("Tab")
    page.wait_for_timeout(350)
    actual = field.input_value().strip()
    if actual != value:
        raise FanqieRetryable(
            f"{description}回读不一致：期望“{value}”，实际“{actual}”"
        )


def fill_editor(page: Page, chapter: Chapter) -> None:
    serial = unique(
        page.locator("input.serial-input:not([placeholder='请输入标题'])"),
        "章节号输入框",
    )
    title = unique(page.get_by_placeholder("请输入标题", exact=True), "标题输入框")
    body = unique(
        page.locator(
            ".syl-editor-container .ProseMirror[contenteditable='true']"
        ),
        "正文编辑器",
    )
    # “第”和“章”是固定文字，只向两者中间的输入框输入阿拉伯数字。
    type_controlled_input(page, serial, str(chapter.number), "章节号")
    # 标题框是 React 受控输入；必须先点击，再键入并用 Tab 触发失焦。
    type_controlled_input(page, title, chapter.title, "标题")
    body.click()
    body.fill(chapter.body)
    body.press("Tab")
    page.wait_for_timeout(1200)

    # 正文写入会导致编辑器重渲染，因此要在最后再次检查章节号和标题。
    serial_value = serial.input_value().strip()
    title_value = title.input_value().strip()
    if serial_value != str(chapter.number):
        raise FanqieRetryable(
            f"正文写入后章节号丢失：期望“{chapter.number}”，实际“{serial_value}”"
        )
    if title_value != chapter.title:
        raise FanqieRetryable(
            f"正文写入后标题丢失：期望“{chapter.title}”，实际“{title_value}”"
        )

    rendered = body.inner_text().strip()
    first = chapter.body.splitlines()[0].strip()
    last = chapter.body.splitlines()[-1].strip()
    if not rendered.startswith(first) or not rendered.endswith(last):
        raise FanqieRetryable("正文首尾回读不一致")
    if len(rendered) < max(1000, int(len(chapter.body) * 0.75)):
        raise FanqieRetryable("平台正文长度明显偏离本地章节")


def visible_button(page: Page, name: str) -> Locator | None:
    matches = page.get_by_role("button", name=name, exact=True)
    visible = [item for item in matches.all() if item.is_visible()]
    if len(visible) == 1:
        return visible[0]
    if len(visible) > 1:
        raise FanqieRetryable(f"按钮“{name}”不唯一")
    return None


def click_if_visible(page: Page, name: str) -> bool:
    button = visible_button(page, name)
    if not button:
        return False
    button.click()
    page.wait_for_timeout(500)
    return True


def choose_basic_check(page: Page) -> None:
    for label in ("仅基础检测", "基础检测"):
        options = page.get_by_text(label, exact=True)
        visible = [item for item in options.all() if item.is_visible()]
        if len(visible) == 1:
            visible[0].click()
            page.wait_for_timeout(600)
            return
    text = page_text(page)
    if "请选择内容检测方式" in text:
        raise FanqieRetryable("未找到“仅基础检测”选项")


def publish_dialog(page: Page) -> Locator:
    dialogs = page.locator(".arco-modal:visible").filter(has_text="是否使用AI")
    if dialogs.count() != 1:
        raise FanqieRetryable(
            f"发布设置弹窗匹配到 {dialogs.count()} 个，无法安全操作"
        )
    return dialogs


def choose_ai_yes(page: Page) -> None:
    scope = publish_dialog(page)
    # 按番茄当前实际 DOM 点击 radio 的可见蒙层。两个蒙层顺序为“是、否”。
    masks = scope.locator("span.arco-radio-mask-wrapper")
    visible = [item for item in masks.all() if item.is_visible()]
    if len(visible) != 2:
        raise FanqieRetryable(
            f"发布设置中的 AI 可见按钮匹配到 {len(visible)} 个，期望 2 个"
        )
    yes_mask = visible[0]
    yes_mask.click()
    page.wait_for_timeout(500)

    yes_label = yes_mask.locator("xpath=ancestor::label[1]")
    yes_input = yes_label.locator("input[type='radio']")
    no_input = visible[1].locator(
        "xpath=ancestor::label[1]//input[@type='radio']"
    )
    if (
        yes_input.count() != 1
        or no_input.count() != 1
        or not yes_input.is_checked()
        or no_input.is_checked()
    ):
        raise FanqieRetryable("AI 选项回读失败：没有唯一选中“是”")


def enable_timed_publish(page: Page) -> None:
    scope = publish_dialog(page)
    switches = scope.locator("button[role='switch'].arco-switch")
    visible = [item for item in switches.all() if item.is_visible()]
    if len(visible) != 1:
        raise FanqieRetryable(
            f"定时发布按钮匹配到 {len(visible)} 个，期望 1 个"
        )
    switch = visible[0]
    if switch.get_attribute("aria-checked") != "true":
        switch.click()
        page.wait_for_timeout(600)
    if switch.get_attribute("aria-checked") != "true":
        raise FanqieRetryable("定时发布开关回读失败")


def debug_checkpoint(message: str) -> None:
    print(
        f"\n{message}\n请查看 Chrome 页面；确认后回到终端按 Enter 继续。",
        file=sys.stderr,
        flush=True,
    )
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        pass


def publishing_field(
    scope: Locator,
    value_pattern: str,
    description: str,
) -> Locator:
    fields = [
        item
        for item in scope.locator("input").all()
        if item.is_visible()
        and re.fullmatch(value_pattern, item.input_value().strip())
    ]
    if len(fields) != 1:
        values = [
            item.input_value()
            for item in scope.locator("input").all()
            if item.is_visible()
        ]
        raise FanqieRetryable(
            f"{description}匹配到 {len(fields)} 个；弹窗可见输入值为 {values}"
        )
    return fields[0]


def choose_date(page: Page, target: str) -> None:
    target_date = date.fromisoformat(target)
    scope = publish_dialog(page)
    field = publishing_field(
        scope,
        r"\d{4}-\d{2}-\d{2}",
        "发布日期输入框",
    )
    # 打开定时发布后，番茄有时会自动展开日期面板。此时再次点击输入框
    # 会被面板中的日期按钮拦截，Playwright 会一直重试到超时。
    if page.locator(".arco-picker-header-value:visible").count() == 0:
        field.click()
        page.wait_for_timeout(300)
    # 先切换到目标年月，再点击 in-view 单元格，避免误点相邻月份同号日期。
    for _ in range(14):
        headers = page.locator(".arco-picker-header-value")
        header_text = " ".join(
            item.inner_text() for item in headers.all() if item.is_visible()
        )
        if str(target_date.year) in header_text and (
            f"{target_date.month}月" in header_text
            or f"{target_date.month:02d}" in header_text
        ):
            break
        # Arco 的四个箭头依次是上一年、上个月、下个月、下一年。
        header_icons = page.locator(
            ".arco-picker-header:visible "
            ".arco-picker-header-icon:not(.arco-picker-header-icon-hidden)"
        )
        if header_icons.count() < 4:
            raise FanqieRetryable("无法切换到目标发布月份")
        header_icons.nth(2).click()
        page.wait_for_timeout(250)
    else:
        raise FanqieRetryable("目标发布日期超出可选择范围")
    cell = page.locator(
        ".arco-picker-cell-in-view:not(.arco-picker-cell-disabled)"
    ).filter(has_text=re.compile(rf"^\s*{target_date.day}\s*$"))
    unique(cell, "目标发布日期").click()
    page.wait_for_timeout(300)
    if target not in field.input_value():
        raise FanqieRetryable(
            f"发布日期回读失败：期望 {target}，实际 {field.input_value()}"
        )


def choose_time(page: Page, target: str) -> None:
    scope = publish_dialog(page)
    field = publishing_field(scope, r"\d{2}:\d{2}", "发布时间输入框")
    if field.input_value().strip() != target:
        field.click()
        field.press("Control+A")
        field.press("Backspace")
        field.type(target, delay=80)
        field.press("Enter")
        field.press("Tab")
        page.wait_for_timeout(500)
    if field.input_value().strip() != target:
        raise FanqieRetryable(
            f"发布时间回读失败：期望 {target}，实际 {field.input_value()}"
        )


def verify_list(page: Page, chapter: Chapter) -> dict:
    try:
        page.wait_for_url(re.compile(r".*/chapter-manage/.*"), timeout=45_000)
    except PlaywrightTimeoutError:
        pass
    assert_safe_page(page, parse_publish_config(chapter.path.parents[1]))
    chapter_text = f"第{chapter.number}章 {chapter.title}"
    title = page.get_by_text(chapter_text, exact=True)
    try:
        title.wait_for(state="visible", timeout=45_000)
    except PlaywrightTimeoutError as exc:
        raise FanqieRetryable(
            f"章节管理页等待“{chapter_text}”出现超时"
        ) from exc
    visible = [item for item in title.all() if item.is_visible()]
    if len(visible) != 1:
        raise FanqieRetryable("章节管理页无法唯一定位目标章节")
    row = visible[0].locator(
        "xpath=ancestor::tr[contains(concat(' ', normalize-space(@class), ' '), "
        "' arco-table-tr ')][1]"
    )
    if row.count() != 1:
        raise FanqieRetryable("章节管理页无法定位目标章节所在表格行")
    text = row.inner_text()
    status = next((item for item in SUCCESS_STATUSES if item in text), None)
    if not status:
        raise FanqieRetryable("目标章节尚未显示待发布、审核中或已发布")
    return {"status": status, "row_text": text, "url": page.url}


def publish(
    project: Path,
    chapter_path: Path,
    publish_date: str,
    publish_time: str,
    debug_browser: bool = False,
) -> dict:
    config = parse_publish_config(project)
    chapter = parse_chapter(chapter_path)
    with sync_playwright() as playwright:
        context = launch_context(playwright)
        try:
            page = active_page(context)
            wait_editor(page, config)
            fill_editor(page, chapter)
            assert_safe_page(page, config)
            unique(
                page.get_by_role("button", name="下一步", exact=True),
                "下一步按钮",
            ).click()
            page.wait_for_timeout(700)
            assert_safe_page(page, config)
            click_if_visible(page, "提交")
            assert_safe_page(page, config)
            choose_basic_check(page)
            assert_safe_page(page, config)
            if not config.submit_publish:
                if not click_if_visible(page, "存草稿"):
                    raise FanqieRetryable("未找到存草稿按钮")
                return {
                    "chapter": chapter.number,
                    "status": "draft_saved",
                    "url": page.url,
                }
            choose_ai_yes(page)
            enable_timed_publish(page)
            if debug_browser:
                debug_checkpoint(
                    "调试检查点 1/3：已选择“是否使用AI=是”，并打开“定时发布”。"
                )
            choose_date(page, publish_date)
            if debug_browser:
                debug_checkpoint(
                    f"调试检查点 2/3：已选择发布日期 {publish_date}。"
                )
            choose_time(page, publish_time)
            if debug_browser:
                debug_checkpoint(
                    f"调试检查点 3/3：已设置发布时间 {publish_time}；"
                    "下一步将点击“确认发布”。"
                )
            assert_safe_page(page, config)
            confirm = visible_button(page, "确认发布")
            if not confirm:
                raise FanqieRetryable("未找到确认发布按钮")
            confirm.click()
            result = verify_list(page, chapter)
            return {"chapter": chapter.number, **result}
        except Exception as exc:
            if debug_browser:
                print(
                    f"\n浏览器操作已停在出错页面：{exc}\n"
                    "请查看 Chrome 中的页面和弹窗。记录现象后，"
                    "回到终端按 Enter 关闭浏览器。",
                    file=sys.stderr,
                    flush=True,
                )
                try:
                    input()
                except (EOFError, KeyboardInterrupt):
                    pass
            if isinstance(exc, PlaywrightError):
                raise FanqieRetryable(f"浏览器操作失败：{exc}") from exc
            raise
        finally:
            try:
                context.close()
            except PlaywrightError:
                pass


def setup(project: Path, timeout_seconds: int) -> dict:
    config = parse_publish_config(project)
    PROFILE_READY.unlink(missing_ok=True)
    with sync_playwright() as playwright:
        context = launch_context(playwright)
        try:
            page = active_page(context)
            try:
                page.goto(
                    config.writer_url,
                    wait_until="domcontentloaded",
                    timeout=45_000,
                )
            except PlaywrightTimeoutError:
                # 页面若已进入番茄，继续由下面的可见状态检查判断。
                pass
            deadline = time.monotonic() + timeout_seconds
            print(
                "请在已打开的专用 Chrome 中完成番茄登录。"
                "程序只检查页面是否进入目标作品，不读取登录资料。",
                flush=True,
            )
            while time.monotonic() < deadline:
                try:
                    text = page_text(page)
                    if (
                        config.book_id in page.url
                        and "作家专区" in page.title()
                        and not STOP_PATTERNS.search(text)
                    ):
                        editor = page.locator(
                            ".syl-editor-container "
                            ".ProseMirror[contenteditable='true']"
                        )
                        if editor.count() != 1 or not editor.is_visible():
                            page.wait_for_timeout(1000)
                            continue
                        PROFILE_READY.write_text(
                            json.dumps(
                                {
                                    "book_id": config.book_id,
                                    "verified_at": datetime.now().astimezone().isoformat(),
                                },
                                ensure_ascii=False,
                                indent=2,
                            )
                            + "\n",
                            encoding="utf-8",
                        )
                        return {
                            "ready": True,
                            "book_id": config.book_id,
                            "profile": str(PROFILE_DIR),
                            "url": page.url,
                        }
                except PlaywrightTimeoutError:
                    pass
                page.wait_for_timeout(1000)
            raise FanqieBlocked("等待登录超时，请重新运行 --setup-browser。")
        finally:
            context.close()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="番茄专用 Chrome 按需发布工作器")
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--chapter-file", type=Path)
    parser.add_argument("--date")
    parser.add_argument("--time")
    parser.add_argument("--debug-browser", action="store_true")
    parser.add_argument("--login-timeout", type=int, default=600)
    args = parser.parse_args()
    project = args.project.resolve()
    try:
        if args.setup:
            emit(setup(project, args.login_timeout))
            return 0
        if args.check:
            if not PROFILE_READY.is_file():
                raise FanqieBlocked(
                    "专用 Chrome 尚未完成首次登录配置；"
                    "请在 VS Code 终端运行 `python xiaoshuo --setup-browser`。"
                )
            config = parse_publish_config(project)
            with sync_playwright() as playwright:
                context = launch_context(playwright)
                try:
                    page = active_page(context)
                    wait_editor(page, config)
                    emit(
                        {
                            "ready": True,
                            "book_id": config.book_id,
                            "profile": str(PROFILE_DIR),
                        }
                    )
                finally:
                    context.close()
            return 0
        if not args.chapter_file or not args.date or not args.time:
            parser.error("发布需要 --chapter-file、--date 和 --time")
        emit(
            publish(
                project,
                args.chapter_file,
                args.date,
                args.time,
                debug_browser=args.debug_browser,
            )
        )
        return 0
    except FanqieBlocked as exc:
        emit({"status": "blocked_manual", "message": str(exc)})
        return 4
    except (FanqieRetryable, PlaywrightError, ValueError, OSError) as exc:
        emit({"status": "failed_retryable", "message": str(exc)})
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
