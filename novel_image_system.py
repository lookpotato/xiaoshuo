#!/usr/bin/env python3
"""Per-book image catalog validation for the novel production pipeline."""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path, PurePosixPath


SCHEMA_VERSION = 1
MAX_IMAGES_PER_CHAPTER = 1
CATALOG_RELATIVE_PATH = Path("images") / "catalog.json"
ALLOWED_ENTITY_TYPES = {
    "character": "characters",
    "item": "items",
    "location": "locations",
    "creature": "creatures",
    "organization": "organizations",
    "scene": "scenes",
}
REQUIRED_VISUAL_CHECKS = {
    "subject_identity",
    "canonical_features",
    "colors_and_materials",
    "shape_and_parts",
    "no_contradictions",
    "no_unrequested_text_or_watermark",
}
IMAGE_SIGNATURES = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".webp": (b"RIFF",),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_catalog(project: Path) -> dict:
    path = project / CATALOG_RELATIVE_PATH
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_asset_path(project: Path, relative: object) -> tuple[Path | None, str | None]:
    if not isinstance(relative, str) or not relative:
        return None, "image.path 必须是非空字符串"
    if "\\" in relative:
        return None, "image.path 必须使用正斜杠"
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts or pure.parts[0] != "images":
        return None, "image.path 必须是 images/ 下的安全相对路径"
    candidate = (project / Path(*pure.parts)).resolve()
    image_root = (project / "images").resolve()
    if candidate != image_root and image_root not in candidate.parents:
        return None, "image.path 越出本书 images/ 目录"
    return candidate, None


def _valid_image_signature(path: Path) -> bool:
    """Perform a dependency-free structural check, not just an extension check."""
    suffix = path.suffix.lower()
    if suffix not in IMAGE_SIGNATURES:
        return False
    data = path.read_bytes()
    if suffix == ".png":
        if not data.startswith(IMAGE_SIGNATURES[".png"][0]):
            return False
        offset = 8
        saw_ihdr = False
        saw_iend = False
        while offset + 12 <= len(data):
            length = struct.unpack(">I", data[offset : offset + 4])[0]
            chunk_end = offset + 12 + length
            if chunk_end > len(data):
                return False
            chunk_type = data[offset + 4 : offset + 8]
            chunk_data = data[offset + 8 : offset + 8 + length]
            recorded_crc = struct.unpack(">I", data[offset + 8 + length : chunk_end])[0]
            if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != recorded_crc:
                return False
            if chunk_type == b"IHDR":
                if saw_ihdr or offset != 8 or length != 13:
                    return False
                width, height = struct.unpack(">II", chunk_data[:8])
                if width < 1 or height < 1:
                    return False
                saw_ihdr = True
            if chunk_type == b"IEND":
                saw_iend = length == 0 and chunk_end == len(data)
                break
            offset = chunk_end
        return saw_ihdr and saw_iend
    if suffix in {".jpg", ".jpeg"}:
        return len(data) >= 4 and data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9")
    if suffix == ".webp":
        return (
            len(data) >= 12
            and data.startswith(b"RIFF")
            and data[8:12] == b"WEBP"
            and struct.unpack("<I", data[4:8])[0] + 8 == len(data)
        )
    return False


