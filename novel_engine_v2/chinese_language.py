"""Read and retrieve confirmed Chinese dialogue experience."""

from __future__ import annotations

import json
import re
from pathlib import Path


def _text(value: object) -> str:
    return str(value or "").strip()


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        value = re.sub(r"\s+", " ", _text(value))
        if value and value not in result:
            result.append(value)
    return result


def normalize_record(record: dict, line_number: int = 0) -> dict:
    """Normalize legacy examples and confirmed learning rules to one shape."""
    principles = record.get("principles")
    if not isinstance(principles, list):
        principles = []
    tags = record.get("tags")
    if not isinstance(tags, list):
        tags = []
    category = _text(record.get("category"))
    scope = _text(record.get("scope"))
    original = _text(record.get("original"))
    preferred = _text(record.get("preferred"))
    principle = _text(record.get("principle")) or preferred or (
        "；".join(_unique([_text(item) for item in principles]))
    )
    applies_when = _text(record.get("applies_when")) or scope or "与该经验的关系和场景相似时"
    avoid = _text(record.get("avoid")) or "不要脱离人物关系、场合和具体动作机械套用。"
    rationale = _text(record.get("rationale")) or "来自用户确认的具体语言反馈。"
    derived_tags = [category, scope, *[_text(item) for item in principles]]
    tags = _unique([_text(item) for item in tags] + derived_tags)
    return {
        "id": _text(record.get("id")) or f"legacy-line-{line_number}",
        "category": category,
        "tags": tags,
        "principle": principle,
        "applies_when": applies_when,
        "avoid": avoid,
        "rationale": rationale,
        "original": original,
        "preferred": preferred,
        "status": _text(record.get("status")) or "user_confirmed",
    }


def load_records(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    records: list[dict] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            record = normalize_record(value, line_number)
            if record["principle"]:
                records.append(record)
    return records


def _terms(query: str) -> list[str]:
    chunks = re.findall(r"[\u3400-\u9fff]{2,}|[A-Za-z0-9_]{2,}", query or "")
    return _unique(chunks)


def retrieve(path: Path, query: str = "", limit: int = 8) -> list[dict]:
    """Return the most relevant confirmed rules; no query returns newest rules first."""
    records = load_records(path)
    if not records:
        return []
    terms = _terms(query)
    if not terms:
        return records[-limit:]
    ranked: list[tuple[int, int, dict]] = []
    for index, record in enumerate(records):
        searchable = " ".join([
            record["category"], *record["tags"], record["principle"],
            record["applies_when"], record["avoid"], record["original"], record["preferred"],
        ])
        score = sum(3 if term in " ".join(record["tags"]) else 1 for term in terms if term in searchable)
        if score:
            ranked.append((score, index, record))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [record for _, _, record in ranked[:limit]]


def render(records: list[dict]) -> str:
    if not records:
        return "暂无匹配的用户确认中文语言经验。"
    lines = []
    for record in records:
        tags = "、".join(record["tags"][:12]) or "未标注"
        lines.append(
            f"- 原则：{record['principle']}\n"
            f"  适用：{record['applies_when']}\n"
            f"  避免：{record['avoid']}\n"
            f"  标签：{tags}"
        )
    return "\n".join(lines)
