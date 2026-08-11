from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from fanqie_browser_worker import (
    Chapter,
    PublishConfig,
    author_note_text,
    parse_chapter,
    publish,
    save_author_note,
    select_author_note_image,
    select_fanqie_crop_ratio,
)


class FanqieImageMarkdownTests(unittest.TestCase):
    def test_local_image_is_not_uploaded_as_plain_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "chapters").mkdir()
            (project / "images" / "items").mkdir(parents=True)
            image = project / "images" / "items" / "soul-lock-v1.png"
            image.write_bytes(b"test")
            chapter = project / "chapters" / "0001-test.md"
            prose = "这是用于测试上传正文的句子。" * 100
            chapter.write_text(
                "# 第1章 测试章节\n\n"
                + prose
                + "\n\n![锁魂钉](../images/items/soul-lock-v1.png)\n\n"
                + prose
                + "\n\n---\n\n## Metadata\n\n- upload_status: not_uploaded\n",
                encoding="utf-8",
            )
            parsed = parse_chapter(chapter)
            self.assertNotIn("![", parsed.body)
            self.assertNotIn("../images/", parsed.body)
            self.assertEqual(parsed.body.count(prose), 2)
            self.assertEqual(parsed.image_path, image.resolve())
            self.assertEqual(parsed.image_alt_text, "锁魂钉")
            self.assertEqual(parsed.image_crop_ratio, "1:1")

    def test_catalog_crop_ratio_overrides_folder_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "chapters").mkdir()
            (project / "images" / "characters").mkdir(parents=True)
            image = project / "images" / "characters" / "hero.png"
            image.write_bytes(b"test")
            (project / "images" / "catalog.json").write_text(
                json.dumps(
                    {
                        "entities": {
                            "character:hero": {
                                "image": {
                                    "path": "images/characters/hero.png",
                                    "display": {"fanqie_crop_ratio": "9:16"},
                                }
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            chapter = project / "chapters" / "0001-test.md"
            chapter.write_text(
                "# 第1章 测试章节\n\n" + "正文" * 600
                + "\n\n![主角](../images/characters/hero.png)\n",
                encoding="utf-8",
            )
            self.assertEqual(parse_chapter(chapter).image_crop_ratio, "9:16")

    def test_more_than_one_local_image_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "chapters").mkdir()
            (project / "images" / "items").mkdir(parents=True)
            for name in ("first.png", "second.png"):
                (project / "images" / "items" / name).write_bytes(b"test")
            chapter = project / "chapters" / "0001-test.md"
            chapter.write_text(
                "# 第1章 测试章节\n\n"
                + "正文" * 600
                + "\n\n![第一张](../images/items/first.png)\n"
                + "\n![第二张](../images/items/second.png)\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "最多只能包含 1 张"):
                parse_chapter(chapter)

    def test_author_note_uses_the_image_description(self) -> None:
        chapter = Chapter(
            number=1,
            title="测试章节",
            body="正文" * 600,
            path=Path("C:/test-book/chapters/0001-test.md"),
            image_path=Path("C:/test-book/images/items/test.png"),
            image_alt_text="  陈序   在修理站门前  ",
        )
        self.assertEqual(author_note_text(chapter), "本章配图：陈序 在修理站门前")

    def test_author_note_falls_back_to_chapter_title(self) -> None:
        chapter = Chapter(
            number=1,
            title="没建成的站不能收人",
            body="正文" * 600,
            path=Path("C:/test-book/chapters/0001-test.md"),
        )
        self.assertEqual(author_note_text(chapter), "本章配图：没建成的站不能收人")

    def test_image_selection_clicks_visible_dropzone(self) -> None:
        page = MagicMock()
        upload_control = MagicMock()
        file_inputs = Mock()
        upload_control.locator.return_value = file_inputs
        file_inputs.count.return_value = 1
        chooser_info = MagicMock()
        page.expect_file_chooser.return_value = chooser_info
        chooser_info.__enter__.return_value = chooser_info
        image_path = Path("C:/test-book/images/items/test.png")

        select_author_note_image(page, upload_control, image_path)

        upload_control.locator.assert_called_once_with("input[type='file']")
        page.expect_file_chooser.assert_called_once_with(timeout=5_000)
        upload_control.click.assert_called_once_with()
        chooser_info.value.set_files.assert_called_once_with(str(image_path))

    def test_crop_selector_clicks_exact_catalog_ratio(self) -> None:
        dialog = MagicMock()
        option = Mock()
        option.is_visible.return_value = True
        option.is_enabled.return_value = True
        dialog.get_by_text.return_value.all.return_value = [option]

        select_fanqie_crop_ratio(dialog, "2:3")

        dialog.get_by_text.assert_called_once_with("2:3", exact=True)
        option.click.assert_called_once_with()

    def test_author_note_save_waits_for_edit_state(self) -> None:
        page = Mock()
        scope = MagicMock()
        save = Mock()
        config = PublishConfig(
            writer_url="https://fanqienovel.com/test",
            book_id="test-book",
            submit_publish=False,
        )

        with (
            patch("fanqie_browser_worker._author_note_scope", return_value=scope),
            patch(
                "fanqie_browser_worker._single_visible_control",
                return_value=save,
            ),
            patch("fanqie_browser_worker.visible_matches", return_value=[Mock()]),
            patch("fanqie_browser_worker.assert_safe_page"),
        ):
            save_author_note(page, config)

        scope.get_by_text.assert_any_call("保存", exact=True)
        save.click.assert_called_once_with()
        scope.get_by_text.assert_any_call("编辑", exact=True)

    def test_publish_uploads_author_note_image_before_next_step(self) -> None:
        project = Path("C:/test-book")
        chapter = Chapter(
            number=1,
            title="测试章节",
            body="正文" * 600,
            path=project / "chapters" / "0001-test.md",
            image_path=project / "images" / "items" / "test.png",
            image_alt_text="测试图片",
        )
        config = PublishConfig(
            writer_url="https://fanqienovel.com/test",
            book_id="test-book",
            submit_publish=False,
        )
        page = Mock()
        context = Mock()
        playwright_context = MagicMock()
        playwright_context.__enter__.return_value = Mock()
        playwright_context.__exit__.return_value = False
        events: list[str] = []

        with (
            patch("fanqie_browser_worker.parse_publish_config", return_value=config),
            patch("fanqie_browser_worker.parse_chapter", return_value=chapter),
            patch("fanqie_browser_worker.sync_playwright", return_value=playwright_context),
            patch("fanqie_browser_worker.launch_context", return_value=context),
            patch("fanqie_browser_worker.active_page", return_value=page),
            patch(
                "fanqie_browser_worker.wait_editor",
                side_effect=lambda *_args: events.append("wait_editor"),
            ),
            patch(
                "fanqie_browser_worker.fill_editor",
                side_effect=lambda *_args: events.append("fill_editor"),
            ),
            patch(
                "fanqie_browser_worker.upload_author_note_image",
                side_effect=lambda *_args: events.append("upload_image"),
            ),
            patch("fanqie_browser_worker.assert_safe_page"),
            patch("fanqie_browser_worker.visible_button", return_value=Mock()),
            patch(
                "fanqie_browser_worker.click_with_hidden_overlay_guard",
                side_effect=lambda *_args: events.append("next"),
            ),
            patch("fanqie_browser_worker.wait_through_publish_checks"),
            patch("fanqie_browser_worker.click_if_visible", return_value=True),
        ):
            result = publish(project, chapter.path, "2026-08-12", "12:00")

        self.assertEqual(result["status"], "draft_saved")
        self.assertTrue(result["author_note_image_uploaded"])
        self.assertLess(events.index("fill_editor"), events.index("upload_image"))
        self.assertLess(events.index("upload_image"), events.index("next"))


if __name__ == "__main__":
    unittest.main()
