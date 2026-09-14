from pathlib import Path
from unittest import TestCase, mock

import xiaoshuo_on_demand


class LengthPromptIntegrationTest(TestCase):
    def test_exempt_chapter_overrides_novel_and_author_length_instructions(self):
        book = {
            "id": "test-book",
            "mode": "write_only",
            "word_count_limit_enabled": True,
            "word_count_exempt_chapters": [7],
        }
        with (
            mock.patch.object(
                xiaoshuo_on_demand.manager, "config", return_value={"books": [book]}
            ),
            mock.patch.object(
                xiaoshuo_on_demand, "project_for", return_value=Path("test-project")
            ),
            mock.patch.object(
                xiaoshuo_on_demand, "author_prompt_context", return_value=""
            ),
            mock.patch.object(
                xiaoshuo_on_demand.manager,
                "read_json",
                return_value={"next_chapter_number": 7},
            ),
            mock.patch.object(
                xiaoshuo_on_demand.stage_pipeline, "enabled_for", return_value=False
            ),
            mock.patch.object(
                xiaoshuo_on_demand.stage_pipeline,
                "available_book_sources",
                return_value=[],
            ),
            mock.patch.object(
                xiaoshuo_on_demand.creative_modules,
                "prompt",
                return_value="作者档案要求约两千字",
            ),
        ):
            prompt = xiaoshuo_on_demand.local_write_only_prompt(
                "test-book", {"id": "job-test"}
            )

        self.assertIn("作者档案要求约两千字", prompt)
        self.assertIn("第 7 章已关闭字数限制", prompt)
        self.assertIn("忽略本书 novel_config.md", prompt)
        self.assertIn("不豁免读者验收", prompt)
