#!/usr/bin/env python3
"""一次性完成指定数量的小说写作、番茄上传与排期。"""

from __future__ import annotations

import argparse
import json
import os
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
MAX_AUTOMATIC_REPAIRS = 2
MAX_CODEX_PROCESS_RETRIES = 2


class ArchiveGateFailure(RuntimeError):
    """A generated chapter exists but its local archive artifacts are inconsistent."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("；".join(errors))


def project_for(data: dict, book_id: str) -> Path:
    return manager.project_path(manager.find_book(data, book_id))


def queued_job(
    book_id: str, count: int, publish_fanqie: bool, sync_git: bool
) -> dict | None:
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
            and job.get("run_options", {})
            == {"publish_fanqie": publish_fanqie, "sync_git": sync_git}
        ):
            candidates.append(job)
    return min(candidates, key=lambda item: item["created_at"]) if candidates else None


def start_job(
    data: dict,
    book_id: str,
    count: int,
    resume: str | None,
    publish_fanqie: bool,
    sync_git: bool,
) -> dict:
    if resume:
        job = manager.read_job(resume)
        if job["book_id"] != book_id:
            raise ValueError("--resume job 与 --book 不一致")
        if job.get("result") == "success":
            raise ValueError("该 job 已成功完成")
    else:
        job = queued_job(book_id, count, publish_fanqie, sync_git) or manager.create_job(
            data, book_id, count
        )
        job["run_options"] = {
            "publish_fanqie": publish_fanqie,
            "sync_git": sync_git,
        }
    claim_errors = manager.validate_book(
        manager.find_book(data, book_id), require_publish_complete=False
    )
    if recoverable_draft_errors(project_for(data, book_id), claim_errors):
        result = claim_repair_job(data, book_id)
    else:
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


def claim_repair_job(data: dict, book_id: str) -> int:
    """Claim the normal manager lock while an interrupted next chapter is repaired."""
    now = manager.now_for(data)
    lock = manager.live_lock(data, now)
    if lock:
        print(
            f"已有任务运行: {lock['book_id']}，领取于 {lock['claimed_at']}",
            file=sys.stderr,
        )
        return 2
    payload = {
        "book_id": book_id,
        "claimed_at": now.isoformat(),
        "pid": os.getpid(),
        "repairing_interrupted_chapter": True,
    }
    try:
        fd = os.open(manager.LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except FileExistsError:
        print("任务刚被另一个进程领取", file=sys.stderr)
        return 2
    runtime = manager.read_json(manager.RUNTIME, {"books": {}})
    status = runtime.setdefault("books", {}).setdefault(book_id, {})
    previous_claim = status.get("last_claimed_at")
    if previous_claim and datetime.fromisoformat(previous_claim).date() == now.date():
        status["attempt_count"] = status.get("attempt_count", 0) + 1
    else:
        status["attempt_count"] = 1
    status.update(
        {
            "last_claimed_at": now.isoformat(),
            "run_status": "claimed_for_auto_repair",
            "retry_after": None,
            "message": "正在自动修复上一轮遗留的下一章",
        }
    )
    manager.write_json(manager.RUNTIME, runtime)
    print(
        json.dumps(
            {
                **payload,
                "project_path": str(
                    project_for(data, book_id)
                ),
                "mode": manager.find_book(data, book_id).get("mode"),
            },
            ensure_ascii=False,
        )
    )
    return 0


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
    book = manager.find_book(manager.config(), book_id)
    if book.get("mode") == "write_only" and not job.get("run_options", {}).get(
        "publish_fanqie", False
    ):
        return local_write_only_prompt(book_id, job)
    return f"""使用 fanqie-auto-novel 技能，只在本地为书籍 `{book_id}` 生成并归档一章。

