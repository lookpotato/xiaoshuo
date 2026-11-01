"""Chapter title comparison shared by local archive paths."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path


HEADING = re.compile(r"^#\s*第\s*\d+\s*章\s+(.+?)\s*$")


def title_key(title: str) -> str:
    """Ignore spacing, punctuation and presentation variants when comparing titles."""
    normalized = unicodedata.normalize("NFKC", title).casefold()
    return "".join(
        char for char in normalized
        if not char.isspace() and not unicodedata.category(char).startswith("P")
    )


def duplicate_title_paths(project: Path, title: str, *, exclude: Path | None = None) -> list[Path]:
    duplicates: list[Path] = []
    for path in sorted((project / "chapters").glob("[0-9][0-9][0-9][0-9]-*.md")):
        if exclude is not None and path.resolve() == exclude.resolve():
            continue
        try:
            with path.open(encoding="utf-8-sig") as chapter_file:
                first = chapter_file.readline().strip()
        except OSError:
            continue
        match = HEADING.fullmatch(first)
        existing_title = match.group(1) if match else path.stem[5:]
        if title_key(existing_title) == title_key(title):
            duplicates.append(path)
    return duplicates


def duplicate_title_message(chapter: int, title: str, paths: list[Path]) -> str:
    names = "、".join(path.name for path in paths[:3])
    return f"第{chapter}章标题《{title}》与本书已有章节重复：{names}；请先修改标题"
