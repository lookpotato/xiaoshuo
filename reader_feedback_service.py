"""Structured real-reader feedback with author-governed revision proposals."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path


CATEGORIES = {
    "uncomfortable": "读着不舒服",
    "confusing": "看不懂",
    "character_voice": "人物不像本人",
    "ai_flavor": "AI味明显",
    "pacing": "节奏不对",
    "emotion": "情绪不对",
    "other": "其他",
}
SAFE_ID = re.compile(r"^[0-9A-Za-z_-]{8,80}$")
MAX_QUOTE_CHARS = 3000
MAX_COMMENT_CHARS = 5000


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temp, path)


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(value, encoding="utf-8")
    os.replace(temp, path)


def _book(root: Path, book_id: str) -> tuple[dict, Path]:
    config = read_json(root / "manager_config.json", {})
    books = config.get("books", []) if isinstance(config, dict) else []
    book = next((item for item in books if item.get("id") == book_id), None)
    if not isinstance(book, dict):
        raise ValueError(f"未知书籍 id: {book_id}")
    project = (root / str(book.get("path", ""))).resolve()
    root = root.resolve()
    if root != project and root not in project.parents:
        raise ValueError("书籍路径越界")
    return book, project


def _chapter(project: Path, number: int) -> Path:
    matches = list((project / "chapters").glob(f"{number:04d}-*.md"))
    if len(matches) != 1:
        raise ValueError(f"找不到唯一的第 {number} 章")
    return matches[0]


def _narrative(text: str) -> str:
    marker = re.search(r"\n---\s*\n+\s*##\s+Metadata\b", text, re.I)
    return (text[: marker.start()] if marker else text).strip()


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _feedback_dir(root: Path, book_id: str, feedback_id: str) -> Path:
    if not SAFE_ID.fullmatch(feedback_id):
        raise ValueError("feedback id 格式不正确")
    _, project = _book(root, book_id)
    return project / "reader_feedback" / feedback_id


def create_feedback(root: Path, payload: dict) -> dict:
    book_id = str(payload.get("book_id", "")).strip()
    try:
        chapter = int(payload.get("chapter", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("章节号无效") from exc
    category = str(payload.get("category", "uncomfortable")).strip()
    quote = str(payload.get("quote", "")).strip()
    comment = str(payload.get("comment", "")).strip()
    if category not in CATEGORIES:
        raise ValueError("反馈类型无效")
    if not 1 <= chapter <= 100000:
        raise ValueError("章节号无效")
    if not comment or len(comment) > MAX_COMMENT_CHARS:
        raise ValueError(f"读者留言必须为1—{MAX_COMMENT_CHARS}字")
    if len(quote) > MAX_QUOTE_CHARS:
        raise ValueError(f"选中原文不能超过{MAX_QUOTE_CHARS}字")
    book, project = _book(root, book_id)
    chapter_path = _chapter(project, chapter)
    source = chapter_path.read_text(encoding="utf-8")
    if quote and _compact(quote) not in _compact(_narrative(source)):
        raise ValueError("选中原文不属于当前章节，请重新选择")
    feedback_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    folder = project / "reader_feedback" / feedback_id
    data = {
        "schema_version": 1,
        "id": feedback_id,
        "book_id": book_id,
        "book_title": str(book.get("title", book_id)),
        "chapter": chapter,
        "chapter_file": str(chapter_path.resolve()),
        "chapter_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "category": category,
        "category_label": CATEGORIES[category],
        "quote": quote,
        "comment": comment,
        "created_at": datetime.now().astimezone().isoformat(),
    }
    atomic_json(folder / "feedback.json", data)
    atomic_json(folder / "status.json", {
        "status": "queued",
        "message": "已收到真实读者反馈，等待作者判断",
        "updated_at": datetime.now().astimezone().isoformat(),
    })
    return feedback_item(root, book_id, feedback_id)


def update_status(root: Path, book_id: str, feedback_id: str, **changes) -> dict:
    folder = _feedback_dir(root, book_id, feedback_id)
    status = read_json(folder / "status.json", {}) or {}
    status.update(changes)
    status["updated_at"] = datetime.now().astimezone().isoformat()
    atomic_json(folder / "status.json", status)
    return status


def feedback_item(root: Path, book_id: str, feedback_id: str) -> dict:
    folder = _feedback_dir(root, book_id, feedback_id)
    feedback = read_json(folder / "feedback.json")
    if not isinstance(feedback, dict) or feedback.get("book_id") != book_id:
        raise ValueError("找不到反馈记录")
    status = read_json(folder / "status.json", {}) or {}
    analysis = read_json(folder / "analysis.json")
    return {
        **feedback,
        "status": status.get("status", "queued"),
        "status_message": status.get("message", ""),
        "run_id": status.get("run_id"),
        "analysis": analysis if isinstance(analysis, dict) else None,
        "has_revision": (folder / "proposed_revision.md").is_file(),
        "applied_at": status.get("applied_at"),
    }


def list_feedback(root: Path, book_id: str, chapter: int | None = None) -> list[dict]:
    _, project = _book(root, book_id)
    base = project / "reader_feedback"
    if not base.is_dir():
        return []
    rows = []
    for folder in sorted(base.iterdir(), key=lambda item: item.name, reverse=True):
        if not folder.is_dir() or not SAFE_ID.fullmatch(folder.name):
            continue
        try:
            item = feedback_item(root, book_id, folder.name)
        except ValueError:
            continue
        if chapter is None or item.get("chapter") == chapter:
            rows.append(item)
        if len(rows) >= 100:
            break
    return rows


def apply_revision(root: Path, book_id: str, feedback_id: str) -> dict:
    folder = _feedback_dir(root, book_id, feedback_id)
    feedback = read_json(folder / "feedback.json")
    analysis = read_json(folder / "analysis.json")
    if not isinstance(feedback, dict) or not isinstance(analysis, dict):
        raise ValueError("作者分析尚未完成")
    if analysis.get("decision") not in {"accept", "partial"}:
        raise ValueError("作者判断为不采纳，不能应用修订")
    revision_path = folder / "proposed_revision.md"
    if not revision_path.is_file():
        raise ValueError("没有可应用的候选修订")
    chapter_path = Path(str(feedback.get("chapter_file", ""))).resolve()
    _, project = _book(root, book_id)
    if project.resolve() not in chapter_path.parents:
        raise ValueError("章节路径越界")
    current = chapter_path.read_text(encoding="utf-8")
    current_hash = hashlib.sha256(current.encode("utf-8")).hexdigest()
    if current_hash != feedback.get("chapter_sha256"):
        raise ValueError("章节在分析期间已经变化，请重新提交反馈")
    revision = revision_path.read_text(encoding="utf-8").strip() + "\n"
    number = int(feedback["chapter"])
    first = revision.splitlines()[0] if revision else ""
    if not re.fullmatch(rf"#\s*第\s*{number}\s*章\s+.+", first):
        raise ValueError("候选修订的章节标题不匹配")
    if len(_compact(_narrative(revision))) < 500:
        raise ValueError("候选修订正文过短")
    backup = folder / "original_before_apply.md"
    if not backup.exists():
        atomic_text(backup, current)
    atomic_text(chapter_path, revision)
    status = update_status(
        root,
        book_id,
        feedback_id,
        status="applied",
        message="候选修订已应用到本地章节；原文已备份，未触发发布",
        applied_at=datetime.now().astimezone().isoformat(),
    )
    return {"item": feedback_item(root, book_id, feedback_id), "status": status}
