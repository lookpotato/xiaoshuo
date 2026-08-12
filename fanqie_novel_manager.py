#!/usr/bin/env python3
"""番茄小说管理器：批量创作、发布恢复与运行状态调度入口。"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import importlib.util
from importlib.metadata import PackageNotFoundError, version
import io
import json
import os
import re
import shutil
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from novel_image_system import validate_image_catalog
from novel_reader_gate import validate_reader_checks

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "manager_config.json"
RUNTIME = ROOT / ".manager_runtime.json"
LOCK = ROOT / ".manager.lock"
JOB_DIR = ROOT / ".manager_jobs"
MANAGER_NAME = "番茄小说管理器"
MANAGER_SCRIPT = "fanqie_novel_manager.py"
MIN_PLAYWRIGHT_VERSION = (1, 61, 0)
REQUIRED_FILES = {
    "novel_config.md", "outline.md", "characters.md", "world.md",
    "style_guide.md", "publish_config.md", "chapter_state.json",
}
REQUIRED_DIRS = {"chapters", "drafts", "logs", "reader_checks"}
DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
INCOMPLETE_UPLOAD_STATUSES = {
    "draft_saved", "not_uploaded", "upload_pending", "failed", "failed_retryable", "blocked_manual",
}
SUBMITTED_UPLOAD_STATUSES = {
    "submitted", "scheduled", "scheduled_review", "submitted_pending_review",
    "pending_review", "pending_publish", "published",
}
PUBLISH_ATTENTION_NOTES = [
    "submit_publish=true 时，番茄草稿箱不是终点。",
    "章节只有明确显示为待发布、审核中或已发布，才算完成。",
    "章节已存入番茄草稿但尚未确认定时发布时，记录 publish_pending。",
    "遇到 publish_pending 时，只从现有番茄草稿继续发布，不重写、不推进章节号。",
    "记录 success 前必须核对章节号、标题、日期时间、AI=是、定时发布开关和最终列表状态。",
]
FANQIE_FIXED_UPLOAD_STEPS = [
    "打开 publish_config.md 中的 fanqie_writer_url，并核对 book_id 与作品名。",
    "先判断目标章节是否已有番茄草稿；已有草稿必须沿用其编辑 URL，不得重新新建同章。",
    "填写章节号和标题；标题输入后重新读取页面值，确认没有漏填或焦点错位。",
    "定位正文编辑器，确认光标进入可编辑正文区域后再粘贴纯正文。",
    "粘贴后读取正文首段、末段和平台字数；三项都正确才允许继续。",
    "若本章有唯一配图，在页面底部“作者有话说”悬停“添加”，点击“添加图文”；先填写以“【本章辅助说明｜以下内容仅帮助理解配图，不属于小说正文】”开头、以“【辅助说明结束】”结尾的说明，再点击输入框左下角图片按钮。",
    "在“点击或拖拽文件到此上传”弹窗中提交本书 images/ 下的本章图片，等待完成后按 catalog 的 fanqie_crop_ratio 选择右侧比例，再点击“确定”。",
    "回查“作者有话说”区域出现图片预览或文件名；点击右侧“保存”并等待恢复为“编辑”状态，本地有图但平台未回显或未保存时不得点击“下一步”。",
    "点击“下一步”；若出现错别字未修改提示，确认目标作品无误后点击“提交”。",
    "内容检测方式固定选择“仅基础检测”或同义的“基础检测”，不选择“全面检测”。",
    "发布设置中“是否使用AI”固定选择“是”。",
    "打开“定时发布”开关；日期和时间使用可见选择器点击，禁止直接 fill 受控输入框。",
    "选择后回读日期输入值、时间输入值、AI 单选 checked 和定时开关 aria-checked。",
    "四项与计划完全一致时才点击一次“确认发布”，不得在未知结果下重复确认。",
    "确认后应返回章节管理页；按章节号和标题唯一定位该行，读取状态与计划时间。",
    "目标行显示待发布、审核中或已发布才算成功；审核中通过后会自动转为待发布。",
    "成功后立即更新 chapter_state.json、对应 batch_schedule 文件和当日日志，再进入下一章。",
]
FANQIE_SUCCESS_CHECKS = [
    "只看到草稿箱记录不算成功；记录 publish_pending 并下次继续。",
    "看到待发布、审核中、已发布、发布成功或已提交审核，才可更新为 success。",
    "正文为空、正文首尾不匹配、平台字数为 0、字数明显偏离本地章节时，不得点击下一步。",
    "章节本地有图时，“作者有话说”必须回显唯一图片及完整的非正文辅助说明边界；空白、缺开头结尾、重复图片或上传弹窗未确认都不得点击下一步。",
    "登录失效、验证码、风控、政策警告、陌生确认框、作品不匹配时立即停止并记录 blocked_manual。",
    "页面加载失败、控件暂不可用、网络超时时记录 failed_retryable。",
]
FANQIE_BROWSER_RELIABILITY_STEPS = [
    "浏览器控制日志中的 ab.chatgpt.com/Statsig 超时属于控制层遥测失败，不等同于番茄提交失败。",
    "每次浏览器调用只执行一个有副作用的动作；点击后另起一次只读检查，不把多个点击打包成长调用。",
    "单步控制调用至少预留 60 秒；若仍超时或连接重置，假定动作结果未知，禁止立即重放。",
    "动作结果未知时，复用同一浏览器并重新取得现有页面；先读取当前 URL、可见弹窗和章节列表，再决定恢复点。",
    "若确认发布后进入章节管理页，只以列表中的待发布、审核中或已发布为成功信号。",
    "日期和时间必须通过可见日历/时间选项点击；不得只对输入框 fill，因为受控输入值可能回退到平台默认值。",
    "选择日期或时间后，回读日期、时间、AI=是和定时开关；四项完全一致才允许点击确认发布。",
    "同一不确定动作最多进行一次只读恢复；未确认失败前不得再次点击，避免重复提交。",
    "重新打开草稿若回到正文编辑页，说明上次发布设置未最终提交；必须重新走发布检查流程。",
    "列表核对必须按章节号和标题唯一匹配目标行，并同时读取状态与发布时间，不能只看页面上存在某个“待发布”字样。",
]
FANQIE_BODY_INPUT_STEPS = [
    "优先定位真正的正文编辑器：contenteditable=true 或 ProseMirror 正文区域。",
    "不要点击“AI 开书灵感/生成大纲”等提示入口；只点击正文空白输入区。",
    "不要并行填章节号、标题和正文；同一页面输入必须串行完成，避免焦点被抢。",
    "正文只粘贴纯正文，不带章节 Markdown 标题、分隔线、Metadata、写作说明或自动化信息。",
    "Markdown 图片路径不粘进正文；每章唯一图片只从“作者有话说→添加→添加图文”的图片按钮上传。",
    "粘贴后等待平台字数刷新，再读取编辑器文本、首段、末段和平台显示字数。",
    "若编辑器文本为空、只出现提示词、首尾不匹配或字数不刷新，重新聚焦正文区再粘贴一次。",
    "重试后仍不正确，停止并记录 failed_retryable；不要点“下一步”，不要把空正文存草稿。",
]


def read_json(path: Path, default=None):
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(
        f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        tmp.unlink(missing_ok=True)


def job_path(job_id: str) -> Path:
    if not re.fullmatch(r"[0-9A-Za-z_-]{8,80}", job_id):
        raise ValueError(f"非法 job id: {job_id}")
    return JOB_DIR / f"{job_id}.json"


def read_job(job_id: str) -> dict:
    path = job_path(job_id)
    if not path.exists():
        raise ValueError(f"找不到 job: {job_id}")
    return read_json(path)


def build_batch_prompt(job: dict) -> str:
    job_file = job_path(job["id"])
    completed = len(job.get("completed_chapters", []))
    remaining = int(job["target_chapters"]) - completed
    return f"""使用 fanqie-auto-novel 技能执行番茄小说批次任务。

