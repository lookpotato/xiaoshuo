import json
from pathlib import Path
from unittest import TestCase, mock

import settings_service
import generation_version


class SettingsServiceTest(TestCase):
    def setUp(self):
        self.temp = self.enterContext(__import__("tempfile").TemporaryDirectory())
        self.root = Path(self.temp)
        self.config_path = self.root / "manager_config.json"
        self.book_path = self.root / "book-one"
        self.book_path.mkdir()
        (self.root / "shared").mkdir()
        (self.root / "novel_engine_v2" / "authors").mkdir(parents=True)
        (self.book_path / "automation_prompt.md").write_text("旧提示词\n", encoding="utf-8")
        (self.root / "shared" / "character_engine.md").write_text("旧人物规则\n", encoding="utf-8")
        self.config = {
            "schema_version": 1,
            "timezone": "Asia/Shanghai",
            "global_lock_minutes": 180,
            "max_daily_attempts": 2,
            "retry_delay_minutes": 30,
            "default_failure_policy": "retry_same_day",
            "default_book_id": "book-one",
            "writing_policy": {
                "character_engine": {
                    "enabled": True,
                    "shared_rules": "shared/character_engine.md",
                    "book_rules": "<project>/characters.md",
                }
            },
            "books": [
                {
                    "id": "book-one",
                    "title": "第一本书",
                    "path": "book-one",
                    "enabled": True,
                    "schedule": {"days": ["mon"], "time": "12:00"},
                    "priority": 100,
                    "mode": "write_only",
                    "daily_chapter_target": 2,
                    "reader_gate_from_chapter": 1,
                    "default_publish_times": ["12:00"],
                    "note": "测试",
                    "keep_me": "untouched",
                }
            ],
        }
        self.config_path.write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")
        self.author_system_path = self.root / "novel_engine_v2" / "system.json"
        self.author_system_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "books": {
                        "book-one": {
                            "title": "第一本书",
                            "project": "book-one",
                            "author": "owner",
                        }
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (self.root / "novel_engine_v2" / "authors" / "owner.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "id": "owner",
                    "name": "当前作者",
                    "creative_identity": ["独特取舍"],
                    "reader_contract": ["正文可理解"],
                    "language_principles": ["符合人物身份"],
                    "author_introduction": "专注原创故事与现实产品的连接。",
                    "specialties": ["故事物件产品化", "群像经营题材"],
                    "unknowns": ["叙述距离"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.enterContext(mock.patch.object(settings_service, "ROOT", self.root))
        self.enterContext(mock.patch.object(settings_service, "CONFIG_PATH", self.config_path))
        self.enterContext(mock.patch.object(generation_version, "PATH", self.root / "generation_version.json"))
        self.enterContext(mock.patch.object(settings_service, "settings_lock", return_value=None))

    def test_book_settings_expose_only_whitelisted_modules(self):
        result = settings_service.get_book_settings("book-one")
        ids = {item["id"] for item in result["documents"]}
        self.assertIn("automation_prompt.md", ids)
        self.assertIn("style_guide.md", ids)
        self.assertNotIn("publish_config.md", ids)
        prompt = next(item for item in result["documents"] if item["id"] == "automation_prompt.md")
        self.assertEqual(prompt["content"], "旧提示词\n")
        self.assertTrue(prompt["exists"])
        self.assertEqual(result["registry"]["author"], "owner")
        self.assertTrue(result["registry"]["word_count_limit_enabled"])
        self.assertEqual(result["registry"]["word_count_exempt_chapters"], [])
        self.assertEqual(result["registry"]["next_chapter_number"], 1)
        self.assertEqual(result["authors"][0]["name"], "当前作者")
        self.assertEqual(result["authors"][0]["introduction"], "专注原创故事与现实产品的连接。")
        self.assertEqual(result["authors"][0]["specialties"], ["故事物件产品化", "群像经营题材"])

    def test_delete_book_requires_exact_confirmation_and_moves_files_to_trash(self):
        second = self.root / "book-two"
        second.mkdir()
        (second / "chapter.md").write_text("第二本的正文", encoding="utf-8")
        self.config["books"].append({"id": "book-two", "title": "第二本书", "path": "book-two"})
        self.config_path.write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")
        for version in ("v2", "v3"):
            path = self.root / f"novel_engine_{version}" / "system.json"
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps({"books": {"book-one": {}, "book-two": {}}}), encoding="utf-8")
        payload = {"book_id": "book-one", "config_revision": settings_service._revision(self.config_path),
                   "confirm_title": "第一本书", "confirm_book_id": "book-one"}
        with self.assertRaises(ValueError):
            settings_service.delete_book({**payload, "confirm_title": "错字"})
        self.assertTrue(self.book_path.exists())
        result = settings_service.delete_book(payload)
        self.assertFalse(self.book_path.exists())
        self.assertTrue((self.root / result["trash_path"] / "automation_prompt.md").exists())
        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(config["default_book_id"], "book-two")
        self.assertEqual([item["id"] for item in config["books"]], ["book-two"])
        for version in ("v2", "v3"):
            system = json.loads((self.root / f"novel_engine_{version}" / "system.json").read_text(encoding="utf-8"))
            self.assertNotIn("book-one", system["books"])

    def test_delete_book_refuses_stale_revision_and_shared_directory(self):
        payload = {"book_id": "book-one", "config_revision": "stale",
                   "confirm_title": "第一本书", "confirm_book_id": "book-one"}
        with self.assertRaises(settings_service.SettingsConflict):
            settings_service.delete_book(payload)
        self.config["books"].append({"id": "book-two", "title": "第二本书", "path": "book-one"})
        self.config_path.write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ValueError):
            settings_service.delete_book({**payload, "config_revision": settings_service._revision(self.config_path)})
        self.assertTrue(self.book_path.exists())

    def test_system_settings_expose_registry_and_shared_modules(self):
        result = settings_service.get_system_settings()
        self.assertEqual(result["generation_version"], "v3")
        self.assertEqual(result["modules"][0]["id"], "character_engine")
        ids = {item["id"] for item in result["documents"]}
        self.assertIn("shared/character_engine.md", ids)
        self.assertIn("shared/chinese_dialogue_foundation.md", ids)
        self.assertIn("novel_engine_v2/authors/owner.json", ids)
        self.assertEqual(result["authors"][0]["id"], "owner")

    def test_system_settings_switch_generation_version(self):
        current = settings_service.get_system_settings()
        saved = settings_service.save_settings({
            "scope": "system",
            "config_revision": current["config_revision"],
            "generation_version_revision": current["generation_version_revision"],
            "generation_version": "v2",
            "general": current["general"],
            "writing_policy": current["writing_policy"],
            "documents": [],
        })
        self.assertEqual(saved["generation_version"], "v2")
        self.assertEqual(generation_version.selected(), "v2")

    def test_save_book_updates_registry_and_document_without_losing_unknown_fields(self):
        current = settings_service.get_book_settings("book-one")
        registry = dict(current["registry"])
        registry.update({
            "title": "新书名",
            "daily_chapter_target": 3,
            "word_count_limit_enabled": False,
            "word_count_exempt_chapters": [2, 5],
        })
        prompt = next(item for item in current["documents"] if item["id"] == "automation_prompt.md")
        saved = settings_service.save_settings(
            {
                "scope": "book",
                "book_id": "book-one",
                "config_revision": current["config_revision"],
                "author_config_revision": current["author_config_revision"],
                "registry": registry,
                "documents": [
                    {"id": prompt["id"], "revision": prompt["revision"], "content": "新提示词\n"}
                ],
            }
        )
        written = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(written["books"][0]["title"], "新书名")
        self.assertEqual(written["books"][0]["daily_chapter_target"], 3)
        self.assertFalse(written["books"][0]["word_count_limit_enabled"])
        self.assertEqual(written["books"][0]["word_count_exempt_chapters"], [2, 5])
        self.assertEqual(written["books"][0]["keep_me"], "untouched")
        self.assertEqual((self.book_path / "automation_prompt.md").read_text(encoding="utf-8"), "新提示词\n")
        self.assertTrue(saved["saved"])
        binding = json.loads(self.author_system_path.read_text(encoding="utf-8"))
        self.assertEqual(binding["books"]["book-one"]["author"], "owner")

    def test_stale_document_revision_is_rejected(self):
        current = settings_service.get_book_settings("book-one")
        prompt = next(item for item in current["documents"] if item["id"] == "automation_prompt.md")
        (self.book_path / "automation_prompt.md").write_text("外部改动", encoding="utf-8")
        with self.assertRaises(settings_service.SettingsConflict):
            settings_service.save_settings(
                {
                    "scope": "book",
                    "book_id": "book-one",
                    "config_revision": current["config_revision"],
                    "author_config_revision": current["author_config_revision"],
                    "registry": current["registry"],
                    "documents": [
                        {"id": prompt["id"], "revision": prompt["revision"], "content": "覆盖"}
                    ],
                }
            )

    def test_invalid_clock_time_is_rejected(self):
        current = settings_service.get_book_settings("book-one")
        registry = dict(current["registry"])
        registry["schedule_time"] = "29:00"
        with self.assertRaisesRegex(ValueError, "HH:MM"):
            settings_service.save_settings(
                {
                    "scope": "book",
                    "book_id": "book-one",
                    "config_revision": current["config_revision"],
                    "author_config_revision": current["author_config_revision"],
                    "registry": registry,
                    "documents": [],
                }
            )

    def test_creative_modules_can_be_saved_per_book(self):
        current = settings_service.get_book_settings("book-one")
        self.assertIn("world_presentation", current["creative_catalog"]["modules"])
        config = {"schema_version": 1, "modules": {"world_presentation": {"mode": "review", "from_chapter": 8}}}
        payload = {"scope": "book", "book_id": "book-one", "config_revision": current["config_revision"], "author_config_revision": current["author_config_revision"], "registry": current["registry"], "documents": [{"id": "creative_modules.json", "revision": "missing", "content": json.dumps(config)}]}
        settings_service.save_settings(payload)
        self.assertEqual(json.loads((self.book_path / "creative_modules.json").read_text()), config)

    def test_invalid_creative_config_does_not_write_anything(self):
        current = settings_service.get_book_settings("book-one")
        before = self.config_path.read_bytes()
        with self.assertRaises(ValueError):
            settings_service.save_settings({"scope": "book", "book_id": "book-one", "config_revision": current["config_revision"], "author_config_revision": current["author_config_revision"], "registry": current["registry"], "documents": [{"id": "creative_modules.json", "revision": "missing", "content": '{"schema_version":1,"modules":{"unknown":{"mode":"on"}}}'}]})
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertFalse((self.book_path / "creative_modules.json").exists())

    def test_book_save_rejects_missing_author(self):
        current = settings_service.get_book_settings("book-one")
        registry = dict(current["registry"])
        registry["author"] = ""
        with self.assertRaisesRegex(ValueError, "必须选择作者"):
            settings_service.save_settings(
                {
                    "scope": "book",
                    "book_id": "book-one",
                    "config_revision": current["config_revision"],
                    "author_config_revision": current["author_config_revision"],
                    "registry": registry,
                    "documents": [],
                }
            )

    def test_book_save_rejects_unknown_author(self):
        current = settings_service.get_book_settings("book-one")
        registry = dict(current["registry"])
        registry["author"] = "ghost"
        with self.assertRaisesRegex(ValueError, "作者不存在"):
            settings_service.save_settings(
                {
                    "scope": "book",
                    "book_id": "book-one",
                    "config_revision": current["config_revision"],
                    "author_config_revision": current["author_config_revision"],
                    "registry": registry,
                    "documents": [],
                }
            )

    def test_unbound_book_can_open_settings_and_choose_author(self):
        system = json.loads(self.author_system_path.read_text(encoding="utf-8"))
        del system["books"]["book-one"]
        self.author_system_path.write_text(json.dumps(system), encoding="utf-8")
        current = settings_service.get_book_settings("book-one")
        self.assertEqual(current["registry"]["author"], "")
        self.assertIn("尚未配置作者", current["author_binding_error"])
        registry = dict(current["registry"])
        registry["author"] = "owner"
        settings_service.save_settings(
            {
                "scope": "book",
                "book_id": "book-one",
                "config_revision": current["config_revision"],
                "author_config_revision": current["author_config_revision"],
                "registry": registry,
                "documents": [],
            }
        )
        rebound = json.loads(self.author_system_path.read_text(encoding="utf-8"))
        self.assertEqual(rebound["books"]["book-one"]["author"], "owner")