这是由小说工作台 API 启动的一次性串行批次，job id 为 `{job["id"]}`。
完整读取 AGENTS.md、目标作品 automation_prompt.md、技能及其要求的引用文件；
必须读取 shared/narrative_prose_foundation.md、shared/chinese_dialogue_foundation.md 与 shared/chinese_dialogue_feedback.jsonl；若目标项目存在 narrative_style_pack.md，也必须读取。每场固定一个认知中心，新人物必须有来路、先行动和关系锚，新道具必须先交代眼下用途与使用代价。对白先按人物关系、共同经历、场合和情绪确定称呼与省略，不得用固定方言词替换冒充真人口语，完稿前抽查至少十句执行口语逆翻译。
运行项目校验，读取设定、连续性账本、状态、最近三章和批量排期。
同时读取 manager session 输出的 writing_policy；新道具首次出现时先直说用途并尽快触发效果，跨章再次使用前先用一句情境化短句回顾，悬念只留来源、上限或隐藏代价。
必须读取 shared/image_workflow.md、本书 images/catalog.json 与 image_browser_config.json，并通过 browser_image_worker.py 调用已登录的图片专用 Chrome 执行本章图片工作流；禁止调用 Codex imagegen，也禁止失败后自动降级到 Codex 生图：
- 续跑失败批次时，先检查 next_chapter_number 对应的既有草稿和本书 images/ 中尚未登记的同章成图；正文与图片通过现行门禁后必须直接复用，不得仅因上次流程中断而重写正文、重复生图或覆盖文件；
- 本书章节标题必须唯一；定稿前扫描 chapters/，禁止只差空格或标点的重复标题；
- 列出本章首次出现、会持续影响读者理解的重要人物、道具、地点、异兽或组织形象作为候选；同名同设定实体沿用目录，不重复生图；
- 每章总计最多 1 张，只选择最需要视觉解释的新实体；同章其他新实体必须用正文白话解释。首次启用且本章没有更高优先级新实体时，可用唯一名额补齐主角参考图；
- 生图前先确定目标画幅并写入提示词：人物默认 2:3，道具或徽记 1:1，宽场景或地点 16:9，横向异兽或动作画面 3:2，仅明确超长竖构图使用 9:16；catalog 的 generation_aspect_ratio 与 fanqie_crop_ratio 必须一致，并写清主体安全区；
- 把完整提示词写入本任务临时文件，执行 `python browser_image_worker.py --prompt-file <文件> --output <本书 images 分类目录的新文件> --ratio <画幅>`；网页会生成并下载原图到指定位置，catalog 记录 generated_with=chrome-web 和实际 web_provider；
- 网页 GPT 成图下载后直接采用，禁止调用 view_image 或其他 Codex 视觉能力回看内容，也不得因主观画面判断要求重生或 verified；只做文件头、SHA-256、像素画幅、分类目录、正文引用和上传回显等机械校验；
- 图片浏览器未登录、出现验证码/风控/政策提示、控件变化或连续失败时，只保留章节草稿，不得归档正文、推进状态、伪造图片或改用 Codex imagegen；
- 只有网页未产出、下载失败、文件损坏或像素画幅错误时才重试；不对网页 GPT 已完成的图片做内容复审；
- 图片文件、images/catalog.json 与章节文件属于同一批原子改动，并在结束前运行 `python -m unittest` 和管理器 validate。
必须读取 shared/reader_gate.md 并执行无大纲读者反向验收：大纲关键句只能规划方向，正文必须实际写出“承接→问题→依据→判断→行动→结果”；草稿完成后停止查看大纲、设定、连续性账本和写作提示，只读正文回答六个规定问题，每题引用逐字存在的正文证据，清零 unexplained_terms，并保存 reader_checks/NNNN.json。若必须靠作者解释才能答题，先补写正文再重新验收；缺少验收文件、正文哈希不符或未通过时，不得归档、推进状态或上传。
每次新章完成后必须建立 character_threads/NNNN/：先写 00-cast.md，再为名单中的每个人物写独立私线，写 interaction_map.md 汇总交织，最后用 state_update.md 回写所有人物的下一状态；不得只围绕主角编写。00-cast.md 必须用“## 角色名单”分节，且每名真实人物单独一行“- 人物名：...”；“主要视角、出场人物、当章目标、当章小胜负、主要钩子”等是元数据，不能写成会被解析为人物的列表项；车、锅、机甲等行动性物件放在“## 行动物件”下，不建立人物私线。人物线门禁通过前不得进入归档、推进状态或上传。

本次仅处理 `chapter_state.json` 的 next_chapter_number：
1. 写作、质检、修订并保存 drafts 与 chapters 文件；
2. 更新 chapter_state.json、continuity_ledger.md、reader_checks、batch_schedule 和当日日志；
3. 排期文件沿用 manager_config.json 的每日发布时间；补传或修改章节时不要设置定时发布，改为立即提交；
4. Metadata 的 upload_status 写为 not_uploaded；
5. 只允许 browser_image_worker.py 访问图片专用 Chrome 生成和下载配图；不访问番茄浏览器、不上传番茄、不调用 job-progress/job-finish/claim/finish；
6. 不改动 `.manager_jobs` 或 `.manager_runtime.json`；
7. 本写作子任务不上传番茄、不运行 Git；外层任务会严格按照本次 API 运行配置决定是否更新番茄正式环境以及是否同步 Git。

