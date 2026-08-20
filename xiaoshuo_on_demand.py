#!/usr/bin/env python3
"""一次性完成指定数量的小说写作、番茄上传与排期。"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import fanqie_novel_manager as manager
from fanqie_browser_worker import (
    FanqieBlocked,
    FanqieRetryable,
    PROFILE_READY,
    parse_chapter,
    publish,
)


ROOT = Path(__file__).resolve().parent


def project_for(data: dict, book_id: str) -> Path:
    return manager.project_path(manager.find_book(data, book_id))


def queued_job(book_id: str, count: int) -> dict | None:
    if not manager.JOB_DIR.exists():
        return None
    candidates = []
    for path in manager.JOB_DIR.glob("*.json"):
        job = manager.read_json(path, {})
        if (
            job.get("status") == "queued"
            and job.get("book_id") == book_id
            and int(job.get("target_chapters", 0)) == count
            and not job.get("completed_chapters")
        ):
            candidates.append(job)
    return min(candidates, key=lambda item: item["created_at"]) if candidates else None


def start_job(data: dict, book_id: str, count: int, resume: str | None) -> dict:
    if resume:
        job = manager.read_job(resume)
        if job["book_id"] != book_id:
            raise ValueError("--resume job 与 --book 不一致")
        if job.get("result") == "success":
            raise ValueError("该 job 已成功完成")
    else:
        job = queued_job(book_id, count) or manager.create_job(data, book_id, count)
    result = manager.cmd_claim(
        data, argparse.Namespace(book=book_id, force=True)
    )
    if result:
        raise RuntimeError("无法取得小说管理器运行锁")
    lock = manager.read_json(manager.LOCK, {})
    lock["owner_mode"] = "on_demand_process"
    manager.write_json(manager.LOCK, lock)
    job.update(
        {
            "status": "running",
            "result": None,
            "message": "",
            "updated_at": manager.now_for(data).isoformat(),
        }
    )
    job.setdefault("events", []).append(
        {"type": "on_demand_started", "at": manager.now_for(data).isoformat()}
    )
    manager.write_json(manager.job_path(job["id"]), job)
    return job


def resolve_codex() -> str:
    codex = shutil.which("codex")
    if not codex:
        raise RuntimeError("找不到 codex CLI；请先安装并运行 `codex login`")
    status = subprocess.run(
        [codex, "login", "status"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if status.returncode:
        raise RuntimeError("Codex 尚未登录；请先运行 `codex login`")
    return codex


def local_write_prompt(book_id: str, job: dict) -> str:
    return f"""使用 fanqie-auto-novel 技能，只在本地为书籍 `{book_id}` 生成并归档一章。

