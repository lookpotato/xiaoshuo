from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from novel_engine_v2 import NovelEngine


class NovelEngineV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        home = self.root / "novel_engine_v2"
        (home / "authors").mkdir(parents=True)
        project = self.root / "book"
        for folder in ("chapters", "drafts", "logs"):
            (project / folder).mkdir(parents=True, exist_ok=True)
        (project / "chapters" / "0001-开门.md").write_text("# 第 1 章 开门\n正文", encoding="utf-8")
        (project / "chapter_state.json").write_text(
            json.dumps({"next_chapter_number": 2}), encoding="utf-8"
        )
        for name in ("novel_config.md", "outline.md", "characters.md", "world.md"):
            (project / name).write_text(name, encoding="utf-8")
        (home / "system.json").write_text(json.dumps({
            "schema_version": 2,
            "limits": {"max_writer_modules": 2, "max_reader_modules": 2, "recent_chapters": 1, "max_author_context_chars": 8000},
            "legacy_adapter": {"book_sources": ["novel_config.md", "chapter_state.json"]},
            "books": {"demo": {"title": "测试", "project": "book", "author": "owner"}},
        }, ensure_ascii=False), encoding="utf-8")
        modules = {
            "core": {"title": "核心", "stage": "writer", "mandatory": True, "priority": 1000, "triggers": [], "purpose": "p", "instruction": "core"},
            "dialogue": {"title": "对白", "stage": "writer", "mandatory": False, "priority": 10, "triggers": ["dialogue"], "purpose": "p", "instruction": "dialogue"},
            "unused": {"title": "未选", "stage": "writer", "mandatory": False, "priority": 1, "triggers": ["battle"], "purpose": "p", "instruction": "unused"},
            "reader": {"title": "读者", "stage": "reader", "mandatory": True, "priority": 1000, "triggers": [], "purpose": "p", "instruction": "reader"},
        }
        (home / "modules.json").write_text(json.dumps({"schema_version": 2, "modules": modules}, ensure_ascii=False), encoding="utf-8")
        (home / "authors" / "owner.json").write_text(json.dumps({
            "schema_version": 1,
            "id": "owner",
            "creative_identity": ["作者规则"],
            "reader_contract": ["读者规则"],
            "language_principles": ["语言规则"],
            "default_signals": [],
        }, ensure_ascii=False), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_selects_only_relevant_writer_modules(self) -> None:
        engine = NovelEngine(self.root)
        selected = engine.select_modules("writer", {"dialogue"})
        self.assertEqual([item["id"] for item in selected], ["core", "dialogue"])

    def test_writer_context_excludes_legacy_prompt_pile(self) -> None:
        engine = NovelEngine(self.root)
        run = engine.prepare("demo", {"dialogue"})
        writer = (run / "writer.md").read_text(encoding="utf-8")
        self.assertIn("作者规则", writer)
        self.assertIn("对白", writer)
        self.assertNotIn("unused", writer)
        self.assertNotIn("automation_prompt.md", writer)

    def test_author_method_and_book_application_are_separate_and_bounded(self) -> None:
        author_path = self.root / "novel_engine_v2" / "authors" / "owner.json"
        author = json.loads(author_path.read_text(encoding="utf-8"))
        author["author_method"] = ["先让关系推动场景"]
        author["book_application"] = ["本书先写一扇打不开的门"]
        author_path.write_text(json.dumps(author, ensure_ascii=False), encoding="utf-8")
        engine = NovelEngine(self.root)
        run = engine.prepare("demo", set())
        writer = (run / "writer.md").read_text(encoding="utf-8")
        self.assertIn("### 创作方法", writer)
        self.assertIn("### 本书应用", writer)
        self.assertLessEqual(len(engine._compile_author_context(author)), 8000)

        author["book_application"] = ["字" * 8001]
        author_path.write_text(json.dumps(author, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "不得继续堆提示词"):
            NovelEngine(self.root).author("owner")

    def test_reader_isolated_from_world_and_outline(self) -> None:
        engine = NovelEngine(self.root)
        run = engine.prepare("demo", set())
        reader = (run / "reader.md").read_text(encoding="utf-8")
        self.assertIn("只读取", reader)
        self.assertNotIn("world.md", reader)
        self.assertNotIn("outline.md", reader)

    def test_review_cannot_pass_with_blocking_issues(self) -> None:
        engine = NovelEngine(self.root)
        run = engine.prepare("demo", set())
        (run / "reader_review.json").write_text(json.dumps({
            "decision": "pass",
            "blocking_issues": ["人物身份不明"],
            "nonblocking_notes": [],
            "evidence": [],
        }, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "不能存在"):
            engine.validate_review(run)

    def test_contract_requires_bounded_writer_context(self) -> None:
        engine = NovelEngine(self.root)
        run = engine.prepare("demo", set())
        contract = {
            "continuation": "接上开门",
            "single_mission": "进屋",
            "protagonist_want": "找人",
            "opposition": "门被锁住",
            "choice": "破窗",
            "visible_result": "进入屋内",
            "next_reason": "屋里有人",
            "cast": ["甲"],
            "required_context": ["门的位置"],
            "protected_unknowns": ["屋内人的身份"],
        }
        (run / "chapter_contract.json").write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
        (run / "writer_context.md").write_text("甲来自上一章。", encoding="utf-8")
        self.assertEqual(engine.validate_contract(run)["single_mission"], "进屋")
        (run / "writer_context.md").write_text("字" * 6001, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "6000"):
            engine.validate_contract(run)

    def test_new_book_with_opening_contract_does_not_require_old_chapter(self) -> None:
        project = self.root / "book"
        (project / "chapters" / "0001-开门.md").unlink()
        (project / "chapter_state.json").write_text(
            json.dumps({"next_chapter_number": 1}), encoding="utf-8"
        )
        (project / "opening_contract.md").write_text("先写一个人开门。", encoding="utf-8")
        engine = NovelEngine(self.root)
        self.assertEqual(engine.validate_project("demo"), [])

    def test_candidate_requires_matching_chapter_heading(self) -> None:
        engine = NovelEngine(self.root)
        run = engine.prepare("demo", set())
        (run / "candidate.md").write_text("# 第 3 章 错章\n" + "正文" * 900, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "第 2 章"):
            engine.validate_candidate(run, 2)

    def test_repository_free_sky_rewrite_is_a_separate_authored_book(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        engine = NovelEngine(project_root)
        legacy = engine.book("free-sky")
        book = engine.book("free-sky-rewrite")
        self.assertNotEqual(legacy.project, book.project)
        self.assertEqual(legacy.project.name, "道友你这天命与我有缘")
        self.assertEqual(book.project.name, "道友你这天命与我有缘_重写版")
        self.assertGreaterEqual(engine.next_chapter(book), 1)
        self.assertEqual(book.author, "free-sky-rulebreaker")
        author = engine.author(book.author)
        self.assertEqual(author["scope"], ["free-sky", "free-sky-rewrite"])
        self.assertIn("人物关系", author["decision_order"][0])
        self.assertTrue(author["author_method"])
        self.assertTrue(author["book_application"])
        self.assertIn("机制解释重复", author["calibration_evidence"]["finding"])
        manifest = engine.context_manifest(book, engine.next_chapter(book))
        self.assertTrue(any(
            path.endswith("free-sky-rewrite-blueprint.md")
            for path in manifest["book_sources"]
        ))


if __name__ == "__main__":
    unittest.main()