完成一章后立即结束，不得生成第二章。"""


def local_write_only_prompt(book_id: str, job: dict) -> str:
    return f"""使用 fanqie-auto-novel 技能，只在本地为书籍 `{book_id}` 生成并归档一章。

这是工作台 API 的本地创作子任务，job id 为 `{job["id"]}`。只处理下一章；本子任务不上传番茄、不打开浏览器、不生图、不定时发布、不运行 Git。外层任务会按照本次运行配置决定是否同步 Git。

读取 AGENTS.md、目标项目 automation_prompt.md、shared/narrative_prose_foundation.md、shared/chinese_dialogue_foundation.md、shared/chinese_dialogue_feedback.jsonl、shared/character_engine.md、shared/parallel_character_pipeline.md、shared/quality_scorecard.md、shared/reader_gate.md，以及目标项目的 novel_config.md、story_bible.md、resource_ledger.md、characters.md、world.md、style_guide.md、存在时的 narrative_style_pack.md、character_voice_bible.md、voice_packs/、continuity_ledger.md、chapter_state.json 和最近三章正文。不要读取其他书。

对白开写前先确定每对人物的关系、共同经历、当下场合、谁更有权力以及各自此刻想藏什么；称呼可以是哥、姐、老哥、大哥、兄弟、姐们、姓名、外号或直接省略，必须由关系决定，不能全书固定替换。完稿前抽查至少十句执行口语逆翻译：删掉现场不会主动交代的完整背景、书面连接词和过分清楚的步骤说明，让人物按中国人的共享语境说话，同时保留读者理解眼前行动所需的因果。

严格按人物线流程：先为每个出场人物记录独立目标、误解、底线、行动、代价和下一步，再写 interaction_map.md 和 state_update.md。interaction_map.md 可以使用 Markdown 表格，也可以使用至少 2 条带人物相互影响、行动与结果的编号交织记录。00-cast.md 只列真实人物，必须使用“## 角色名单”及逐人一行的“- 人物名：...”格式；不要把“主要视角/出场人物/当章目标/当章小胜负/主要钩子”写成角色列表项。正文必须让至少三名人物独立行动，资源必须有消耗或获得，完成一个当章小胜负并留下钩子。

新版《404修理站》从旧稿素材中重建，书名不变但旧世界观不继承。文风优先热血和现场感；对白必须按 voice_packs 写，每个人有自己的地域、年龄、职业、关系和情绪声音。允许省略、抢话、重复、损人、适量脏话和不完整句子，不能让人物说成统一的清晰书面语。

正文写入 drafts/ 和 chapters/，正文目标 2000—2600 字；补齐 reader_checks/NNNN.json。完成后停止读取设定，只凭正文回答六个读者问题，每项引用正文原句，校验正文哈希、证据和 unexplained_terms。失败就修正，不得伪造 passed。

只更新本地必要的章节、reader_checks、character_threads、continuity_ledger、chapter_state 和日志文件；Metadata 的 upload_status 写为 not_uploaded。完成一章后立即结束，不得生成第二章。最后只报告文件、字数和校验结果，不要输出正文。"""


def local_repair_prompt(
    book_id: str,
    job: dict,
    chapter_number: int,
    errors: list[str],
    repair_number: int,
) -> str:
    error_payload = json.dumps(errors, ensure_ascii=False, indent=2)
    return f"""使用 fanqie-auto-novel 技能，修复书籍 `{book_id}` 第 {chapter_number} 章现有本地稿件。

这是工作台 API 自动发起的第 {repair_number}/{MAX_AUTOMATIC_REPAIRS} 次门禁修复，job id 为 `{job['id']}`。不要另写下一章，不要删除有效情节，不要输出小说正文；只检查并定点修复现有第 {chapter_number} 章及其配套状态文件。

本轮机器校验错误如下：
{error_payload}

修复要求：
1. 先读取报错涉及的正文、reader_checks/{chapter_number:04d}.json、character_threads/{chapter_number:04d}/、chapter_state.json、continuity_ledger.md 和本书写作规范；只修改解决错误所需的文件。
2. 正文若有改动，必须最后重新生成 reader_checks/{chapter_number:04d}.json：正文哈希与最终正文完全一致，所有 evidence 必须逐字存在，因果证据必须按依据→行动原理→结果代价排列，新名词按规定及时用白话解释。
3. 修正人物线时，00-cast.md 只列真实人物；每个出场人物都有独立私线；interaction_map.md 写清人物相互影响、行动与结果；state_update.md 回写全部出场人物状态。
4. 修复完成后运行实际项目校验。只有全部门禁通过，才把 chapter_state.json 推进到第 {chapter_number} 章完成；仍有错误就继续修，不得伪造 passed。
5. 本轮不上传番茄、不打开浏览器、不生图、不定时发布、不运行 Git，也不改 `.manager_jobs` 或 `.manager_runtime.json`。

