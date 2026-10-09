"""Per-book Codex model settings for the novel production workflow."""

from __future__ import annotations

import json
from pathlib import Path

MODEL = "gpt-6-sol"
REASONING_EFFORT = "medium"
MODEL_OPTIONS = (
    {"id": "gpt-6-sol", "label": "GPT-6 Sol"},
    {"id": "gpt-6.1-sol", "label": "GPT-6.1 Sol"},
    {"id": "gpt-6-astra", "label": "GPT-6 Astra"},
    {"id": "gpt-6-luna", "label": "GPT-6 Luna"},
)
REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max")


def validate(model: object, effort: object) -> tuple[str, str]:
    if model not in {option["id"] for option in MODEL_OPTIONS}:
        raise ValueError(f"不支持的 Codex 模型：{model}")
    if effort not in REASONING_EFFORTS:
        raise ValueError(f"不支持的推理档位：{effort}")
    return model, effort


def book_settings(book: dict) -> tuple[str, str]:
    return validate(book.get("codex_model", MODEL), book.get("codex_reasoning_effort", REASONING_EFFORT))


def settings_for_book(book_id: str | None, root: Path | None = None) -> tuple[str, str]:
    if book_id is None:
        return MODEL, REASONING_EFFORT
    config_path = (root or Path(__file__).resolve().parent) / "manager_config.json"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    book = next((item for item in data.get("books", []) if item.get("id") == book_id), None)
    if book is None:
        raise ValueError(f"小说不存在：{book_id}")
    return book_settings(book)


def exec_prefix(codex: str, book_id: str | None = None, root: Path | None = None) -> list[str]:
    model, effort = settings_for_book(book_id, root)
    return [
        codex, "exec", "--model", model,
        "--config", f'model_reasoning_effort="{effort}"',
    ]
