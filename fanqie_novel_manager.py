#!/usr/bin/env python3
"""番茄小说管理器：批量创作、发布恢复与运行状态调度入口。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "manager_config.json"
RUNTIME = ROOT / ".manager_runtime.json"
LOCK = ROOT / ".manager.lock"
MANAGER_NAME = "番茄小说管理器"
MANAGER_SCRIPT = "fanqie_novel_manager.py"
REQUIRED_FILES = {
    "novel_config.md", "outline.md", "characters.md", "world.md",
    "style_guide.md", "publish_config.md", "chapter_state.json",
}
REQUIRED_DIRS = {"chapters", "drafts", "logs"}
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
    "粘贴后等待平台字数刷新，再读取编辑器文本、首段、末段和平台显示字数。",
    "若编辑器文本为空、只出现提示词、首尾不匹配或字数不刷新，重新聚焦正文区再粘贴一次。",
    "重试后仍不正确，停止并记录 failed_retryable；不要点“下一步”，不要把空正文存草稿。",
]


def read_json(path: Path, default=None):
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


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
    state_path = path / "chapter_state.json"
    if state_path.exists():
        try:
            state = read_json(state_path)
            for key in ("last_completed_chapter", "next_chapter_number", "last_uploaded_status"):
                if key not in state:
                    errors.append(f"chapter_state.json 缺少 {key}")
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
        },
        "state": state,
        "runtime": runtime.get("books", {}).get(book["id"], {}),
        "pending_batch_chapters": pending_publish_entries(project),
        "required_read_order": [
            str(project / "automation_prompt.md"),
            str(ROOT / "shared" / "writing_playbook.md"),
            str(ROOT / "shared" / "quality_scorecard.md"),
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
    if now - claimed > timedelta(minutes=data.get("global_lock_minutes", 180)):
        LOCK.unlink(missing_ok=True)
        return None
    return lock


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
    if result == "success":
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
            "blocked_manual", "failed", "blocked",
        ],
        required=True,
    )
    finish.add_argument("--message", default="")
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
    }
    result = funcs[args.command](data, args)
    raise SystemExit(result or 0)


if __name__ == "__main__":
    main()
