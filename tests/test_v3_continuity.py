from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from novel_engine_v3 import V3ValidationError
from novel_engine_v3 import AuthorEngine
from novel_engine_v3.continuity import (
    stamp_candidate, validate_characters, validate_chronology, validate_review,
    validate_viewpoint_contract, validate_timeline_head,
)


class UnifiedContinuityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "book"
        self.run = self.root / ".novel_runs_v3" / "night-floor" / "0010-test"
        self.project.mkdir()
        self.run.mkdir(parents=True)
        (self.root / "novel_pipeline.json").write_text(json.dumps({
            "schema_version": 1, "enabled": True,
            "recent_chapters_for_director": 5, "recent_chapters_for_reviewer": 2,
            "max_new_questions_per_chapter": 1,
            "plan_directory": "chapter_plans", "review_directory": "literary_reviews",
        }), encoding="utf-8")
        (self.project / "chapter_plans").mkdir()
        (self.project / "timeline_state.json").write_text(json.dumps({
            "last_chapter": 9, "day": 5, "floor": 10, "after_2347": False,
        }), encoding="utf-8")

    def write_clock(self, **changes):
        clock = {
            "start_day": 5, "end_day": 5,
            "start_after_2347": False, "end_after_2347": True,
            "start_floor": 10, "end_floor": 11, "crossed_2347": 1,
        } | changes
        (self.project / "chapter_plans" / "0010.json").write_text(
            json.dumps({"story_clock": clock}), encoding="utf-8"
        )
        review = {"chapter": 10, **clock,
                  "events": [{"day": 5, "floor": 11, "quote": "23:47，十一楼出现了。"}],
                  "start_quote": "第五天早晨", "end_quote": "十一楼出现了。"}
        (self.run / "chronology.json").write_text(json.dumps(review), encoding="utf-8")
        (self.run / "candidate.md").write_text(
            "# 第十章\n\n第五天早晨，她出门。\n23:47，十一楼出现了。",
            encoding="utf-8",
        )

    def test_timeline_head_rejects_stale_reset_before_planning(self):
        (self.project / "chapter_state.json").write_text(json.dumps({
            "last_completed_chapter": 9, "next_chapter_number": 10,
        }), encoding="utf-8")
        validate_timeline_head(self.project, 10)
        (self.project / "timeline_state.json").write_text(json.dumps({
            "last_chapter": 10, "day": 5, "floor": 10, "after_2347": False,
        }), encoding="utf-8")
        with self.assertRaisesRegex(V3ValidationError, "章节状态与时间线未同步"):
            validate_timeline_head(self.project, 10)

    def test_chronology_advances_only_when_night_and_floor_match(self):
        self.write_clock()
        self.assertEqual(validate_chronology(self.run, self.project, 10), {
            "last_chapter": 10, "day": 5, "floor": 11, "after_2347": True,
        })
        self.assertEqual(json.loads((self.project / "timeline_state.json").read_text())["floor"], 10)

    def test_chronology_rejects_missing_floor_and_mismatched_plan(self):
        self.write_clock()
        path = self.run / "chronology.json"
        review = json.loads(path.read_text(encoding="utf-8"))
        review["events"] = []
        path.write_text(json.dumps(review), encoding="utf-8")
        with self.assertRaisesRegex(V3ValidationError, "事件数量"):
            validate_chronology(self.run, self.project, 10)
        review["events"] = [{"day": 5, "floor": 11, "quote": "23:47，十一楼出现了。"}]
        review["end_floor"] = 10
        path.write_text(json.dumps(review), encoding="utf-8")
        with self.assertRaises(V3ValidationError):
            validate_chronology(self.run, self.project, 10)

    def test_character_and_review_artifacts_are_required(self):
        directory = self.project / "character_threads" / "0010"
        directory.mkdir(parents=True)
        (directory / "00-cast.md").write_text("## 角色名单\n- 周寻：维修", encoding="utf-8")
        (directory / "interaction_map.md").write_text("周寻与邻居碰面。", encoding="utf-8")
        with self.assertRaisesRegex(V3ValidationError, "人物私线"):
            validate_characters(self.project, 10, final=False)
        (directory / "01-周寻.md").write_text("周寻自己去检查。", encoding="utf-8")
        validate_characters(self.project, 10, final=False)
        with self.assertRaisesRegex(V3ValidationError, "state_update"):
            validate_characters(self.project, 10, final=True)
        (directory / "state_update.md").write_text("周寻：继续检查。", encoding="utf-8")
        validate_characters(self.project, 10, final=True)
        self.write_clock()
        (self.run / "continuity_review.json").write_text(
            '{"chapter":10,"blocking":[],"next_hook":"十一楼已出现","unresolved":[]}', encoding="utf-8"
        )
        stamp_candidate(self.run)
        validate_review(self.run, 10)
        (self.run / "candidate.md").write_text("# 已被替换", encoding="utf-8")
        with self.assertRaisesRegex(V3ValidationError, "正文发生变化"):
            validate_review(self.run, 10)

    def test_viewpoint_mismatch_is_stopped_before_drafting(self):
        plan = self.project / "chapter_plans" / "0010.json"
        plan.write_text(json.dumps({
            "schema_version": 3,
            "viewpoint_contract": "田小雨为主视角；夜间楼内行动由电话获报。",
        }, ensure_ascii=False), encoding="utf-8")
        directory = self.project / "character_threads" / "0010"
        directory.mkdir(parents=True)
        interaction = directory / "interaction_map.md"
        interaction.write_text("夜间直接跟随周寻上楼。", encoding="utf-8")
        with self.assertRaisesRegex(V3ValidationError, "视角约定不一致"):
            validate_viewpoint_contract(self.root, self.project, 10)
        interaction.write_text(
            "**视角合同**：田小雨为主视角；夜间楼内行动由电话获报。\n",
            encoding="utf-8",
        )
        validate_viewpoint_contract(self.root, self.project, 10)

    def test_prepare_wires_director_characters_and_clock_into_one_run(self):
        home = self.root / "novel_engine_v3"
        home.mkdir()
        (home / "system.json").write_text(json.dumps({
            "schema_version": 3,
            "books": {"night-floor": {
                "title": "我家旧楼每晚多一层", "project": "book",
                "sources": ["timeline_state.json"],
            }},
        }, ensure_ascii=False), encoding="utf-8")
        (self.project / "chapter_state.json").write_text(
            '{"next_chapter_number":10}', encoding="utf-8"
        )
        run = AuthorEngine(self.root).prepare("night-floor")
        manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["recent_chapters"], [])
        self.assertEqual(manifest["workflow"][0:3], ["director", "characters", "author_room"])
        self.assertIn("story_clock", (run / "director.md").read_text(encoding="utf-8"))
        self.assertIn("state_update.md", (run / "continuity.md").read_text(encoding="utf-8"))
        self.assertIn("viewpoint_contract", (run / "characters.md").read_text(encoding="utf-8"))
        self.assertIn("viewpoint_contract", (run / "continuity.md").read_text(encoding="utf-8"))
        self.assertIn("scene_seed", manifest["workflow"])
        self.assertIn("报清单", (run / "scene_seed.md").read_text(encoding="utf-8"))
        self.assertIn("人物状态表", (run / "reader.md").read_text(encoding="utf-8"))
        draft_prompt = (run / "draft.md").read_text(encoding="utf-8")
        self.assertIn("scene_seed.md", draft_prompt)
        self.assertIn("prose_brief.md", draft_prompt)
        self.assertNotIn(str(self.project / "chapter_plans"), draft_prompt)

    def test_prose_brief_keeps_story_facts_without_full_planning_context(self):
        home = self.root / "novel_engine_v3"
        home.mkdir()
        (home / "system.json").write_text(json.dumps({
            "schema_version": 3,
            "books": {"night-floor": {"title": "旧楼", "project": "book", "sources": []}},
        }, ensure_ascii=False), encoding="utf-8")
        (self.project / "chapter_state.json").write_text('{"next_chapter_number":10}', encoding="utf-8")
        run = AuthorEngine(self.root).prepare("night-floor")
        (self.project / "chapter_plans" / "0010.json").write_text(json.dumps({
            "immediate_goal": "找出新门", "central_choice": "先守住入口",
            "scene_plan": [{"turn": "邻居先把门推开"}],
            "reader_payoff": {"on_page_event": "两栋楼相通"},
            "ending_pressure": {"visible_trigger": "有人带尺进入"},
        }, ensure_ascii=False), encoding="utf-8")
        path = AuthorEngine(self.root).write_prose_brief(run)
        brief = path.read_text(encoding="utf-8")
        self.assertIn("找出新门", brief)
        self.assertIn("邻居先把门推开", brief)
        self.assertIn("有人带尺进入", brief)
        self.assertNotIn("character_drives", brief)


if __name__ == "__main__":
    unittest.main()
