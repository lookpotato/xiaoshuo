from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import chapter_admin_service as service


class ChapterAdminServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.project = Path(self.temp.name) / "book"
        for folder in (
            "chapters", "drafts", "chapter_plans", "reader_checks",
            "dialogue_reviews", "literary_reviews", "character_threads",
            "reader_feedback", "images",
        ):
            (self.project / folder).mkdir(parents=True, exist_ok=True)
        for number in (1, 2, 3):
            (self.project / "chapters" / f"{number:04d}-chapter.md").write_text(
                f"# 第 {number} 章\n\n正文", encoding="utf-8"
            )
            for folder in ("chapter_plans", "reader_checks", "dialogue_reviews", "literary_reviews"):
                (self.project / folder / f"{number:04d}.json").write_text(
                    "{}", encoding="utf-8"
                )
            (self.project / "character_threads" / f"{number:04d}").mkdir()
        (self.project / "drafts" / "2026-09-18-chapter-0002.md").write_text(
            "draft", encoding="utf-8"
        )
        feedback = self.project / "reader_feedback" / "feedback-two"
        feedback.mkdir()
        (feedback / "feedback.json").write_text(
            json.dumps({"chapter": 2}), encoding="utf-8"
        )
        (self.project / "chapter_state.json").write_text(
            json.dumps({
                "last_completed_chapter": 3, "next_chapter_number": 4,
                "last_uploaded_chapter": 0, "notes_for_next_chapter": "第三章之后",
            }),
            encoding="utf-8",
        )
        (self.project / "continuity_ledger.md").write_text(
            "# 连续性账本\n\n## 第 1 章后\n\n保留\n\n"
            "## 第 2 章后\n\n删除二\n\n## 第 3 章后\n\n删除三\n",
            encoding="utf-8",
        )
        (self.project / "images" / "catalog.json").write_text(
            json.dumps({"chapter_images": {"1": {}, "2": {}, "3": {}}}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_deletes_tail_recoverably_and_rewinds_state(self) -> None:
        result = service.delete_chapter_tail(
            self.project, "demo", 2, confirmed_chapter=2
        )
        self.assertEqual(result["deleted_chapters"], [2, 3])
        self.assertEqual(result["next_chapter_number"], 2)
        self.assertTrue((self.project / "chapters" / "0001-chapter.md").is_file())
        self.assertFalse((self.project / "chapters" / "0002-chapter.md").exists())
        self.assertFalse((self.project / "chapters" / "0003-chapter.md").exists())
        self.assertFalse((self.project / "reader_checks" / "0002.json").exists())
        self.assertFalse((self.project / "character_threads" / "0003").exists())
        state = json.loads((self.project / "chapter_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["last_completed_chapter"], 1)
        self.assertEqual(state["next_chapter_number"], 2)
        ledger = (self.project / "continuity_ledger.md").read_text(encoding="utf-8")
        self.assertIn("第 1 章后", ledger)
        self.assertNotIn("第 2 章后", ledger)
        catalog = json.loads((self.project / "images" / "catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(set(catalog["chapter_images"]), {"1"})
        trash = self.project / ".chapter_trash" / result["trash_id"]
        self.assertTrue((trash / "manifest.json").is_file())
        self.assertTrue((trash / "files" / "chapters" / "0002-chapter.md").is_file())
        self.assertTrue((trash / "before" / "chapter_state.json").is_file())

    def test_rejects_uploaded_tail(self) -> None:
        state_path = self.project / "chapter_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["last_uploaded_chapter"] = 2
        state_path.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "已经上传"):
            service.delete_chapter_tail(
                self.project, "demo", 2, confirmed_chapter=2
            )
        self.assertTrue((self.project / "chapters" / "0002-chapter.md").is_file())

    def test_requires_exact_confirmation(self) -> None:
        with self.assertRaisesRegex(ValueError, "确认信息不一致"):
            service.delete_chapter_tail(
                self.project, "demo", 2, confirmed_chapter=3
            )


if __name__ == "__main__":
    unittest.main()
