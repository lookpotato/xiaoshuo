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

    safe_to_retry: bool = False


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
    image_path: Path | None = None
    image_alt_text: str | None = None


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
    image_pattern = re.compile(
        r"(?m)^[ \t]*!\[([^\]\r\n]*)\]\((\.\./images/[^)\r\n]+)\)[ \t]*\r?\n?"
    )
    image_matches = list(image_pattern.finditer(body))
    if len(image_matches) > 1:
        raise ValueError("每章最多只能包含 1 张本地图片")
    image_path: Path | None = None
    image_alt_text: str | None = None
    if image_matches:
        image_alt_text = image_matches[0].group(1).strip() or None
        relative = image_matches[0].group(2)
        candidate = (path.parent / Path(relative)).resolve()
        project = path.resolve().parents[1]
        image_root = (project / "images").resolve()
        if image_root not in candidate.parents or not candidate.is_file():
            raise ValueError(f"章节图片不存在或越出本书 images/ 目录：{relative}")
        image_path = candidate
    # Project-local illustrations are rendered by Markdown readers. Fanqie's
    # editor receives plain text, so never leak a local path as visible prose.
    body = image_pattern.sub("", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    if len(body) < 1000:
        raise ValueError(f"章节正文过短：{path}")
    return Chapter(
        number=int(title_match.group(1)),
        title=title_match.group(2).strip(),
        body=body,
        path=path.resolve(),
        image_path=image_path,
        image_alt_text=image_alt_text,
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
            ignore_default_args=["--no-sandbox"],
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
        # The editor shell appears before Fanqie's cloud-draft hydration finishes.
        # Filling immediately can be overwritten by that late React render.
        page.wait_for_timeout(1500)
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
    actual = ""
    for attempt in range(3):
        field.scroll_into_view_if_needed()
        field.click()
        field.press("Control+A")
        field.press("Backspace")
        if attempt == 1:
            # Atomic fill avoids Fanqie's React render swallowing the second digit.
            field.fill(value)
        else:
            field.press_sequentially(value, delay=100 + attempt * 100)
        field.press("Tab")
        page.wait_for_timeout(500)
        actual = field.input_value().strip()
        if actual == value:
            return
        page.wait_for_timeout(400)
    raise FanqieRetryable(
        f"{description}连续 3 次回读不一致：期望“{value}”，实际“{actual}”"
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
    # React may replace the title row while ProseMirror accepts the body. Re-query
    # the live controls and restore only values that were lost during that render.
    serial = unique(
        page.locator("input.serial-input:not([placeholder='请输入标题'])"),
        "章节号输入框",
    )
    title = unique(page.get_by_placeholder("请输入标题", exact=True), "标题输入框")
    if serial.input_value().strip() != str(chapter.number):
        type_controlled_input(page, serial, str(chapter.number), "章节号")
    if title.input_value().strip() != chapter.title:
        type_controlled_input(page, title, chapter.title, "标题")
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


def _author_note_scope(page: Page) -> Locator:
    labels = visible_matches(page.get_by_text("作者有话说", exact=True))
    if len(labels) != 1:
        raise FanqieRetryable(
            f"“作者有话说”区域标题匹配到 {len(labels)} 个，期望 1 个"
        )
    scope = labels[0]
    for _ in range(8):
        scope = scope.locator("xpath=..")
        if scope.count() != 1:
            break
        has_add = bool(visible_matches(scope.get_by_text("添加", exact=True)))
        has_editor = bool(
            visible_matches(
                scope.locator("textarea, [contenteditable='true'], input[type='file']")
            )
        )
        if has_add or has_editor:
            return scope
    raise FanqieRetryable("无法定位“作者有话说”完整操作区域")


def _single_visible_control(locator: Locator, description: str) -> Locator:
    matches = [item for item in locator.all() if item.is_visible() and item.is_enabled()]
    if len(matches) != 1:
        raise FanqieRetryable(f"{description}匹配到 {len(matches)} 个，期望 1 个")
    return matches[0]


def _author_note_image_button(scope: Locator) -> Locator:
    selectors = (
        "button[aria-label*='图片'], button[title*='图片'], "
        "[role='button'][aria-label*='图片'], [role='button'][title*='图片'], "
        "button:has([class*='image']), button:has([class*='picture']), "
        "[role='button']:has([class*='image']), [role='button']:has([class*='picture'])"
    )
    matches = [
        item
        for item in scope.locator(selectors).all()
        if item.is_visible() and item.is_enabled()
    ]
    if len(matches) == 1:
        return matches[0]
    text_matches = [
        item
        for item in scope.get_by_text("图片", exact=True).all()
        if item.is_visible()
    ]
    if len(text_matches) == 1:
        return text_matches[0]
    raise FanqieRetryable(
        f"作者有话说区域的图片按钮无法唯一定位：图标候选 {len(matches)} 个，"
        f"文字候选 {len(text_matches)} 个"
    )


def _upload_dialog(page: Page) -> tuple[Locator, Locator]:
    hints = visible_matches(
        # The live Fanqie UI currently says “点击或拖拽文件到此处上传”.
        # Match the stable prefix so a minor wording change cannot prevent
        # the uploader from being recognized before it is clicked.
        page.get_by_text("点击或拖拽文件", exact=False)
    )
    if len(hints) != 1:
        raise FanqieRetryable(
            f"本地图片上传提示匹配到 {len(hints)} 个，期望 1 个"
        )
    hint = hints[0]
    upload_control = hint
    for _ in range(8):
        upload_control = upload_control.locator("xpath=..")
        if upload_control.count() != 1:
            break
        if upload_control.locator("input[type='file']").count() == 1:
            break
    else:
        raise FanqieRetryable("无法定位本地图片拖拽上传区域")
    if upload_control.locator("input[type='file']").count() != 1:
        raise FanqieRetryable("无法定位本地图片拖拽上传区域")

    dialog = upload_control
    for _ in range(8):
        dialog = dialog.locator("xpath=..")
        if dialog.count() != 1:
            break
        has_file_input = dialog.locator("input[type='file']").count() == 1
        has_confirm = bool(visible_matches(dialog.get_by_text("确定", exact=True)))
        if has_file_input and has_confirm:
            return dialog, upload_control
    raise FanqieRetryable("无法定位本地图片上传弹窗")


def _author_note_preview_count(scope: Locator) -> int:
    return scope.locator(
        "img[src], [style*='background-image'], [data-src], [data-url], "
        "[class*='image-preview'], [class*='upload-item']"
    ).count()


def author_note_text(chapter: Chapter) -> str:
    """Build the required 作者有话说 text from the chapter image description."""
    description = re.sub(
        r"\s+", " ", chapter.image_alt_text or chapter.title
    ).strip()
    return f"本章配图：{description}"


def fill_author_note_text(scope: Locator, chapter: Chapter) -> None:
    editors = [
        item
        for item in scope.locator(
            "textarea, [contenteditable='true'], input:not([type='file'])"
        ).all()
        if item.is_visible() and item.is_enabled()
    ]
    if len(editors) != 1:
        raise FanqieRetryable(
            f"作者有话说正文输入框匹配到 {len(editors)} 个，期望 1 个"
        )
    editor = editors[0]
    note = author_note_text(chapter)
    editor.fill(note)
    tag_name = editor.evaluate("element => element.tagName.toLowerCase()")
    rendered = (
        editor.input_value().strip()
        if tag_name in {"input", "textarea"}
        else editor.inner_text().strip()
    )
    if rendered != note:
        raise FanqieRetryable(
            f"作者有话说正文回读不一致：期望“{note}”，实际“{rendered}”"
        )


def select_author_note_image(
    page: Page, upload_control: Locator, image_path: Path
) -> None:
    """Click the visible dropzone, then select the exact local image."""
    file_inputs = upload_control.locator("input[type='file']")
    if file_inputs.count() != 1:
        raise FanqieRetryable(
            f"上传组件内部文件输入控件匹配到 {file_inputs.count()} 个，期望 1 个"
        )
    try:
        with page.expect_file_chooser(timeout=5_000) as chooser_info:
            upload_control.click()
        chooser_info.value.set_files(str(image_path))
    except PlaywrightTimeoutError as exc:
        raise FanqieRetryable(
            "点击可见拖拽上传区域后未出现本地文件选择器"
        ) from exc


def upload_author_note_image(page: Page, config: PublishConfig, chapter: Chapter) -> None:
    """Upload the chapter's sole image through 作者有话说 → 添加图文."""
    if chapter.image_path is None:
        return
    if not chapter.image_path.is_file():
        raise FanqieRetryable(f"本地章节图片不存在：{chapter.image_path}")

    scope = _author_note_scope(page)
    add_candidates = [
        item
        for item in scope.get_by_text("添加", exact=True).all()
        if item.is_visible() and item.is_enabled()
    ]
    if len(add_candidates) != 1:
        raise FanqieRetryable(
            f"作者有话说的“添加”按钮匹配到 {len(add_candidates)} 个，期望 1 个"
        )
    add_candidates[0].hover()
    page.wait_for_timeout(300)
    add_rich = _single_visible_control(
        page.get_by_text("添加图文", exact=True), "“添加图文”选项"
    )
    add_rich.click()
    page.wait_for_timeout(500)
    assert_safe_page(page, config)

    scope = _author_note_scope(page)
    fill_author_note_text(scope, chapter)
    previews_before = _author_note_preview_count(scope)
    image_button = _author_note_image_button(scope)
    image_button.click()
    page.wait_for_timeout(400)
    assert_safe_page(page, config)

    dialog, upload_control = _upload_dialog(page)
    select_author_note_image(page, upload_control, chapter.image_path)
    confirm: Locator | None = None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        page.wait_for_timeout(500)
        assert_safe_page(page, config)
        dialog_text = dialog.inner_text()
        if re.search(r"上传失败|文件格式不支持|图片过大|上传异常", dialog_text):
            raise FanqieRetryable(f"番茄图片上传失败：{dialog_text[:300]}")
        candidates = [
            item
            for item in dialog.get_by_text("确定", exact=True).all()
            if item.is_visible() and item.is_enabled()
        ]
        if len(candidates) == 1:
            confirm = candidates[0]
            break
        if len(candidates) > 1:
            raise FanqieRetryable("图片上传弹窗的“确定”按钮不唯一")
    if confirm is None:
        raise FanqieRetryable("等待本地图片上传完成超时，“确定”按钮仍不可用")
    confirm.click()
    try:
        upload_control.wait_for(state="hidden", timeout=15_000)
    except PlaywrightTimeoutError as exc:
        raise FanqieRetryable("点击图片上传“确定”后弹窗未关闭") from exc
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        page.wait_for_timeout(500)
        assert_safe_page(page, config)
        scope = _author_note_scope(page)
        previews_after = _author_note_preview_count(scope)
        filename_visible = chapter.image_path.name in scope.inner_text()
        if previews_after > previews_before or filename_visible:
            return
    raise FanqieRetryable("作者有话说区域未回显已上传图片，禁止进入下一步")


def visible_button(page: Page, name: str) -> Locator | None:
    matches = page.get_by_role("button", name=name, exact=True)
    viewport = page.viewport_size or {"width": 1440, "height": 960}
    visible = []
    for item in matches.all():
        if not item.is_visible() or not item.is_enabled():
            continue
        box = item.bounding_box()
        if not box:
            continue
        if (
            box["x"] + box["width"] <= 0
            or box["y"] + box["height"] <= 0
            or box["x"] >= viewport["width"]
            or box["y"] >= viewport["height"]
        ):
            continue
        visible.append(item)
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


def click_with_hidden_overlay_guard(page: Page, button: Locator, name: str) -> None:
    """Bypass only stale dialogs explicitly marked aria-hidden=true."""
    try:
        button.click(timeout=3_000)
        return
    except PlaywrightTimeoutError as exc:
        dialogs = visible_matches(page.locator("[role='dialog']:visible"))
        active = [
            item
            for item in dialogs
            if item.get_attribute("aria-hidden") != "true"
        ]
        if active:
            summaries = [item.inner_text().strip()[:300] for item in active]
            raise FanqieBlocked(
                f"点击“{name}”前出现未授权的可见弹窗：{summaries}"
            ) from exc
        hidden = [
            item for item in dialogs if item.get_attribute("aria-hidden") == "true"
        ]
        if not hidden:
            raise FanqieRetryable(f"“{name}”按钮无法点击，且未识别到隐藏遮罩") from exc
        # 这些节点已由页面明确标为隐藏，只是退出动画残留层仍错误拦截指针。
        # force=True 仍会把鼠标坐标事件送到遮罩；调用唯一已核验按钮自身的
        # DOM click 才能避开命中测试，同时不触碰任何可见弹窗。
        button.evaluate("element => element.click()")
        page.wait_for_timeout(500)


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


def visible_publish_switches(page: Page) -> list[Locator]:
    switches: list[Locator] = []
    for frame in page.frames:
        candidates = frame.locator("[role='switch'], .arco-switch")
        switches.extend(item for item in candidates.all() if item.is_visible())
    return switches


def wait_through_publish_checks(page: Page, config: PublishConfig) -> None:
    """Follow Fanqie's async warning/check dialogs until publish settings exist."""
    deadline = time.monotonic() + 35
    submitted_typo_warning = False
    selected_basic_check = False
    while time.monotonic() < deadline:
        assert_safe_page(page, config)
        switches = visible_publish_switches(page)
        if len(switches) == 1:
            return
        if len(switches) > 1:
            raise FanqieRetryable(
                f"发布设置中的定时开关匹配到 {len(switches)} 个，无法安全操作"
            )

        submit = visible_button(page, "提交")
        if submit:
            click_with_hidden_overlay_guard(page, submit, "提交")
            submitted_typo_warning = True
            page.wait_for_timeout(500)
            continue

        basic_options: list[Locator] = []
        for label in ("仅基础检测", "基础检测"):
            basic_options.extend(
                item
                for item in page.get_by_text(label, exact=True).all()
                if item.is_visible()
            )
        if len(basic_options) == 1:
            basic_options[0].click()
            selected_basic_check = True
            page.wait_for_timeout(600)
            continue
        if len(basic_options) > 1:
            raise FanqieRetryable("内容检测方式无法唯一定位")

        active_dialogs = [
            item
            for item in visible_matches(page.locator("[role='dialog']:visible"))
            if item.get_attribute("aria-hidden") != "true"
        ]
        unknown = []
        for dialog in active_dialogs:
            summary = dialog.inner_text().strip()
            if summary and not any(
                known in summary
                for known in (
                    "检测到你还有错别字未修改",
                    "请选择内容检测方式",
                    "发布设置",
                )
            ):
                unknown.append(summary[:300])
        if unknown:
            raise FanqieBlocked(f"发布流程出现未授权的新弹窗：{unknown}")
        page.wait_for_timeout(250)

    raise FanqieRetryable(
        "等待番茄发布检查流程超时："
        f"错别字提示已提交={submitted_typo_warning}，"
        f"基础检测已选择={selected_basic_check}"
    )


def publish_dialog(page: Page) -> Locator:
    deadline = time.monotonic() + 15
    switches: list[Locator] = []
    while time.monotonic() < deadline:
        switches = visible_publish_switches(page)
        if len(switches) == 1:
            break
        if len(switches) > 1:
            raise FanqieRetryable(
                f"发布设置中的定时开关匹配到 {len(switches)} 个，无法安全操作"
            )
        page.wait_for_timeout(250)
    if len(switches) != 1:
        raise FanqieRetryable(
            f"等待发布设置中的定时开关出现超时（frame 数：{len(page.frames)}）"
        )
    dialog = switches[0].locator(
        "xpath=ancestor::*[.//input[@type='radio']][1]"
    )
    if dialog.count() != 1 or not dialog.is_visible():
        raise FanqieRetryable("无法从发布控件定位发布设置弹窗")
    return dialog


def publishing_button(scope: Locator | Page, name: str) -> Locator | None:
    visible = [
        item
        for item in scope.locator("button").all()
        if item.is_visible() and item.inner_text().strip() == name
    ]
    if len(visible) == 1:
        return visible[0]
    if len(visible) > 1:
        raise FanqieRetryable(f"发布设置中的按钮“{name}”不唯一")
    return None


def submit_publish_confirmation(page: Page, confirm: Locator) -> None:
    """Click once and surface Fanqie's business-level rejection immediately."""
    try:
        with page.expect_response(
            lambda response: "/api/author/publish_article/" in response.url,
            timeout=20_000,
        ) as response_info:
            confirm.evaluate("element => element.click()")
        response = response_info.value
        payload = response.json()
    except (PlaywrightTimeoutError, PlaywrightError, ValueError, json.JSONDecodeError):
        # Some successful UI revisions may navigate without exposing this response;
        # the authoritative chapter-list verification remains the fallback.
        return
    if not isinstance(payload, dict):
        return
    code = payload.get("code")
    message = payload.get("message") or payload.get("msg") or "未知原因"
    if code not in (None, 0, "0"):
        raise FanqieBlocked(f"番茄拒绝确认发布：code={code}，message={message}")


def choose_ai_yes(page: Page) -> None:
    scope = publish_dialog(page)
    # 装饰层的 class 会随番茄/Arco 版本变化；真实 radio 顺序稳定为“是、否”。
    radios = scope.locator("input[type='radio']")
    if radios.count() != 2:
        raise FanqieRetryable(
            f"发布设置中的 AI 单选框匹配到 {radios.count()} 个，期望 2 个"
        )
    yes_input = radios.nth(0)
    no_input = radios.nth(1)
    if not yes_input.is_checked():
        yes_label = yes_input.locator("xpath=ancestor::label[1]")
        if yes_label.count() != 1 or not yes_label.is_visible():
            raise FanqieRetryable("无法定位“是否使用AI=是”的可见标签")
        yes_label.click(force=True)
        page.wait_for_timeout(500)
    if not yes_input.is_checked() or no_input.is_checked():
        raise FanqieRetryable("AI 选项回读失败：没有唯一选中“是”")


def enable_timed_publish(page: Page) -> None:
    scope = publish_dialog(page)
    switches = scope.locator("[role='switch'], .arco-switch")
    visible = [item for item in switches.all() if item.is_visible()]
    if len(visible) != 1:
        raise FanqieRetryable(
            f"定时发布按钮匹配到 {len(visible)} 个，期望 1 个"
        )
    switch = visible[0]
    if switch.get_attribute("aria-checked") != "true":
        switch.click(force=True)
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
        page.wait_for_timeout(300)
        containers = visible_matches(
            page.locator(".arco-timepicker-container:visible")
        )
        if len(containers) != 1:
            raise FanqieRetryable(
                f"发布时间面板匹配到 {len(containers)} 个，期望 1 个"
            )
        container = containers[0]
        lists = visible_matches(container.locator(".arco-timepicker-list"))
        if len(lists) != 2:
            raise FanqieRetryable(
                f"发布时间小时/分钟列表匹配到 {len(lists)} 个，期望 2 个"
            )
        hour, minute = target.split(":")
        for column, value, description in (
            (lists[0], hour, "小时"),
            (lists[1], minute, "分钟"),
        ):
            options = visible_matches(
                column.locator("li.arco-timepicker-cell").filter(
                    has_text=re.compile(rf"^\s*{value}\s*$")
                )
            )
            if len(options) != 1:
                raise FanqieRetryable(
                    f"发布时间{description} {value} 匹配到 {len(options)} 个"
                )
            options[0].scroll_into_view_if_needed()
            options[0].click()
            # 时间列点击后会异步重渲染。若立刻点击下一列，小时选择可能
            # 被旧状态覆盖，表现为只改分钟（例如 12:50 回读成 18:50）。
            page.wait_for_timeout(300)
            selected = visible_matches(
                column.locator("li.arco-timepicker-cell-selected")
            )
            if len(selected) != 1 or selected[0].inner_text().strip() != value:
                actual = [item.inner_text().strip() for item in selected]
                raise FanqieRetryable(
                    f"发布时间{description}选择未生效：期望 {value}，实际 {actual}"
                )
        confirms = visible_matches(container.get_by_text("确定", exact=True))
        if len(confirms) != 1:
            raise FanqieRetryable("发布时间面板无法唯一定位“确定”")
        confirms[0].click()
        page.wait_for_timeout(500)
    if field.input_value().strip() != target:
        raise FanqieRetryable(
            f"发布时间回读失败：期望 {target}，实际 {field.input_value()}"
        )


def verify_publish_settings(page: Page, publish_date: str, publish_time: str) -> None:
    scope = publish_dialog(page)
    date_field = publishing_field(scope, r"\d{4}-\d{2}-\d{2}", "发布日期输入框")
    time_field = publishing_field(scope, r"\d{2}:\d{2}", "发布时间输入框")
    radios = scope.locator("input[type='radio']")
    switches = visible_matches(scope.locator("[role='switch'], .arco-switch"))
    if (
        date_field.input_value().strip() != publish_date
        or time_field.input_value().strip() != publish_time
        or radios.count() != 2
        or not radios.nth(0).is_checked()
        or radios.nth(1).is_checked()
        or len(switches) != 1
        or switches[0].get_attribute("aria-checked") != "true"
    ):
        raise FanqieRetryable(
            "确认发布前最终回读失败：日期、时间、AI=是或定时开关不一致"
        )


def chapter_manage_url(config: PublishConfig, project: Path | None = None) -> str:
    match = re.match(r"(https?://[^/]+)", config.writer_url)
    if not match:
        raise FanqieBlocked("publish_config.md 中的番茄地址无效")
    origin = match.group(1)
    if project:
        known_urls: list[str] = []
        for schedule_path in sorted(project.glob("batch_schedule_*.json")):
            try:
                payload = json.loads(schedule_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for entry in payload.get("entries", []):
                value = str(entry.get("fanqie_url", ""))
                if (
                    value.startswith(origin + "/main/writer/chapter-manage/")
                    and config.book_id in value
                ):
                    known_urls.append(value)
        if known_urls:
            return known_urls[-1]
    return f"{origin}/main/writer/chapter-manage/{config.book_id}?type=1"


def visible_matches(locator: Locator) -> list[Locator]:
    return [item for item in locator.all() if item.is_visible()]


def find_chapter_row(page: Page, chapter: Chapter) -> Locator:
    """Find a chapter by identity while following Fanqie's 15-row pagination."""
    chapter_text = f"第{chapter.number}章 {chapter.title}"
    visited_pages: set[str] = set()
    for _ in range(200):
        assert_safe_page(page, parse_publish_config(chapter.path.parents[1]))
        try:
            page.wait_for_selector("tr.arco-table-tr", timeout=15_000)
        except PlaywrightTimeoutError as exc:
            raise FanqieRetryable("章节管理表格加载超时") from exc
        titles = visible_matches(page.get_by_text(chapter_text, exact=True))
        if len(titles) == 1:
            row = titles[0].locator(
                "xpath=ancestor::tr[contains(concat(' ', normalize-space(@class), ' '), "
                "' arco-table-tr ')][1]"
            )
            if row.count() == 1:
                return row
        if len(titles) > 1:
            raise FanqieRetryable("章节管理页无法唯一定位目标章节")

        rows = page.locator("tr.arco-table-tr:visible")
        row_items = rows.all()
        row_texts = [item.inner_text() for item in row_items]
        number_pattern = re.compile(rf"第\s*{chapter.number}\s*章")
        title_prefix = chapter.title[: min(6, len(chapter.title))]
        fuzzy = [
            row
            for row, text in zip(row_items, row_texts)
            if number_pattern.search(text) and title_prefix in text
        ]
        if len(fuzzy) == 1:
            return fuzzy[0]
        if len(fuzzy) > 1:
            raise FanqieRetryable("章节管理页按章节号和标题前缀匹配到多行")

        signature = "\n".join(row_texts)
        if signature in visited_pages:
            break
        visited_pages.add(signature)

        next_buttons = visible_matches(
            page.locator(
                ".arco-pagination-item-next:not(.arco-pagination-item-disabled), "
                "li[aria-label='下一页']:not(.arco-pagination-item-disabled), "
                "button[aria-label='下一页']:not([disabled])"
            )
        )
        if len(next_buttons) != 1:
            break
        next_buttons[0].click()
        previous_signature = signature
        for _ in range(30):
            page.wait_for_timeout(250)
            current_rows = page.locator("tr.arco-table-tr:visible")
            current_signature = "\n".join(
                item.inner_text() for item in current_rows.all()
            )
            if current_signature and current_signature != previous_signature:
                break
    raise FanqieRetryable(f"章节管理分页中未找到“{chapter_text}”")


def open_chapter_editor(page: Page, config: PublishConfig, chapter: Chapter) -> None:
    try:
        page.goto(
            chapter_manage_url(config, chapter.path.parents[1]),
            wait_until="domcontentloaded",
            timeout=45_000,
        )
        page.wait_for_selector("tr.arco-table-tr", timeout=45_000)
    except PlaywrightTimeoutError as exc:
        raise FanqieRetryable("番茄章节管理页加载超时") from exc
    assert_safe_page(page, config)
    row = find_chapter_row(page, chapter)
    operation = row.locator("td").last
    edit_links = visible_matches(
        operation.locator(
            "a[href*='/publish/']:has(.icon-edit), "
            "a[href*='enter_from=modifychapter']"
        )
    )
    if len(edit_links) != 1:
        raise FanqieRetryable(
            "目标章节操作列中无法唯一定位编辑入口："
            f"实际找到 {len(edit_links)} 个"
        )
    before = page.url
    # 章节列表偶尔残留 byte-popconfirm 浮层并错误拦截指针。目标行和
    # 编辑链接已按章节身份及 href 唯一核验，直接触发该入口自身事件。
    # 不可再按 span 顺序取最后一个：当前操作列会同时包含外层容器、
    # 编辑图标和删除图标，最后一个 span 实际是删除入口。
    edit_links[0].evaluate("element => element.click()")
    try:
        page.wait_for_url(re.compile(r".*/publish/.*"), timeout=45_000)
        page.wait_for_selector(
            "input.serial-input, input[placeholder='请输入标题']", timeout=45_000
        )
    except PlaywrightTimeoutError as exc:
        raise FanqieRetryable("点击编辑后未进入章节编辑页") from exc
    if page.url == before:
        raise FanqieRetryable("章节编辑入口点击后 URL 未变化")
    assert_safe_page(page, config)


def verify_editor_identity(page: Page, chapter: Chapter) -> None:
    # 番茄编辑路由会先渲染空输入框，再异步回填已有章节。必须等回填完成
    # 后再核对身份；等待期间绝不点击“下一步”或修改任何字段。
    deadline = time.monotonic() + 20
    actual_number = ""
    actual_title = ""
    while time.monotonic() < deadline:
        serial = unique(
            page.locator("input.serial-input:not([placeholder='请输入标题'])"),
            "章节号输入框",
        )
        title = unique(
            page.get_by_placeholder("请输入标题", exact=True), "标题输入框"
        )
        actual_number = serial.input_value().strip()
        actual_title = title.input_value().strip()
        # 新版编辑页会先回填标题、稍后再回填章节号。只有两者都出现时
        # 才能进行身份核验，否则会把同一章节误报成“第章”。
        if actual_number and actual_title:
            break
        page.wait_for_timeout(500)
        assert_safe_page(page, parse_publish_config(chapter.path.parents[1]))
    if actual_number != str(chapter.number) or actual_title != chapter.title:
        raise FanqieBlocked(
            "编辑页章节身份不匹配："
            f"期望第{chapter.number}章《{chapter.title}》，"
            f"实际第{actual_number}章《{actual_title}》；URL={page.url}"
        )


def dismiss_scheduled_chapter_edit_notice(page: Page) -> None:
    """Close the user-approved notice shown when editing a submitted chapter."""
    notice = "请在发布时间前30分钟提交修改内容，否则无法完成修改"
    dialogs = visible_matches(page.locator("[role='dialog']:visible"))
    matching = [item for item in dialogs if notice in item.inner_text()]
    if not matching:
        return
    if len(matching) != 1:
        raise FanqieRetryable("已提交章节的修改提示弹窗不唯一")
    dialog = matching[0]
    buttons = [
        item
        for item in dialog.get_by_role("button", name="我知道了", exact=True).all()
        if item.is_visible() and item.is_enabled()
    ]
    if not buttons:
        buttons = [
            item
            for item in dialog.get_by_text("我知道了", exact=True).all()
            if item.is_visible()
        ]
    if len(buttons) != 1:
        raise FanqieRetryable("修改提示中无法唯一定位“我知道了”按钮")
    buttons[0].click()
    try:
        dialog.wait_for(state="hidden", timeout=10_000)
    except PlaywrightTimeoutError as exc:
        raise FanqieRetryable("点击“我知道了”后修改提示未关闭") from exc
    page.wait_for_timeout(300)


def verify_list(page: Page, chapter: Chapter) -> dict:
    try:
        page.wait_for_url(re.compile(r".*/chapter-manage/.*"), timeout=25_000)
    except PlaywrightTimeoutError as exc:
        dialogs = [
            item.inner_text().strip()[:500]
            for item in visible_matches(page.locator("[role='dialog']:visible"))
            if item.inner_text().strip()
        ]
        notices = [
            item.inner_text().strip()[:500]
            for item in visible_matches(
                page.locator(
                    ".arco-message, .arco-notification, .semi-toast, "
                    ".byte-toast, [role='alert']"
                )
            )
            if item.inner_text().strip()
        ]
        raise FanqieRetryable(
            "确认后未返回章节管理页："
            f"URL={page.url}；可见弹窗={dialogs}；提示消息={notices}"
        ) from exc
    assert_safe_page(page, parse_publish_config(chapter.path.parents[1]))
    row = find_chapter_row(page, chapter)
    text = row.inner_text()
    status = next((item for item in SUCCESS_STATUSES if item in text), None)
    if not status:
        raise FanqieRetryable("目标章节尚未显示待发布、审核中或已发布")
    return {"status": status, "row_text": text, "url": page.url}


def inspect_chapter(project: Path, chapter_path: Path) -> dict:
    """Read a chapter's current platform row without editing or submitting it."""
    config = parse_publish_config(project)
    chapter = parse_chapter(chapter_path)
    with sync_playwright() as playwright:
        context = launch_context(playwright)
        try:
            page = active_page(context)
            page.goto(
                chapter_manage_url(config, project),
                wait_until="domcontentloaded",
                timeout=45_000,
            )
            result = verify_list(page, chapter)
            return {"chapter": chapter.number, **result}
        finally:
            try:
                context.close()
            except PlaywrightError:
                pass


def complete_publish_settings(
    page: Page,
    config: PublishConfig,
    chapter: Chapter,
    publish_date: str,
    publish_time: str,
    debug_browser: bool,
) -> dict:
    assert_safe_page(page, config)
    next_button = visible_button(page, "下一步")
    if not next_button:
        raise FanqieRetryable("未找到视口内可用的“下一步”按钮")
    click_with_hidden_overlay_guard(page, next_button, "下一步")
    wait_through_publish_checks(page, config)
    assert_safe_page(page, config)
    choose_ai_yes(page)
    enable_timed_publish(page)
    if debug_browser:
        debug_checkpoint(
            "调试检查点 1/3：已选择“是否使用AI=是”，并打开“定时发布”。"
        )
    choose_date(page, publish_date)
    if debug_browser:
        debug_checkpoint(f"调试检查点 2/3：已选择发布日期 {publish_date}。")
    choose_time(page, publish_time)
    verify_publish_settings(page, publish_date, publish_time)
    if debug_browser:
        debug_checkpoint(
            f"调试检查点 3/3：已设置发布时间 {publish_time}；"
            "下一步将点击“确认发布”。"
        )
    assert_safe_page(page, config)
    confirm = publishing_button(page, "确认发布")
    if not confirm:
        raise FanqieRetryable("未找到确认发布按钮")
    submit_publish_confirmation(page, confirm)
    return verify_list(page, chapter)


def reschedule(
    project: Path,
    chapter_path: Path,
    publish_date: str,
    publish_time: str,
    debug_browser: bool = False,
) -> dict:
    """Move an existing scheduled chapter without rewriting its content."""
    config = parse_publish_config(project)
    if not config.submit_publish:
        raise FanqieBlocked("submit_publish=false，不能调整正式发布排期")
    chapter = parse_chapter(chapter_path)
    confirm_started = False
    with sync_playwright() as playwright:
        context = launch_context(playwright)
        try:
            page = active_page(context)
            open_chapter_editor(page, config, chapter)
            verify_editor_identity(page, chapter)
            dismiss_scheduled_chapter_edit_notice(page)
            confirm_started = True
            result = complete_publish_settings(
                page,
                config,
                chapter,
                publish_date,
                publish_time,
                debug_browser,
            )
            return {"chapter": chapter.number, **result}
        except Exception as exc:
            if isinstance(exc, FanqieRetryable) and not confirm_started:
                exc.safe_to_retry = True
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


def publish(
    project: Path,
    chapter_path: Path,
    publish_date: str,
    publish_time: str,
    debug_browser: bool = False,
) -> dict:
    config = parse_publish_config(project)
    chapter = parse_chapter(chapter_path)
    confirm_started = False
    author_note_started = False
    with sync_playwright() as playwright:
        context = launch_context(playwright)
        try:
            page = active_page(context)
            wait_editor(page, config)
            fill_editor(page, chapter)
            author_note_started = chapter.image_path is not None
            upload_author_note_image(page, config, chapter)
            assert_safe_page(page, config)
            next_button = visible_button(page, "下一步")
            if not next_button:
                raise FanqieRetryable("未找到视口内可用的“下一步”按钮")
            click_with_hidden_overlay_guard(page, next_button, "下一步")
            wait_through_publish_checks(page, config)
            assert_safe_page(page, config)
            if not config.submit_publish:
                if not click_if_visible(page, "存草稿"):
                    raise FanqieRetryable("未找到存草稿按钮")
                return {
                    "chapter": chapter.number,
                    "status": "draft_saved",
                    "url": page.url,
                    "author_note_image_uploaded": chapter.image_path is not None,
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
            verify_publish_settings(page, publish_date, publish_time)
            if debug_browser:
                debug_checkpoint(
                    f"调试检查点 3/3：已设置发布时间 {publish_time}；"
                    "下一步将点击“确认发布”。"
                )
            assert_safe_page(page, config)
            confirm = publishing_button(page, "确认发布")
            if not confirm:
                raise FanqieRetryable("未找到确认发布按钮")
            # From this point onward an automatic retry could create a duplicate.
            confirm_started = True
            submit_publish_confirmation(page, confirm)
            result = verify_list(page, chapter)
            return {
                "chapter": chapter.number,
                "author_note_image_uploaded": chapter.image_path is not None,
                **result,
            }
        except Exception as exc:
            if (
                isinstance(exc, FanqieRetryable)
                and not confirm_started
                and not author_note_started
            ):
                exc.safe_to_retry = True
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
