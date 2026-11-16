"""Pause V3 after planning, let the author revise an overview, then expand it."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
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
    run = _run(root, book_id, chapter)
    if run is None:
        return {"book_id": book_id, "chapter": chapter, "status": "none"}
    project, run, meta = _paths(root, book_id, chapter)
    overview = _read(run / "overview.md")
    plan_path = project / "chapter_plans" / f"{chapter:04d}.json"
    plan_revision = _hash(_read(plan_path))
    return {
        "book_id": book_id, "chapter": chapter, "status": meta["status"],
        "overview": overview, "revision": _hash(overview),
        "plan_stale": plan_revision != meta["plan_revision"],
        "questions": meta.get("questions", []),
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
    (run / "overview.md").write_text(overview, encoding="utf-8")
    _write_meta(run, {"status": "pending", "plan_revision": _hash(_read(plan_path)), "questions": []})
    return run


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
    _, run, _ = _check_pending(root, book_id, chapter)
    if not isinstance(content, str) or not 50 <= len(content.strip()) <= 100_000:
        raise ValueError("总览应在 50 到 100000 字符之间")
    if not re.fullmatch(r"[0-9a-f]{64}", revision or "") or _hash(_read(run / "overview.md")) != revision:
        raise ValueError("总览已在别处更新，请刷新后再保存")
    temporary = run / "overview.tmp"
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(run / "overview.md")
    return {"revision": _hash(content)}


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
        "以确认总览替换本章旧设想，逐项同步目标、人物驱动、场景、回报、结尾与视角合同。"
        "被总览删除的事件、承诺和观察位置不得继续出现在合同其他字段。"
        "总览中的未来打算不能写成前章已发生事实；与既有事实冲突应报错。"
        "不要调用工具修改文件，不生成正文。最终回复只输出完整章节合同 JSON 对象，"
        "沿用合同结构和章号，不加代码围栏；前面要求写文件的指令改为只在回复输出。\n",
        encoding="utf-8",
    )
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
    _write_meta(run, meta)
    threads = project / "character_threads" / f"{chapter:04d}"
    if threads.is_dir():
        if project.resolve() not in threads.resolve().parents:
            raise ValueError("人物线目录越界")
        backup_dir = Path(tempfile.mkdtemp(prefix="overview-characters-", dir=run))
        threads.rename(backup_dir / threads.name)
    for stage in ("characters", "author_room", "scene_seed"):
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


def expand(root: Path, book_id: str, chapter: int, revision: str) -> Path:
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
    meta["status"] = "expanding"
    meta["approved_revision"] = revision
    _write_meta(run, meta)
    try:
        sync_overview_context(root, project, run, chapter, revision, meta)
        for stale_review in ("reader_review.json", "continuity_review.json", "chronology.json"):
            (run / stale_review).unlink(missing_ok=True)
        if resume_candidate:
            print(f"复用第 {chapter:04d} 章候选稿，重新审读后归档。", flush=True)
        else:
            for stage in ("draft", "deep_edit"):
                execute(run / f"{stage}.md", run / f"{stage}.result.md", run,
                        capture_candidate=stage == "deep_edit")
            meta["candidate_revision"] = revision
            _write_meta(run, meta)
        # Refresh old run packages too; their continuity prompt predates this gate.
        from novel_v3 import OVERVIEW_CONTINUITY_CONTRACT
        continuity = run / "continuity.md"
        if OVERVIEW_CONTINUITY_CONTRACT not in _read(continuity):
            continuity.write_text(_read(continuity) + "\n" + OVERVIEW_CONTINUITY_CONTRACT, encoding="utf-8")
        archived = review_and_archive(run, project, chapter)
    except Exception:
        meta["status"] = "pending"
        _write_meta(run, meta)
        raise
    meta["status"] = "archived"
    _write_meta(run, meta)
    return archived


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "ask", "expand"))
    parser.add_argument("--book", required=True)
    parser.add_argument("--chapter", type=int, required=True)
    parser.add_argument("--question")
    parser.add_argument("--revision")
    args = parser.parse_args()
    if args.action == "prepare":
        print(f"写前总览已准备：{prepare(ROOT, args.book, args.chapter)}", flush=True)
    elif args.action == "ask":
        print(ask(ROOT, args.book, args.chapter, args.question, args.revision), flush=True)
    else:
        print(f"章节已归档：{expand(ROOT, args.book, args.chapter, args.revision)}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except V3ValidationError as exc:
        raise SystemExit(f"V3 错误：{exc}")
