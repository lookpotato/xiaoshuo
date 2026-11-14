"""Pause V3 after planning, let the author revise an overview, then expand it."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from novel_engine_v3 import AuthorEngine
from novel_engine_v3.continuity import validate_characters, validate_timeline_head, validate_viewpoint_contract
from novel_v3 import ensure_director_plan, execute, review_and_archive, validate_scene_seed


ROOT = Path(__file__).resolve().parent


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
    execute(prompt, answer, run, read_only=True)
    response = _read(answer).strip()
    if not response:
        raise ValueError("作者没有返回回答，请重试")
    meta["questions"].append({"question": question.strip(), "answer": response})
    _write_meta(run, meta)
    return response


def expand(root: Path, book_id: str, chapter: int, revision: str) -> Path:
    project, run, meta = _check_pending(root, book_id, chapter, allow_expanding=True)
    overview = _read(run / "overview.md")
    if _hash(overview) != revision:
        raise ValueError("总览版本已变化，请刷新后再扩写")
    if len(overview.strip()) < 50:
        raise ValueError("请先完成写前总览")
    (run / "approved_overview.md").write_text(overview, encoding="utf-8")
    meta["status"] = "expanding"
    meta["approved_revision"] = revision
    _write_meta(run, meta)
    try:
        for stage in ("draft", "deep_edit"):
            execute(run / f"{stage}.md", run / f"{stage}.result.md", run,
                    capture_candidate=stage == "deep_edit")
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
    main()
