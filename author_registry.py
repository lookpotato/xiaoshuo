"""Author profiles and mandatory per-book author bindings."""

from __future__ import annotations

import json
import re
from pathlib import Path


class AuthorConfigError(ValueError):
    pass


def system_path(root: Path) -> Path:
    return root.resolve() / "novel_engine_v2" / "system.json"


def authors_path(root: Path) -> Path:
    return root.resolve() / "novel_engine_v2" / "authors"


def read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthorConfigError(f"无法读取作者配置 {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AuthorConfigError(f"作者配置必须是 JSON 对象：{path}")
    return value


def validate_author_profile(value: object, expected_id: str = "") -> dict:
    if not isinstance(value, dict):
        raise AuthorConfigError("作者档案必须是 JSON 对象")
    author_id = value.get("id")
    if not isinstance(author_id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", author_id):
        raise AuthorConfigError("作者 ID 只能使用小写字母、数字、下划线或短横线")
    if expected_id and author_id != expected_id:
        raise AuthorConfigError(f"作者档案 ID 必须为 {expected_id}")
    if value.get("schema_version") != 1:
        raise AuthorConfigError(f"作者 {author_id} 的 schema_version 必须为 1")
    if not isinstance(value.get("name"), str) or not value["name"].strip():
        raise AuthorConfigError(f"作者 {author_id} 缺少名称")
    for key in ("creative_identity", "reader_contract", "language_principles"):
        items = value.get(key)
        if not isinstance(items, list) or not items or not all(
            isinstance(item, str) and item.strip() for item in items
        ):
            raise AuthorConfigError(f"作者 {author_id} 的 {key} 必须是非空文本数组")
    return value


def list_authors(root: Path) -> list[dict]:
    rows = []
    for path in sorted(authors_path(root).glob("*.json")):
        profile = validate_author_profile(read_object(path), path.stem)
        rows.append(
            {
                "id": profile["id"],
                "name": profile["name"].strip(),
                "calibration_status": str(profile.get("calibration_status", "uncalibrated")),
                "unknown_count": len(profile.get("unknowns", []))
                if isinstance(profile.get("unknowns", []), list)
                else 0,
                "document_id": f"novel_engine_v2/authors/{path.name}",
            }
        )
    if not rows:
        raise AuthorConfigError("系统至少需要一个有效作者档案")
    return rows


def load_system(root: Path) -> dict:
    path = system_path(root)
    value = read_object(path)
    if value.get("schema_version") != 2 or not isinstance(value.get("books"), dict):
        raise AuthorConfigError("novel_engine_v2/system.json 缺少有效 books 配置")
    return value


def book_author(root: Path, book_id: str) -> dict:
    authors = {row["id"]: row for row in list_authors(root)}
    raw = load_system(root)["books"].get(book_id)
    author_id = raw.get("author") if isinstance(raw, dict) else None
    if not isinstance(author_id, str) or not author_id:
        raise AuthorConfigError(f"小说 {book_id} 尚未配置作者")
    if author_id not in authors:
        raise AuthorConfigError(f"小说 {book_id} 绑定了不存在的作者：{author_id}")
    return authors[author_id]


def updated_book_binding(root: Path, book: dict, author_id: str) -> dict:
    authors = {row["id"] for row in list_authors(root)}
    if author_id not in authors:
        raise AuthorConfigError(f"作者不存在：{author_id or '未选择'}")
    system = load_system(root)
    books = dict(system["books"])
    current = books.get(book["id"], {})
    if not isinstance(current, dict):
        current = {}
    books[book["id"]] = {
        **current,
        "title": str(book.get("title", book["id"])),
        "project": str(book["path"]),
        "author": author_id,
    }
    return {**system, "books": books}


def binding_errors(root: Path, books: list[dict]) -> list[str]:
    errors = []
    for book in books:
        try:
            book_author(root, str(book.get("id", "")))
        except (AuthorConfigError, KeyError) as exc:
            errors.append(str(exc))
    return errors
