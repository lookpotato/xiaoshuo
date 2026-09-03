"""Safe, modular settings service for the local novel workbench."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path

import fanqie_novel_manager as manager


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "manager_config.json"
SYSTEM_GENERAL_FIELDS = (
    "timezone",
    "global_lock_minutes",
    "max_daily_attempts",
    "retry_delay_minutes",
    "default_failure_policy",
    "default_book_id",
)
BOOK_DOCUMENTS = {
    "automation_prompt.md": ("系统给本书的提示词", "每次生成前必须遵守的单书总指令"),
    "novel_config.md": ("小说基础配置", "题材、目标读者、篇幅与创作边界"),
    "style_guide.md": ("基础文风", "句式、节奏、叙事视角和禁用写法"),
    "narrative_style_pack.md": ("专属叙事包", "这本书独有的旁白与场景组织方式"),
    "character_voice_bible.md": ("人物说话规则", "称呼、地域、年龄、职业和关系声音"),
    "story_bible.md": ("故事总纲", "核心冲突、长期方向和不可破坏的事实"),
    "characters.md": ("人物设定", "人物身份、关系、欲望与底线"),
    "world.md": ("世界观", "世界规则、地点和力量边界"),
}
SYSTEM_BASE_DOCUMENTS = {
    "shared/narrative_prose_foundation.md": ("中国网文叙事底座", "旁白、视角、出场和信息落地的共享规则"),
    "shared/chinese_dialogue_foundation.md": ("中国人物对白底座", "称呼、关系、口语、省略和地域表达的共享规则"),
    "shared/character_engine.md": ("独立人物引擎", "人物欲望、状态、误解、行动与支线运行规则"),
    "shared/parallel_character_pipeline.md": ("多人物顺序线", "人物私线、交织、汇总和状态回写流程"),
    "shared/quality_scorecard.md": ("质量评分卡", "章节质量的统一检查维度"),
    "shared/reader_gate.md": ("读者理解门禁", "无大纲读者验收和因果证据规则"),
    "shared/image_workflow.md": ("章节配图流程", "图片生成、复用、验证与上传规则"),
    "shared/writing_playbook.md": ("写作经验手册", "从实际章节反馈沉淀的共享经验"),
}
ALLOWED_MODES = {"write_only", "write_then_upload"}
MAX_DOCUMENT_CHARS = 200_000


class SettingsConflict(ValueError):
    pass


def _read_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _revision(path: Path) -> str:
    if not path.is_file():
        return "missing"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _document(path: Path, document_id: str, title: str, description: str) -> dict:
    return {
        "id": document_id,
        "title": title,
        "description": description,
        "content": path.read_text(encoding="utf-8") if path.is_file() else "",
        "revision": _revision(path),
        "exists": path.is_file(),
    }


def _project_path(book: dict) -> Path:
    path = (ROOT / str(book["path"])).resolve()
    if ROOT != path and ROOT not in path.parents:
        raise ValueError("书籍目录越界")
    return path


def _system_document_path(document_id: str) -> Path:
    if not re.fullmatch(r"shared/[0-9A-Za-z_.-]+\.(?:md|json|jsonl)", document_id):
        raise ValueError("共享模块路径不在白名单中")
    path = (ROOT / document_id).resolve()
    shared = (ROOT / "shared").resolve()
    if path.parent != shared:
        raise ValueError("共享模块路径越界")
    return path


def _book_document_path(book: dict, document_id: str) -> Path:
    if document_id not in BOOK_DOCUMENTS:
        raise ValueError("单书配置文件不在白名单中")
    return _project_path(book) / document_id


def _referenced_system_documents(policy: dict) -> list[str]:
    found: set[str] = set()
    for module in policy.values():
        if not isinstance(module, dict):
            continue
        for value in module.values():
            if isinstance(value, str) and value.startswith("shared/"):
                try:
                    _system_document_path(value)
                except ValueError:
                    continue
                found.add(value)
    return sorted(found)


def settings_lock() -> dict | None:
    lock = manager.read_json(manager.LOCK, None)
    if not isinstance(lock, dict):
        return None
    pid = int(lock.get("pid", 0) or 0)
    if pid and manager.process_is_running(pid):
        return {
            "book_id": lock.get("book_id"),
            "claimed_at": lock.get("claimed_at"),
            "message": "小说任务运行中，设置暂时只读；任务结束后可保存",
        }
    return None


def get_system_settings() -> dict:
    data = _read_config()
    policy = data.get("writing_policy", {})
    documents = []
    document_ids = set(SYSTEM_BASE_DOCUMENTS)
    document_ids.update(_referenced_system_documents(policy))
    for document_id in sorted(document_ids):
        title, description = SYSTEM_BASE_DOCUMENTS.get(
            document_id,
            (Path(document_id).stem.replace("_", " "), "由所有小说共享、按模块装配进生成提示词"),
        )
        documents.append(
            _document(
                _system_document_path(document_id),
                document_id,
                title,
                description,
            )
        )
    modules = []
    for module_id, module in policy.items():
        if isinstance(module, dict):
            modules.append(
                {
                    "id": module_id,
                    "enabled": module.get("enabled", True),
                    "rules": [
                        value
                        for value in module.values()
                        if isinstance(value, str) and value.startswith("shared/")
                    ],
                    "field_count": len(module),
                }
            )
    return {
        "scope": "system",
        "config_revision": _revision(CONFIG_PATH),
        "locked": settings_lock(),
        "general": {key: data.get(key) for key in SYSTEM_GENERAL_FIELDS},
        "writing_policy": policy,
        "modules": modules,
        "documents": documents,
    }


def get_book_settings(book_id: str) -> dict:
    data = _read_config()
    book = manager.find_book(data, book_id)
    project = _project_path(book)
    documents = [
        _document(project / name, name, title, description)
        for name, (title, description) in BOOK_DOCUMENTS.items()
    ]
    return {
        "scope": "book",
        "book_id": book_id,
        "config_revision": _revision(CONFIG_PATH),
        "locked": settings_lock(),
        "registry": {
            "id": book["id"],
            "path": book["path"],
            "title": book.get("title", book_id),
            "enabled": bool(book.get("enabled", True)),
            "mode": book.get("mode", "write_only"),
            "priority": int(book.get("priority", 0)),
            "daily_chapter_target": int(book.get("daily_chapter_target", 1)),
            "reader_gate_from_chapter": int(book.get("reader_gate_from_chapter", 1)),
            "schedule_time": str(book.get("schedule", {}).get("time", "12:00")),
            "default_publish_times": list(book.get("default_publish_times", [])),
            "note": str(book.get("note", "")),
        },
        "documents": documents,
    }


def get_settings(scope: str, book_id: str = "") -> dict:
    if scope == "system":
        return get_system_settings()
    if scope == "book":
        if not book_id:
            raise ValueError("缺少 book_id")
        return get_book_settings(book_id)
    raise ValueError("settings scope 必须是 system 或 book")


def _validate_document_updates(
    updates: object, path_resolver
) -> dict[Path, str]:
    if not isinstance(updates, list):
        raise ValueError("documents 必须是数组")
    writes: dict[Path, str] = {}
    for item in updates:
        if not isinstance(item, dict):
            raise ValueError("documents 项必须是对象")
        path = path_resolver(str(item.get("id", "")))
        content = item.get("content")
        if not isinstance(content, str) or len(content) > MAX_DOCUMENT_CHARS:
            raise ValueError(f"{path.name} 内容无效或超过 {MAX_DOCUMENT_CHARS} 字")
        if item.get("revision") != _revision(path):
            raise SettingsConflict(f"{path.name} 已被其他进程修改，请刷新后再保存")
        writes[path] = content.replace("\r\n", "\n")
    return writes


def _atomic_write_many(writes: dict[Path, str]) -> None:
    backups = {path: path.read_bytes() if path.is_file() else None for path in writes}
    written: list[Path] = []
    try:
        for path, content in writes.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            temp.write_text(content, encoding="utf-8")
            os.replace(temp, path)
            written.append(path)
    except Exception:
        for path in reversed(written):
            prior = backups[path]
            if prior is None:
                path.unlink(missing_ok=True)
            else:
                temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.rollback")
                temp.write_bytes(prior)
                os.replace(temp, path)
        raise


def _validated_general(value: object, current: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("general 必须是对象")
    result = {key: current.get(key) for key in SYSTEM_GENERAL_FIELDS}
    result.update({key: value[key] for key in SYSTEM_GENERAL_FIELDS if key in value})
    for key, low, high in (
        ("global_lock_minutes", 10, 1440),
        ("max_daily_attempts", 1, 20),
        ("retry_delay_minutes", 0, 1440),
    ):
        if not isinstance(result[key], int) or not low <= result[key] <= high:
            raise ValueError(f"{key} 必须在 {low}—{high} 之间")
    if not isinstance(result["timezone"], str) or not result["timezone"].strip():
        raise ValueError("timezone 不能为空")
    if not isinstance(result["default_failure_policy"], str):
        raise ValueError("default_failure_policy 无效")
    if not any(book.get("id") == result["default_book_id"] for book in current["books"]):
        raise ValueError("default_book_id 不存在")
    return result


def _validated_book_registry(value: object, current: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("registry 必须是对象")
    updated = dict(current)
    title = value.get("title", current.get("title"))
    mode = value.get("mode", current.get("mode"))
    if not isinstance(title, str) or not title.strip():
        raise ValueError("书名不能为空")
    if mode not in ALLOWED_MODES:
        raise ValueError("mode 无效")
    for key, low, high in (
        ("priority", 0, 10000),
        ("daily_chapter_target", 1, 20),
        ("reader_gate_from_chapter", 1, 100000),
    ):
        number = value.get(key, current.get(key))
        if not isinstance(number, int) or not low <= number <= high:
            raise ValueError(f"{key} 必须在 {low}—{high} 之间")
        updated[key] = number
    schedule_time = value.get("schedule_time", current.get("schedule", {}).get("time"))
    publish_times = value.get("default_publish_times", current.get("default_publish_times", []))
    time_pattern = r"(?:[01]\d|2[0-3]):[0-5]\d"
    if not isinstance(schedule_time, str) or not re.fullmatch(time_pattern, schedule_time):
        raise ValueError("schedule_time 必须为 HH:MM")
    if not isinstance(publish_times, list) or not all(
        isinstance(item, str) and re.fullmatch(time_pattern, item)
        for item in publish_times
    ):
        raise ValueError("default_publish_times 必须是 HH:MM 数组")
    updated.update(
        {
            "title": title.strip(),
            "enabled": bool(value.get("enabled", current.get("enabled", True))),
            "mode": mode,
            "schedule": {**current.get("schedule", {}), "time": schedule_time},
            "default_publish_times": publish_times,
            "note": str(value.get("note", current.get("note", ""))),
        }
    )
    return updated


def save_settings(payload: dict) -> dict:
    if settings_lock():
        raise ValueError("小说任务正在运行，设置暂时只读；请在任务结束后保存")
    data = _read_config()
    if payload.get("config_revision") != _revision(CONFIG_PATH):
        raise SettingsConflict("系统配置已被其他进程修改，请刷新后再保存")
    scope = payload.get("scope")
    if scope == "system":
        general = _validated_general(payload.get("general"), data)
        policy = payload.get("writing_policy")
        if not isinstance(policy, dict):
            raise ValueError("writing_policy 必须是 JSON 对象")
        updated = dict(data)
        updated.update(general)
        updated["writing_policy"] = policy
        writes = _validate_document_updates(payload.get("documents", []), _system_document_path)
    elif scope == "book":
        book_id = str(payload.get("book_id", ""))
        current_book = manager.find_book(data, book_id)
        updated_book = _validated_book_registry(payload.get("registry"), current_book)
        updated = dict(data)
        updated["books"] = [updated_book if book.get("id") == book_id else book for book in data["books"]]
        writes = _validate_document_updates(
            payload.get("documents", []),
            lambda document_id: _book_document_path(current_book, document_id),
        )
    else:
        raise ValueError("settings scope 必须是 system 或 book")
    writes[CONFIG_PATH] = json.dumps(updated, ensure_ascii=False, indent=2) + "\n"
    _atomic_write_many(writes)
    result = get_settings(str(scope), str(payload.get("book_id", "")))
    result["saved"] = True
    return result
