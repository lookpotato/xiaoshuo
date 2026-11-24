from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import overview_handoff_service as service


class OverviewHandoffTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "novel_engine_v3").mkdir()
        (self.root / "novel_engine_v3" / "system.json").write_text(
            json.dumps({"schema_version": 3, "books": {"book": {"project": "book"}}}), encoding="utf-8")
        self.project = self.root / "book"
        (self.project / "chapter_plans").mkdir(parents=True)
        (self.project / "chapter_state.json").write_text(
            json.dumps({"next_chapter_number": 3}), encoding="utf-8")
        (self.project / "chapter_plans" / "0003.json").write_text(
            json.dumps({"chapter_number": 3, "immediate_goal": "开门"}, ensure_ascii=False), encoding="utf-8")
        self.run = self.root / ".novel_runs_v3" / "book" / "0003-test"
        self.run.mkdir(parents=True)
        (self.run / "manifest.json").write_text(json.dumps({
            "book_id": "book", "chapter": 3, "project": str(self.project),
        }), encoding="utf-8")
        overview = "# 第三章\n" + "人物先确认门后的声音。" * 12
        (self.run / "overview.md").write_text(overview, encoding="utf-8")
        service._write_meta(self.run, {"status": "pending", "plan_revision": service._hash(
            (self.project / "chapter_plans" / "0003.json").read_text(encoding="utf-8")), "questions": [],
            "understanding": {"status": "ready", "revision": service._hash(overview), "text": "测试：已理解"}})

    def test_save_and_expand_use_author_revision(self):
        initial = service.workspace(self.root, "book", 3)
        revised = initial["overview"] + "\n最后由她亲自开门。"
        saved = service.save(self.root, "book", 3, revised, initial["revision"])
        self.assertEqual(saved["change_summary"]["count"], 1)
        self.assertEqual(saved["change_summary"]["changes"][0]["kind"], "新增")
        self.assertIn("亲自开门", saved["change_summary"]["changes"][0]["after"])
        self.assertEqual(service.workspace(self.root, "book", 3)["change_summary"], saved["change_summary"])
        service.validate_pending(self.root, "book", 3, "expand", saved["revision"])
        with patch.object(service, "execute", side_effect=lambda prompt, output, run, **kwargs:
                          output.write_text("理解：应由她亲自开门，先确认门后声音；这是当前章拟写，并非前章事实。后续不可再写她没有进门。尚未同步规划，待确认扩写时落实。" * 3, encoding="utf-8")):
            service.summarize(self.root, "book", 3, saved["revision"])
        with self.assertRaisesRegex(ValueError, "别处更新"):
            service.save(self.root, "book", 3, initial["overview"], initial["revision"])
        (self.run / "reader_review.json").write_text('{"blocking":[]}', encoding="utf-8")

        def fake_execute(prompt, output, run, **kwargs):
            if prompt.name == "draft.md":
                self.assertEqual((run / "approved_overview.md").read_text(encoding="utf-8"), revised)
                self.assertFalse((run / "reader_review.json").exists())

        with patch.object(service, "sync_overview_context"), patch.object(service, "execute", side_effect=fake_execute), patch.object(
            service, "review_and_archive", return_value=self.project / "chapters" / "0003.md"
        ):
            service.expand(self.root, "book", 3, saved["revision"])
        self.assertEqual(service.workspace(self.root, "book", 3)["status"], "archived")
        next_page = service.workspace(self.root, "book", 4)
        self.assertEqual(next_page["last_report"]["chapter"], 3)
        self.assertEqual(next_page["last_report"]["change_summary"], saved["change_summary"])

    def test_initial_overview_is_a_short_decision_card(self):
        plan = {"immediate_goal": "找到门", "central_choice": "她决定亲自开门",
                "reader_payoff": {"on_page_event": "门后有人回应"},
                "ending_pressure": {"visible_trigger": "门内传来第二个人的声音"},
                "scene_plan": [{"purpose": "逐项交接任务"}]}
        (self.run / "author_room.md").write_text("很长的作者分析", encoding="utf-8")
        card = service._render_overview(plan, self.run, 3)
        for phrase in ("找到门", "她决定亲自开门", "门后有人回应", "第二个人的声音"):
            self.assertIn(phrase, card)
        self.assertNotIn("逐项交接任务", card)
        self.assertNotIn("很长的作者分析", card)

    def test_recovered_legacy_comparison_is_labelled_against_initial_plan(self):
        initial = "初始总览" * 20
        (self.run / "overview.original.md").write_text(initial, encoding="utf-8")
        result = service.workspace(self.root, "book", 3)
        self.assertIn("初始规划", result["change_summary"]["baseline"])

    def test_comparison_preserves_added_deleted_and_replaced_text(self):
        report = service.compare_overviews("删掉的承诺\n保持\n旧路线", "保持\n新路线\n")
        self.assertEqual([item["kind"] for item in report["changes"]], ["删除", "修改"])
        self.assertEqual(report["changes"][0]["before"], "删掉的承诺")
        self.assertEqual(report["changes"][1]["after"], "新路线")

    def test_question_is_read_only_and_saved_without_changing_overview(self):
        initial = service.workspace(self.root, "book", 3)

        def fake_execute(prompt, output, run, **kwargs):
            self.assertTrue(kwargs["read_only"])
            self.assertIn("钥匙", prompt.read_text(encoding="utf-8"))
            self.assertEqual(service.workspace(self.root, "book", 3)["questions"][-1]["status"], "running")
            output.write_text("钥匙先交给她，动机才成立。", encoding="utf-8")

        with patch.object(service, "execute", side_effect=fake_execute):
            answer = service.ask(self.root, "book", 3, "他为什么交钥匙？", initial["revision"])
        self.assertIn("动机", answer)
        self.assertEqual(service.workspace(self.root, "book", 3)["revision"], initial["revision"])
        self.assertEqual(len(service.workspace(self.root, "book", 3)["questions"]), 1)
        self.assertEqual(service.workspace(self.root, "book", 3)["questions"][0]["status"], "answered")

    def test_answer_revision_is_proposed_before_author_saves(self):
        initial = service.workspace(self.root, "book", 3)
        with patch.object(service, "execute", side_effect=lambda prompt, output, run, **kwargs:
                          output.write_text("动机应该先落在门后的声音。", encoding="utf-8")):
            service.ask(self.root, "book", 3, "为什么开门？", initial["revision"])
        revised = initial["overview"] + "\n她先确认门后的声音，再决定开门。"

        def propose(prompt, output, run, **kwargs):
            self.assertTrue(kwargs["read_only"])
            self.assertIn("为什么开门？", prompt.read_text(encoding="utf-8"))
            output.write_text(revised, encoding="utf-8")

        with patch.object(service, "execute", side_effect=propose):
            proposal = service.revise(self.root, "book", 3, initial["revision"], 0)
        current = service.workspace(self.root, "book", 3)
        self.assertEqual(current["overview"], initial["overview"])
        self.assertEqual(current["revision"], initial["revision"])
        self.assertEqual(proposal["content"], revised)
        self.assertEqual(current["proposal"]["content"], revised)
        service.save(self.root, "book", 3, revised, initial["revision"])
        self.assertIsNone(service.workspace(self.root, "book", 3)["proposal"])

    def test_unanswered_question_cannot_generate_revision(self):
        initial = service.workspace(self.root, "book", 3)
        with self.assertRaisesRegex(ValueError, "请先完成作者对话"):
            service.validate_pending(self.root, "book", 3, "revise", initial["revision"], question_index=0)

    def test_understanding_is_read_only_revision_bound_and_preserves_planning(self):
        initial = service.workspace(self.root, "book", 3)
        before_plan = (self.project / "chapter_plans" / "0003.json").read_bytes()
        before_state = (self.project / "chapter_state.json").read_bytes()

        def execute(prompt, output, run, **kwargs):
            self.assertTrue(kwargs["read_only"])
            self.assertEqual(service.workspace(self.root, "book", 3)["understanding"]["status"], "running")
            self.assertIn("不能只罗列字符差异", prompt.read_text(encoding="utf-8"))
            output.write_text("旧稿没有交代进门动机；现在应先核对门后声音，再由本人决定开门。这是本章拟写，不是既有事实。理解已形成，但人物线和接力尚未同步。仍有疑问：门后人的身份待查。" * 3, encoding="utf-8")

        with patch.object(service, "execute", side_effect=execute):
            service.summarize(self.root, "book", 3, initial["revision"])
        report = service.workspace(self.root, "book", 3)["understanding"]
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["revision"], initial["revision"])
        self.assertIn("尚未同步", report["text"])
        self.assertEqual((self.project / "chapter_plans" / "0003.json").read_bytes(), before_plan)
        self.assertEqual((self.project / "chapter_state.json").read_bytes(), before_state)
        service.save(self.root, "book", 3, initial["overview"] + "新增改动", initial["revision"])
        self.assertEqual(service.workspace(self.root, "book", 3)["understanding"]["status"], "pending")

    def test_failed_understanding_can_retry_without_losing_saved_overview(self):
        initial = service.workspace(self.root, "book", 3)
        with patch.object(service, "execute", side_effect=RuntimeError("网络失败")):
            with self.assertRaisesRegex(RuntimeError, "网络失败"):
                service.summarize(self.root, "book", 3, initial["revision"])
        result = service.workspace(self.root, "book", 3)
        self.assertEqual(result["understanding"]["status"], "failed")
        self.assertEqual(result["overview"], initial["overview"])
        service.validate_pending(self.root, "book", 3, "expand", initial["revision"])

    def test_summary_of_changed_overview_cannot_be_marked_ready(self):
        initial = service.workspace(self.root, "book", 3)

        def execute(prompt, output, run, **kwargs):
            service.save(self.root, "book", 3, initial["overview"] + "\n改变了后续路线", initial["revision"])
            output.write_text("这是针对旧总览的理解，不能覆盖新版本的待分析状态。" * 8, encoding="utf-8")

        with patch.object(service, "execute", side_effect=execute):
            with self.assertRaisesRegex(ValueError, "旧总结"):
                service.summarize(self.root, "book", 3, initial["revision"])
        self.assertEqual(service.workspace(self.root, "book", 3)["understanding"]["status"], "pending")

    def test_failed_question_is_visible_for_retry(self):
        revision = service.workspace(self.root, "book", 3)["revision"]
        with patch.object(service, "execute", side_effect=RuntimeError("模型失败")):
            with self.assertRaisesRegex(RuntimeError, "模型失败"):
                service.ask(self.root, "book", 3, "钥匙呢？", revision)
        self.assertEqual(service.workspace(self.root, "book", 3)["questions"][0]["status"], "failed")

    def test_failed_expansion_reuses_candidate_and_reviews_it_again(self):
        revision = service.workspace(self.root, "book", 3)["revision"]
        (self.run / "candidate.md").write_text("# 第三章\n候选正文\n", encoding="utf-8")
        (self.run / "reader_review.json").write_text('{"blocking": ["旧问题"]}', encoding="utf-8")
        (self.run / "continuity_review.json").write_text('{"blocking": ["旧问题"]}', encoding="utf-8")
        meta = json.loads((self.run / "overview_handoff.json").read_text(encoding="utf-8"))
        meta["candidate_revision"] = revision
        service._write_meta(self.run, meta)

        def review(run, project, chapter):
            self.assertFalse((run / "reader_review.json").exists())
            self.assertFalse((run / "continuity_review.json").exists())
            self.assertEqual((run / "candidate.md").read_text(encoding="utf-8"), "# 第三章\n候选正文\n")
            return project / "chapters" / "0003.md"

        with patch.object(service, "sync_overview_context"), patch.object(service, "execute") as execute, patch.object(
            service, "review_and_archive", side_effect=review
        ):
            service.expand(self.root, "book", 3, revision)
        execute.assert_not_called()

    def test_overview_rebuilds_plan_and_removes_old_character_inputs(self):
        revision = service.workspace(self.root, "book", 3)["revision"]
        (self.run / "approved_overview.md").write_text("住户已经回家，不借窗口。", encoding="utf-8")
        threads = self.project / "character_threads" / "0003"
        threads.mkdir(parents=True)
        (threads / "旧邻楼观察者.md").write_text("借窗取证", encoding="utf-8")
        meta = json.loads((self.run / "overview_handoff.json").read_text(encoding="utf-8"))
        stages = []

        def execute(prompt, output, run, **kwargs):
            stages.append(prompt.name)
            progress = service.workspace(self.root, "book", 3)["progress"]
            self.assertTrue(progress["stage"].startswith("正在"))
            if prompt.name == "overview_sync.md":
                self.assertTrue(kwargs["read_only"])
                output.write_text(json.dumps({"chapter_number": 3, "immediate_goal": "街对面复测"}), encoding="utf-8")
            elif prompt.name == "characters.overview.md":
                self.assertFalse(threads.exists())
                threads.mkdir()
                (threads / "新人物.md").write_text("街对面复测", encoding="utf-8")

        with patch.object(service, "execute", side_effect=execute), \
             patch.object(service, "planning_context_sha256", return_value="current"), \
             patch.object(service, "validate_plan"), patch.object(service, "validate_characters"), \
             patch.object(service, "validate_viewpoint_contract"), patch.object(service, "validate_scene_seed"), \
             patch.object(service.AuthorEngine, "write_prose_brief") as write_brief:
            service.sync_overview_context(self.root, self.project, self.run, 3, revision, meta)
            service.sync_overview_context(self.root, self.project, self.run, 3, revision, meta)
        write_brief.assert_called_once_with(self.run)
        self.assertEqual(stages, ["overview_sync.md", "characters.overview.md", "author_room.overview.md", "scene_seed.overview.md"])
        self.assertEqual(meta["context_revision"], revision)
        self.assertFalse(service.workspace(self.root, "book", 3)["plan_stale"])
        self.assertEqual(len(list(self.run.glob("overview-characters-*/0003/旧邻楼观察者.md"))), 1)

    def test_invalid_synced_plan_restores_original_and_keeps_context_pending(self):
        plan = self.project / "chapter_plans" / "0003.json"
        original = plan.read_bytes()
        meta = json.loads((self.run / "overview_handoff.json").read_text(encoding="utf-8"))

        def execute(prompt, output, run, **kwargs):
            output.write_text('{"chapter_number":3}', encoding="utf-8")

        with patch.object(service, "execute", side_effect=execute), \
             patch.object(service, "planning_context_sha256", return_value="current"), \
             patch.object(service, "validate_plan", side_effect=ValueError("合同不完整")):
            with self.assertRaisesRegex(ValueError, "合同不完整"):
                service.sync_overview_context(self.root, self.project, self.run, 3, "revision", meta)
        self.assertEqual(plan.read_bytes(), original)
        self.assertNotIn("context_revision", meta)

    def test_sync_failure_keeps_chapter_pending_and_never_starts_prose(self):
        revision = service.workspace(self.root, "book", 3)["revision"]
        with patch.object(service, "sync_overview_context", side_effect=ValueError("同步失败")), \
             patch.object(service, "execute") as execute, patch.object(service, "review_and_archive") as archive:
            with self.assertRaisesRegex(ValueError, "同步失败"):
                service.expand(self.root, "book", 3, revision)
        execute.assert_not_called()
        archive.assert_not_called()
        self.assertEqual(service.workspace(self.root, "book", 3)["status"], "pending")
        self.assertIn("任务失败", service.workspace(self.root, "book", 3)["progress"]["stage"])
        self.assertEqual(json.loads((self.project / "chapter_state.json").read_text())["next_chapter_number"], 3)

    def test_changed_plan_blocks_expansion(self):
        initial = service.workspace(self.root, "book", 3)
        (self.project / "chapter_plans" / "0003.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "规划已变化"):
            service.validate_pending(self.root, "book", 3, "expand", initial["revision"])

    def test_direct_expansion_learns_after_archive_without_summary_approval(self):
        from novel_stage_pipeline import available_book_sources, director_prompt
        revision = service.workspace(self.root, "book", 3)["revision"]
        meta = json.loads((self.run / "overview_handoff.json").read_text(encoding="utf-8"))
        meta.pop("understanding", None)
        service._write_meta(self.run, meta)
        calls = []

        def execute(prompt, output, run, **kwargs):
            calls.append(prompt.name)
            if prompt.name == "long_term_learning.prompt.md":
                self.assertTrue((self.project / "chapters" / "0003-进门.md").exists())
                self.assertTrue(kwargs["read_only"])
                output.write_text(json.dumps({"summary": "已解决的路线不要重复制造阻拦；后续规划按最终事实承接。", "lessons": [{
                    "problem": "总览重复回家受阻", "principle": "以最终正文为准，已完成事件不再作为待办",
                    "applies_when": "接续已解决的通行安排", "avoid": "有新阻碍仍应按新因果处理", "evidence": "她已经进门了。"
                }]}, ensure_ascii=False), encoding="utf-8")

        def archive(run, project, chapter):
            (project / "chapters").mkdir()
            path = project / "chapters" / "0003-进门.md"
            path.write_text("# 第3章 进门\n她已经进门了。", encoding="utf-8")
            (project / "chapter_state.json").write_text('{"next_chapter_number":4,"notes_for_next_chapter":"人已回家"}', encoding="utf-8")
            return path

        with patch.object(service, "sync_overview_context"), patch.object(service, "execute", side_effect=execute), \
             patch.object(service, "review_and_archive", side_effect=archive):
            service.expand(self.root, "book", 3, revision)
            final_meta = json.loads((self.run / "overview_handoff.json").read_text(encoding="utf-8"))
            service.learn_from_overview(self.project, self.run, 3, revision, final_meta)
        self.assertEqual(calls, ["draft.md", "deep_edit.md", "long_term_learning.prompt.md"])
        memory = json.loads((self.project / "overview_learning.json").read_text(encoding="utf-8"))
        self.assertEqual(len(memory["entries"]), 1)
        self.assertEqual(service.workspace(self.root, "book", 4)["last_report"]["learning"]["status"], "ready")
        self.assertIn(self.project / "overview_learning.json", available_book_sources(self.project))
        (self.root / "novel_pipeline.json").write_text((service.ROOT / "novel_pipeline.json").read_text(encoding="utf-8"), encoding="utf-8")
        self.assertIn("overview_learning.json", director_prompt(self.root, self.project, 4, "测试"))

    def test_learning_failure_does_not_undo_successful_archive(self):
        revision = service.workspace(self.root, "book", 3)["revision"]
        with patch.object(service, "sync_overview_context"), patch.object(service, "execute"), \
             patch.object(service, "review_and_archive", return_value=self.project / "chapters" / "0003.md"), \
             patch.object(service, "learn_from_overview", side_effect=RuntimeError("总结网络失败")):
            result = service.expand(self.root, "book", 3, revision)
        self.assertEqual(result.name, "0003.md")
        state = service.workspace(self.root, "book", 3)
        self.assertEqual(state["status"], "archived")
        self.assertEqual(state["learning"]["status"], "failed")
        self.assertFalse((self.project / "overview_learning.json").exists())


if __name__ == "__main__":
    unittest.main()
