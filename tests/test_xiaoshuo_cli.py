from __future__ import annotations

import unittest
from unittest.mock import patch

import xiaoshuo


BOOKS = [
    {
        "id": "first",
        "title": "第一本",
        "enabled": True,
        "priority": 10,
        "manual_extra_chapters_supported": False,
    },
    {
        "id": "second",
        "title": "第二本",
        "enabled": True,
        "priority": 20,
        "manual_extra_chapters_supported": True,
    },
    {
        "id": "disabled",
        "title": "停用书",
        "enabled": False,
        "priority": 100,
        "manual_extra_chapters_supported": True,
    },
]


class BookSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.loader = patch.object(
            xiaoshuo,
            "load_books",
            return_value=({"default_book_id": "first"}, BOOKS),
        )
        self.loader.start()

    def tearDown(self) -> None:
        self.loader.stop()

    def test_uses_configured_default_book(self) -> None:
        self.assertEqual(
            [book["id"] for book in xiaoshuo.selected_books(None, False, "update")],
            ["first"],
        )

    def test_all_updates_enabled_books_by_priority(self) -> None:
        self.assertEqual(
            [book["id"] for book in xiaoshuo.selected_books(None, True, "update")],
            ["second", "first"],
        )

    def test_all_rewards_only_supported_books(self) -> None:
        self.assertEqual(
            [book["id"] for book in xiaoshuo.selected_books(None, True, "reward")],
            ["second"],
        )

    def test_explicit_unsupported_reward_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "未启用平台加更"):
            xiaoshuo.selected_books("first", False, "reward")

    def test_unknown_book_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "未知书籍"):
            xiaoshuo.selected_books("missing", False, "update")


class SequentialRunTests(unittest.TestCase):
    def test_omitted_count_uses_book_daily_target(self) -> None:
        self.assertEqual(xiaoshuo.target_count(BOOKS[0], None), 1)
        self.assertEqual(xiaoshuo.target_count({**BOOKS[0], "daily_chapter_target": 3}, None), 3)
        self.assertEqual(xiaoshuo.target_count(BOOKS[0], 5), 5)

    def test_invalid_daily_target_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "daily_chapter_target"):
            xiaoshuo.target_count({**BOOKS[0], "daily_chapter_target": 0}, None)

    @patch.object(xiaoshuo.subprocess, "run")
    def test_continues_after_one_book_fails(self, run) -> None:
        run.side_effect = [
            type("Result", (), {"returncode": 3})(),
            type("Result", (), {"returncode": 0})(),
        ]
        result = xiaoshuo.run_commands(
            [(BOOKS[0], ["first"]), (BOOKS[1], ["second"])]
        )
        self.assertEqual(result, 3)
        self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
