"""Human-first chapter handoff: scene guide, saved draft, and polish preview."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from novel_engine_v3 import AuthorEngine
from novel_v3 import execute
from novel_engine_v3.continuity import validate_director


def _project(root: Path, book_id: str) -> Path:
    project, _ = AuthorEngine(root).book(book_id)
    return project


def _paths(root: Path, book_id: str, chapter: int) -> tuple[Path, Path, Path, Path]:
    if not isinstance(chapter, int) or isinstance(chapter, bool) or chapter < 1:
        raise ValueError("章节号无效")
    project = _project(root, book_id)
    plan = project / "chapter_plans" / f"{chapter:04d}.json"
    handoff = project / "human_drafts"
    return project, plan, handoff / f"{chapter:04d}.md", handoff / f"{chapter:04d}-preview.md"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig") if path.is_file() else ""


def workspace(root: Path, book_id: str, chapter: int) -> dict:
    project, plan_path, draft_path, preview_path = _paths(root, book_id, chapter)
    plan = json.loads(plan_path.read_text(encoding="utf-8-sig")) if plan_path.is_file() else None
    draft = _read(draft_path)
    preview = _read(preview_path)
    meta_path = preview_path.with_suffix(".json")
    meta = json.loads(meta_path.read_text(encoding="utf-8-sig")) if meta_path.is_file() else {}
    if plan and plan.get("chapter_number") != chapter:
        raise ValueError("章节规划编号不匹配")
    scenes = [] if not plan else [
        {"purpose": str(item.get("purpose", "")), "conflict": str(item.get("conflict", "")),
         "turn": str(item.get("turn", ""))}
        for item in plan.get("scene_plan", []) if isinstance(item, dict)
    ]
    existing = sorted((project / "chapters").glob(f"{chapter:04d}-*.md"))
    notes = _read(project / "human_guides" / f"{chapter:04d}.md")
    return {
        "book_id": book_id, "chapter": chapter, "has_plan": bool(plan),
        "guide": None if not plan else {
            "goal": plan.get("immediate_goal", ""), "conflict": plan.get("central_conflict", ""),
            "choice": plan.get("central_choice", ""), "payoff": plan.get("reader_payoff", {}).get("on_page_event", ""),
            "ending": plan.get("ending_pressure", {}).get("visible_trigger", ""),
            "viewpoint": plan.get("viewpoint_contract", ""), "clock": plan.get("story_clock"),
            "scenes": scenes,
        },
        "existing_chapter": existing[0].name if existing else None,
        "scene_explanation": notes,
        "draft": draft, "draft_revision": _hash(draft),
        "preview": preview if meta.get("draft_sha256") == _hash(draft) else "",
        "preview_stale": bool(preview) and meta.get("draft_sha256") != _hash(draft),
    }


def save_draft(root: Path, book_id: str, chapter: int, content: str, revision: str) -> dict:
    _, _, draft_path, preview_path = _paths(root, book_id, chapter)
    if not isinstance(content, str) or len(content) > 100_000:
        raise ValueError("手写稿长度无效")
    if not re.fullmatch(r"[0-9a-f]{64}", revision or ""):
        raise ValueError("草稿版本无效，请刷新后重试")
    if _hash(_read(draft_path)) != revision:
        raise ValueError("手写稿已在别处更新，请刷新后再保存")
    draft_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = draft_path.with_suffix(".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(draft_path)
    return {"draft_revision": _hash(content), "preview_stale": preview_path.is_file()}


def validate_polish_request(root: Path, book_id: str, chapter: int) -> None:
    _, plan_path, draft_path, _ = _paths(root, book_id, chapter)
    if not plan_path.is_file():
        raise ValueError("本章尚无章节规划，请先完成 V3 规划")
    draft = _read(draft_path)
    if sum("\u4e00" <= char <= "\u9fff" for char in draft) < 200:
        raise ValueError("请先保存至少 200 汉字的手写正文")


def validate_plan_request(root: Path, book_id: str, chapter: int) -> None:
    project, plan_path, _, _ = _paths(root, book_id, chapter)
    if plan_path.exists():
        raise ValueError("本章已有规划，请重新载入")
    state = json.loads((project / "chapter_state.json").read_text(encoding="utf-8-sig"))
    if chapter != state.get("next_chapter_number"):
        raise ValueError("只能为当前下一章生成规划")


def plan_chapter(root: Path, book_id: str, chapter: int) -> Path:
    validate_plan_request(root, book_id, chapter)
    engine = AuthorEngine(root)
    project, _ = engine.book(book_id)
    run = engine.prepare(book_id, chapter)
    execute(run / "director.md", run / "director.result.md", run)
    validate_director(root, project, chapter)
    return project / "chapter_plans" / f"{chapter:04d}.json"


def polish(root: Path, book_id: str, chapter: int) -> Path:
    validate_polish_request(root, book_id, chapter)
    project, plan_path, draft_path, preview_path = _paths(root, book_id, chapter)
    original = _read(draft_path)
    run = draft_path.parent / "polish_runs" / f"{chapter:04d}-{_hash(original)[:12]}"
    run.mkdir(parents=True, exist_ok=True)
    (run / "candidate.md").write_text(original, encoding="utf-8")
    plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    facts = {
        "viewpoint": plan.get("viewpoint_contract"),
        "story_clock": plan.get("story_clock"),
        "forbidden_shortcuts": plan.get("forbidden_shortcuts", []),
    }
    prompt = run / "polish.md"
    prompt.write_text(
        "# 作者手写稿轻润色\n\n"
        f"只读取 `{run / 'candidate.md'}`。这是作者亲自写的正文，保留其场景顺序、叙述声音、人物选择、信息量和对白意图。\n"
        "只修正会让中文读者出戏的语病、重复、信息挤在一句对白里的问题；优先删去不必说出口的信息，"
        "必要时用现有动作承接。不要补新情节、新设定、新解释，不要改写成统一的模型口吻。\n"
        "每句对白只保留此刻对方需要听见、并会改变其下一步反应的内容。原稿有意留下的停顿和含混不要补齐。\n"
        f"只用于核错的事实边界：{json.dumps(facts, ensure_ascii=False)}\n"
        "不要调用工具修改文件。最终回复只输出完整 Markdown 章节，从 # 标题开始，不加说明或代码围栏。\n",
        encoding="utf-8",
    )
    execute(prompt, run / "polish.result.md", run, capture_candidate=True)
    if _hash(_read(draft_path)) != _hash(original):
        raise ValueError("润色期间手写稿已更新；预览保留在运行包中，请重新启动润色")
    preview_path.write_text(_read(run / "candidate.md"), encoding="utf-8")
    preview_path.with_suffix(".json").write_text(
        json.dumps({"draft_sha256": _hash(original), "chapter": chapter}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return preview_path
