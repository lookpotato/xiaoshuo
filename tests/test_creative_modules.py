import hashlib
import json
import tempfile
from pathlib import Path
from unittest import TestCase, mock

import creative_modules as cm
from novel_reader_gate import narrative_sha256


class CreativeModulesTest(TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp)
        for folder in ("chapters", "module_plans", "module_reports"):
            (self.root / folder).mkdir()
        self.body = self.root / "chapters/0002-test.md"
        self.body.write_text("# 第 2 章 进城\n\n城门外的人靠修机器换取粮票。\n", encoding="utf-8")
        self.config = {"schema_version": 1, "modules": {"world_presentation": {"mode": "enforce", "from_chapter": 2}}}
        self.write(cm.CONFIG, self.config)
        self.write("chapter_state.json", {"next_chapter_number": 2})
        self.plan = {"schema_version": 1, "chapter_number": 2, "modules": {"world_presentation": "通过城门交易呈现身份与生活"}}
        self.write("module_plans/0002.json", self.plan)
        self.ref = {"chapter": 2, "quote": "城门外的人靠修机器换取粮票。", "narrative_sha256": narrative_sha256(self.body)}
        self.item = {"status": "passed", "assessment": "读者能理解谋生与交易场景", "evidence": [self.ref], "issues": [], "cross_chapter_review": "当前可理解谋生方式，其他维度需继续补足", "state": {"reader_knowledge": [
            {"dimension": d, "status": "unknown", "summary": "正文尚未充分呈现", "resolve_by": 3, "next_action": "在相关事件补足", "evidence": []} for d in cm.DIMENSIONS
        ]}}
        self.report = {"schema_version": 1, "chapter_number": 2, "review_method": "same-context-evidence-review", "narrative_sha256": narrative_sha256(self.body), "plan_sha256": hashlib.sha256((self.root / "module_plans/0002.json").read_bytes()).hexdigest(), "modules": {"world_presentation": self.item}}
        self.save_report()

    def write(self, path, data):
        (self.root / path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def save_report(self):
        self.write("module_reports/0002.json", self.report)

    def test_valid_report_and_selective_prompt(self):
        self.assertEqual(cm.validate_reports(self.root), [])
        prompt = cm.prompt(self.root)
        self.assertIn("world_presentation", prompt)
        self.assertNotIn("emotional_payoff", prompt)

    def test_disabled_and_future_rollout_need_no_report(self):
        (self.root / "module_reports/0002.json").unlink()
        self.config["modules"]["world_presentation"]["from_chapter"] = 3
        self.write(cm.CONFIG, self.config)
        self.assertEqual(cm.validate_reports(self.root), [])
        self.assertEqual(cm.prompt(self.root), "")

    def test_missing_report_blocks(self):
        (self.root / "module_reports/0002.json").unlink()
        self.assertTrue(cm.validate_reports(self.root))

    def test_body_and_plan_edits_invalidate_report(self):
        self.body.write_text("新正文与旧版不同。", encoding="utf-8")
        self.assertIn("正文变更", str(cm.validate_reports(self.root)))
        self.report["narrative_sha256"] = narrative_sha256(self.body)
        self.save_report()
        self.write("module_plans/0002.json", {**self.plan, "coordination": "changed"})
        self.assertIn("计划变更", str(cm.validate_reports(self.root)))

    def test_fabricated_and_future_evidence_rejected(self):
        self.ref["quote"] = "设定中有这句话但正文没有。"
        self.save_report()
        self.assertTrue(cm.validate_reports(self.root))
        self.ref["quote"] = "城门外的人靠修机器换取粮票。"
        self.ref["chapter"] = 3
        self.save_report()
        self.assertTrue(cm.validate_reports(self.root))

    def test_cumulative_known_requires_evidence(self):
        self.item["state"]["reader_knowledge"][0]["status"] = "known"
        self.save_report()
        self.assertIn("已知或线索", str(cm.validate_reports(self.root)))

    def test_cross_chapter_review_due(self):
        self.item.pop("cross_chapter_review")
        self.save_report()
        self.assertIn("跨章", str(cm.validate_reports(self.root)))

    def test_review_mode_allows_literary_issues_but_not_false_evidence(self):
        self.config["modules"]["world_presentation"]["mode"] = "review"
        self.write(cm.CONFIG, self.config)
        self.item["issues"] = ["社会规则仍需简述"]
        self.item["status"] = "needs_revision"
        self.save_report()
        self.assertEqual(cm.validate_reports(self.root), [])
        self.ref["narrative_sha256"] = "wrong"
        self.save_report()
        self.assertTrue(cm.validate_reports(self.root))

    def test_enforce_blocks_issues(self):
        self.item["issues"] = ["影响当前理解"]
        self.save_report()
        self.assertTrue(cm.validate_reports(self.root))

    def test_invalid_config_and_unknown_module(self):
        for modules in ({"../../outside": {"mode": "off"}}, {"world_presentation": {"mode": "oops"}}, {"world_presentation": {"mode": "enforce", "from_chapter": True}}):
            with self.assertRaises(ValueError):
                cm.validate_config({"schema_version": 1, "modules": modules})

    def test_book_isolation(self):
        other = self.root / "other"
        other.mkdir()
        self.assertEqual(cm.prompt(other), "")
        self.assertEqual(cm.validate_reports(other), [])

    def test_repair_and_both_generation_prompts_assemble_modules(self):
        import xiaoshuo_on_demand as runner
        book = {"id": "test", "path": str(self.root), "mode": "write_only"}
        with mock.patch.object(runner.manager, "config", return_value={"books": [book]}), mock.patch.object(runner.manager, "project_path", return_value=self.root):
            job = {"id": "test"}
            self.assertIn("world_presentation", runner.local_write_prompt("test", job))
            book["mode"] = "write_then_upload"
            self.assertIn("world_presentation", runner.local_write_prompt("test", job))
            self.assertIn("world_presentation", runner.local_repair_prompt("test", job, 2, [], 1))