这是从命令行启动的独立任务，不得依赖任何旧聊天。唯一任务状态来源是项目文件、
`session` 输出和 job 文件 `{job_file}`。

目标：
- 书籍：{job["book_id"]}
- 本 job 总目标：{job["target_chapters"]} 个；已完成：{completed} 个；本次剩余：{remaining} 个
- 严格串行完成剩余章节槽位，不得重复处理 job 中已完成章节。
- 先恢复 pending/session 中章节号最小的既有番茄草稿；既有草稿算一个槽位。
- 没有待发布草稿后，才从 next_chapter_number 写新章。
- 每个槽位必须完整执行：读取连续性 → 写作或恢复 → 质检 → 归档 → 上传 →
  设置排期 → 章节管理页权威核验 → 本地状态与日志回写 → Git 提交并推送。
- 当前槽位未在番茄显示待发布、审核中或已发布时，不得进入下一槽位。

启动顺序：
1. 完整读取 `AGENTS.md`、fanqie-auto-novel 技能和技能要求的引用文件。
2. 运行 `python .\\fanqie_novel_manager.py validate`。
3. 运行 `python .\\fanqie_novel_manager.py session --book {job["book_id"]}`。
4. 运行 `python .\\fanqie_novel_manager.py pending --book {job["book_id"]}`。
5. 桌面工作器通过 `job-next` 领取了本 job 和全局锁；不得再次 claim。
   通过 session 确认 runtime_status=claimed。

每个章节槽位取得番茄权威成功状态并完成本地回写后，运行：
`python .\\fanqie_novel_manager.py job-progress --job {job["id"]} --chapter <章节号> --platform-status <pending_publish|pending_review|published> --message "<标题、排期和核验摘要>"`

结束要求：
- job 累计达到 {job["target_chapters"]} 个成功槽位后，运行管理器 finish batch_success，再运行：
  `python .\\fanqie_novel_manager.py job-finish --job {job["id"]} --result success --message "完成摘要"`
- 临时失败或仍在草稿/发布设置时，用 publish_pending 或 failed_retryable 释放管理器锁，
  再以 partial/failed 结束 job。
- 登录失效、验证码、二维码、风控、政策警告或陌生确认框时，立即停止；用
  blocked_manual 释放管理器锁，再以 blocked 结束 job。