这是由 `python xiaoshuo` 启动的一次性串行批次，job id 为 `{job["id"]}`。
完整读取 AGENTS.md、目标作品 automation_prompt.md、技能及其要求的引用文件；
运行项目校验，读取设定、连续性账本、状态、最近三章和批量排期。
同时读取 manager session 输出的 writing_policy；新道具首次出现时先直说用途并尽快触发效果，跨章再次使用前先用一句情境化短句回顾，悬念只留来源、上限或隐藏代价。
必须读取 shared/image_workflow.md、本书 images/catalog.json 与 image_browser_config.json，并通过 browser_image_worker.py 调用已登录的图片专用 Chrome 执行本章图片工作流；禁止调用 Codex imagegen，也禁止失败后自动降级到 Codex 生图：
- 续跑失败批次时，先检查 next_chapter_number 对应的既有草稿和本书 images/ 中尚未登记的同章成图；正文与图片通过现行门禁后必须直接复用，不得仅因上次流程中断而重写正文、重复生图或覆盖文件；
- 列出本章首次出现、会持续影响读者理解的重要人物、道具、地点、异兽或组织形象作为候选；同名同设定实体沿用目录，不重复生图；
- 每章总计最多 1 张，只选择最需要视觉解释的新实体；同章其他新实体必须用正文白话解释。首次启用且本章没有更高优先级新实体时，可用唯一名额补齐主角参考图；
- 生图前先确定目标画幅并写入提示词：人物默认 2:3，道具或徽记 1:1，宽场景或地点 16:9，横向异兽或动作画面 3:2，仅明确超长竖构图使用 9:16；catalog 的 generation_aspect_ratio 与 fanqie_crop_ratio 必须一致，并写清主体安全区；
- 把完整提示词写入本任务临时文件，执行 `python browser_image_worker.py --prompt-file <文件> --output <本书 images 分类目录的新文件> --ratio <画幅>`；网页会生成并下载原图到指定位置，catalog 记录 generated_with=chrome-web 和实际 web_provider；
- 网页 GPT 成图下载后直接采用，禁止调用 view_image 或其他 Codex 视觉能力回看内容，也不得因主观画面判断要求重生或 verified；只做文件头、SHA-256、像素画幅、分类目录、正文引用和上传回显等机械校验；
- 图片浏览器未登录、出现验证码/风控/政策提示、控件变化或连续失败时，只保留章节草稿，不得归档正文、推进状态、伪造图片或改用 Codex imagegen；
- 只有网页未产出、下载失败、文件损坏或像素画幅错误时才重试；不对网页 GPT 已完成的图片做内容复审；
- 番茄作者有话说中的配图解释必须明确标为非正文：固定以“【本章辅助说明｜以下内容仅帮助理解配图，不属于小说正文】”开头，以“【辅助说明结束】”结尾；它只能帮助识图，正文因果不得依赖该说明；
- 图片文件、images/catalog.json 与章节文件属于同一批原子改动，并在结束前运行 `python -m unittest` 和管理器 validate。
必须读取 shared/reader_gate.md 并执行无大纲读者反向验收：大纲关键句只能规划方向，正文必须实际写出“承接→问题→依据→判断→行动→结果”；草稿完成后停止查看大纲、设定、连续性账本和写作提示，只读正文回答六个规定问题，每题引用逐字存在的正文证据，清零 unexplained_terms，并保存 reader_checks/NNNN.json。若必须靠作者解释才能答题，先补写正文再重新验收；缺少验收文件、正文哈希不符或未通过时，不得归档、推进状态或上传。

本次仅处理 `chapter_state.json` 的 next_chapter_number：
1. 写作、质检、修订并保存 drafts 与 chapters 文件；
2. 更新 chapter_state.json、continuity_ledger.md、reader_checks、batch_schedule 和当日日志；
3. 新排期沿用 manager_config.json 的每日发布时间，未特别指定时固定 12:00；
4. Metadata 的 upload_status 写为 not_uploaded；
5. 只允许 browser_image_worker.py 访问图片专用 Chrome 生成和下载配图；不访问番茄浏览器、不上传番茄、不调用 job-progress/job-finish/claim/finish；
6. 不改动 `.manager_jobs` 或 `.manager_runtime.json`；
7. 按 AGENTS.md 只提交并推送本章涉及的文件。

