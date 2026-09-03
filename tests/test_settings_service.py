import json
from pathlib import Path
from unittest import TestCase, mock

import settings_service


class SettingsServiceTest(TestCase):
    def setUp(self):
        self.temp = self.enterContext(__import__("tempfile").TemporaryDirectory())
        self.root = Path(self.temp)
        self.config_path = self.root / "manager_config.json"
        self.book_path = self.root / "book-one"
        self.book_path.mkdir()
        (self.root / "shared").mkdir()
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
        self.enterContext(mock.patch.object(settings_service, "ROOT", self.root))
        self.enterContext(mock.patch.object(settings_service, "CONFIG_PATH", self.config_path))
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

    def test_system_settings_expose_registry_and_shared_modules(self):
        result = settings_service.get_system_settings()
        self.assertEqual(result["modules"][0]["id"], "character_engine")
        ids = {item["id"] for item in result["documents"]}
        self.assertIn("shared/character_engine.md", ids)
        self.assertIn("shared/chinese_dialogue_foundation.md", ids)

    def test_save_book_updates_registry_and_document_without_losing_unknown_fields(self):
        current = settings_service.get_book_settings("book-one")
        registry = dict(current["registry"])
        registry.update({"title": "新书名", "daily_chapter_target": 3})
        prompt = next(item for item in current["documents"] if item["id"] == "automation_prompt.md")
        saved = settings_service.save_settings(
            {
                "scope": "book",
                "book_id": "book-one",
                "config_revision": current["config_revision"],
                "registry": registry,
                "documents": [
                    {"id": prompt["id"], "revision": prompt["revision"], "content": "新提示词\n"}
                ],
            }
        )
        written = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(written["books"][0]["title"], "新书名")
        self.assertEqual(written["books"][0]["daily_chapter_target"], 3)
        self.assertEqual(written["books"][0]["keep_me"], "untouched")
        self.assertEqual((self.book_path / "automation_prompt.md").read_text(encoding="utf-8"), "新提示词\n")
        self.assertTrue(saved["saved"])

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
                    "registry": registry,
                    "documents": [],
                }
            )