结束时只报告修复项和校验结果，不得粘贴正文。"""


def _codex_result_detail(result_file: Path) -> str:
    # Codex's final response may contain the generated chapter. Never copy that
    # response into job errors or run logs; diagnostics come from the process
    # exit code and the local gate checks instead.
    if not result_file.is_file():
        return "Codex 未写入任务结果文件；未发现章节归档"
    return "Codex 已结束但未发现章节归档；请检查本地门禁和终端错误信息"


def normalize_character_thread_dir(project: Path, chapter_number: int) -> None:
    """Normalize a uniquely matching unpadded character-thread directory."""
    parent = project / "character_threads"
    target = parent / f"{int(chapter_number):04d}"
    if target.is_dir() or not parent.is_dir():
        return
    candidates = [
        path for path in parent.iterdir()
        if path.is_dir() and path.name.isdigit()
        and int(path.name) == int(chapter_number)
    ]
    if len(candidates) == 1:
        candidates[0].rename(target)


def collect_local_archive_errors(
    project: Path,
    chapter_number: int,
    reader_gate_from: int,
    book: dict | None = None,
) -> list[str]:
    normalize_character_thread_dir(project, chapter_number)
    errors = list(manager.validate_parallel_character_threads(project, chapter_number))
    errors.extend(manager.validate_reader_checks(project, reader_gate_from))
    if book is not None:
        errors.extend(manager.validate_book(book, require_publish_complete=False))
    state = manager.read_json(project / "chapter_state.json", {})
    if int(state.get("last_completed_chapter", 0) or 0) < chapter_number:
        errors.append(
            f"第 {chapter_number} 章未正确推进 chapter_state.json；"
            "门禁通过后必须更新完成章节和下一章编号"
        )
    return list(dict.fromkeys(errors))


def enforce_local_archive_gates(
    project: Path,
    chapter_number: int,
    reader_gate_from: int,
    original_state: dict,
    book: dict | None = None,
) -> None:
    """Run all local archive gates before a chapter can be reported complete."""
    errors = collect_local_archive_errors(
        project, chapter_number, reader_gate_from, book
    )
    if errors:
        manager.write_json(project / "chapter_state.json", original_state)
        raise ArchiveGateFailure(errors)


def recoverable_draft_errors(project: Path, errors: list[str]) -> bool:
    """Allow an interrupted next-chapter draft back into the AI repair loop."""
    state = manager.read_json(project / "chapter_state.json", {})
    chapter_number = int(state.get("next_chapter_number", 0) or 0)
    if chapter_number < 1:
        return False
    chapter_exists = any((project / "chapters").glob(f"{chapter_number:04d}-*.md"))
    draft_exists = any((project / "drafts").glob(f"*{chapter_number:04d}*.md"))
    if not chapter_exists and not draft_exists:
        return False
    explicit_chapters = {
        int(value)
        for error in errors
        for value in re.findall(r"第\s*(\d+)\s*章", error)
    }
    if explicit_chapters - {chapter_number}:
        return False
    allowed_prefixes = (
        f"第 {chapter_number} 章",
        f"第{chapter_number}章",
        "归档最高章节为",
        "人物 ",
        "interaction_map.md",
        "state_update.md",
        "00-cast.md",
        "人物线",
        "并行人物线",
    )
    return bool(errors) and all(error.startswith(allowed_prefixes) for error in errors)


def write_one(book_id: str, job: dict) -> None:
    codex = resolve_codex()
    data = manager.config()
    book = manager.find_book(data, book_id)
    project = project_for(data, book_id)
    reader_gate_from = int(book["reader_gate_from_chapter"])
    expected_chapter = int(
        manager.read_json(project / "chapter_state.json")["next_chapter_number"]
    )
    original_state = manager.read_json(project / "chapter_state.json", {})
    result_file = manager.JOB_DIR / f"{job['id']}-write-{datetime.now():%H%M%S}.md"
    command = [
        codex,
        "exec",
        "--ephemeral",
        "-C",
        str(ROOT),
        "--sandbox",
        "danger-full-access",
        "--config",
        'approval_policy="never"',
        "--output-last-message",
        str(result_file),
        "-",
    ]
    prompt = local_write_prompt(book_id, job)
    repair_count = 0
    connection_retry_count = 0
    while True:
        action = "生成下一章" if repair_count == 0 else f"自动修复第 {expected_chapter} 章"
        print(f"正在调用 Codex {action}……", flush=True)
        process = subprocess.run(
            command,
            cwd=ROOT,
            input=prompt,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        state = manager.read_json(project / "chapter_state.json", {})
        chapter_advanced = int(state.get("last_completed_chapter", 0) or 0) >= expected_chapter
        candidate_exists = any(
            any((project / folder).glob(pattern))
            for folder, pattern in (
                ("chapters", f"{expected_chapter:04d}-*.md"),
                ("drafts", f"*{expected_chapter:04d}*.md"),
            )
        )
        if chapter_advanced or candidate_exists:
            try:
                enforce_local_archive_gates(
                    project,
                    expected_chapter,
                    reader_gate_from,
                    original_state,
                    book,
                )
            except ArchiveGateFailure as exc:
                if process.returncode:
                    if connection_retry_count < MAX_CODEX_PROCESS_RETRIES:
                        connection_retry_count += 1
                        print(
                            "Codex 连接中断，现有章节已保留；"
                            f"正在重试连接（{connection_retry_count}/"
                            f"{MAX_CODEX_PROCESS_RETRIES}），不消耗内容修复次数。",
                            flush=True,
                        )
                        for error in exc.errors:
                            print(f"[连接恢复后待修复] {error}", flush=True)
                        prompt = local_repair_prompt(
                            book_id,
                            job,
                            expected_chapter,
                            exc.errors,
                            min(repair_count + 1, MAX_AUTOMATIC_REPAIRS),
                        )
                        continue
                    raise RuntimeError(
                        "Codex 连接连续中断，未消耗内容修复次数；"
                        f"第 {expected_chapter} 章现有稿件已保留，可按原 job 续跑。"
                        "当前待修复：" + "；".join(exc.errors)
                    ) from exc
                if repair_count >= MAX_AUTOMATIC_REPAIRS:
                    raise RuntimeError(
                        f"第 {expected_chapter} 章自动修复 {repair_count} 次后仍未通过，"
                        "已回滚章节状态：" + "；".join(exc.errors)
                    ) from exc
                repair_count += 1
                print(
                    f"第 {expected_chapter} 章门禁未通过，正在把 {len(exc.errors)} 项问题"
                    f"回传给 AI 自动修复（{repair_count}/{MAX_AUTOMATIC_REPAIRS}）。",
                    flush=True,
                )
                for error in exc.errors:
                    print(f"[自动修复问题] {error}", flush=True)
                prompt = local_repair_prompt(
                    book_id, job, expected_chapter, exc.errors, repair_count
                )
                continue
            if chapter_advanced:
                if process.returncode:
                    print(
                        "Codex 回传中断，但新章节已通过全部本地门禁；继续执行本地流程。",
                        flush=True,
                    )
                elif repair_count:
                    print(
                        f"第 {expected_chapter} 章已由 AI 自动修复并通过全部门禁。",
                        flush=True,
                    )
                return

        if process.returncode:
            if connection_retry_count < MAX_CODEX_PROCESS_RETRIES:
                connection_retry_count += 1
                print(
                    "Codex 连接中断且尚未形成可校验稿件；"
                    f"正在重试连接（{connection_retry_count}/"
                    f"{MAX_CODEX_PROCESS_RETRIES}），不消耗内容修复次数。",
                    flush=True,
                )
                continue
            raise RuntimeError(
                "Codex 连接连续中断，且没有形成可校验的本地稿件；"
                "未消耗内容修复次数，可按原 job 续跑"
            )
        missing_errors = [
            f"第 {expected_chapter} 章没有形成可校验的本地稿件或没有正确推进章节状态"
        ]
        if repair_count < MAX_AUTOMATIC_REPAIRS:
            repair_count += 1
            print(
                f"第 {expected_chapter} 章没有完成归档，正在回传给 AI 自动修复"
                f"（{repair_count}/{MAX_AUTOMATIC_REPAIRS}）。",
                flush=True,
            )
            prompt = local_repair_prompt(
                book_id, job, expected_chapter, missing_errors, repair_count
            )
            continue
        raise RuntimeError(
            f"第 {expected_chapter} 章自动修复 {repair_count} 次后仍未形成有效归档。"
            f"任务报告：{_codex_result_detail(result_file)}"
        )


def schedule_entries(project: Path) -> list[tuple[Path, dict]]:
    output = []
    for path in manager.batch_schedule_files(project):
        payload = manager.read_json(path, {})
        for entry in payload.get("entries", []):
            output.append((path, entry))
    return output


def chapter_metadata_status(path: Path) -> str | None:
    """Read the explicit local upload status without parsing the whole chapter."""
    text = path.read_text(encoding="utf-8")
    match = re.search(r"(?m)^-\s*upload_status\s*:\s*(\S+)\s*$", text)
    return match.group(1).strip() if match else None


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


def uploaded_today_count(
    data: dict,
    project: Path,
    now: datetime | None = None,
) -> int:
    """Count platform-confirmed chapters uploaded on the configured local date."""
    current = now or manager.now_for(data)
    today = current.date().isoformat()
    return sum(
        1
        for _, entry in schedule_entries(project)
        if str(entry.get("date", "")) == today
        and str(entry.get("status", "")).strip()
        in manager.SUBMITTED_UPLOAD_STATUSES
    )


def should_publish_immediately(
    data: dict,
    book_id: str,
    project: Path,
    now: datetime | None = None,
    chapter_path: Path | None = None,
) -> bool:
    """Use immediate submission for local backfills and until today's quota is full."""
    if chapter_path is not None and chapter_metadata_status(chapter_path) in {
        "not_uploaded", "local_archived", "upload_pending"
    }:
        return True
    book = manager.find_book(data, book_id)
    target = max(1, int(book.get("daily_chapter_target", 1)))
    return uploaded_today_count(data, project, now) < target


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
        number = int(entry["chapter"])
        files = list((project / "chapters").glob(f"{number:04d}-*.md"))
        if len(files) == 1:
            # A stale schedule status must not hide a chapter whose own
            # metadata explicitly says the upload never happened.
            local_status = chapter_metadata_status(files[0])
            if (
                entry.get("status") in manager.SUBMITTED_UPLOAD_STATUSES
                and not (
                    local_status == "not_uploaded"
                    and number >= int(
                        manager.read_json(project / "chapter_state.json", {})
                        .get("last_uploaded_chapter", 0)
                    ) - 1
                )
            ):
                continue
            return schedule_path, entry, files[0]
    return None


