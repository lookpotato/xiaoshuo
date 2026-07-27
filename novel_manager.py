#!/usr/bin/env python3
"""Small, dependency-free dispatcher for multiple Fanqie novel projects."""

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
    "填写章节号、标题和纯正文；正文不得包含 Markdown 标题、分隔线、Metadata 或自动化说明。",
    "核对平台正文字数非零，且与本地章节字数大致一致。",
    "点击“下一步”；若出现错别字未修改提示，确认目标作品无误后点击“提交”。",
    "内容检测方式固定选择“仅基础检测”或同义的“基础检测”，不选择“全面检测”。",
    "发布设置中“是否使用AI”固定选择“是”。",
    "打开“定时发布”开关，选择计划日期和时间。",
    "最终核对章节号、标题、日期、时间、AI=是、定时发布开关后，点击“确认发布”。",
    "返回章节列表或结果页，确认目标章节显示为待发布、审核中或已发布。",
]
FANQIE_SUCCESS_CHECKS = [
    "只看到草稿箱记录不算成功；记录 publish_pending 并下次继续。",
    "看到待发布、审核中、已发布、发布成功或已提交审核，才可更新为 success。",
    "登录失效、验证码、风控、政策警告、陌生确认框、作品不匹配时立即停止并记录 blocked_manual。",
    "页面加载失败、控件暂不可用、网络超时时记录 failed_retryable。",
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


def validate_book(book):
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
        elif publish_flag(text, "submit_publish") and state_path.exists():
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
        errors.extend(validate_book(book))
        if errors:
            failed = True
            print(f"[FAIL] {book.get('id')}: " + "；".join(errors))
        else:
            print(f"[OK]   {book.get('id')}: {project_path(book)}")
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
        print("success_checks:")
        for check in FANQIE_SUCCESS_CHECKS:
            print(f"- {check}")
        if publish_required and not upload_is_publish_complete(state):
            print("current_action: 先继续完成番茄确认发布，再报告 success。")
        print()


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
    errors = validate_book(book)
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
    parser = argparse.ArgumentParser(description="多小说调度器")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    sub.add_parser("validate")
    notes = sub.add_parser("notes")
    notes.add_argument("--book")
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
