#!/usr/bin/env python3
"""把已有番茄定时章节提前到今天，并同步本地排期。"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import fanqie_novel_manager as manager
from fanqie_browser_worker import (
    FanqieBlocked,
    FanqieRetryable,
    PROFILE_READY,
    parse_chapter,
    reschedule,
)
from xiaoshuo_on_demand import git_sync, schedule_entries


ROOT = Path(__file__).resolve().parent
ELIGIBLE_STATUSES = manager.SUBMITTED_UPLOAD_STATUSES


def default_reward_time(now: datetime) -> str:
    # 番茄要求已提交章节必须在新发布时间至少 30 分钟前完成修改。
    # 预留 45 分钟并向上取整，避免页面操作耗时吃掉平台硬门槛。
    candidate = now + timedelta(minutes=45)
    rounded_minute = int(math.ceil(candidate.minute / 10) * 10)
    candidate = candidate.replace(second=0, microsecond=0)
    if rounded_minute == 60:
        candidate = candidate.replace(minute=0) + timedelta(hours=1)
    else:
        candidate = candidate.replace(minute=rounded_minute)
    if candidate.date() != now.date():
        raise ValueError("今天已没有安全的定时发布窗口，请明天再执行加更")
    return candidate.strftime("%H:%M")


def validate_time(value: str, now: datetime) -> str:
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value or ""):
        raise ValueError("加更时间必须是 HH:MM，例如 16:30")
    target = datetime.combine(
        now.date(), datetime.strptime(value, "%H:%M").time(), now.tzinfo
    )
    if target < now + timedelta(minutes=40):
        raise ValueError("已提交章节的加更时间必须至少晚于当前时间 40 分钟")
    return value


def reward_candidates(
    project: Path, count: int, now: datetime, target_time: str
) -> list[tuple[Path, dict, Path]]:
    target = datetime.combine(
        now.date(), datetime.strptime(target_time, "%H:%M").time(), now.tzinfo
    )
    candidates: list[tuple[Path, dict, Path]] = []
    seen: set[int] = set()
    ordered = sorted(
        schedule_entries(project),
        key=lambda item: (
            str(item[1].get("date", "")),
            str(item[1].get("time", "")),
            int(item[1].get("chapter", 0)),
        ),
    )
    for schedule_path, entry in ordered:
        number = int(entry.get("chapter", 0))
        if number in seen or entry.get("status") not in ELIGIBLE_STATUSES:
            continue
        seen.add(number)
        try:
            scheduled = datetime.fromisoformat(
                f"{entry['date']}T{entry.get('time', '12:00')}"
            ).replace(tzinfo=now.tzinfo)
        except (KeyError, TypeError, ValueError):
            continue
        # “今天多发”只能从后续日期借章；调整今天已有章节的具体时刻
        # 不会增加今日发布总数，不能占用加更名额。
        if scheduled.date() <= now.date() or scheduled <= target:
            continue
        chapter_files = list((project / "chapters").glob(f"{number:04d}-*.md"))
        if len(chapter_files) == 1:
            candidates.append((schedule_path, entry, chapter_files[0]))
        if len(candidates) == count:
            break
    return candidates


def record_reward(
    data: dict,
    project: Path,
    schedule_path: Path,
    entry: dict,
    target_date: str,
    target_time: str,
    result: dict,
) -> list[Path]:
    number = int(entry["chapter"])
    changed_at = manager.now_for(data).isoformat()
    payload = manager.read_json(schedule_path)
    target = next(item for item in payload["entries"] if int(item["chapter"]) == number)
    previous = {"date": target["date"], "time": target.get("time", "12:00")}
    target.setdefault("reward_history", []).append(
        {
            "from": previous,
            "to": {"date": target_date, "time": target_time},
            "changed_at": changed_at,
        }
    )
    target["date"] = target_date
    target["time"] = target_time
    target["verified_at"] = changed_at
    target["fanqie_url"] = result["url"]
    manager.write_json(schedule_path, payload)

    state_path = project / "chapter_state.json"
    state = manager.read_json(state_path)
    state["last_schedule_adjustment"] = {
        "mode": "reward",
        "chapter": number,
        "from": previous,
        "to": {"date": target_date, "time": target_time},
        "platform_status": result["status"],
        "adjusted_at": changed_at,
    }
    manager.write_json(state_path, state)

    log_path = project / "logs" / f"{manager.now_for(data):%Y-%m-%d}-run.md"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(
            f"\n\n## 打赏加更：提前第{number}章\n\n"
            f"- 原排期：{previous['date']} {previous['time']}\n"
            f"- 新排期：{target_date} {target_time}\n"
            f"- 平台状态：{result['status']}\n"
            f"- 核验 URL：{result['url']}\n"
        )
    return [schedule_path, state_path, log_path]


def run(
    count: int,
    book_id: str,
    requested_time: str | None,
    dry_run: bool,
    debug_browser: bool,
) -> int:
    if not 1 <= count <= 50:
        raise ValueError("加更章节数必须在 1 到 50 之间")
    data = manager.config()
    book = manager.find_book(data, book_id)
    project = manager.project_path(book)
    errors = manager.validate_book(book, require_publish_complete=False)
    if errors:
        raise RuntimeError("项目校验失败：" + "；".join(errors))
    now = manager.now_for(data)
    target_time = (
        validate_time(requested_time, now)
        if requested_time
        else default_reward_time(now)
    )
    target_date = now.date().isoformat()
    candidates = reward_candidates(project, count, now, target_time)
    if len(candidates) != count:
        raise RuntimeError(
            f"只找到 {len(candidates)} 个可提前的未来排期章节，少于要求的 {count} 个"
        )
    preview = [
        {
            "chapter": int(entry["chapter"]),
            "title": parse_chapter(chapter_path).title,
            "from": f"{entry['date']} {entry.get('time', '12:00')}",
            "to": f"{target_date} {target_time}",
        }
        for _, entry, chapter_path in candidates
    ]
    if dry_run:
        print(
            json.dumps(
                {"mode": "reward", "book": book_id, "chapters": preview},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if not PROFILE_READY.is_file():
        raise RuntimeError(
            "专用 Chrome 尚未完成首次登录配置；"
            "请先运行 `python xiaoshuo --setup-browser`"
        )

    if manager.cmd_claim(data, argparse.Namespace(book=book_id, force=True)):
        raise RuntimeError("无法取得番茄小说管理器运行锁")
    lock = manager.read_json(manager.LOCK, {})
    lock["owner_mode"] = "reward_reschedule"
    manager.write_json(manager.LOCK, lock)

    completed: list[int] = []
    git_push_pending = False
    try:
        for schedule_path, entry, chapter_path in candidates:
            chapter = parse_chapter(chapter_path)
            print(
                f"正在提前第{chapter.number}章《{chapter.title}》："
                f"{entry['date']} {entry.get('time', '12:00')} → "
                f"{target_date} {target_time}",
                flush=True,
            )
            result = reschedule(
                project,
                chapter_path,
                target_date,
                target_time,
                debug_browser=debug_browser,
            )
            changed = record_reward(
                data,
                project,
                schedule_path,
                entry,
                target_date,
                target_time,
                result,
            )
            if not git_sync(changed, f"打赏加更：提前发布第{chapter.number}章"):
                git_push_pending = True
            completed.append(chapter.number)
    except FanqieBlocked as exc:
        manager.cmd_finish(
            data,
            argparse.Namespace(book=book_id, result="blocked_manual", message=str(exc)),
        )
        raise
    except KeyboardInterrupt:
        manager.cmd_finish(
            data,
            argparse.Namespace(
                book=book_id,
                result="publish_pending",
                message=f"用户中断；已完成 {len(completed)}/{count} 章",
            ),
        )
        raise
    except Exception as exc:
        manager.cmd_finish(
            data,
            argparse.Namespace(book=book_id, result="failed_retryable", message=str(exc)),
        )
        raise
    manager.cmd_finish(
        data,
        argparse.Namespace(
            book=book_id,
            result="batch_success",
            message=f"打赏加更完成 {len(completed)}/{count} 章",
        ),
    )
    print(
        json.dumps(
            {
                "status": "success",
                "mode": "reward",
                "completed": completed,
                "publish_at": f"{target_date} {target_time}",
                "git_push_pending": git_push_pending,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="把已有番茄定时章节提前到今天")
    parser.add_argument("count", type=int, help="今天额外提前发布的章节数")
    parser.add_argument("--book", default="cosmic-404")
    parser.add_argument("--time", help="目标时间 HH:MM；默认当前时间后 30 分钟")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--debug-browser", action="store_true")
    args = parser.parse_args()
    try:
        return run(args.count, args.book, args.time, args.dry_run, args.debug_browser)
    except FanqieBlocked as exc:
        print(f"已安全停止：{exc}", file=sys.stderr)
        return 4
    except (FanqieRetryable, RuntimeError, ValueError, OSError) as exc:
        print(f"加更失败；请按终端已显示的完成数扣除后重试：{exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("加更已由用户中断；本地完成状态已保留", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