def _title_key(title: str) -> str:
    return re.sub(r"[\s，。！？、：；‘’“”《》【】（）()\[\]{}]+", "", title).casefold()


def duplicate_title_paths(project: Path, chapter) -> list[Path]:
    """Find earlier local chapters whose titles Fanqie would reject."""
    key = _title_key(chapter.title)
    duplicates = []
    for path in sorted((project / "chapters").glob("*.md")):
        if path.resolve() == chapter.path:
            continue
        try:
            existing = parse_chapter(path)
        except (OSError, ValueError):
            continue
        if _title_key(existing.title) == key:
            duplicates.append(path)
    return duplicates


def ensure_unique_chapter_title(project: Path, chapter) -> None:
    duplicates = duplicate_title_paths(project, chapter)
    if duplicates:
        names = "、".join(path.name for path in duplicates[:3])
        raise RuntimeError(
            f"第{chapter.number}章标题《{chapter.title}》与本书已有章节重复：{names}；"
            "请修改标题后再上传"
        )


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
    immediate: bool = False,
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
                immediate=immediate,
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
    if result.get("immediate"):
        # Immediate submissions consume today's quota even when their old local
        # slot was in the future or already expired. Keep the configured slot
        # time so the next regular chapter can advance to the next day.
        target["date"] = manager.now_for(data).date().isoformat()
        target["immediate_submitted_at"] = manager.now_for(data).isoformat()
    target["status"] = local_status
    target["verified_at"] = manager.now_for(data).isoformat()
    target["fanqie_url"] = result["url"]
    target["author_note_image_uploaded"] = bool(
        result.get("author_note_image_uploaded", False)
    )
    manager.write_json(schedule_path, payload)

    state_path = project / "chapter_state.json"
    state = manager.read_json(state_path)
    previous_number = int(state.get("last_uploaded_chapter", 0) or 0)
    if number >= previous_number:
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
            f"- {'立即发布时间' if result.get('immediate') else '排期'}："
            f"{entry['date']} {resolved_time(data, book_id, entry['time'])}\n"
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


