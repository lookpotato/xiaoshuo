"""Pause V3 after planning, let the author revise an overview, then expand it."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from novel_engine_v3 import AuthorEngine, V3ValidationError
from novel_engine_v3.continuity import validate_characters, validate_timeline_head, validate_viewpoint_contract
from novel_v3 import ensure_director_plan, execute, review_and_archive, validate_scene_seed
from novel_stage_pipeline import planning_context_sha256, validate_plan


ROOT = Path(__file__).resolve().parent
ORIGINAL_TABLE_NOTE = (
    "这里的‘原表’是街道手里的二零二旧登记表：二零二一栏写着‘已退出’，"
    "但刘桂芳仍实际居住。街道已暂停按空户处理，原表尚未改正。"
    "表上的旧凭据号与郭冬梅母亲的收条末四位相同，错记的来历还没查清。"
    "扩写时让读者从当场可见的表格、条目和人物对话中理解这些关系，不要只用‘原表’这个简称带过。"
)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig") if path.is_file() else ""


def compare_overviews(before: str, after: str) -> dict:
    changes = []
    old, new = before.splitlines(), after.splitlines()
    for tag, a, b, c, d in SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag != "equal":
            changes.append({"kind": {"replace": "修改", "delete": "删除", "insert": "新增"}[tag],
                            "before": "\n".join(old[a:b]), "after": "\n".join(new[c:d])})
    return {"before_revision": _hash(before), "after_revision": _hash(after),
            "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "changes": changes, "count": len(changes)}


def _progress(run: Path, meta: dict, stage: str) -> None:
    meta["progress"] = {"stage": stage, "updated_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    _write_meta(run, meta)
    print(f"[总览接力] {stage}", flush=True)


def _run(root: Path, book_id: str, chapter: int) -> Path | None:
    parent = root / ".novel_runs_v3" / book_id
    if not parent.is_dir():
        return None
    for run in sorted(parent.glob(f"{chapter:04d}-*"), reverse=True):
        if (run / "overview_handoff.json").is_file():
            return run
    return None


def _paths(root: Path, book_id: str, chapter: int) -> tuple[Path, Path, dict]:
    if not isinstance(chapter, int) or isinstance(chapter, bool) or chapter < 1:
        raise ValueError("章节号无效")
    project, _ = AuthorEngine(root).book(book_id)
    run = _run(root, book_id, chapter)
    if run is None:
        raise ValueError("本章尚未生成待确认总览")
    manifest = json.loads(_read(run / "manifest.json"))
    if manifest.get("book_id") != book_id or manifest.get("chapter") != chapter or Path(manifest.get("project", "")).resolve() != project:
        raise ValueError("运行包与所选作品不一致")
    return project, run, json.loads(_read(run / "overview_handoff.json"))


def workspace(root: Path, book_id: str, chapter: int) -> dict:
    AuthorEngine(root).book(book_id)
    previous = _run(root, book_id, chapter - 1) if chapter > 1 else None
    last_report = json.loads(_read(previous / "overview_handoff.json")) if previous else {}
    run = _run(root, book_id, chapter)
    if run is None:
        return {"book_id": book_id, "chapter": chapter, "status": "none", "last_report": last_report.get("result_summary")}
    project, run, meta = _paths(root, book_id, chapter)
    overview = _read(run / "overview.md")
    plan_path = project / "chapter_plans" / f"{chapter:04d}.json"
    plan_revision = _hash(_read(plan_path))
    comparison = meta.get("change_summary")
    if (not comparison and meta.get("status") == "pending" and not meta.get("context_revision")
            and plan_revision == meta.get("plan_revision")):
        baseline = _read(run / "overview.original.md")
        if not baseline and plan_path.is_file():
            baseline = _render_overview(json.loads(_read(plan_path)), run, chapter)
        if baseline and baseline != overview:
            comparison = compare_overviews(baseline, overview)
            comparison["baseline"] = "与初始规划生成的总览对比"
    return {
        "book_id": book_id, "chapter": chapter, "status": meta["status"],
        "overview": overview, "revision": _hash(overview),
        "plan_stale": plan_revision != meta["plan_revision"],
        "questions": meta.get("questions", []),
        "change_summary": comparison, "progress": meta.get("progress"),
        "plan_change_summary": meta.get("plan_change_summary"),
        "understanding": meta.get("understanding"),
        "learning": meta.get("learning"),
        "result_summary": meta.get("result_summary"),
        "last_report": last_report.get("result_summary"),
        "term_note": ORIGINAL_TABLE_NOTE if book_id == "night-floor" and "原表" in overview else "",
        "run_dir": str(run),
    }


def validate_prepare(root: Path, book_id: str, chapter: int) -> None:
    project, _ = AuthorEngine(root).book(book_id)
    state = json.loads(_read(project / "chapter_state.json"))
    if chapter != state.get("next_chapter_number"):
        raise ValueError("只能为当前下一章生成总览")
    existing = _run(root, book_id, chapter)
    if existing:
        meta = json.loads(_read(existing / "overview_handoff.json"))
        current_plan = _hash(_read(project / "chapter_plans" / f"{chapter:04d}.json"))
        if meta.get("plan_revision") == current_plan:
            raise ValueError("本章已有接力总览，请先继续现有流程")
    validate_timeline_head(project, chapter)


def prepare(root: Path, book_id: str, chapter: int) -> Path:
    validate_prepare(root, book_id, chapter)
    engine = AuthorEngine(root)
    project, _ = engine.book(book_id)
    run = engine.prepare(book_id, chapter)
    ensure_director_plan(run, project, chapter)
    engine.write_prose_brief(run)
    for stage in ("characters", "author_room", "scene_seed"):
        execute(run / f"{stage}.md", run / f"{stage}.result.md", run)
        if stage == "characters":
            validate_characters(project, chapter, final=False)
            validate_viewpoint_contract(root, project, chapter)
        elif stage == "scene_seed":
            validate_scene_seed(run)
    plan_path = project / "chapter_plans" / f"{chapter:04d}.json"
    plan = json.loads(_read(plan_path))
    overview = _render_overview(plan, run, chapter)
    (run / "overview.md").write_text(overview, encoding="utf-8")
    (run / "overview.original.md").write_text(overview, encoding="utf-8")
    _write_meta(run, {"status": "pending", "plan_revision": _hash(_read(plan_path)), "questions": []})
    return run


def _render_overview(plan: dict, run: Path, chapter: int) -> str:
    scenes = "\n".join(
        f"{index}. {item.get('purpose', '')}；阻力：{item.get('conflict', '')}；变化：{item.get('turn', '')}"
        for index, item in enumerate(plan.get("scene_plan", []), 1) if isinstance(item, dict)
    )
    overview = (
        f"# 第 {chapter} 章写前总览\n\n"
        f"## 本章要做的事\n{plan.get('immediate_goal', '')}\n\n"
        f"## 冲突与选择\n{plan.get('central_conflict', '')}\n{plan.get('central_choice', '')}\n\n"
        f"## 场景推进\n{scenes}\n\n"
        f"## 读者看见的结果\n{plan.get('reader_payoff', {}).get('on_page_event', '')}\n\n"
        f"## 作者对人物与现场的理解\n{_read(run / 'author_room.md').strip()}\n\n"
        "## 结尾压力\n"
        f"{plan.get('ending_pressure', {}).get('visible_trigger', '')}\n"
    )
    return overview


def _write_meta(run: Path, meta: dict) -> None:
    path = run / "overview_handoff.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _check_pending(root: Path, book_id: str, chapter: int, *, allow_expanding: bool = False) -> tuple[Path, Path, dict]:
    project, run, meta = _paths(root, book_id, chapter)
    if meta.get("status") != "pending" and not (allow_expanding and meta.get("status") == "expanding"):
        raise ValueError("这份总览已进入扩写或归档，不能再次修改")
    state = json.loads(_read(project / "chapter_state.json"))
    if state.get("next_chapter_number") != chapter:
        raise ValueError("下一章编号已变化，请刷新作品状态")
    if _hash(_read(project / "chapter_plans" / f"{chapter:04d}.json")) != meta.get("plan_revision"):
        raise ValueError("章节规划已变化，请重新生成总览")
    return project, run, meta


def validate_pending(root: Path, book_id: str, chapter: int, action: str,
                     revision: str, question: str | None = None) -> None:
    _, run, _ = _check_pending(root, book_id, chapter, allow_expanding=action == "expand")
    if _hash(_read(run / "overview.md")) != revision:
        raise ValueError("总览版本已变化，请刷新后重试")
    if action == "ask" and (not isinstance(question, str) or not 2 <= len(question.strip()) <= 2000):
        raise ValueError("问题长度无效")
    if action == "expand" and len(_read(run / "overview.md").strip()) < 50:
        raise ValueError("请先完成写前总览")


def save(root: Path, book_id: str, chapter: int, content: str, revision: str) -> dict:
    _, run, meta = _check_pending(root, book_id, chapter)
    if not isinstance(content, str) or not 50 <= len(content.strip()) <= 100_000:
        raise ValueError("总览应在 50 到 100000 字符之间")
    if not re.fullmatch(r"[0-9a-f]{64}", revision or "") or _hash(_read(run / "overview.md")) != revision:
        raise ValueError("总览已在别处更新，请刷新后再保存")
    temporary = run / "overview.tmp"
    before = _read(run / "overview.md")
    if not (run / "overview.original.md").exists():
        (run / "overview.original.md").write_text(before, encoding="utf-8")
    temporary.write_text(content, encoding="utf-8")
    (run / "overview.previous.md").write_text(before, encoding="utf-8")
    temporary.replace(run / "overview.md")
    if before != content or not meta.get("change_summary"):
        meta["change_summary"] = compare_overviews(before, content)
    if before != content:
        meta["understanding"] = {"revision": _hash(content), "status": "pending", "text": ""}
    _write_meta(run, meta)
    return {"revision": _hash(content), "change_summary": meta["change_summary"], "understanding": meta.get("understanding")}


def summarize(root: Path, book_id: str, chapter: int, revision: str) -> str:
    project, run, meta = _check_pending(root, book_id, chapter)
    if _hash(_read(run / "overview.md")) != revision:
        raise ValueError("总览版本已变化，请针对当前总览重新总结")
    meta["understanding"] = {"revision": revision, "status": "running", "text": ""}
    _progress(run, meta, "AI 正在分析修改意见，整理理解与后续影响")
    prompt = run / f"understanding-{revision}.prompt.md"
    output = run / f"understanding-{revision}.result.md"
    recent = sorted((project / "chapters").glob("*.md"))
    recent = [p for p in recent if re.match(r"^\d+-", p.name) and int(p.name.split("-", 1)[0]) < chapter][-2:]
    prompt.write_text(
        "# 向副作者说明你如何理解本次剧情修改\n\n"
        f"当前已保存总览：`{run / 'overview.md'}`；上一保存版本：`{run / 'overview.previous.md'}`；"
        f"最初总览（如存在）：`{run / 'overview.original.md'}`；原章节规划：`{project / 'chapter_plans' / f'{chapter:04d}.json'}`。\n"
        f"核对作品世界规则 `{project / 'world.md'}`、接力状态 `{project / 'chapter_state.json'}` 与近期正文："
        + "、".join(f"`{p}`" for p in recent) + "。不存在的文件跳过。\n\n"
        "用户需要审核你是否真正理解并吸收修改，请用自然中文说明，不能只罗列字符差异或空泛说‘已吸收’。"
        "逐项写清：用户修改了什么、旧安排为何有问题、你现在理解的因果/空间/人物动机、准备在本章及后续怎样落实、哪些旧情节必须删除不再沿用。"
        "每项引用当前总览或前章正文的短句并注明来源，区分正文已经发生、当前章拟写和未来待办。"
        "用户未表达的意图不能替他认定。若修改仍有冲突、歧义或未吸收的要求，直说原因与需要澄清之处，不要为了显得接受而全盘赞同。\n"
        "最后明确列出‘已理解、待扩写时落实’与‘仍有疑问’。当前操作仅分析：规划、人物线与后续接力尚未同步，"
        "绝不能声称已经写入或已完成剧情更新，也不要直接替用户扩写或改任何文件。\n"
        "只输出供用户阅读的 Markdown 总结，建议400—900个汉字，不输出代码差异或JSON。\n",
        encoding="utf-8",
    )
    try:
        execute(prompt, output, run, read_only=True)
        response = _read(output).strip()
        if len(response) < 80:
            raise ValueError("AI 理解总结为空或过短，请重新生成")
        _, _, latest = _check_pending(root, book_id, chapter)
        if _hash(_read(run / "overview.md")) != revision:
            raise ValueError("总结期间总览已修改，旧总结不适用于当前版本")
    except Exception as exc:
        _, _, latest = _paths(root, book_id, chapter)
        if _hash(_read(run / "overview.md")) == revision:
            latest["understanding"] = {"revision": revision, "status": "failed", "text": "", "error": str(exc)[:300]}
            _progress(run, latest, "AI 理解总结失败，保存内容保留，可重新总结")
        raise
    latest["understanding"] = {"revision": revision, "status": "ready", "text": response}
    _progress(run, latest, "修改分析已生成，可直接保存总览并扩写")
    return response


def ask(root: Path, book_id: str, chapter: int, question: str, revision: str) -> str:
    project, run, meta = _check_pending(root, book_id, chapter)
    if not isinstance(question, str) or not 2 <= len(question.strip()) <= 2000:
        raise ValueError("问题长度无效")
    if _hash(_read(run / "overview.md")) != revision:
        raise ValueError("请先保存当前总览，再向作者提问")
    index = len(meta.get("questions", [])) + 1
    prompt = run / f"overview_question_{index}.md"
    answer = run / f"overview_answer_{index}.md"
    prompt.write_text(
        "# 写前作者问答\n\n"
        f"阅读总览 `{run / 'overview.md'}`、章节合同 `{project / 'chapter_plans' / f'{chapter:04d}.json'}`、"
        f"作者房间 `{run / 'author_room.md'}` 与场景粗稿 `{run / 'scene_seed.md'}`。\n"
        "你是本书作者。针对用户指出的不合理处，简短、具体地回答；承认真实漏洞并提出可行改法。"
        "不要替用户改总览，不要写章节正文，不要修改任何文件。\n\n"
        f"用户的问题：\n{question.strip()}\n",
        encoding="utf-8",
    )
    exchange = {"question": question.strip(), "answer": "", "status": "running"}
    meta.setdefault("questions", []).append(exchange)
    _write_meta(run, meta)
    try:
        execute(prompt, answer, run, read_only=True)
        response = _read(answer).strip()
        if not response:
            raise ValueError("作者没有返回回答，请重试")
    except Exception:
        exchange["status"] = "failed"
        _write_meta(run, meta)
        raise
    exchange.update({"answer": response, "status": "answered"})
    _write_meta(run, meta)
    return response


def sync_overview_context(root: Path, project: Path, run: Path, chapter: int,
                          revision: str, meta: dict) -> None:
    """Rebuild planning inputs before prose can reuse superseded story decisions."""
    if meta.get("context_revision") == revision:
        return
    plan_path = project / "chapter_plans" / f"{chapter:04d}.json"
    original = plan_path.read_bytes()
    backup = run / "plan.before_overview.json"
    if not backup.exists():
        backup.write_bytes(original)
    prompt = run / "overview_sync.md"
    result = run / "overview_sync.result.json"
    prompt.write_text(
        _read(run / "director.md") + "\n\n# 作者确认后的规划同步\n"
        f"读取 `{run / 'approved_overview.md'}` 与当前合同 `{plan_path}`、前章正文和世界规则。"
        f"如有 `{run / 'approved_understanding.md'}`，读取用户已审阅的理解总结以保持修改意图；以确认总览和既有正文为准，不能把疑问和建议自动当成既定事实。"
        "以确认总览替换本章旧设想，逐项同步目标、人物驱动、场景、回报、结尾与视角合同。"
        "被总览删除的事件、承诺和观察位置不得继续出现在合同其他字段。"
        "总览中的未来打算不能写成前章已发生事实；与既有事实冲突应报错。"
        "不要调用工具修改文件，不生成正文。最终回复只输出完整章节合同 JSON 对象，"
        "沿用合同结构和章号，不加代码围栏；前面要求写文件的指令改为只在回复输出。\n",
        encoding="utf-8",
    )
    _progress(run, meta, "正在对比确认总览并同步本章规划")
    execute(prompt, result, run, read_only=True)
    plan = json.loads(_read(result))
    if not isinstance(plan, dict) or plan.get("chapter_number") != chapter:
        raise ValueError("总览同步返回的章节合同章号错误")
    plan["planning_context_sha256"] = planning_context_sha256(root, project, chapter)
    try:
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        validate_plan(root, project, chapter)
    except Exception:
        plan_path.write_bytes(original)
        raise
    meta["plan_revision"] = _hash(_read(plan_path))
    meta["plan_change_summary"] = compare_overviews(original.decode("utf-8-sig"), _read(plan_path))
    _write_meta(run, meta)
    threads = project / "character_threads" / f"{chapter:04d}"
    if threads.is_dir():
        if project.resolve() not in threads.resolve().parents:
            raise ValueError("人物线目录越界")
        backup_dir = Path(tempfile.mkdtemp(prefix="overview-characters-", dir=run))
        threads.rename(backup_dir / threads.name)
    for stage in ("characters", "author_room", "scene_seed"):
        _progress(run, meta, {"characters": "正在重建人物线", "author_room": "正在更新人物与现场理解", "scene_seed": "正在重写场景粗稿"}[stage])
        refreshed = run / f"{stage}.overview.md"
        refreshed.write_text(
            _read(run / f"{stage}.md") + "\n\n"
            f"先读取 `{run / 'approved_overview.md'}` 和刚同步的章节合同。"
            "按确认总览重新建立本章材料，覆盖旧版本；旧稿已删除的情节与承诺不得沿用。"
            "人物名单与私线须服从修改后的实际出场安排。\n", encoding="utf-8",
        )
        execute(refreshed, run / f"{stage}.overview.result.md", run)
        if stage == "characters":
            validate_characters(project, chapter, final=False)
            validate_viewpoint_contract(root, project, chapter)
        elif stage == "scene_seed":
            validate_scene_seed(run)
    meta["context_revision"] = revision
    _write_meta(run, meta)


def learn_from_overview(project: Path, run: Path, chapter: int, revision: str, meta: dict) -> dict:
    chapters = list((project / "chapters").glob(f"{chapter:04d}-*.md"))
    if len(chapters) != 1:
        raise ValueError("须先完成章节归档，再总结长期经验")
    storage = project / "overview_learning.json"
    saved = json.loads(_read(storage)) if storage.is_file() else {"schema_version": 1, "entries": []}
    key = f"{chapter:04d}-{revision}"
    existing = next((entry for entry in saved["entries"] if entry.get("id") == key), None)
    if existing:
        return existing
    _progress(run, meta, "正文已归档，正在提炼本次修改的长期经验")
    prompt, output = run / "long_term_learning.prompt.md", run / "long_term_learning.result.json"
    prompt.write_text(
        "# 从用户修改总览中提炼长期经验\n"
        f"阅读确认总览 `{run / 'approved_overview.md'}`、旧规划 `{run / 'plan.before_overview.json'}`、"
        f"原总览 `{run / 'overview.original.md'}`（存在时）、最终归档正文 `{chapters[0]}`、已有经验 `{storage}`（存在时）。\n"
        "说明用户纠正了什么问题、为什么不合理、以后规划怎样避免。只提炼有修改证据且可跨章复用的原则，"
        "包括空间因果、人物主动性、事件兑现和救援行动等确有证据的问题；不要机械记住具体楼层、站位或人物动作。"
        "每条必须注明适用条件和误用边界；不能将未发生的情节变成事实，也不能把一次选择说成所有章节必须照办。"
        "不要空泛宣称吸收，不修改任何文件。只输出JSON："
        '{"summary":"供用户审阅的自然中文总结：发现的问题、吸收的原则、后续怎样应用",'
        '"lessons":[{"problem":"旧问题","principle":"以后应怎样规划","applies_when":"适用条件",'
        '"avoid":"误用边界","evidence":"确认总览或归档正文中的逐字短句"}]}。'
        "没有可复用修改则 lessons 为[]并说明，不编造；最多8条。\n", encoding="utf-8",
    )
    execute(prompt, output, run, read_only=True)
    report = json.loads(_read(output))
    if not isinstance(report, dict) or not isinstance(report.get("summary"), str) or not report["summary"].strip():
        raise ValueError("长期总结缺少有效说明")
    lessons = report.get("lessons")
    if not isinstance(lessons, list) or len(lessons) > 8:
        raise ValueError("长期经验格式无效")
    evidence_source = _read(run / "approved_overview.md") + "\n" + _read(chapters[0])
    for item in lessons:
        if (not isinstance(item, dict) or not all(isinstance(item.get(k), str) and item[k].strip()
                for k in ("problem", "principle", "applies_when", "avoid", "evidence"))
                or item["evidence"] not in evidence_source):
            raise ValueError("长期经验缺少适用边界或修改依据")
    entry = {"id": key, "chapter": chapter, "revision": revision,
             "summary": report["summary"], "lessons": lessons}
    saved["entries"].append(entry)
    temporary = storage.with_suffix(".tmp")
    temporary.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(storage)
    return entry


def expand(root: Path, book_id: str, chapter: int, revision: str) -> Path:
    validate_pending(root, book_id, chapter, "expand", revision)
    project, run, meta = _check_pending(root, book_id, chapter, allow_expanding=True)
    overview = _read(run / "overview.md")
    if _hash(overview) != revision:
        raise ValueError("总览版本已变化，请刷新后再扩写")
    if len(overview.strip()) < 50:
        raise ValueError("请先完成写前总览")
    approved = overview
    if book_id == "night-floor" and "原表" in overview:
        approved += "\n\n## 扩写时需要讲清的旧表\n" + ORIGINAL_TABLE_NOTE + "\n"
    resume_candidate = (
        meta.get("candidate_revision") == revision
        and (run / "candidate.md").is_file()
    )
    (run / "approved_overview.md").write_text(approved, encoding="utf-8")
    # An earlier optional analysis must not become a mandatory approval or stale input.
    (run / "approved_understanding.md").unlink(missing_ok=True)
    meta["status"] = "expanding"
    meta["approved_revision"] = revision
    _write_meta(run, meta)
    try:
        _progress(run, meta, "开始同步总览与剧情规划")
        sync_overview_context(root, project, run, chapter, revision, meta)
        for stale_review in ("reader_review.json", "continuity_review.json", "chronology.json"):
            (run / stale_review).unlink(missing_ok=True)
        if resume_candidate:
            print(f"复用第 {chapter:04d} 章候选稿，重新审读后归档。", flush=True)
        else:
            for stage in ("draft", "deep_edit"):
                _progress(run, meta, "正在扩写章节正文" if stage == "draft" else "正在编辑章节正文")
                execute(run / f"{stage}.md", run / f"{stage}.result.md", run,
                        capture_candidate=stage == "deep_edit")
            meta["candidate_revision"] = revision
            _write_meta(run, meta)
        # Refresh old run packages too; their continuity prompt predates this gate.
        from novel_v3 import OVERVIEW_CONTINUITY_CONTRACT
        continuity = run / "continuity.md"
        if OVERVIEW_CONTINUITY_CONTRACT not in _read(continuity):
            continuity.write_text(_read(continuity) + "\n" + OVERVIEW_CONTINUITY_CONTRACT, encoding="utf-8")
        _progress(run, meta, "正在审稿、核对连续性并更新后续接力")
        prior_hook = json.loads(_read(project / "chapter_state.json")).get("notes_for_next_chapter", "")
        archived = review_and_archive(run, project, chapter)
    except Exception as exc:
        meta["status"] = "pending"
        _progress(run, meta, f"任务失败，可重试：{str(exc)[:300]}")
        raise
    meta["status"] = "archived"
    state = json.loads(_read(project / "chapter_state.json"))
    superseded = run / "superseded_future"
    meta["result_summary"] = {"chapter": chapter, "next_hook": state.get("notes_for_next_chapter", ""),
                              "handoff_change_summary": compare_overviews(prior_hook, state.get("notes_for_next_chapter", "")),
                              "invalidated": [str(p.relative_to(superseded)) for p in superseded.rglob("*") if p.is_file()] if superseded.exists() else [],
                              "change_summary": meta.get("change_summary"), "plan_change_summary": meta.get("plan_change_summary"),
                              "understanding": meta.get("understanding")}
    meta["learning"] = {"status": "running"}
    meta["result_summary"]["learning"] = meta["learning"]
    _write_meta(run, meta)
    try:
        entry = learn_from_overview(project, run, chapter, revision, meta)
        meta["learning"] = {"status": "ready", **entry}
    except Exception as exc:
        meta["learning"] = {"status": "failed", "error": str(exc)[:300]}
    meta["result_summary"]["learning"] = meta["learning"]
    _progress(run, meta, "扩写、归档及长期经验总结已完成" if meta["learning"]["status"] == "ready"
              else "扩写归档已完成；长期经验总结失败，尚未写入经验库")
    return archived


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "ask", "summarize", "expand"))
    parser.add_argument("--book", required=True)
    parser.add_argument("--chapter", type=int, required=True)
    parser.add_argument("--question")
    parser.add_argument("--revision")
    args = parser.parse_args()
    if args.action == "prepare":
        print(f"写前总览已准备：{prepare(ROOT, args.book, args.chapter)}", flush=True)
    elif args.action == "ask":
        print(ask(ROOT, args.book, args.chapter, args.question, args.revision), flush=True)
    elif args.action == "summarize":
        summarize(ROOT, args.book, args.chapter, args.revision)
        print("AI 理解总结已生成，请在页面审阅。", flush=True)
    else:
        print(f"章节已归档：{expand(ROOT, args.book, args.chapter, args.revision)}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except V3ValidationError as exc:
        raise SystemExit(f"V3 错误：{exc}")
