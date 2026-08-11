from __future__ import annotations

import base64
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from novel_image_system import sha256_file, validate_image_catalog


class ImageCatalogTests(unittest.TestCase):
    PNG_1X1 = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        (self.project / "images" / "items").mkdir(parents=True)
        (self.project / "chapters").mkdir()
        self.image_path = self.project / "images" / "items" / "soul-lock-v1.png"
        self.image_path.write_bytes(self.PNG_1X1)
        (self.project / "chapters" / "0007-test.md").write_text(
            "# 第7章 测试\n\n锁魂钉能固定游魂。\n\n"
            "![锁魂钉](../images/items/soul-lock-v1.png)\n",
            encoding="utf-8",
        )
        self.catalog = {
            "schema_version": 2,
            "book_id": "test-book",
            "max_images_per_chapter": 1,
            "style_bible": {"visual_style": "test"},
            "entities": {
                "item:soul-lock": {
                    "name": "锁魂钉",
                    "type": "item",
                    "first_chapter": 7,
                    "image_created_chapter": 7,
                    "canonical_description": "一枚用于固定游魂的乌黑长钉",
                    "source_excerpt": "锁魂钉能固定游魂。",
                    "distinctive_features": ["四棱钉身", "尾端一圈暗红刻痕"],
                    "forbidden_features": ["剑形", "金色"],
                    "image": {
                        "path": "images/items/soul-lock-v1.png",
                        "sha256": sha256_file(self.image_path),
                        "alt_text": "四棱乌黑锁魂钉，尾端有暗红刻痕",
                        "generated_with": "codex-imagegen",
                        "prompt": "小说道具参考图：四棱乌黑锁魂钉",
                        "display": {
                            "content_kind": "item",
                            "generation_aspect_ratio": "1:1",
                            "fanqie_crop_ratio": "1:1",
                            "safe_area": "道具完整居中，四周保留至少百分之十安全边距",
                        },
                        "verification": {
                            "status": "verified",
                            "reviewer": "codex-visual-review",
                            "checked_at": "2026-08-11T12:00:00+08:00",
                            "attempts": 1,
                            "notes": "肉眼确认四棱钉身、暗红尾环，且没有文字水印。",
                            "checks": {
                                "subject_identity": True,
                                "canonical_features": True,
                                "colors_and_materials": True,
                                "shape_and_parts": True,
                                "no_contradictions": True,
                                "no_unrequested_text_or_watermark": True,
                                "crop_safe_composition": True,
                            },
                        },
                    },
                }
            },
            "chapter_images": {"7": {"entity_ids": ["item:soul-lock"]}},
        }
        self.write_catalog()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_catalog(self) -> None:
        (self.project / "images" / "catalog.json").write_text(
            json.dumps(self.catalog, ensure_ascii=False), encoding="utf-8"
        )

    def test_valid_verified_asset_passes(self) -> None:
        self.assertEqual(validate_image_catalog(self.project, "test-book"), [])

    def test_more_than_one_image_in_one_chapter_fails(self) -> None:
        source = self.catalog["entities"]["item:soul-lock"]
        for index in range(2, 3):
            entity_id = f"item:test-{index}"
            entity = json.loads(json.dumps(source, ensure_ascii=False))
            entity["name"] = f"测试道具{index}"
            entity["image"]["path"] = f"images/items/test-{index}.png"
            target = self.project / entity["image"]["path"]
            target.write_bytes(self.PNG_1X1)
            entity["image"]["sha256"] = sha256_file(target)
            self.catalog["entities"][entity_id] = entity
            self.catalog["chapter_images"]["7"]["entity_ids"].append(entity_id)
            chapter = self.project / "chapters" / "0007-test.md"
            chapter.write_text(
                chapter.read_text(encoding="utf-8")
                + f"\n![测试](../images/items/test-{index}.png)\n",
                encoding="utf-8",
            )
        self.write_catalog()
        errors = validate_image_catalog(self.project, "test-book")
        self.assertTrue(any("图片超过 1 张" in error for error in errors))

    def test_failed_visual_check_is_rejected(self) -> None:
        checks = self.catalog["entities"]["item:soul-lock"]["image"]["verification"][
            "checks"
        ]
        checks["shape_and_parts"] = False
        self.write_catalog()
        errors = validate_image_catalog(self.project, "test-book")
        self.assertTrue(any("shape_and_parts" in error for error in errors))

    def test_missing_display_metadata_is_rejected(self) -> None:
        del self.catalog["entities"]["item:soul-lock"]["image"]["display"]
        self.write_catalog()
        errors = validate_image_catalog(self.project, "test-book")
        self.assertTrue(any("image.display" in error for error in errors))

    def test_unsupported_crop_ratio_is_rejected(self) -> None:
        display = self.catalog["entities"]["item:soul-lock"]["image"]["display"]
        display["fanqie_crop_ratio"] = "4:5"
        self.write_catalog()
        errors = validate_image_catalog(self.project, "test-book")
        self.assertTrue(any("fanqie_crop_ratio" in error for error in errors))

    def test_actual_pixel_ratio_must_match_generation_ratio(self) -> None:
        display = self.catalog["entities"]["item:soul-lock"]["image"]["display"]
        display["generation_aspect_ratio"] = "2:3"
        display["fanqie_crop_ratio"] = "2:3"
        self.write_catalog()
        errors = validate_image_catalog(self.project, "test-book")
        self.assertTrue(any("实际尺寸 1x1" in error for error in errors))

    def test_changed_image_hash_is_rejected(self) -> None:
        chunk_type = b"tEXt"
        chunk_data = b"note=changed"
        chunk = (
            struct.pack(">I", len(chunk_data))
            + chunk_type
            + chunk_data
            + struct.pack(">I", zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF)
        )
        self.image_path.write_bytes(self.PNG_1X1[:-12] + chunk + self.PNG_1X1[-12:])
        errors = validate_image_catalog(self.project, "test-book")
        self.assertTrue(any("sha256" in error for error in errors))

    def test_chapter_must_reference_the_asset(self) -> None:
        (self.project / "chapters" / "0007-test.md").write_text(
            "# 第7章 测试\n", encoding="utf-8"
        )
        errors = validate_image_catalog(self.project, "test-book")
        self.assertTrue(any("正文未引用" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
