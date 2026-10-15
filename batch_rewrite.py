#!/usr/bin/env python3
"""Rewrite a local chapter tail with V3 in an isolated copy, then install it."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

from novel_engine_v3 import AuthorEngine, V3ValidationError
from novel_engine_v3.continuity import validate_characters, validate_director
from novel_v3 import ROOT, execute, review_and_archive

WORK_ROOT = ROOT / ".batch_rewrites"
STAGES = ("director", "characters", "author_room", "draft", "deep_edit", "reader")
EXCLUDED = {".chapter_trash", "reader_feedback", "logs", "drafts", "images", ".batch_rewrites"}


def chapter_path(project: Path, number: int) -> Path:
    matches = list((project / "chapters").glob(f"{number:04d}-*.md"))
    if len(matches) != 1:
        raise V3ValidationError(f"第 {number} 章必须有且仅有一份原文")
    return matches[0]


def validate_request(project: Path, start: int) -> int:
    state = json.loads((project / "chapter_state.json").read_text(encoding="utf-8-sig"))
    last = int(state.get("last_completed_chapter", 0))
    if start < 1 or start > last or last - start + 1 > 20:
        raise V3ValidationError("起始章必须在已归档范围内，单批最多重写 20 章")
    if int(state.get("last_uploaded_chapter", 0) or 0) >= start:
        raise V3ValidationError("重写范围内已有上传章节；请先处理平台版本")
    for number in range(start, last + 1):
        chapter_path(project, number)
    return last


def baseline(root: Path, project: Path, book_id: str, start: int) -> dict | None:
    state_path = project / "timeline_state.json"
    if not state_path.is_file():
        return None
    if start == 1:
        if book_id != "night-floor":
            raise V3ValidationError("此书缺少第一章之前的时间线基准")
        return {"last_chapter": 0, "day": 1, "floor": 6, "after_2347": False}
    current = json.loads(state_path.read_text(encoding="utf-8-sig"))
    if current.get("last_chapter") == start - 1:
        return current
    saved = project / ".rewrite_checkpoints.json"
    if saved.is_file():
        point = json.loads(saved.read_text(encoding="utf-8-sig")).get(str(start - 1))
        if isinstance(point, dict) and point.get("last_chapter") == start - 1:
            return point
    checkpoint_file = root / "novel_engine_v3" / "rewrite_checkpoints" / f"{book_id}.json"
    if not checkpoint_file.is_file():
        raise V3ValidationError(f"缺少第 {start - 1} 章时间线检查点，不能安全重写")
    checkpoints = json.loads(checkpoint_file.read_text(encoding="utf-8-sig"))
    point = checkpoints.get(str(start - 1))
    if not isinstance(point, dict) or point.get("last_chapter") != start - 1:
        raise V3ValidationError(f"缺少第 {start - 1} 章时间线检查点，不能安全重写")
    return point


def trim_ledger(content: str, start: int) -> str:
    heading = re.compile(r"(?m)^#{1,6}\s*第\s*(\d+)(?:\s*[—–-]\s*(\d+))?\s*章")
    for match in heading.finditer(content):
        if int(match.group(2) or match.group(1)) >= start:
            return content[:match.start()].rstrip() + "\n"
    return content


def trim_timeline(content: str, start: int) -> str:
    kept = []
    for line in content.splitlines():
        if line.startswith("# 时间线与楼层核对"):
            line = f"# 时间线与楼层核对（截至第 {start - 1} 章）"
        match = re.search(r"\|\s*第\s*(\d+)(?:\s*[—–-]\s*(\d+))?\s*章", line)
        if match and int(match.group(2) or match.group(1)) >= start:
            continue
        if line.startswith("写下一章前"):
            continue
        kept.append(line)
    kept.append(f"\n第 {start} 章及之后正在重写；旧版本对应的时间线记录已暂时移出工作目录。")
    return "\n".join(kept).rstrip() + "\n"


def prepare_workspace(engine: AuthorEngine, book_id: str, start: int, last: int) -> tuple[Path, Path]:
    project, _ = engine.book(book_id)
    work = WORK_ROOT / book_id / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
    stage = work / "project"
    shutil.copytree(project, stage, ignore=lambda _dir, names: [name for name in names if name in EXCLUDED])
    originals = work / "originals"
    originals.mkdir()
    for number in range(start, last + 1):
        source = chapter_path(project, number)
        shutil.copy2(source, originals / source.name)
        chapter_path(stage, number).unlink()
        for directory in ("chapter_plans", "reader_checks", "dialogue_reviews", "literary_reviews", "module_plans", "module_reports"):
            for artifact in (stage / directory).glob(f"{number:04d}.*"):
                artifact.unlink()
        shutil.rmtree(stage / "character_threads" / f"{number:04d}", ignore_errors=True)
    state = json.loads((stage / "chapter_state.json").read_text(encoding="utf-8-sig"))
    state["last_completed_chapter"] = start - 1
    state["next_chapter_number"] = start
    state["notes_for_next_chapter"] = f"从现存第 {start - 1} 章正文承接；第 {start} 章原文仅作重写参考。"
    (stage / "chapter_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    point = baseline(engine.root, project, book_id, start)
    if point is not None:
        (stage / "timeline_state.json").write_text(json.dumps(point, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ledger = stage / "continuity_ledger.md"
    if ledger.is_file():
        ledger.write_text(trim_ledger(ledger.read_text(encoding="utf-8-sig"), start), encoding="utf-8")
    timeline = stage / "timeline.md"
    if timeline.is_file():
        timeline.write_text(trim_timeline(timeline.read_text(encoding="utf-8-sig"), start), encoding="utf-8")
    (work / "manifest.json").write_text(json.dumps({"book_id": book_id, "from_chapter": start,
        "through_chapter": last, "status": "preparing", "project": str(project)}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return work, stage


def _managed_paths(project: Path, start: int, last: int) -> set[Path]:
    paths = {Path("chapter_state.json"), Path("continuity_ledger.md"), Path("timeline.md"),
             Path(".rewrite_checkpoints.json")}
    if (project / "timeline_state.json").is_file():
        paths.add(Path("timeline_state.json"))
    for number in range(start, last + 1):
        for source in (project / "chapters").glob(f"{number:04d}-*.md"):
            paths.add(source.relative_to(project))
        for directory in ("chapter_plans", "reader_checks", "dialogue_reviews", "literary_reviews", "module_plans", "module_reports"):
            for source in (project / directory).glob(f"{number:04d}.*"):
                paths.add(source.relative_to(project))
        thread = project / "character_threads" / f"{number:04d}"
        if thread.is_dir():
            paths.update(path.relative_to(project) for path in thread.rglob("*") if path.is_file())
    return paths


def project_fingerprint(project: Path, start: int, last: int) -> str:
    digest = hashlib.sha256()
    for relative in sorted(_managed_paths(project, start, last)):
        source = project / relative
        digest.update(str(relative).encode("utf-8"))
        digest.update(source.read_bytes() if source.is_file() else b"<missing>")
    return digest.hexdigest()


def install(project: Path, stage: Path, work: Path, start: int, last: int) -> None:
    before = work / "before"
    originals = _managed_paths(project, start, last)
    replacement = _managed_paths(stage, start, last)
    for relative in originals:
        source = project / relative
        if source.is_file():
            target = before / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    try:
        for relative in originals | replacement:
            target = project / relative
            source = stage / relative
            if source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            else:
                target.unlink(missing_ok=True)
    except Exception:
        for relative in originals | replacement:
            target = project / relative
            source = before / relative
            if source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            else:
                target.unlink(missing_ok=True)
        raise


def run(book_id: str, start: int) -> Path:
    engine = AuthorEngine(ROOT)
    project, _ = engine.book(book_id)
    last = validate_request(project, start)
    initial_fingerprint = project_fingerprint(project, start, last)
    work, stage = prepare_workspace(engine, book_id, start, last)
    print(f"批量重写：工作目录 {work}", flush=True)
    try:
        for number in range(start, last + 1):
            originals = list((work / "originals").glob(f"{number:04d}-*.md"))
            original = originals[0]
            prepared = engine.prepare(book_id, number, project_override=stage, original_chapter=original)
            print(f"批量重写：正在处理第 {number}/{last} 章；参考原文 {original.name}", flush=True)
            for step in STAGES:
                execute(prepared / f"{step}.md", prepared / f"{step}.result.md", prepared,
                        capture_candidate=step == "deep_edit")
                if step == "director":
                    validate_director(ROOT, stage, number)
                elif step == "characters":
                    validate_characters(stage, number, final=False)
            archived = review_and_archive(prepared, stage, number)
            review = json.loads((prepared / "continuity_review.json").read_text(encoding="utf-8-sig"))
            ledger = stage / "continuity_ledger.md"
            if ledger.is_file():
                with ledger.open("a", encoding="utf-8") as handle:
                    handle.write(f"\n## 第 {number} 章后\n\n{review['next_hook']}\n")
            timeline = stage / "timeline.md"
            chronology_path = prepared / "chronology.json"
            if timeline.is_file() and chronology_path.is_file():
                chronology = json.loads(chronology_path.read_text(encoding="utf-8-sig"))
                with timeline.open("a", encoding="utf-8") as handle:
                    handle.write(f"\n第 {number} 章：故事第 {chronology['end_day']} 天，"
                                 f"最高 {chronology['end_floor']} 楼，"
                                 f"23:47 {'后' if chronology['end_after_2347'] else '前'}。\n")
                checkpoint_path = stage / ".rewrite_checkpoints.json"
                checkpoints = json.loads(checkpoint_path.read_text(encoding="utf-8-sig")) if checkpoint_path.is_file() else {}
                checkpoints[str(number)] = json.loads((stage / "timeline_state.json").read_text(encoding="utf-8-sig"))
                checkpoint_path.write_text(json.dumps(checkpoints, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"批量重写：第 {number} 章完成候选 {archived.name}", flush=True)
        if validate_request(stage, start) != last:
            raise V3ValidationError("重写工作目录章节不完整")
        if validate_request(project, start) != last or project_fingerprint(project, start, last) != initial_fingerprint:
            raise V3ValidationError("原项目在重写期间发生变化，已保留新稿但未替换原文")
        install(project, stage, work, start, last)
        status = "completed"
        print(f"批量重写：完成第 {start}—{last} 章；原文备份 {work / 'before'}", flush=True)
    except Exception:
        status = "failed"
        raise
    finally:
        manifest = work / "manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["status"] = status
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return work


def main() -> int:
    parser = argparse.ArgumentParser(description="按最新 V3 流水线批量重写本地章节")
    parser.add_argument("--book", required=True)
    parser.add_argument("--from-chapter", type=int, required=True)
    args = parser.parse_args()
    try:
        run(args.book, args.from_chapter)
    except (V3ValidationError, OSError, ValueError) as exc:
        print(f"批量重写失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
