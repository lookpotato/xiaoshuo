"""Selected chapter generation pipeline, shared by the UI and CLI."""

from __future__ import annotations

import json
from pathlib import Path


PATH = Path(__file__).resolve().parent / "generation_version.json"
DEFAULT = "v3"
VERSIONS = (
    {"id": "v2", "name": "V2 · 经典生产链", "description": "沿用现有章节规划、写作与质量检查流程，支持原有续跑和交付选项。"},
    {"id": "v3", "name": "V3 · 作者房间", "description": "人物带着现实目标进入场景，经过作者房间、初稿、深层编辑和读者检查后归档。当前只支持本地归档。"},
)


def selected() -> str:
    if not PATH.is_file():
        return DEFAULT
    value = json.loads(PATH.read_text(encoding="utf-8")).get("version")
    if value not in {item["id"] for item in VERSIONS}:
        raise ValueError(f"无效的生成版本：{value}")
    return value