def validate_image_catalog(project: Path, expected_book_id: str | None = None) -> list[str]:
    """Return actionable validation errors without mutating the project."""
    catalog_path = project / CATALOG_RELATIVE_PATH
    if not catalog_path.is_file():
        return ["缺少 images/catalog.json"]
    try:
        catalog = read_catalog(project)
    except Exception as exc:
        return [f"images/catalog.json 无法解析: {exc}"]

    errors: list[str] = []
    if catalog.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"图片目录 schema_version 必须为 {SCHEMA_VERSION}")
    if expected_book_id and catalog.get("book_id") != expected_book_id:
        errors.append(
            f"图片目录 book_id={catalog.get('book_id')!r}，期望 {expected_book_id!r}"
        )
    if catalog.get("max_images_per_chapter") != MAX_IMAGES_PER_CHAPTER:
        errors.append(f"max_images_per_chapter 必须固定为 {MAX_IMAGES_PER_CHAPTER}")
    style_bible = catalog.get("style_bible")
    if not isinstance(style_bible, dict) or not isinstance(
        style_bible.get("visual_style"), str
    ) or not style_bible.get("visual_style", "").strip():
        errors.append("style_bible.visual_style 不能为空")

    entities = catalog.get("entities")
    chapter_images = catalog.get("chapter_images")
    if not isinstance(entities, dict):
        errors.append("entities 必须是对象")
        entities = {}
    if not isinstance(chapter_images, dict):
        errors.append("chapter_images 必须是对象")
        chapter_images = {}

    names: set[tuple[str, str]] = set()
    image_paths: set[str] = set()
    created_by_chapter: dict[int, set[str]] = {}
    for entity_id, entity in entities.items():
        label = f"entities.{entity_id}"
        if not isinstance(entity_id, str) or not entity_id.strip():
            errors.append("实体 id 必须是非空字符串")
            continue
        if not isinstance(entity, dict):
            errors.append(f"{label} 必须是对象")
            continue
        entity_type = entity.get("type")
        name = entity.get("name")
        if entity_type not in ALLOWED_ENTITY_TYPES:
            errors.append(f"{label}.type 不受支持: {entity_type!r}")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{label}.name 必须是非空字符串")
        elif entity_type in ALLOWED_ENTITY_TYPES:
            identity = (entity_type, name.strip())
            if identity in names:
                errors.append(f"重复实体名称: {entity_type}/{name.strip()}")
            names.add(identity)
        if not isinstance(entity.get("canonical_description"), str) or not entity.get(
            "canonical_description", ""
        ).strip():
            errors.append(f"{label}.canonical_description 不能为空")
        source_excerpt = entity.get("source_excerpt")
        if not isinstance(source_excerpt, str) or len(source_excerpt.strip()) < 8:
            errors.append(f"{label}.source_excerpt 必须引用至少 8 个字的正文原文")
        features = entity.get("distinctive_features")
        if not isinstance(features, list) or not features or not all(
            isinstance(value, str) and value.strip() for value in features
        ):
            errors.append(f"{label}.distinctive_features 至少需要一条明确特征")

        first_chapter = entity.get("first_chapter")
        created_chapter = entity.get("image_created_chapter")
        if not isinstance(first_chapter, int) or first_chapter < 1:
            errors.append(f"{label}.first_chapter 必须是正整数")
        if not isinstance(created_chapter, int) or created_chapter < 1:
            errors.append(f"{label}.image_created_chapter 必须是正整数")
        elif isinstance(first_chapter, int) and created_chapter < first_chapter:
            errors.append(f"{label} 的图片不能早于实体首次出现章节")
        else:
            created_by_chapter.setdefault(created_chapter, set()).add(entity_id)
        if isinstance(first_chapter, int) and first_chapter >= 1 and isinstance(
            source_excerpt, str
        ):
            first_files = list((project / "chapters").glob(f"{first_chapter:04d}-*.md"))
            if len(first_files) != 1:
                errors.append(f"{label} 找不到唯一的首次出现章节")
            elif source_excerpt.strip() not in first_files[0].read_text(encoding="utf-8"):
                errors.append(f"{label}.source_excerpt 与首次出现章节原文不一致")

        image = entity.get("image")
        if not isinstance(image, dict):
            errors.append(f"{label}.image 必须是对象")
            continue
        asset_path, path_error = _safe_asset_path(project, image.get("path"))
        if path_error:
            errors.append(f"{label}: {path_error}")
        elif asset_path is not None:
            expected_dir = ALLOWED_ENTITY_TYPES.get(entity_type)
            relative_parts = PurePosixPath(str(image.get("path"))).parts
            normalized_path = str(image.get("path"))
            if normalized_path in image_paths:
                errors.append(f"{label}.image.path 与其他实体重复")
            image_paths.add(normalized_path)
            if expected_dir and (len(relative_parts) < 2 or relative_parts[1] != expected_dir):
                errors.append(f"{label}.image.path 应放在 images/{expected_dir}/")
            if not asset_path.is_file():
                errors.append(f"{label} 图片文件不存在: {image.get('path')}")
            elif not _valid_image_signature(asset_path):
                errors.append(f"{label} 图片格式或文件头无效: {image.get('path')}")
            else:
                actual_hash = sha256_file(asset_path)
                if image.get("sha256") != actual_hash:
                    errors.append(f"{label} 图片 sha256 与文件不一致")
        if image.get("generated_with") != "codex-imagegen":
            errors.append(f"{label}.image.generated_with 必须为 codex-imagegen")
        if not isinstance(image.get("alt_text"), str) or not image.get("alt_text", "").strip():
            errors.append(f"{label}.image.alt_text 不能为空")
        if not isinstance(image.get("prompt"), str) or not image.get("prompt", "").strip():
            errors.append(f"{label}.image.prompt 不能为空")
        verification = image.get("verification")
        if not isinstance(verification, dict) or verification.get("status") != "verified":
            errors.append(f"{label} 图片尚未通过 verified 核验")
        else:
            if verification.get("reviewer") != "codex-visual-review":
                errors.append(f"{label}.image.verification.reviewer 必须为 codex-visual-review")
            if not isinstance(verification.get("checked_at"), str) or not verification.get(
                "checked_at", ""
            ).strip():
                errors.append(f"{label}.image.verification.checked_at 不能为空")
            if not isinstance(verification.get("notes"), str) or len(
                verification.get("notes", "").strip()
            ) < 8:
                errors.append(f"{label}.image.verification.notes 必须记录具体核验依据")
            if not isinstance(verification.get("attempts"), int) or verification.get(
                "attempts", 0
            ) < 1:
                errors.append(f"{label}.image.verification.attempts 必须是正整数")
            checks = verification.get("checks")
            if not isinstance(checks, dict):
                errors.append(f"{label}.image.verification.checks 必须是对象")
            else:
                missing = sorted(
                    key for key in REQUIRED_VISUAL_CHECKS if checks.get(key) is not True
                )
                if missing:
                    errors.append(f"{label} 视觉核验未通过: {', '.join(missing)}")

    recorded_entities: set[str] = set()
    for chapter_key, manifest in chapter_images.items():
        label = f"chapter_images.{chapter_key}"
        try:
            chapter_number = int(chapter_key)
            if chapter_number < 1:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"{label} 的章节号无效")
            continue
        if not isinstance(manifest, dict):
            errors.append(f"{label} 必须是对象")
            continue
        entity_ids = manifest.get("entity_ids")
        if not isinstance(entity_ids, list) or not all(isinstance(value, str) for value in entity_ids):
            errors.append(f"{label}.entity_ids 必须是字符串数组")
            continue
        if len(entity_ids) > MAX_IMAGES_PER_CHAPTER:
            errors.append(f"第 {chapter_number} 章图片超过 {MAX_IMAGES_PER_CHAPTER} 张")
        if len(entity_ids) != len(set(entity_ids)):
            errors.append(f"{label}.entity_ids 存在重复")
        for entity_id in entity_ids:
            if entity_id not in entities:
                errors.append(f"{label} 引用了未知实体 {entity_id}")
            if entity_id in recorded_entities:
                errors.append(f"实体 {entity_id} 被多个章节重复登记为新图片")
            recorded_entities.add(entity_id)
        expected = created_by_chapter.get(chapter_number, set())
        if set(entity_ids) != expected:
            errors.append(
                f"{label}.entity_ids 与 image_created_chapter 不一致："
                f"登记={sorted(entity_ids)}，期望={sorted(expected)}"
            )
        chapter_files = list((project / "chapters").glob(f"{chapter_number:04d}-*.md"))
        if len(chapter_files) != 1:
            errors.append(f"第 {chapter_number} 章图片清单找不到唯一归档章节")
        else:
            chapter_text = chapter_files[0].read_text(encoding="utf-8")
            for entity_id in entity_ids:
                entity = entities.get(entity_id, {})
                image = entity.get("image", {}) if isinstance(entity, dict) else {}
                relative = image.get("path") if isinstance(image, dict) else None
                if isinstance(relative, str) and f"](../{relative})" not in chapter_text:
                    errors.append(
                        f"第 {chapter_number} 章正文未引用实体 {entity_id} 的 ../{relative}"
                    )

    missing_manifests = sorted(set(entities) - recorded_entities)
    if missing_manifests:
        errors.append(f"实体缺少章节图片登记: {', '.join(missing_manifests)}")
    return errors
