import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

import character_story_service


class CharacterStoryServiceTest(TestCase):
    def setUp(self):
        self.temp = self.enterContext(TemporaryDirectory())
        self.root = Path(self.temp)
        self.config_path = self.root / "manager_config.json"
        self.config_path.write_text(
            json.dumps({"books": [{"id": "book", "path": "book", "title": "测试书"}]}),
            encoding="utf-8",
        )
        thread_root = self.root / "book" / "character_threads"
        (thread_root / "0001").mkdir(parents=True)
        (thread_root / "0002").mkdir()
        (thread_root / "0001" / "01-甲.md").write_text("# 甲\n\n- 目标：先救人。\n", encoding="utf-8")
        (thread_root / "0002" / "01-甲.md").write_text("# 甲\n\n- 代价：受伤。\n", encoding="utf-8")
        (thread_root / "0002" / "02-乙.md").write_text("# 乙\n\n- 行动：独自离开。\n", encoding="utf-8")
        (thread_root / "0002" / "00-cast.md").write_text("# 名单", encoding="utf-8")
        (thread_root / "01-旧格式.md").write_text("不应读取", encoding="utf-8")
        self.enterContext(mock.patch.object(character_story_service, "ROOT", self.root))
        self.enterContext(mock.patch.object(character_story_service, "CONFIG_PATH", self.config_path))
        self.enterContext(mock.patch.object(character_story_service.settings_service, "settings_lock", return_value=None))

    def test_groups_private_threads_by_character_and_chapter(self):
        result = character_story_service.get_character_stories("book")
        self.assertEqual([item["name"] for item in result["characters"]], ["甲", "乙"])
        self.assertEqual(result["selected_character"], "甲")
        self.assertEqual([item["chapter"] for item in result["entries"]], [2, 1])
        self.assertNotIn("00-cast.md", {item["id"] for item in result["entries"]})

    def test_can_select_another_character(self):
        result = character_story_service.get_character_stories("book", "乙")
        self.assertEqual(result["selected_character"], "乙")
        self.assertEqual(len(result["entries"]), 1)

    def test_save_updates_only_selected_existing_thread(self):
        current = character_story_service.get_character_stories("book", "乙")
        entry = current["entries"][0]
        saved = character_story_service.save_character_story(
            {
                "book_id": "book",
                "character": "乙",
                "entry_id": entry["id"],
                "revision": entry["revision"],
                "content": "# 乙\n\n- 下一步：回头救甲。\n",
            }
        )
        self.assertTrue(saved["saved"])
        self.assertIn("回头救甲", saved["entries"][0]["content"])

    def test_rejects_stale_revision_and_path_spoofing(self):
        current = character_story_service.get_character_stories("book", "甲")
        entry = current["entries"][0]
        with self.assertRaises(character_story_service.settings_service.SettingsConflict):
            character_story_service.save_character_story(
                {"book_id": "book", "character": "甲", "entry_id": entry["id"], "revision": "stale", "content": "覆盖"}
            )
        with self.assertRaisesRegex(ValueError, "不存在"):
            character_story_service.save_character_story(
                {"book_id": "book", "character": "甲", "entry_id": "../characters.md", "revision": entry["revision"], "content": "越界"}
            )