def project_file_snapshot(project: Path) -> dict[Path, tuple[int, int]]:
    """Capture enough local state to identify files changed by one writing step."""
    snapshot: dict[Path, tuple[int, int]] = {}
    for path in project.rglob("*"):
        if path.is_file():
            stat = path.stat()
            snapshot[path.resolve()] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def changed_project_files(
    project: Path, before: dict[Path, tuple[int, int]]
) -> list[Path]:
    after = project_file_snapshot(project)
    return sorted(
        path for path in set(before) | set(after) if before.get(path) != after.get(path)
    )


def git_sync(paths: list[Path], message: str) -> bool:
    unique_paths = sorted({path.resolve() for path in paths})
    relative: list[str] = []
    for path in unique_paths:
        try:
            relative.append(str(path.relative_to(ROOT)))
        except ValueError as exc:
            raise RuntimeError(f"拒绝同步项目目录外文件：{path}") from exc
    staged_before = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout.strip()
    if staged_before:
        raise RuntimeError(
            "Git 暂存区已有其他文件，为避免混入本批内容，本次没有提交；"
            "请先处理暂存区后续跑"
        )
    if relative:
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
    immediate: bool = False,
    publish_fanqie: bool | None = None,
    sync_git: bool | None = None,
) -> int:
    data = manager.config()
    book = manager.find_book(data, book_id)
    project = project_for(data, book_id)
    legacy_publish_default = book.get("mode") != "write_only"
    publish_fanqie = (
        legacy_publish_default if publish_fanqie is None else publish_fanqie
    )
    sync_git = publish_fanqie if sync_git is None else sync_git
    if resume:
        saved_options = manager.read_job(resume).get("run_options")
        if isinstance(saved_options, dict):
            publish_fanqie = bool(saved_options.get("publish_fanqie", False))
            sync_git = bool(saved_options.get("sync_git", False))
    errors = manager.validate_book(
        book, require_publish_complete=False
    )
    if errors and not recoverable_draft_errors(project, errors):
        raise RuntimeError("项目校验失败：" + "；".join(errors))
    if errors:
        print(
            "检测到上一轮遗留的下一章门禁问题；将保留现有稿件并交给 AI 自动修复。",
            flush=True,
        )
        for error in errors:
            print(f"[待自动修复] {error}", flush=True)
    if dry_run:
        print(
            json.dumps(
                {
                    "mode": "on_demand",
                    "book": book_id,
                    "target": count,
                    "delivery": {
                        "local_archive": True,
                        "publish_fanqie": publish_fanqie,
                        "sync_git": sync_git,
                    },
                    "background_polling": False,
                    "steps": [
                        "调用 Codex 写一章并完成本地质检归档；门禁失败时最多自动修复两次",
                        *([] if not publish_fanqie else [
                            "恢复未发布章节或启动专用 Chrome 上传并排期",
                            "平台列表核验",
                        ]),
                        "提交并推送本批文件" if sync_git else "不执行 Git",
                        "进程退出",
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if publish_fanqie and not PROFILE_READY.is_file():
        raise RuntimeError(
            "专用 Chrome 尚未完成首次登录配置；"
            "请先在 VS Code 终端运行 `python xiaoshuo --setup-browser`"
        )
    job = start_job(
        data, book_id, count, resume, publish_fanqie, sync_git
    )
    try:
        recovery_paths = [
            Path(item) for item in job.get("changed_paths_pending_git", [])
        ]
        if recovery_paths and sync_git:
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
            if not publish_fanqie:
                before_files = project_file_snapshot(project)
                before = manager.read_json(project / "chapter_state.json")[
                    "last_completed_chapter"
                ]
                write_one(book_id, job)
                state = manager.read_json(project / "chapter_state.json")
                after = int(state["last_completed_chapter"])
                if after != int(before) + 1:
                    raise RuntimeError("Codex 退出后未发现唯一的新章节")
                normalize_character_thread_dir(project, after)
                parallel_errors = manager.validate_parallel_character_threads(project, after)
                if parallel_errors:
                    raise RuntimeError("并行人物线门禁失败：" + "；".join(parallel_errors))
                chapter_path = next(
                    project.joinpath("chapters").glob(f"{after:04d}-*.md")
                )
                ensure_unique_chapter_title(project, parse_chapter(chapter_path))
                archive_errors = manager.validate_book(
                    book, require_publish_complete=False
                )
                if archive_errors:
                    raise RuntimeError(
                        "新章不得记录为成功，项目归档校验失败："
                        + "；".join(archive_errors)
                    )
                manager.cmd_job_progress(
                    data,
                    argparse.Namespace(
                        job=job["id"],
                        chapter=after,
                        platform_status="local_archived",
                        message=(
                            "新章已完成本地质检与本地归档；未访问番茄；"
                            + ("等待本批结束后同步 Git" if sync_git else "未执行 Git")
                        ),
                    ),
                )
                if sync_git:
                    job = manager.read_job(job["id"])
                    pending_git = {
                        Path(item).resolve()
                        for item in job.get("changed_paths_pending_git", [])
                    }
                    pending_git.update(changed_project_files(project, before_files))
                    job["changed_paths_pending_git"] = [
                        str(path) for path in sorted(pending_git)
                    ]
                    manager.write_json(manager.job_path(job["id"]), job)
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
                normalize_character_thread_dir(project, int(after))
                parallel_errors = manager.validate_parallel_character_threads(project, int(after))
                if parallel_errors:
                    raise RuntimeError("并行人物线门禁失败：" + "；".join(parallel_errors))
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
            ensure_unique_chapter_title(project, chapter)
            current_time = manager.now_for(data)
            immediate_for_chapter = should_publish_immediately(
                data, book_id, project, current_time, chapter_path
            )
            if immediate_for_chapter:
                publish_date = current_time.date().isoformat()
                publish_time = current_time.strftime("%H:%M")
                print(
                    f"当天尚未完成每日 {book.get('daily_chapter_target', 1)} 章，"
                    f"第{chapter.number}章《{chapter.title}》立即发布："
                    f"{publish_date} {publish_time}",
                    flush=True,
                )
            else:
                normalized_schedule = normalize_schedule_entry(
                    data, book_id, project, chapter.number
                )
                if normalized_schedule:
                    entry = next(
                        item
                        for item in manager.read_json(normalized_schedule)["entries"]
                        if int(item["chapter"]) == chapter.number
                    )
                    schedule_path = normalized_schedule
                    print(
                        f"当天已完成每日 {book.get('daily_chapter_target', 1)} 章，"
                        f"第{chapter.number}章顺延到 {entry['date']} {entry['time']}。",
                        flush=True,
                    )
                publish_time = resolved_time(data, book_id, str(entry["time"]))
                publish_date = str(entry["date"])
                print(
                    f"正在排期发布第{chapter.number}章《{chapter.title}》："
                    f"{publish_date} {publish_time}",
                    flush=True,
                )
            upload = publish_with_retry(
                project,
                chapter_path,
                publish_date,
                publish_time,
                debug_browser,
                immediate=immediate or immediate_for_chapter,
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
            if sync_git:
                pending_git = {
                    Path(item).resolve()
                    for item in job.get("changed_paths_pending_git", [])
                }
                pending_git.update(path.resolve() for path in changed)
                job["changed_paths_pending_git"] = [
                    str(path) for path in sorted(pending_git)
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
                        f"《{chapter.title}》{publish_date} {publish_time}，"
                        f"平台核验为{upload['status']}"
                    ),
                ),
            )
            completed += 1
        if sync_git:
            job = manager.read_job(job["id"])
            pending_paths = [
                Path(item) for item in job.get("changed_paths_pending_git", [])
            ]
            if git_sync(
                pending_paths,
                f"{book.get('title', book_id)} 按需完成 {completed} 章",
            ):
                job.pop("changed_paths_pending_git", None)
                manager.write_json(manager.job_path(job["id"]), job)
        git_push_pending = bool(job.get("changed_paths_pending_git"))
        completion_message = f"按需完成 {completed}/{job['target_chapters']} 章"
        if not publish_fanqie:
            completion_message += "；未更新番茄正式环境"
        else:
            completion_message += "；已按配置更新番茄正式环境"
        if sync_git and not git_push_pending:
            completion_message += "；Git 已同步"
        elif not sync_git:
            completion_message += "；未同步 Git"
        if git_push_pending:
            completion_message += "；Git 推送待恢复"
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
    parser.add_argument(
        "--immediate",
        action="store_true",
        help="本次上传关闭定时发布，直接提交；仅用于用户明确要求的临时补更",
    )
    git_group = parser.add_mutually_exclusive_group()
    git_group.add_argument("--sync-git", dest="sync_git", action="store_true")
    git_group.add_argument("--no-sync-git", dest="sync_git", action="store_false")
    parser.set_defaults(sync_git=None)
    publish_group = parser.add_mutually_exclusive_group()
    publish_group.add_argument(
        "--publish-fanqie", dest="publish_fanqie", action="store_true"
    )
    publish_group.add_argument(
        "--no-publish-fanqie", dest="publish_fanqie", action="store_false"
    )
    parser.set_defaults(publish_fanqie=None)
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
            args.immediate,
            args.publish_fanqie,
            args.sync_git,
        )
    except (RuntimeError, ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
