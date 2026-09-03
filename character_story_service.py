"""Read and update per-character private story threads without exposing chapters."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path

import fanqie_novel_manager as manager
import settings_service


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "manager_config.json"
CHAPTER_DIR_RE = re.compile(r"^\d{4}$")
THREAD_FILE_RE = re.compile(r"^(?P<slot>\d{2})-(?P<character>[^/\\]+)\.md$")
MAX_STORY_CHARS = 100_000
MAX_ENTRIES_PER_CHARACTER = 200


def _revision(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _project_path(book_id: str) -> Path:
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    book = manager.find_book(data, book_id)
    path = (ROOT / str(book["path"])).resolve()
    if ROOT != path and ROOT not in path.parents:
        raise ValueError("书籍目录越界")
    return path


def _scan(book_id: str) -> dict[str, list[dict]]:
    thread_root = _project_path(book_id) / "character_threads"
    grouped: dict[str, list[dict]] = {}
    if not thread_root.is_dir():
        return grouped
    for chapter_dir in sorted(thread_root.iterdir()):
        if not chapter_dir.is_dir() or not CHAPTER_DIR_RE.fullmatch(chapter_dir.name):
            continue
        chapter = int(chapter_dir.name)
        for path in sorted(chapter_dir.iterdir()):
            match = THREAD_FILE_RE.fullmatch(path.name) if path.is_file() else None
            if not match or int(match.group("slot")) == 0:
                continue
            character = match.group("character").strip()
            if not character:
                continue
            grouped.setdefault(character, []).append(
                {
                    "id": f"character_threads/{chapter_dir.name}/{path.name}",
                    "chapter": chapter,
                    "slot": int(match.group("slot")),
                    "character": character,
                    "_path": path,
                }
            )
    for entries in grouped.values():
        entries.sort(key=lambda item: (item["chapter"], item["slot"]), reverse=True)
    return grouped


def _summary(content: str) -> str:
    parts = []
    for line in content.splitlines():
        text = re.sub(r"^\s*(?:#+|[-*])\s*", "", line).strip()
        if text:
            parts.append(text)
        if len(" ".join(parts)) >= 100:
            break
    summary = " ".join(parts)
    return summary[:140] + ("…" if len(summary) > 140 else "")


def get_character_stories(book_id: str, character: str = "") -> dict:
    grouped = _scan(book_id)
    characters = []
    for name, entries in grouped.items():
        characters.append(
            {
                "name": name,
                "entry_count": len(entries),
                "latest_chapter": entries[0]["chapter"],
                "first_chapter": entries[-1]["chapter"],
                "slot": min(item["slot"] for item in entries),
            }
        )
    characters.sort(key=lambda item: (item["slot"], -item["latest_chapter"], item["name"]))
    selected = character if character in grouped else (characters[0]["name"] if characters else "")
    entries = []
    for item in grouped.get(selected, [])[:MAX_ENTRIES_PER_CHARACTER]:
        content = item["_path"].read_text(encoding="utf-8")
        entries.append(
            {
                key: value for key, value in item.items() if key != "_path"
            }
            | {
                "content": content,
                "revision": _revision(item["_path"]),
                "summary": _summary(content),
            }
        )
    return {
        "book_id": book_id,
        "locked": settings_service.settings_lock(),
        "selected_character": selected,
        "characters": characters,
        "entries": entries,
    }


def save_character_story(payload: dict) -> dict:
    if settings_service.settings_lock():
        raise ValueError("小说任务正在运行，人物私线暂时只读；请在任务结束后保存")
    book_id = str(payload.get("book_id", ""))
    character = str(payload.get("character", ""))
    entry_id = str(payload.get("entry_id", ""))
    content = payload.get("content")
    if not isinstance(content, str) or len(content) > MAX_STORY_CHARS:
        raise ValueError(f"人物私线内容无效或超过 {MAX_STORY_CHARS} 字")
    grouped = _scan(book_id)
    entry = next(
        (item for item in grouped.get(character, []) if item["id"] == entry_id),
        None,
    )
    if not entry:
        raise ValueError("人物私线不存在或不属于所选人物")
    project = _project_path(book_id)
    path = entry["_path"].resolve()
    thread_root = (project / "character_threads").resolve()
    if thread_root not in path.parents:
        raise ValueError("人物私线路径越界")
    if payload.get("revision") != _revision(path):
        raise settings_service.SettingsConflict("该人物私线已被其他进程修改，请刷新后再保存")
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(content.replace("\r\n", "\n"), encoding="utf-8")
    os.replace(temp, path)
    result = get_character_stories(book_id, character)
    result["saved"] = True
    return result
