from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import human_draft_service as service
from novel_reader_gate import narrative_sha256


class HumanDraftServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "novel_engine_v3").mkdir()
        (self.root / "novel_engine_v3" / "system.json").write_text(
            json.dumps({"schema_version": 3, "books": {"book": {"project": "book"}}}), encoding="utf-8"
        )
        self.project = self.root / "book"
        (self.project / "chapter_plans").mkdir(parents=True)
        (self.project / "chapters").mkdir()
        (self.project / "chapter_state.json").write_text(
            json.dumps({"last_completed_chapter": 2, "next_chapter_number": 3}), encoding="utf-8"
        )
        (self.project / "chapter_plans" / "0002.json").write_text(json.dumps({
            "chapter_number": 2, "immediate_goal": "先找到能住的屋",
            "central_conflict": "两人争一把钥匙", "central_choice": "谁留下",
            "reader_payoff": {"on_page_event": "门终于打开"},
            "ending_pressure": {"visible_trigger": "灯灭了"},
            "viewpoint_contract": "贴甲的视角", "story_clock": {"start_day": 2, "end_day": 2},
            "scene_plan": [{"purpose": "上楼", "conflict": "灯坏", "turn": "找到门"}],
        }, ensure_ascii=False), encoding="utf-8")
        (self.project / "chapters" / "0002-旧稿.md").write_text("# 旧稿\n\n原文", encoding="utf-8")

    def test_scene_guide_and_draft_revision(self) -> None:
        initial = service.workspace(self.root, "book", 2)
        self.assertEqual(initial["guide"]["scenes"][0]["turn"], "找到门")
        draft = "# 第二章 新稿\n\n" + "他推门。" * 55
        saved = service.save_draft(self.root, "book", 2, draft, initial["draft_revision"])
        self.assertEqual(service.workspace(self.root, "book", 2)["draft"], draft)
        with self.assertRaisesRegex(ValueError, "已在别处更新"):
            service.save_draft(self.root, "book", 2, "覆盖", initial["draft_revision"])
        self.assertEqual(saved["draft_revision"], service.workspace(self.root, "book", 2)["draft_revision"])

    def test_scene_explanation_is_hidden_when_plan_changes(self) -> None:
        plan_path = self.project / "chapter_plans" / "0002.json"
        revision = service.workspace(self.root, "book", 2)["plan_revision"]
        guide_path = self.project / "human_guides" / "0002.md"
        guide_path.parent.mkdir()
        guide_path.write_text(f"<!-- plan-sha256: {revision} -->\n# 新说明\n", encoding="utf-8")
        current = service.workspace(self.root, "book", 2)
        self.assertIn("新说明", current["scene_explanation"])
        self.assertFalse(current["guide_stale"])
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["immediate_goal"] = "改后的目标"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        changed = service.workspace(self.root, "book", 2)
        self.assertEqual(changed["guide"]["goal"], "改后的目标")
        self.assertNotIn("新说明", changed["scene_explanation"])
        self.assertIn("改后的目标", changed["scene_explanation"])
        self.assertTrue(changed["guide_stale"])

    def test_new_plan_embedded_explanation_is_displayed_without_guide_file(self) -> None:
        plan_path = self.project / "chapter_plans" / "0002.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["scene_explanation"] = "人物在门前遇到阻力，决定谁留下。"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(service.workspace(self.root, "book", 2)["scene_explanation"], plan["scene_explanation"])

    def test_archived_review_only_follows_its_chapter_version(self) -> None:
        chapter_path = self.project / "chapters" / "0002-旧稿.md"
        review_path = self.project / "chapter_reviews" / "0002.json"
        review_path.parent.mkdir()
        review_path.write_text(json.dumps({
            "chapter_number": 2, "narrative_sha256": narrative_sha256(chapter_path),
            "reader": {"summary": "主线清楚"},
        }, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(service.workspace(self.root, "book", 2)["final_review"]["reader"]["summary"], "主线清楚")
        chapter_path.write_text("# 旧稿\n\n改过的正文", encoding="utf-8")
        changed = service.workspace(self.root, "book", 2)
        self.assertIsNone(changed["final_review"])
        self.assertTrue(changed["final_review_stale"])

    def test_polish_produces_preview_without_replacing_original_or_archive(self) -> None:
        original = "# 第二章 新稿\n\n" + "他推门，先看见灯。" * 50
        revision = service.workspace(self.root, "book", 2)["draft_revision"]
        service.save_draft(self.root, "book", 2, original, revision)
        archive = self.project / "chapters" / "0002-旧稿.md"

        def fake_execute(_prompt, output, run, **kwargs):
            self.assertTrue(kwargs["capture_candidate"])
            self.assertIn("作者亲自写", _prompt.read_text(encoding="utf-8"))
            (run / "candidate.md").write_text(original.replace("先看见灯", "看见了灯"), encoding="utf-8")
            output.write_text("done", encoding="utf-8")

        with patch.object(service, "execute", side_effect=fake_execute):
            preview = service.polish(self.root, "book", 2)
        self.assertIn("看见了灯", preview.read_text(encoding="utf-8"))
        self.assertEqual((self.project / "human_drafts" / "0002.md").read_text(encoding="utf-8"), original)
        self.assertEqual(archive.read_text(encoding="utf-8"), "# 旧稿\n\n原文")
        self.assertFalse(service.workspace(self.root, "book", 2)["preview_stale"])
        service.save_draft(self.root, "book", 2, original + "又一句。", service.workspace(self.root, "book", 2)["draft_revision"])
        self.assertTrue(service.workspace(self.root, "book", 2)["preview_stale"])
        self.assertEqual(service.workspace(self.root, "book", 2)["preview"], "")

    def test_polish_requires_full_draft(self) -> None:
        with self.assertRaisesRegex(ValueError, "200 汉字"):
            service.validate_polish_request(self.root, "book", 2)
        with self.assertRaisesRegex(ValueError, "未知 V3 作品"):
            service.workspace(self.root, "unknown", 2)

    def test_plan_request_only_allows_unplanned_next_chapter(self) -> None:
        service.validate_plan_request(self.root, "book", 3)
        with self.assertRaisesRegex(ValueError, "已有规划"):
            service.validate_plan_request(self.root, "book", 2)
        with self.assertRaisesRegex(ValueError, "当前下一章"):
            service.validate_plan_request(self.root, "book", 4)


if __name__ == "__main__":
    unittest.main()