完成一章后立即结束，不得生成第二章。"""


def _codex_result_detail(result_file: Path) -> str:
    if not result_file.is_file():
        return "Codex 未写入任务结果文件"
    text = re.sub(
        r"\s+",
        " ",
        result_file.read_text(encoding="utf-8", errors="replace"),
    ).strip()
    if not text:
        return "Codex 任务结果为空"
    return text if len(text) <= 800 else text[-800:]


def write_one(book_id: str, job: dict) -> None:
    codex = resolve_codex()
    project = project_for(manager.config(), book_id)
    expected_chapter = int(
        manager.read_json(project / "chapter_state.json")["next_chapter_number"]
    )
    result_file = manager.JOB_DIR / f"{job['id']}-write-{datetime.now():%H%M%S}.md"
    command = [
        codex,
        "exec",
        "--ignore-user-config",
        "--ephemeral",
        "-C",
        str(ROOT),
        "--model",
        "gpt-5.6-luna",
        "--sandbox",
        "danger-full-access",
        "--config",
        'approval_policy="never"',
        "--config",
        'model_reasoning_effort="medium"',
        "--output-last-message",
        str(result_file),
        "-",
    ]
    prompt = local_write_prompt(book_id, job)
    for attempt in range(1, 3):
        suffix = "" if attempt == 1 else "（仅重试一次）"
        print(f"正在调用 Codex 生成下一章……{suffix}", flush=True)
        process = subprocess.run(
            command,
            cwd=ROOT,
            input=prompt,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if not process.returncode:
            state = manager.read_json(project / "chapter_state.json", {})
            if int(state.get("last_completed_chapter", 0) or 0) >= expected_chapter:
                return
            raise RuntimeError(
                "Codex 已结束但章节未通过归档门禁。"
                f"任务报告：{_codex_result_detail(result_file)}"
            )

        state = manager.read_json(project / "chapter_state.json", {})
        if int(state.get("last_completed_chapter", 0) or 0) >= expected_chapter:
            print(
                "Codex 回传中断，但新章节已完整归档；继续执行上传。",
                flush=True,
            )
            return
        if attempt == 1:
            print(
                "Codex 写作连接中断；本次命令将在隔离远程插件后仅重试一次。",
                flush=True,
            )
            continue
        raise RuntimeError(
            f"Codex 写作任务连续两次失败，最后退出码 {process.returncode}"
        )


def schedule_entries(project: Path) -> list[tuple[Path, dict]]:
    output = []
    for path in manager.batch_schedule_files(project):
        payload = manager.read_json(path, {})
        for entry in payload.get("entries", []):
            output.append((path, entry))
    return output


def expected_regular_slot(
    data: dict,
    book_id: str,
    project: Path,
    chapter_number: int,
) -> tuple[str, str] | None:
    """Return the next regular slot, packing each day to its configured target."""
    book = manager.find_book(data, book_id)
    target = max(1, int(book.get("daily_chapter_target", 1)))
    times = list(book.get("default_publish_times") or [book["schedule"]["time"]])
    if not times:
        raise ValueError(f"{book_id} 未配置默认发布时间")
    while len(times) < target:
        times.append(times[-1])

    allowed_days = set(book.get("schedule", {}).get("days", manager.DAY_KEYS))
    prior_slots: list[tuple[datetime, str]] = []
    for _, entry in schedule_entries(project):
        try:
            number = int(entry["chapter"])
            date = datetime.strptime(str(entry["date"]), "%Y-%m-%d")
        except (KeyError, TypeError, ValueError):
            continue
        entry_time = str(entry.get("time", ""))
        if number >= chapter_number or entry_time not in times:
            continue
        prior_slots.append((date, entry_time))
    if not prior_slots:
        return None

    latest_date = max(item[0] for item in prior_slots)
    used = sum(1 for date, _ in prior_slots if date == latest_date)
    if used < target:
        return latest_date.strftime("%Y-%m-%d"), times[used]

    next_date = latest_date + timedelta(days=1)
    while manager.DAY_KEYS[next_date.weekday()] not in allowed_days:
        next_date += timedelta(days=1)
    return next_date.strftime("%Y-%m-%d"), times[0]


def normalize_schedule_entry(
    data: dict,
    book_id: str,
    project: Path,
    chapter_number: int,
) -> Path | None:
    """Correct a newly archived chapter before any Fanqie upload starts."""
    expected = expected_regular_slot(data, book_id, project, chapter_number)
    if expected is None:
        return None
    expected_date, expected_time = expected
    matches = [
        (path, entry)
        for path, entry in schedule_entries(project)
        if int(entry.get("chapter", 0)) == chapter_number
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"第{chapter_number}章排期记录数量异常：期望 1 条，实际 {len(matches)} 条"
        )
    schedule_path, entry = matches[0]
    if (str(entry.get("date")), str(entry.get("time"))) == expected:
        return None

    payload = manager.read_json(schedule_path)
    target = next(
        item for item in payload["entries"]
        if int(item["chapter"]) == chapter_number
    )
    target["date"] = expected_date
    target["time"] = expected_time
    target["schedule_normalized_at"] = manager.now_for(data).isoformat()
    manager.write_json(schedule_path, payload)
    return schedule_path


def pending_chapter(project: Path) -> tuple[Path, dict, Path] | None:
    for schedule_path, entry in sorted(
        schedule_entries(project), key=lambda item: int(item[1]["chapter"])
    ):
        if entry.get("status") in manager.SUBMITTED_UPLOAD_STATUSES:
            continue
        number = int(entry["chapter"])
        files = list((project / "chapters").glob(f"{number:04d}-*.md"))
        if len(files) == 1:
            return schedule_path, entry, files[0]
    return None


def resolved_time(data: dict, book_id: str, value: str) -> str:
    if re.fullmatch(r"\d{2}:\d{2}", value or ""):
        return value
    book = manager.find_book(data, book_id)
    defaults = book.get("default_publish_times", ["12:00", "18:00"])
    return defaults[1] if len(defaults) > 1 else defaults[0]


def publish_with_retry(
    project: Path,
    chapter_path: Path,
    publish_date: str,
    publish_time: str,
    debug_browser: bool,
) -> dict:
    """Retry only failures known to have happened before confirmation started."""
    for attempt in range(1, 4):
        try:
            return publish(
                project,
                chapter_path,
                publish_date,
                publish_time,
                debug_browser=debug_browser,
            )
        except FanqieRetryable as exc:
            if debug_browser or not exc.safe_to_retry or attempt == 3:
                raise
            print(
                f"发布页面临时失败（{exc}）；本次命令自动重试 "
                f"{attempt}/2……",
                flush=True,
            )
    raise AssertionError("unreachable")


def update_metadata(path: Path, upload_status: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = re.subn(
        r"(?m)^-\s*upload_status\s*:\s*\S+\s*$",
        f"- upload_status: {upload_status}",
        text,
        count=1,
    )
    if count:
        path.write_text(updated, encoding="utf-8")


def record_upload(
    data: dict,
    book_id: str,
    project: Path,
    schedule_path: Path,
    entry: dict,
    chapter_path: Path,
    result: dict,
) -> list[Path]:
    number = int(entry["chapter"])
    platform = result["status"]
    status_map = {
        "待发布": "scheduled",
        "审核中": "submitted_pending_review",
        "已发布": "published",
    }
    local_status = status_map[platform]
    payload = manager.read_json(schedule_path)
    target = next(
        item for item in payload["entries"] if int(item["chapter"]) == number
    )
    target["status"] = local_status
    target["verified_at"] = manager.now_for(data).isoformat()
    target["fanqie_url"] = result["url"]
    target["author_note_image_uploaded"] = bool(
        result.get("author_note_image_uploaded", False)
    )
    manager.write_json(schedule_path, payload)

    state_path = project / "chapter_state.json"
    state = manager.read_json(state_path)
    state.update(
        {
            "last_uploaded_chapter": number,
            "last_uploaded_status": local_status,
            "last_uploaded_at": manager.now_for(data).isoformat(),
            "last_fanqie_url": result["url"],
        }
    )
    manager.write_json(state_path, state)
    update_metadata(chapter_path, local_status)

    draft_files = list((project / "drafts").glob(f"*-chapter-{number:04d}.md"))
    for draft in draft_files:
        update_metadata(draft, local_status)

    log_path = project / "logs" / f"{manager.now_for(data):%Y-%m-%d}-run.md"
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(
            f"\n\n## 按需命令发布第{number}章\n\n"
            f"- 平台状态：{platform}\n"
            f"- 本地状态：{local_status}\n"
            f"- 排期：{entry['date']} {resolved_time(data, book_id, entry['time'])}\n"
            f"- 核验 URL：{result['url']}\n"
            f"- 作者有话说图片："
            f"{'已上传唯一图片' if result.get('author_note_image_uploaded') else '本章无图'}\n"
        )
    continuity = project / "continuity_ledger.md"
    return [
        schedule_path,
        state_path,
        chapter_path,
        log_path,
        continuity,
        *draft_files,
    ]


def git_sync(paths: list[Path], message: str) -> bool:
    unique_paths = sorted({path.resolve() for path in paths if path.exists()})
    if not unique_paths:
        return True
    relative = [str(path.relative_to(ROOT)) for path in unique_paths]
    subprocess.run(["git", "add", "--", *relative], cwd=ROOT, check=True)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=ROOT
    )
    if staged.returncode != 0:
        subprocess.run(["git", "commit", "-m", message], cwd=ROOT, check=True)
    # 上一次运行可能已经 commit、只在 push 阶段断网；恢复时仍必须重试推送。
    pushed = subprocess.run(["git", "push"], cwd=ROOT)
    if pushed.returncode:
        print(
            "警告：章节已在番茄成功提交，但 Git 推送暂时失败；"
            "本地提交已保留，后续运行会重试推送。",
            file=sys.stderr,
            flush=True,
        )
        return False
    return True


def finish_job(
    data: dict, job: dict, result: str, message: str, manager_result: str
) -> None:
    manager.cmd_finish(
        data,
        argparse.Namespace(
            book=job["book_id"], result=manager_result, message=message
        ),
    )
    manager.cmd_job_finish(
        data,
        argparse.Namespace(job=job["id"], result=result, message=message),
    )


def run(
    count: int,
    book_id: str,
    resume: str | None,
    dry_run: bool,
    debug_browser: bool,
) -> int:
    data = manager.config()
    book = manager.find_book(data, book_id)
    project = project_for(data, book_id)
    write_only = book.get("mode") == "write_only"
    errors = manager.validate_book(
        book, require_publish_complete=False
    )
    if errors:
        raise RuntimeError("项目校验失败：" + "；".join(errors))
    if dry_run:
        print(
            json.dumps(
                {
                    "mode": "on_demand",
                    "book": book_id,
                    "target": count,
                    "delivery": "local_archive" if write_only else "fanqie_publish",
                    "background_polling": False,
                    "steps": [
                        "调用一次 Codex 写一章并完成本地质检归档",
                        *([] if write_only else [
                            "恢复未发布章节或启动专用 Chrome 上传并排期",
                            "平台列表核验",
                        ]),
                        "更新本地状态、提交并推送 Git",
                        "进程退出",
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if not write_only and not PROFILE_READY.is_file():
        raise RuntimeError(
            "专用 Chrome 尚未完成首次登录配置；"
            "请先在 VS Code 终端运行 `python xiaoshuo --setup-browser`"
        )
    job = start_job(data, book_id, count, resume)
    try:
        recovery_paths = [
            Path(item) for item in job.get("changed_paths_pending_git", [])
        ]
        if recovery_paths:
            if git_sync(recovery_paths, f"恢复按需批次 {job['id']} 状态"):
                job.pop("changed_paths_pending_git", None)
            manager.write_json(manager.job_path(job["id"]), job)
        completed = len(job.get("completed_chapters", []))
        while completed < int(job["target_chapters"]):
            print(
                f"\n本批进度 {completed + 1}/{job['target_chapters']}："
                "准备下一章",
                flush=True,
            )
            if write_only:
                before = manager.read_json(project / "chapter_state.json")[
                    "last_completed_chapter"
                ]
                write_one(book_id, job)
                state = manager.read_json(project / "chapter_state.json")
                after = int(state["last_completed_chapter"])
                if after != int(before) + 1:
                    raise RuntimeError("Codex 退出后未发现唯一的新章节")
                manager.cmd_job_progress(
                    data,
                    argparse.Namespace(
                        job=job["id"],
                        chapter=after,
                        platform_status="local_archived",
                        message="新章已完成本地质检、归档与 Git 同步；未访问番茄",
                    ),
                )
                completed += 1
                continue
            pending = pending_chapter(project)
            if pending is None:
                before = manager.read_json(project / "chapter_state.json")[
                    "last_completed_chapter"
                ]
                write_one(book_id, job)
                after = manager.read_json(project / "chapter_state.json")[
                    "last_completed_chapter"
                ]
                if int(after) != int(before) + 1:
                    raise RuntimeError("Codex 退出后未发现唯一的新章节")
                normalized_schedule = normalize_schedule_entry(
                    data, book_id, project, int(after)
                )
                if normalized_schedule:
                    print(
                        f"已按每天 {book.get('daily_chapter_target', 1)} 章修正"
                        f"第{after}章排期。",
                        flush=True,
                    )
                pending = pending_chapter(project)
                if pending is None:
                    raise RuntimeError("新章节未进入待上传排期")
            schedule_path, entry, chapter_path = pending
            chapter = parse_chapter(chapter_path)
            publish_time = resolved_time(data, book_id, str(entry["time"]))
            print(
                f"正在发布第{chapter.number}章《{chapter.title}》："
                f"{entry['date']} {publish_time}",
                flush=True,
            )
            upload = publish_with_retry(
                project,
                chapter_path,
                str(entry["date"]),
                publish_time,
                debug_browser,
            )
            changed = record_upload(
                data,
                book_id,
                project,
                schedule_path,
                entry,
                chapter_path,
                upload,
            )
            job["changed_paths_pending_git"] = [
                str(path.resolve()) for path in changed
            ]
            manager.write_json(manager.job_path(job["id"]), job)
            manager.cmd_job_progress(
                data,
                argparse.Namespace(
                    job=job["id"],
                    chapter=chapter.number,
                    platform_status={
                        "待发布": "pending_publish",
                        "审核中": "pending_review",
                        "已发布": "published",
                    }[upload["status"]],
                    message=(
                        f"《{chapter.title}》{entry['date']} {publish_time}，"
                        f"平台核验为{upload['status']}"
                    ),
                ),
            )
            pushed = git_sync(changed, f"发布第{chapter.number}章《{chapter.title}》")
            job = manager.read_job(job["id"])
            if pushed:
                job.pop("changed_paths_pending_git", None)
            manager.write_json(manager.job_path(job["id"]), job)
            completed += 1
        git_push_pending = bool(job.get("changed_paths_pending_git"))
        completion_message = f"按需完成 {completed}/{job['target_chapters']} 章"
        if write_only:
            completion_message += "；本书为本地创作模式，未访问番茄"
        if git_push_pending:
            completion_message += "；番茄已成功，Git 推送待恢复"
        finish_job(
            data,
            job,
            "success",
            completion_message,
            "batch_success",
        )
        print(
            json.dumps(
                {
                    "job": job["id"],
                    "status": "finished",
                    "result": "success",
                    "completed": completed,
                    "target": job["target_chapters"],
                    "background_polling": False,
                    "git_push_pending": git_push_pending,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except FanqieBlocked as exc:
        finish_job(data, job, "blocked", str(exc), "blocked_manual")
        print(f"已安全停止：{exc}", file=sys.stderr)
        return 4
    except Exception as exc:
        finish_job(data, job, "failed", str(exc), "failed_retryable")
        print(f"本次运行失败，可按原 job 续跑：{exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        finish_job(
            data,
            job,
            "partial",
            "用户中断；状态已保留，可续跑",
            "publish_pending",
        )
        return 130


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("count", type=int, nargs="?")
    parser.add_argument("--book", default="cosmic-404")
    parser.add_argument("--resume")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--debug-browser", action="store_true")
    args = parser.parse_args()
    if args.resume and args.count is None:
        args.count = int(manager.read_job(args.resume)["target_chapters"])
    if args.count is None or not 1 <= args.count <= 50:
        parser.error("请提供 1 到 50 的章节数，或使用 --resume <job-id>")
    try:
        return run(
            args.count,
            args.book,
            args.resume,
            args.dry_run,
            args.debug_browser,
        )
    except (RuntimeError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