- 无论任何结果，都必须释放管理器锁并调用一次 job-finish。
- `submit_publish: true` 时，番茄草稿箱不算成功。
- 使用已经登录的浏览器会话；不得读取或保存 Cookie、Token、密码、验证码。
- 严格执行 session 输出的 writing_policy：新道具先直说用途，同章尽快触发；跨章再次使用先短句回顾，悬念只留来源、上限或隐藏代价。
- 严格执行图片工作流：调用 imagegen 技能为本章最需要视觉解释的新人物、道具、地点、异兽或组织形象建立图片；每章最多 1 张，生成后必须回看核验，未核验图片不得入库或推进章节状态。
- 草稿完成后严格执行无大纲读者反向验收：停止读取大纲和设定，只凭正文回答六个因果问题并引用正文证据；任何问题需要作者补充说明时先修文，验收文件缺失或未通过不得归档、上传或推进章节状态。
- 浏览器不可用时安全停止并记录 blocked，不得改用其他书号或伪造成功。
- 只提交本批任务涉及文件，保留无关改动；每批改动按 AGENTS.md 自动推送。
"""


def create_job(data, book_id: str, count: int) -> dict:
    now = now_for(data)
    job_id = now.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    job = {
        "schema_version": 1,
        "id": job_id,
        "book_id": book_id,
        "target_chapters": count,
        "completed_chapters": [],
        "status": "queued",
        "result": None,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "codex_exit_code": None,
        "events": [],
    }
    write_json(job_path(job_id), job)
    prompt_path = JOB_DIR / f"{job_id}.prompt.md"
    prompt_path.write_text(build_batch_prompt(job), encoding="utf-8")
    return job


def publish_flag(text: str, key: str) -> bool:
    match = re.search(rf"^\s*{re.escape(key)}\s*:\s*(true|false)\s*$", text, re.I | re.M)
    return bool(match and match.group(1).lower() == "true")


def publish_requires_submission(project: Path) -> bool:
    pub = project / "publish_config.md"
    if not pub.exists():
        return False
    return publish_flag(pub.read_text(encoding="utf-8"), "submit_publish")


def upload_is_publish_complete(state: dict) -> bool:
    status = str(state.get("last_uploaded_status", "")).strip()
    completed = int(state.get("last_completed_chapter", 0) or 0)
    uploaded = int(state.get("last_uploaded_chapter", 0) or 0)
    return uploaded >= completed and status in SUBMITTED_UPLOAD_STATUSES


def batch_schedule_files(project: Path):
    return sorted(project.glob("batch_schedule_*.json"))


def batch_publish_entries(project: Path):
    entries = []
    for schedule_path in batch_schedule_files(project):
        schedule = read_json(schedule_path, {})
        for entry in schedule.get("entries", []):
            if not isinstance(entry, dict) or "chapter" not in entry:
                continue
            entries.append({
                **entry,
                "schedule_file": str(schedule_path.relative_to(ROOT)),
            })
    entries.sort(key=lambda item: (int(item.get("chapter", 0)), item["schedule_file"]))
    return entries


def pending_publish_entries(project: Path):
    return [
        entry for entry in batch_publish_entries(project)
        if str(entry.get("status", "")).strip() not in SUBMITTED_UPLOAD_STATUSES
    ]


def config():
    data = read_json(CONFIG)
    if not isinstance(data.get("books"), list):
        raise ValueError("manager_config.json 缺少 books 数组")
    return data


def now_for(data):
    zone_name = data.get("timezone", "Asia/Shanghai")
    try:
        zone = ZoneInfo(zone_name)
    except Exception:
        if zone_name != "Asia/Shanghai":
            raise
        # Some minimal Windows Python distributions omit the IANA tzdata package.
        zone = timezone(timedelta(hours=8), name="Asia/Shanghai")
    return datetime.now(zone)


def find_book(data, book_id):
    for book in data["books"]:
        if book.get("id") == book_id:
            return book
    raise ValueError(f"未知书籍 id: {book_id}")


def project_path(book):
    path = (ROOT / book["path"]).resolve()
    if ROOT not in path.parents:
        raise ValueError(f"书籍路径越界: {book['path']}")
    return path


def validate_book(book, require_publish_complete=True):
    path = project_path(book)
    errors = []
    if not path.is_dir():
        return [f"目录不存在: {path}"]
    for name in sorted(REQUIRED_FILES):
        if not (path / name).is_file():
            errors.append(f"缺少文件 {name}")
    for name in sorted(REQUIRED_DIRS):
        if not (path / name).is_dir():
            errors.append(f"缺少目录 {name}/")
    errors.extend(validate_image_catalog(path, str(book.get("id", ""))))
    reader_gate_from = book.get("reader_gate_from_chapter")
    if not isinstance(reader_gate_from, int) or reader_gate_from < 1:
        errors.append("书籍配置缺少正整数 reader_gate_from_chapter")
    else:
        errors.extend(validate_reader_checks(path, reader_gate_from))
    state_path = path / "chapter_state.json"
    if state_path.exists():
        try:
            state = read_json(state_path)
            for key in ("last_completed_chapter", "next_chapter_number", "last_uploaded_status"):
                if key not in state:
                    errors.append(f"chapter_state.json 缺少 {key}")
            if (
                isinstance(reader_gate_from, int)
                and isinstance(state.get("next_chapter_number"), int)
                and reader_gate_from > state["next_chapter_number"]
            ):
                errors.append(
                    "reader_gate_from_chapter 不能晚于下一章，避免新章节绕过无大纲读者验收"
                )
            if state.get("next_chapter_number", 0) < state.get("last_completed_chapter", 0) + 1:
                errors.append("next_chapter_number 早于已归档章节")
            chapter_numbers = []
            for chapter in (path / "chapters").glob("*.md") if (path / "chapters").exists() else []:
                match = re.match(r"^(\d+)-", chapter.name)
                if match:
                    chapter_numbers.append(int(match.group(1)))
            if chapter_numbers and max(chapter_numbers) != state.get("last_completed_chapter"):
                errors.append(
                    f"归档最高章节为 {max(chapter_numbers)}，但 last_completed_chapter="
                    f"{state.get('last_completed_chapter')}"
                )
        except Exception as exc:
            errors.append(f"chapter_state.json 无法解析: {exc}")
    pub = path / "publish_config.md"
    if pub.exists():
        text = pub.read_text(encoding="utf-8")
        if "submit_publish:" not in text:
            errors.append("publish_config.md 缺少 submit_publish")
        elif (
            require_publish_complete
            and publish_flag(text, "submit_publish")
            and state_path.exists()
        ):
            status = str(state.get("last_uploaded_status", "")).strip()
            if status in INCOMPLETE_UPLOAD_STATUSES:
                errors.append(
                    "submit_publish=true 时，番茄草稿只算中间态；"
                    f"当前 last_uploaded_status={status}，必须推进到待发布/审核中后才算完成"
                )
    return errors


def scheduled_today(book, now):
    schedule = book.get("schedule", {})
    if DAY_KEYS[now.weekday()] not in schedule.get("days", []):
        return None
    try:
        hour, minute = map(int, schedule["time"].split(":"))
    except Exception:
        raise ValueError(f"{book['id']} 的 schedule.time 无效")
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def is_due(book, now, runtime):
    if not book.get("enabled", False):
        return False, "disabled"
    target = scheduled_today(book, now)
    if target is None or now < target:
        return False, "not_time"
    status = runtime.get("books", {}).get(book["id"], {})
    last_success = status.get("last_success_at")
    if last_success and datetime.fromisoformat(last_success).date() == now.date():
        return False, "already_completed"
    state = status.get("run_status")
    last_claimed = status.get("last_claimed_at")
    if last_claimed and datetime.fromisoformat(last_claimed).date() == now.date():
        if state in {"claimed", "chapter_archived"}:
            return False, state
        if state == "blocked_manual":
            return False, "blocked_manual"
        if state in {"failed_retryable", "upload_pending", "publish_pending"}:
            attempts = status.get("attempt_count", 0)
            max_attempts = runtime.get("max_daily_attempts", 3)
            if attempts >= max_attempts:
                return False, "retry_limit"
            retry_after = status.get("retry_after")
            if retry_after and now < datetime.fromisoformat(retry_after):
                return False, "retry_wait"
    return True, "due"


def cmd_list(data, _args):
    runtime = read_json(RUNTIME, {"books": {}})
    runtime["max_daily_attempts"] = data.get("max_daily_attempts", 3)
    now = now_for(data)
    for book in sorted(data["books"], key=lambda x: -x.get("priority", 0)):
        due, reason = is_due(book, now, runtime)
        print(f"{book['id']:<16} {'ON ' if book.get('enabled') else 'OFF'} {book['schedule']['time']} "
              f"{'DUE' if due else reason:<15} {book['title']} -> {book['path']}")


def cmd_validate(data, _args):
    failed = False
    ids = set()
    paths = set()
    for book in data["books"]:
        errors = []
        if book.get("id") in ids:
            errors.append("重复书籍 id")
        ids.add(book.get("id"))
        if book.get("path") in paths:
            errors.append("重复书籍 path")
        paths.add(book.get("path"))
        errors.extend(validate_book(book, require_publish_complete=False))
        if errors:
            failed = True
            print(f"[FAIL] {book.get('id')}: " + "；".join(errors))
        else:
            print(f"[OK]   {book.get('id')}: {project_path(book)}")
            project = project_path(book)
            state = read_json(project / "chapter_state.json", {})
            if publish_requires_submission(project) and not upload_is_publish_complete(state):
                print(
                    f"[TODO] {book.get('id')}: 发布状态尚未完成，"
                    "可领取任务并从 pending/session 指示的草稿继续"
                )
    return 1 if failed else 0


def cmd_notes(data, args):
    books = data["books"]
    if args.book:
        books = [find_book(data, args.book)]
    runtime = read_json(RUNTIME, {"books": {}})
    for book in sorted(books, key=lambda x: -x.get("priority", 0)):
        path = project_path(book)
        state = read_json(path / "chapter_state.json", {})
        publish_required = publish_requires_submission(path)
        print(f"[{book['id']}] {book.get('title')} -> {path}")
        print(f"submit_publish: {'true' if publish_required else 'false'}")
        print(
            "state: "
            f"completed={state.get('last_completed_chapter', 'unknown')}, "
            f"uploaded={state.get('last_uploaded_chapter', 'unknown')}, "
            f"upload_status={state.get('last_uploaded_status', 'unknown')}"
        )
        run_status = runtime.get("books", {}).get(book["id"], {}).get("run_status")
        if run_status:
            print(f"runtime_status: {run_status}")
        print("attention:")
        for note in PUBLISH_ATTENTION_NOTES:
            print(f"- {note}")
        print("fixed_upload_steps:")
        for index, step in enumerate(FANQIE_FIXED_UPLOAD_STEPS, 1):
            print(f"{index}. {step}")
        print("body_input_steps:")
        for index, step in enumerate(FANQIE_BODY_INPUT_STEPS, 1):
            print(f"{index}. {step}")
        print("success_checks:")
        for check in FANQIE_SUCCESS_CHECKS:
            print(f"- {check}")
        print("browser_reliability_steps:")
        for index, step in enumerate(FANQIE_BROWSER_RELIABILITY_STEPS, 1):
            print(f"{index}. {step}")
        if publish_required and not upload_is_publish_complete(state):
            print("current_action: 先继续完成番茄确认发布，再报告 success。")
        pending = pending_publish_entries(path)
        if pending:
            print("pending_batch_chapters:")
            for entry in pending:
                print(
                    f"- 第{entry.get('chapter')}章 {entry.get('date')} "
                    f"{entry.get('time')} status={entry.get('status')} "
                    f"source={entry.get('schedule_file')}"
                )
        print()


def cmd_pending(data, args):
    books = data["books"]
    if args.book:
        books = [find_book(data, args.book)]
    payload = []
    for book in books:
        project = project_path(book)
        payload.append({
            "book_id": book["id"],
            "title": book.get("title"),
            "project_path": str(project),
            "pending_chapters": pending_publish_entries(project),
        })
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_session(data, args):
    book = find_book(data, args.book)
    project = project_path(book)
    state = read_json(project / "chapter_state.json", {})
    runtime = read_json(RUNTIME, {"books": {}})
    payload = {
        "manager": {
            "name": MANAGER_NAME,
            "script": MANAGER_SCRIPT,
            "schema_version": 1,
            "purpose": "让新 Codex 会话从本地项目状态继续批量创作、归档并发布到番茄。",
        },
        "book": {
            "id": book["id"],
            "title": book.get("title"),
            "project_path": str(project),
            "mode": book.get("mode"),
            "daily_chapter_target": book.get("daily_chapter_target", 1),
            "default_publish_times": book.get("default_publish_times", []),
            "submit_publish": publish_requires_submission(project),
            "reader_gate_from_chapter": book.get("reader_gate_from_chapter"),
        },
        "state": state,
        "runtime": runtime.get("books", {}).get(book["id"], {}),
        "writing_policy": data.get("writing_policy", {}),
        "pending_batch_chapters": pending_publish_entries(project),
        "required_read_order": [
            str(project / "automation_prompt.md"),
            str(ROOT / "shared" / "writing_playbook.md"),
            str(ROOT / "shared" / "quality_scorecard.md"),
            str(ROOT / "shared" / "image_workflow.md"),
            str(ROOT / "shared" / "reader_gate.md"),
            str(ROOT / "shared" / "learning_log.md"),
            str(project / "novel_config.md"),
            str(project / "outline.md"),
            str(project / "characters.md"),
            str(project / "world.md"),
            str(project / "style_guide.md"),
            str(project / "publish_config.md"),
            str(project / "chapter_state.json"),
            str(project / "continuity_ledger.md"),
            str(project / "fanqie_ui_workflow.md"),
            str(project / "images" / "catalog.json"),
            str(project / "reader_checks"),
        ],
        "start_commands": {
            "inspect": [
                f"python .\\{MANAGER_SCRIPT} validate",
                f"python .\\{MANAGER_SCRIPT} session --book {book['id']}",
                f"python .\\{MANAGER_SCRIPT} pending --book {book['id']}",
            ],
            "scheduled_run": f"python .\\{MANAGER_SCRIPT} next",
            "explicit_manual_run": (
                f"python .\\{MANAGER_SCRIPT} claim --book {book['id']}"
            ),
        },
        "batch_workflow": [
            "新道具首次出现时先直说用途并尽快触发效果；跨章再用时先做一句情境化回顾，悬念只留来源、上限或代价。",
            "写作前读取本书 images/catalog.json；同名同设定实体沿用既有图片，不重复生成。",
            "每章只为最需要视觉解释的一个新人物、道具、地点、异兽或组织形象配图；同章其他新实体必须用正文白话解释，不得假装已有图片。",
            "先按实体选定画幅：人物默认2:3，道具或徽记1:1，宽场景或地点16:9，横向异兽或动作画面3:2，仅明确超长竖构图使用9:16；生成画幅与 catalog 的番茄裁剪比例必须一致。",
            "使用内置 imagegen 逐张生成，复制最终图到本书 images/ 分类目录，再用 view_image 回看并逐项核验（含裁剪安全区）；任何关键项不符就定向重生，未通过不得登记 verified。",
            "首次启用图片体系时，若本章没有更重要的新实体，可用唯一图片名额补齐尚无参考图的主角；图片、catalog 和章节归档必须作为同一批原子改动完成。",
            "大纲关键句只能作为内部计划；正文必须补齐承接、问题、依据、判断、行动和结果，不得把作者知道的前因后果当成读者常识。",
            "完稿后停止读取大纲与设定，只读最终正文完成六题反向验收；每题引用正文原句，清零 unexplained_terms，并把正文哈希写入 reader_checks/NNNN.json。",
            "reader gate 未通过时只保留草稿，不得归档、更新 chapter_state、上传或伪造 passed。",
            "先处理 pending_batch_chapters：从既有番茄草稿继续，不重写、不重复创建章节。",
            "没有待发布草稿时，按 next_chapter_number 严格串行执行“写一章→质检→归档→上传→确认列表状态”。",
            "每章归档后更新 chapter_state.json 与 continuity_ledger.md，再记录 chapter_archived。",
            "每章上传前核对作品名与 book_id，正文输入后核对首段、末段、平台字数。",
            "发布固定走“下一步→错别字提示提交→仅基础检测→AI=是→定时发布→确认发布”。",
            "每次浏览器调用只做一个有副作用动作，之后单独只读回查；超时后不得盲目重放。",
            "每章只有在章节列表显示待发布、审核中或已发布后，才更新状态并进入下一章。",
            "达到批量目标后写日志；全部章节成功才 finish success，否则记录可恢复或人工阻塞状态。",
            "无论成功、临时失败还是人工阻塞，最后都必须执行一次 finish 释放运行锁。",
        ],
        "fixed_upload_steps": FANQIE_FIXED_UPLOAD_STEPS,
        "body_input_steps": FANQIE_BODY_INPUT_STEPS,
        "browser_reliability_steps": FANQIE_BROWSER_RELIABILITY_STEPS,
        "success_checks": FANQIE_SUCCESS_CHECKS,
        "finish_commands": {
            "success": f"python .\\{MANAGER_SCRIPT} finish --book {book['id']} --result success",
            "publish_pending": (
                f"python .\\{MANAGER_SCRIPT} finish --book {book['id']} "
                '--result publish_pending --message "已存草稿，等待继续确认发布"'
            ),
            "failed_retryable": (
                f"python .\\{MANAGER_SCRIPT} finish --book {book['id']} "
                '--result failed_retryable --message "具体临时失败原因"'
            ),
            "blocked_manual": (
                f"python .\\{MANAGER_SCRIPT} finish --book {book['id']} "
                '--result blocked_manual --message "具体人工处理原因"'
            ),
        },
        "safety": [
            "不得保存或提交密码、Cookie、Token、验证码、私钥。",
            "验证码、登录失效、风控、政策警告、陌生确认框必须停止。",
            "不得把草稿箱记录当成发布成功，不得重复点击结果未知的确认发布。",
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_due(data, _args):
    runtime = read_json(RUNTIME, {"books": {}})
    runtime["max_daily_attempts"] = data.get("max_daily_attempts", 3)
    now = now_for(data)
    due_books = []
    for book in data["books"]:
        due, _ = is_due(book, now, runtime)
        if due:
            due_books.append(book)
    due_books.sort(key=lambda x: -x.get("priority", 0))
    print(json.dumps(due_books, ensure_ascii=False, indent=2))


def live_lock(data, now):
    if not LOCK.exists():
        return None
    lock = read_json(LOCK)
    claimed = datetime.fromisoformat(lock["claimed_at"])
    pid = int(lock.get("pid", 0) or 0)
    if lock.get("owner_mode") == "on_demand_process":
        if not process_is_running(pid):
            LOCK.unlink(missing_ok=True)
            return None
    elif not process_is_running(pid) and _queued_job_matches_lock(lock):
        # The on-demand launcher first claims the lock, then marks both the
        # lock and job as running. If it dies between those writes, a queued
        # job plus its dead claiming PID is an abandoned half-claim, not an
        # active desktop worker.
        LOCK.unlink(missing_ok=True)
        return None
    if now - claimed > timedelta(minutes=data.get("global_lock_minutes", 180)):
        LOCK.unlink(missing_ok=True)
        return None
    return lock


def _queued_job_matches_lock(lock: dict) -> bool:
    if not JOB_DIR.exists():
        return False
    claimed_at = lock.get("claimed_at")
    book_id = lock.get("book_id")
    if not isinstance(claimed_at, str) or not isinstance(book_id, str):
        return False
    for path in JOB_DIR.glob("*.json"):
        job = read_json(path, {})
        if job.get("status") != "queued" or job.get("book_id") != book_id:
            continue
        created_at = job.get("created_at")
        if isinstance(created_at, str) and created_at <= claimed_at:
            return True
    return False


def process_is_running(pid):
    if pid <= 0:
        return False
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def cmd_claim(data, args):
    now = now_for(data)
    lock = live_lock(data, now)
    if lock:
        print(f"已有任务运行: {lock['book_id']}，领取于 {lock['claimed_at']}", file=sys.stderr)
        return 2
    book = find_book(data, args.book)
    errors = validate_book(book, require_publish_complete=False)
    if errors:
        print("项目校验失败：" + "；".join(errors), file=sys.stderr)
        return 1
    if not book.get("enabled") and not args.force:
        print("该书未启用；如确需手动运行可加 --force", file=sys.stderr)
        return 2
    payload = {"book_id": book["id"], "claimed_at": now.isoformat(), "pid": os.getpid()}
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except FileExistsError:
        print("任务刚被另一个进程领取", file=sys.stderr)
        return 2
    runtime = read_json(RUNTIME, {"books": {}})
    status = runtime.setdefault("books", {}).setdefault(book["id"], {})
    previous_claim = status.get("last_claimed_at")
    if previous_claim and datetime.fromisoformat(previous_claim).date() == now.date():
        status["attempt_count"] = status.get("attempt_count", 0) + 1
    else:
        status["attempt_count"] = 1
    status.update({
        "last_claimed_at": now.isoformat(),
        "run_status": "claimed",
        "retry_after": None,
        "message": "",
    })
    write_json(RUNTIME, runtime)
    print(json.dumps({**payload, "project_path": str(project_path(book)), "mode": book.get("mode")}, ensure_ascii=False))


def cmd_next(data, _args):
    runtime = read_json(RUNTIME, {"books": {}})
    runtime["max_daily_attempts"] = data.get("max_daily_attempts", 3)
    now = now_for(data)
    due_books = [book for book in data["books"] if is_due(book, now, runtime)[0]]
    due_books.sort(key=lambda item: -item.get("priority", 0))
    if not due_books:
        print("{}")
        return 0
    args = argparse.Namespace(book=due_books[0]["id"], force=False)
    return cmd_claim(data, args)


def cmd_progress(data, args):
    now = now_for(data)
    lock = read_json(LOCK, None)
    if not lock or lock.get("book_id") != args.book:
        print("没有与该书匹配的运行锁", file=sys.stderr)
        return 2
    runtime = read_json(RUNTIME, {"books": {}})
    status = runtime.setdefault("books", {}).setdefault(args.book, {})
    status.update({
        "run_status": args.phase,
        "last_progress_at": now.isoformat(),
        "message": args.message,
    })
    write_json(RUNTIME, runtime)
    print(f"已记录 {args.book}: {args.phase}")


def cmd_finish(data, args):
    now = now_for(data)
    lock = read_json(LOCK, None)
    if not lock or lock.get("book_id") != args.book:
        print("没有与该书匹配的运行锁", file=sys.stderr)
        return 2
    book = find_book(data, args.book)
    project = project_path(book)
    if args.result == "success" and publish_requires_submission(project):
        state = read_json(project / "chapter_state.json", {})
        if not upload_is_publish_complete(state):
            status = state.get("last_uploaded_status", "unknown")
            completed = state.get("last_completed_chapter", "unknown")
            uploaded = state.get("last_uploaded_chapter", "unknown")
            print(
                "submit_publish=true 时不能把草稿箱状态记为 success；"
                f"last_completed_chapter={completed}, last_uploaded_chapter={uploaded}, "
                f"last_uploaded_status={status}。请先提交到待发布/审核中，"
                "或使用 upload_pending/publish_pending 保留重试。",
                file=sys.stderr,
            )
            return 1
    runtime = read_json(RUNTIME, {"books": {}})
    status = runtime.setdefault("books", {}).setdefault(args.book, {})
    result_aliases = {"failed": "failed_retryable", "blocked": "blocked_manual"}
    result = result_aliases.get(args.result, args.result)
    status.update({
        "last_finished_at": now.isoformat(),
        "last_result": result,
        "run_status": result,
        "message": args.message,
    })
    if result in {"success", "batch_success"}:
        status["last_success_at"] = now.isoformat()
        status["retry_after"] = None
    elif result in {"failed_retryable", "upload_pending", "publish_pending"}:
        delay = data.get("retry_delay_minutes", 30)
        status["retry_after"] = (now + timedelta(minutes=delay)).isoformat()
    else:
        status["retry_after"] = None
    write_json(RUNTIME, runtime)
    LOCK.unlink(missing_ok=True)
    print(f"已记录 {args.book}: {result}")


def cmd_job_progress(data, args):
    now = now_for(data)
    job = read_job(args.job)
    completed = job.setdefault("completed_chapters", [])
    existing = next((item for item in completed if item["chapter"] == args.chapter), None)
    event = {
        "at": now.isoformat(),
        "chapter": args.chapter,
        "platform_status": args.platform_status,
        "message": args.message,
    }
    if existing:
        existing.update(event)
    else:
        if len(completed) >= int(job["target_chapters"]):
            print("job 已达到目标章节数，拒绝继续推进", file=sys.stderr)
            return 2
        completed.append(event)
        completed.sort(key=lambda item: item["chapter"])
    job["status"] = "running"
    job["updated_at"] = now.isoformat()
    job.setdefault("events", []).append({"type": "chapter_completed", **event})
    write_json(job_path(args.job), job)
    print(json.dumps({
        "job": args.job,
        "completed": len(completed),
        "target": job["target_chapters"],
    }, ensure_ascii=False))


def cmd_job_finish(data, args):
    now = now_for(data)
    job = read_job(args.job)
    completed = len(job.get("completed_chapters", []))
    target = int(job["target_chapters"])
    if args.result == "success" and completed != target:
        print(f"不能标记 success：已完成 {completed}/{target}", file=sys.stderr)
        return 2
    job.update({
        "status": "finished",
        "result": args.result,
        "message": args.message,
        "updated_at": now.isoformat(),
        "finished_at": now.isoformat(),
    })
    job.setdefault("events", []).append({
        "type": "job_finished",
        "at": now.isoformat(),
        "result": args.result,
        "message": args.message,
    })
    write_json(job_path(args.job), job)
    print(json.dumps(job, ensure_ascii=False, indent=2))


def cmd_job_status(_data, args):
    if args.job:
        print(json.dumps(read_job(args.job), ensure_ascii=False, indent=2))
        return
    if not JOB_DIR.exists():
        print("[]")
        return
    jobs = [read_json(path) for path in sorted(JOB_DIR.glob("*.json"), reverse=True)]
    print(json.dumps(jobs, ensure_ascii=False, indent=2))


def cmd_doctor(data, args):
    book = find_book(data, args.book)
    project = project_path(book)
    write_only = book.get("mode") == "write_only"
    profile_dir = (
        Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        / "xiaoshuo"
        / "fanqie-chrome-profile-v2"
    )
    profile_ready = profile_dir.parent / "fanqie-chrome-profile-v2.ready.json"
    chrome_candidates = [
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Google/Chrome/Application/chrome.exe",
    ]
    try:
        installed_playwright = version("playwright")
    except PackageNotFoundError:
        installed_playwright = "0"
    installed_numbers = tuple(
        int(item) for item in re.findall(r"\d+", installed_playwright)[:3]
    )
    checks = {
        "project_valid": not validate_book(book, require_publish_complete=False),
        "codex_cli": bool(shutil.which("codex")),
        "playwright": (
            importlib.util.find_spec("playwright") is not None
            and installed_numbers >= MIN_PLAYWRIGHT_VERSION
        ),
        "google_chrome": any(path.is_file() for path in chrome_candidates),
        "browser_profile_initialized": profile_ready.is_file(),
        "publish_url_bound": False,
        "submit_publish": publish_requires_submission(project),
    }
    publish_text = (project / "publish_config.md").read_text(encoding="utf-8")
    url_match = re.search(r"^\s*fanqie_writer_url\s*:\s*(\S+)\s*$", publish_text, re.M)
    book_match = re.search(r"^\s*book_id\s*:\s*(\S+)\s*$", publish_text, re.M)
    checks["publish_url_bound"] = bool(
        url_match and book_match
        and url_match.group(1).upper() != "UNBOUND"
        and book_match.group(1).upper() != "UNBOUND"
    )
    required_checks = (
        ("project_valid", "codex_cli")
        if write_only
        else tuple(checks)
    )
    ready = all(checks[name] for name in required_checks)
    if ready:
        note = (
            "本地创作环境预检通过；不会访问番茄。"
            if write_only
            else "环境预检通过；该命令不模拟番茄页面点击。"
            "python xiaoshuo N 会按需运行，完成后退出。"
        )
    else:
        note = (
            "本地创作预检未通过；请检查项目结构与 Codex 登录。"
            if write_only
            else "预检未通过；首次使用请运行 python xiaoshuo --setup-browser。"
        )
    print(json.dumps({
        "ready": ready,
        "book": args.book,
        "mode": "write_only" if write_only else "write_then_upload",
        "background_polling": False,
        "browser_profile": str(profile_dir),
        "playwright_version": installed_playwright,
        "minimum_playwright_version": ".".join(
            str(item) for item in MIN_PLAYWRIGHT_VERSION
        ),
        "checks": checks,
        "required_checks": required_checks,
        "manager_busy": live_lock(data, now_for(data)) is not None,
        "check_scope": "environment_only",
        "note": note,
    }, ensure_ascii=False, indent=2))
    return 0 if ready else 2


def cmd_job_next(data, _args):
    now = now_for(data)
    lock = live_lock(data, now)
    if lock:
        print(json.dumps({
            "status": "busy",
            "book_id": lock.get("book_id"),
            "claimed_at": lock.get("claimed_at"),
        }, ensure_ascii=False))
        return
    if not JOB_DIR.exists():
        print("{}")
        return
    queued = []
    for path in JOB_DIR.glob("*.json"):
        job = read_json(path, {})
        if job.get("status") == "queued":
            queued.append((job.get("created_at", ""), path, job))
    if not queued:
        print("{}")
        return
    _, path, job = sorted(queued, key=lambda item: (item[0], item[1].name))[0]
    capture = io.StringIO()
    with contextlib.redirect_stdout(capture):
        result = cmd_claim(
            data,
            argparse.Namespace(book=job["book_id"], force=False),
        )
    if result:
        print(capture.getvalue(), end="", file=sys.stderr)
        return result
    job.update({
        "status": "running",
        "result": None,
        "message": "",
        "worker_claimed_at": now.isoformat(),
        "updated_at": now.isoformat(),
    })
    job.setdefault("events", []).append({
        "type": "worker_claimed",
        "at": now.isoformat(),
    })
    write_json(path, job)
    print(json.dumps({
        "status": "claimed",
        "job": job,
        "remaining_chapters": (
            int(job["target_chapters"]) - len(job.get("completed_chapters", []))
        ),
        "project_path": str(project_path(find_book(data, job["book_id"]))),
    }, ensure_ascii=False, indent=2))


def cmd_run(data, args):
    resume_job = None
    if args.resume:
        resume_job = read_job(args.resume)
        args.book = resume_job["book_id"]
        args.count = int(resume_job["target_chapters"])
        if resume_job.get("status") == "finished" and resume_job.get("result") == "success":
            print("该 job 已成功完成，无需续跑。", file=sys.stderr)
            return 2
        remaining = args.count - len(resume_job.get("completed_chapters", []))
        if remaining < 1:
            print("该 job 没有剩余章节槽位；请检查 job 状态。", file=sys.stderr)
            return 2
    elif args.count is None or args.count < 1 or args.count > 50:
        print("章节数必须在 1 到 50 之间", file=sys.stderr)
        return 2
    book = find_book(data, args.book)
    errors = validate_book(book, require_publish_complete=False)
    if errors:
        print("项目校验失败：" + "；".join(errors), file=sys.stderr)
        return 1
    preview = resume_job or {
        "schema_version": 1,
        "id": "DRY-RUN-JOB",
        "book_id": args.book,
        "target_chapters": args.count,
        "completed_chapters": [],
    }
    if args.dry_run:
        print(json.dumps({
            "action": "enqueue",
            "prompt": build_batch_prompt(preview),
            "note": "dry-run 未创建 job；正式运行会交给 Codex 桌面工作器。",
        }, ensure_ascii=False, indent=2))
        return
    if resume_job:
        job = resume_job
        job.update({
            "status": "queued",
            "result": None,
            "message": "",
            "updated_at": now_for(data).isoformat(),
        })
        job.setdefault("events", []).append({
            "type": "job_requeued",
            "at": now_for(data).isoformat(),
        })
        write_json(job_path(job["id"]), job)
    else:
        job = create_job(data, args.book, args.count)
    print(json.dumps({
        "job": job["id"],
        "status": "queued",
        "book": job["book_id"],
        "completed": len(job.get("completed_chapters", [])),
        "target": job["target_chapters"],
        "message": "已进入 Codex 桌面工作队列，通常会在 5 分钟内领取。",
    }, ensure_ascii=False, indent=2))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description=f"{MANAGER_NAME}：多小说批量创作与番茄发布调度器"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    sub.add_parser("validate")
    notes = sub.add_parser("notes")
    notes.add_argument("--book")
    pending = sub.add_parser("pending")
    pending.add_argument("--book")
    session = sub.add_parser("session")
    session.add_argument("--book", required=True)
    sub.add_parser("due")
    sub.add_parser("next")
    claim = sub.add_parser("claim")
    claim.add_argument("--book", required=True)
    claim.add_argument("--force", action="store_true")
    progress = sub.add_parser("progress")
    progress.add_argument("--book", required=True)
    progress.add_argument("--phase", choices=["chapter_archived", "upload_pending", "publish_pending"], required=True)
    progress.add_argument("--message", default="")
    finish = sub.add_parser("finish")
    finish.add_argument("--book", required=True)
    finish.add_argument(
        "--result",
        choices=[
            "success", "failed_retryable", "upload_pending", "publish_pending",
            "batch_success", "blocked_manual", "failed", "blocked",
        ],
        required=True,
    )
    finish.add_argument("--message", default="")
    run = sub.add_parser("run", help="创建批量 job 并进入桌面工作队列")
    run.add_argument("count", type=int, nargs="?", help="严格串行完成的章节槽位数")
    run.add_argument("--book", default="cosmic-404")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--resume", help="续跑已有 job id")
    job_progress = sub.add_parser("job-progress")
    job_progress.add_argument("--job", required=True)
    job_progress.add_argument("--chapter", type=int, required=True)
    job_progress.add_argument(
        "--platform-status",
        choices=["local_archived", "pending_publish", "pending_review", "published"],
        required=True,
    )
    job_progress.add_argument("--message", default="")
    job_finish = sub.add_parser("job-finish")
    job_finish.add_argument("--job", required=True)
    job_finish.add_argument(
        "--result",
        choices=["success", "partial", "blocked", "failed"],
        required=True,
    )
    job_finish.add_argument("--message", default="")
    job_status = sub.add_parser("job-status")
    job_status.add_argument("--job")
    sub.add_parser("job-next", help="桌面工作器原子领取下一个排队 job")
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--book", default="cosmic-404")
    args = parser.parse_args()
    data = config()
    funcs = {
        "list": cmd_list,
        "validate": cmd_validate,
        "notes": cmd_notes,
        "pending": cmd_pending,
        "session": cmd_session,
        "due": cmd_due,
        "next": cmd_next,
        "claim": cmd_claim,
        "progress": cmd_progress,
        "finish": cmd_finish,
        "run": cmd_run,
        "job-progress": cmd_job_progress,
        "job-finish": cmd_job_finish,
        "job-status": cmd_job_status,
        "job-next": cmd_job_next,
        "doctor": cmd_doctor,
    }
    result = funcs[args.command](data, args)
    raise SystemExit(result or 0)


if __name__ == "__main__":
    main()
