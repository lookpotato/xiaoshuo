#!/usr/bin/env python3
"""把已有番茄定时章节提前到今天，并同步本地排期。"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import uuid
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
REWARD_JOB_VERSION = 1


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


def scheduled_refs(project: Path) -> list[tuple[Path, dict, Path]]:
    refs: list[tuple[Path, dict, Path]] = []
    seen: set[int] = set()
    for schedule_path, entry in sorted(
        schedule_entries(project),
        key=lambda item: (
            str(item[1].get("date", "")),
            str(item[1].get("time", "")),
            int(item[1].get("chapter", 0)),
        ),
    ):
        number = int(entry.get("chapter", 0))
        if number in seen or entry.get("status") not in ELIGIBLE_STATUSES:
            continue
        seen.add(number)
        files = list((project / "chapters").glob(f"{number:04d}-*.md"))
        if len(files) == 1:
            refs.append((schedule_path, entry, files[0]))
    return refs


def move_payload(
    schedule_path: Path,
    entry: dict,
    chapter_path: Path,
    target_date: str,
    target_time: str,
    kind: str,
) -> dict:
    return {
        "chapter": int(entry["chapter"]),
        "title": parse_chapter(chapter_path).title,
        "schedule_file": str(schedule_path.resolve()),
        "chapter_file": str(chapter_path.resolve()),
        "from": {
            "date": str(entry["date"]),
            "time": str(entry.get("time", "12:00")),
        },
        "to": {"date": target_date, "time": target_time},
        "kind": kind,
        "status": "pending",
    }


def build_new_reward_plan(
    project: Path,
    count: int,
    now: datetime,
    target_time: str,
) -> dict:
    future = []
    for ref in scheduled_refs(project):
        entry = ref[1]
        try:
            scheduled_date = datetime.fromisoformat(str(entry["date"])).date()
        except (KeyError, TypeError, ValueError):
            continue
        if scheduled_date > now.date():
            future.append(ref)
    if len(future) < count:
        raise RuntimeError(
            f"只找到 {len(future)} 个可提前的未来排期章节，少于要求的 {count} 个"
        )

    original_slots = [
        {"date": str(entry["date"]), "time": str(entry.get("time", "12:00"))}
        for _, entry, _ in future
    ]
    moves = [
        move_payload(
            schedule_path,
            entry,
            chapter_path,
            now.date().isoformat(),
            target_time,
            "reward_bonus",
        )
        for schedule_path, entry, chapter_path in future[:count]
    ]
    for index, (schedule_path, entry, chapter_path) in enumerate(future[count:]):
        target = original_slots[index]
        current = {
            "date": str(entry["date"]),
            "time": str(entry.get("time", "12:00")),
        }
        if current != target:
            moves.append(
                move_payload(
                    schedule_path,
                    entry,
                    chapter_path,
                    target["date"],
                    target["time"],
                    "reward_reflow",
                )
            )
    return {
        "source": "new_reward",
        "reward_count": count,
        "bonus_chapters": [int(entry["chapter"]) for _, entry, _ in future[:count]],
        "moves": moves,
    }


def legacy_reflow_plan(project: Path, now: datetime) -> dict | None:
    rewards = []
    for schedule_path, entry, chapter_path in scheduled_refs(project):
        for event in entry.get("reward_history", []):
            source = event.get("from", {})
            target = event.get("to", {})
            if (
                target.get("date") == now.date().isoformat()
                and source.get("date", "") > target.get("date", "")
                and not event.get("reflow_completed_at")
            ):
                rewards.append((event.get("changed_at", ""), schedule_path, entry, chapter_path, event))
    if not rewards:
        return None
    _, bonus_schedule, bonus_entry, bonus_chapter, bonus_event = max(
        rewards, key=lambda item: item[0]
    )
    bonus_number = int(bonus_entry["chapter"])
    source_slot = {
        "date": str(bonus_event["from"]["date"]),
        "time": str(bonus_event["from"].get("time", "12:00")),
    }
    followers = [
        ref
        for ref in scheduled_refs(project)
        if int(ref[1]["chapter"]) > bonus_number
        and (
            str(ref[1]["date"]),
            str(ref[1].get("time", "12:00")),
            int(ref[1]["chapter"]),
        )
        >= (source_slot["date"], source_slot["time"], bonus_number)
    ]
    slots = [source_slot] + [
        {"date": str(entry["date"]), "time": str(entry.get("time", "12:00"))}
        for _, entry, _ in followers
    ]
    moves = []
    for index, (schedule_path, entry, chapter_path) in enumerate(followers):
        target = slots[index]
        current = {
            "date": str(entry["date"]),
            "time": str(entry.get("time", "12:00")),
        }
        if current != target:
            moves.append(
                move_payload(
                    schedule_path,
                    entry,
                    chapter_path,
                    target["date"],
                    target["time"],
                    "reward_reflow",
                )
            )
    return {
        "source": "legacy_reward_recovery",
        "reward_count": 1,
        "bonus_chapters": [bonus_number],
        "legacy_event": {
            "schedule_file": str(bonus_schedule.resolve()),
            "chapter": bonus_number,
            "changed_at": bonus_event.get("changed_at"),
        },
        "moves": moves,
    }


def reward_job_path(job_id: str) -> Path:
    return manager.JOB_DIR / f"reward-{job_id}.json"


def active_reward_job(book_id: str) -> tuple[Path, dict] | None:
    for path in sorted(manager.JOB_DIR.glob("reward-*.json")):
        job = manager.read_json(path, {})
        if job.get("book_id") == book_id and job.get("status") in {"running", "partial"}:
            return path, job
    return None


def create_reward_job(data: dict, book_id: str, plan: dict) -> tuple[Path, dict]:
    manager.JOB_DIR.mkdir(exist_ok=True)
    job_id = f"{manager.now_for(data):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
    job = {
        "schema_version": REWARD_JOB_VERSION,
        "id": job_id,
        "book_id": book_id,
        "status": "running",
        "created_at": manager.now_for(data).isoformat(),
        **plan,
    }
    path = reward_job_path(job_id)
    manager.write_json(path, job)
    return path, job


def plan_preview(job: dict) -> dict:
    return {
        "mode": "reward_with_reflow",
        "source": job.get("source"),
        "bonus_chapters": job.get("bonus_chapters", []),
        "total_platform_edits": len(job.get("moves", [])),
        "moves": [
            {
                "chapter": move["chapter"],
                "title": move["title"],
                "kind": move["kind"],
                "from": f"{move['from']['date']} {move['from']['time']}",
                "to": f"{move['to']['date']} {move['to']['time']}",
                "status": move.get("status", "pending"),
            }
            for move in job.get("moves", [])
        ],
    }


def record_reward(
    data: dict,
    project: Path,
    schedule_path: Path,
    entry: dict,
    target_date: str,
    target_time: str,
    result: dict,
    kind: str = "reward_bonus",
    batch_id: str | None = None,
) -> list[Path]:
    number = int(entry["chapter"])
    changed_at = manager.now_for(data).isoformat()
    payload = manager.read_json(schedule_path)
    target = next(item for item in payload["entries"] if int(item["chapter"]) == number)
    previous = {"date": target["date"], "time": target.get("time", "12:00")}
    history = {
        "from": previous,
        "to": {"date": target_date, "time": target_time},
        "changed_at": changed_at,
        "kind": kind,
    }
    if batch_id:
        history["batch_id"] = batch_id
    target.setdefault("reward_history", []).append(history)
    target["date"] = target_date
    target["time"] = target_time
    target["verified_at"] = changed_at
    target["fanqie_url"] = result["url"]
    manager.write_json(schedule_path, payload)

    state_path = project / "chapter_state.json"
    state = manager.read_json(state_path)
    state["last_schedule_adjustment"] = {
        "mode": kind,
        "batch_id": batch_id,
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
            f"\n\n## 打赏加更排期：调整第{number}章\n\n"
            f"- 类型：{kind}\n"
            f"- 批次：{batch_id or 'legacy'}\n"
            f"- 原排期：{previous['date']} {previous['time']}\n"
            f"- 新排期：{target_date} {target_time}\n"
            f"- 平台状态：{result['status']}\n"
            f"- 核验 URL：{result['url']}\n"
        )
    return [schedule_path, state_path, log_path]


def mark_reflow_complete(
    data: dict, project: Path, job: dict
) -> list[Path]:
    completed_at = manager.now_for(data).isoformat()
    changed: list[Path] = []
    legacy = job.get("legacy_event")
    for schedule_path in manager.batch_schedule_files(project):
        payload = manager.read_json(schedule_path, {})
        dirty = False
        for entry in payload.get("entries", []):
            for event in entry.get("reward_history", []):
                matches_batch = event.get("batch_id") == job["id"]
                matches_legacy = bool(
                    legacy
                    and str(schedule_path.resolve()) == legacy.get("schedule_file")
                    and int(entry.get("chapter", 0)) == int(legacy.get("chapter", 0))
                    and event.get("changed_at") == legacy.get("changed_at")
                )
                if matches_batch or matches_legacy:
                    event["reflow_batch_id"] = job["id"]
                    event["reflow_completed_at"] = completed_at
                    dirty = True
        if dirty:
            manager.write_json(schedule_path, payload)
            changed.append(schedule_path)
    state_path = project / "chapter_state.json"
    state = manager.read_json(state_path)
    state["last_reward_reflow"] = {
        "batch_id": job["id"],
        "bonus_chapters": job.get("bonus_chapters", []),
        "adjusted_chapters": [move["chapter"] for move in job.get("moves", [])],
        "completed_at": completed_at,
    }
    manager.write_json(state_path, state)
    changed.append(state_path)
    return changed


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
    active = active_reward_job(book_id)
    job_path: Path | None = None
    if active:
        job_path, job = active
        if int(job.get("reward_count", 0)) != count:
            raise RuntimeError(
                f"已有未完成加更重排 {job['id']}，其加更数为 "
                f"{job.get('reward_count')}；请先用原数量续跑"
            )
    else:
        plan = legacy_reflow_plan(project, now)
        if plan and int(plan.get("reward_count", 0)) != count:
            raise RuntimeError(
                f"检测到今天已有 {plan.get('reward_count')} 章加更尚未完成后续重排；"
                "请先用相同数量续跑"
            )
        if not plan:
            target_time = (
                validate_time(requested_time, now)
                if requested_time
                else default_reward_time(now)
            )
            plan = build_new_reward_plan(project, count, now, target_time)
        job = {
            "id": "preview",
            "book_id": book_id,
            "status": "preview",
            **plan,
        }
    if dry_run:
        print(json.dumps(plan_preview(job), ensure_ascii=False, indent=2))
        return 0
    if not PROFILE_READY.is_file():
        raise RuntimeError(
            "专用 Chrome 尚未完成首次登录配置；"
            "请先运行 `python xiaoshuo --setup-browser`"
        )
    if job_path is None:
        job_path, job = create_reward_job(data, book_id, plan)

    if manager.cmd_claim(data, argparse.Namespace(book=book_id, force=True)):
        raise RuntimeError("无法取得番茄小说管理器运行锁")
    lock = manager.read_json(manager.LOCK, {})
    lock["owner_mode"] = "reward_reschedule"
    manager.write_json(manager.LOCK, lock)

    completed = [
        int(move["chapter"])
        for move in job.get("moves", [])
        if move.get("status") == "completed"
    ]
    git_push_pending = False
    try:
        for move in job.get("moves", []):
            if move.get("status") == "completed":
                continue
            schedule_path = Path(move["schedule_file"])
            chapter_path = Path(move["chapter_file"])
            payload = manager.read_json(schedule_path)
            entry = next(
                item
                for item in payload["entries"]
                if int(item["chapter"]) == int(move["chapter"])
            )
            chapter = parse_chapter(chapter_path)
            print(
                f"正在调整第{chapter.number}章《{chapter.title}》"
                f"[{move['kind']}]："
                f"{entry['date']} {entry.get('time', '12:00')} → "
                f"{move['to']['date']} {move['to']['time']}",
                flush=True,
            )
            result = reschedule(
                project,
                chapter_path,
                move["to"]["date"],
                move["to"]["time"],
                debug_browser=debug_browser,
            )
            changed = record_reward(
                data,
                project,
                schedule_path,
                entry,
                move["to"]["date"],
                move["to"]["time"],
                result,
                kind=move["kind"],
                batch_id=job["id"],
            )
            move["status"] = "completed"
            move["completed_at"] = manager.now_for(data).isoformat()
            job["status"] = "running"
            job["updated_at"] = manager.now_for(data).isoformat()
            manager.write_json(job_path, job)
            if not git_sync(changed, f"打赏加更：提前发布第{chapter.number}章"):
                git_push_pending = True
            completed.append(chapter.number)
    except FanqieBlocked as exc:
        job["status"] = "partial"
        job["last_error"] = str(exc)
        job["updated_at"] = manager.now_for(data).isoformat()
        manager.write_json(job_path, job)
        manager.cmd_finish(
            data,
            argparse.Namespace(book=book_id, result="blocked_manual", message=str(exc)),
        )
        raise
    except KeyboardInterrupt:
        job["status"] = "partial"
        job["last_error"] = "用户中断"
        job["updated_at"] = manager.now_for(data).isoformat()
        manager.write_json(job_path, job)
        manager.cmd_finish(
            data,
            argparse.Namespace(
                book=book_id,
                result="publish_pending",
                message=(
                    f"用户中断；重排已完成 {len(completed)}/"
                    f"{len(job.get('moves', []))} 项"
                ),
            ),
        )
        raise
    except Exception as exc:
        job["status"] = "partial"
        job["last_error"] = str(exc)
        job["updated_at"] = manager.now_for(data).isoformat()
        manager.write_json(job_path, job)
        manager.cmd_finish(
            data,
            argparse.Namespace(book=book_id, result="failed_retryable", message=str(exc)),
        )
        raise
    completion_paths = mark_reflow_complete(data, project, job)
    if not git_sync(completion_paths, f"完成打赏加更重排 {job['id']}"):
        git_push_pending = True
    job["status"] = "completed"
    job["completed_at"] = manager.now_for(data).isoformat()
    job.pop("last_error", None)
    manager.write_json(job_path, job)
    manager.cmd_finish(
        data,
        argparse.Namespace(
            book=book_id,
            result="batch_success",
            message=(
                f"打赏加更及后续重排完成 {len(completed)}/"
                f"{len(job.get('moves', []))} 项"
            ),
        ),
    )
    print(
        json.dumps(
            {
                "status": "success",
                "mode": "reward_with_reflow",
                "job": job["id"],
                "bonus_chapters": job.get("bonus_chapters", []),
                "completed": completed,
                "total_platform_edits": len(job.get("moves", [])),
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
    parser.add_argument("--time", help="目标时间 HH:MM；默认当前时间后 45 分钟")
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
