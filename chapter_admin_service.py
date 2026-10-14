"""Recoverable local chapter administration for the web workbench."""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path


NUMBERED_ARTIFACT_DIRS = (
    "chapter_plans",
    "reader_checks",
    "dialogue_reviews",
    "literary_reviews",
    "module_plans",
    "module_reports",
)
CHAPTER_FILE = re.compile(r"^(\d+)-.+\.md$")
NUMBERED_ARTIFACT = re.compile(r"^(\d+)\.(?:json|md)$")
DRAFT_CHAPTER = re.compile(r"(?:^|[-_])chapter[-_](\d{4})(?:\D|$)", re.I)
LEDGER_HEADING = re.compile(
    r"(?m)^#{1,6}\s*第\s*(\d+)\s*章(?:后|状态|状态回写|归档后)\b"
)


def _read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(content, encoding="utf-8")
    os.replace(temp, path)


def _atomic_json(path: Path, data: dict) -> None:
    _atomic_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _chapter_number(path: Path) -> int | None:
    match = CHAPTER_FILE.match(path.name)
    return int(match.group(1)) if match else None


def _collect_tail_artifacts(project: Path, from_chapter: int) -> list[Path]:
    targets: set[Path] = set()
    for path in (project / "chapters").glob("*.md"):
        number = _chapter_number(path)
        if number is not None and number >= from_chapter:
            targets.add(path)
    for directory in NUMBERED_ARTIFACT_DIRS:
        for path in (project / directory).glob("*"):
            match = NUMBERED_ARTIFACT.match(path.name)
            if path.is_file() and match and int(match.group(1)) >= from_chapter:
                targets.add(path)
    for path in (project / "character_threads").glob("*"):
        if path.is_dir() and path.name.isdigit() and int(path.name) >= from_chapter:
            targets.add(path)
    for path in (project / "drafts").glob("*.md"):
        match = DRAFT_CHAPTER.search(path.name)
        if match and int(match.group(1)) >= from_chapter:
            targets.add(path)
    for folder in (project / "reader_feedback").glob("*"):
        if not folder.is_dir():
            continue
        feedback = _read_json(folder / "feedback.json", {}) or {}
        try:
            number = int(feedback.get("chapter", 0))
        except (TypeError, ValueError):
            number = 0
        if number >= from_chapter:
            targets.add(folder)
    return sorted(targets, key=lambda path: (len(path.parts), str(path)))


def _remaining_chapters(project: Path, from_chapter: int) -> list[int]:
    numbers = []
    for path in (project / "chapters").glob("*.md"):
        number = _chapter_number(path)
        if number is not None and number < from_chapter:
            numbers.append(number)
    return sorted(set(numbers))


def _truncate_ledger(content: str, from_chapter: int) -> tuple[str, bool]:
    for match in LEDGER_HEADING.finditer(content):
        if int(match.group(1)) >= from_chapter:
            return content[: match.start()].rstrip() + "\n", True
    return content, False


def _relative_destination(trash: Path, project: Path, source: Path) -> Path:
    return trash / "files" / source.relative_to(project)


def delete_chapter_tail(
    project: Path,
    book_id: str,
    from_chapter: int,
    *,
    confirmed_chapter: int,
) -> dict:
    """Move one chapter and every later local artifact into a recoverable trash set."""
    project = project.resolve()
    if from_chapter < 1 or confirmed_chapter != from_chapter:
        raise ValueError("删除确认信息不一致")
    chapter_paths = [
        path
        for path in (project / "chapters").glob(f"{from_chapter:04d}-*.md")
        if path.is_file()
    ]
    if len(chapter_paths) != 1:
        raise ValueError(f"找不到唯一的第 {from_chapter} 章")

    state_path = project / "chapter_state.json"
    state = _read_json(state_path, {}) or {}
    uploaded = int(state.get("last_uploaded_chapter", 0) or 0)
    if uploaded >= from_chapter:
        raise ValueError(
            f"第 {from_chapter} 章或后续章节已经上传到平台，不能只从本地删除"
        )

    targets = _collect_tail_artifacts(project, from_chapter)
    deleted_chapters = sorted(
        number
        for path in targets
        if path.parent.name == "chapters"
        and (number := _chapter_number(path)) is not None
    )
    if not deleted_chapters:
        raise ValueError(f"第 {from_chapter} 章没有可删除的正文")

    trash_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    trash = project / ".chapter_trash" / trash_id
    trash.mkdir(parents=True, exist_ok=False)
    originals: dict[Path, bytes | None] = {}
    moved: list[tuple[Path, Path]] = []
    warnings: list[str] = []

    try:
        for mutable in (state_path, project / "continuity_ledger.md", project / "images" / "catalog.json"):
            originals[mutable] = mutable.read_bytes() if mutable.is_file() else None
            if mutable.is_file():
                backup = trash / "before" / mutable.relative_to(project)
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(mutable, backup)

        for source in targets:
            destination = _relative_destination(trash, project, source)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            moved.append((source, destination))

        remaining = _remaining_chapters(project, from_chapter)
        highest = remaining[-1] if remaining else 0
        next_state = dict(state)
        next_state["last_completed_chapter"] = highest
        next_state["next_chapter_number"] = highest + 1
        next_state["notes_for_next_chapter"] = (
            f"从第 {from_chapter} 章起的旧版本已移出活动项目。"
            f"请只承接现存第 {highest} 章正文与当前连续性账本，重新规划第 {highest + 1} 章。"
            if highest
            else "现无已完成章节，请按开篇合同重新生成第 1 章。"
        )
        next_state["status"] = f"chapter_{highest:04d}_archived" if highest else "ready"
        _atomic_json(state_path, next_state)

        ledger_path = project / "continuity_ledger.md"
        if ledger_path.is_file():
            ledger = ledger_path.read_text(encoding="utf-8")
            truncated, changed = _truncate_ledger(ledger, from_chapter)
            if changed:
                _atomic_text(ledger_path, truncated)
            else:
                warnings.append("连续性账本没有可识别的分章标题，已保留原文并保存删除前备份")

        catalog_path = project / "images" / "catalog.json"
        catalog = _read_json(catalog_path)
        if isinstance(catalog, dict) and isinstance(catalog.get("chapter_images"), dict):
            catalog["chapter_images"] = {
                key: value
                for key, value in catalog["chapter_images"].items()
                if not str(key).isdigit() or int(key) < from_chapter
            }
            _atomic_json(catalog_path, catalog)

        manifest = {
            "schema_version": 1,
            "trash_id": trash_id,
            "book_id": book_id,
            "deleted_from_chapter": from_chapter,
            "deleted_chapters": deleted_chapters,
            "created_at": datetime.now().astimezone().isoformat(),
            "recoverable": True,
            "moved_paths": [str(source.relative_to(project)) for source, _ in moved],
            "state_after": next_state,
            "warnings": warnings,
        }
        _atomic_json(trash / "manifest.json", manifest)
        return {
            "book_id": book_id,
            "from_chapter": from_chapter,
            "deleted_chapters": deleted_chapters,
            "next_chapter_number": highest + 1,
            "trash_id": trash_id,
            "recoverable": True,
            "warnings": warnings,
        }
    except Exception:
        for source, destination in reversed(moved):
            if destination.exists() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(destination), str(source))
        for path, prior in originals.items():
            if prior is None:
                if path.exists():
                    path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(prior)
        raise
