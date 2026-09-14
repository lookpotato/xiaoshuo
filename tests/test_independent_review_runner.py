import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock

import novel_stage_pipeline as pipeline
import xiaoshuo_on_demand
from novel_reader_gate import narrative_sha256


class IndependentReviewRunnerTests(TestCase):
    def test_runner_retries_invalid_review_without_rewriting_the_chapter(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "book"
            (project / "chapters").mkdir(parents=True)
            (project / "chapters" / "0001-first.md").write_text(
                "# 第 1 章 开始\n\n前章正文。\n", encoding="utf-8"
            )
            chapter = project / "chapters" / "0002-second.md"
            chapter.write_text(
                "# 第 2 章 查证\n\n林澄合上账册，决定先查清来源。\n",
                encoding="utf-8",
            )
            (root / pipeline.CONFIG_NAME).write_text(
                json.dumps({
                    "schema_version": 1,
                    "enabled": True,
                    "recent_chapters_for_director": 5,
                    "recent_chapters_for_reviewer": 2,
                    "max_new_questions_per_chapter": 1,
                    "plan_directory": "chapter_plans",
                    "review_directory": "literary_reviews",
                }),
                encoding="utf-8",
            )
            review_path = pipeline.review_path(root, project, 2)
            review_path.parent.mkdir()
            review_path.write_text("stale result", encoding="utf-8")
            original_chapter_hash = narrative_sha256(chapter)
            sentence = "林澄合上账册，决定先查清来源。"
            calls = 0

            def fake_codex_run(*args, **kwargs):
                nonlocal calls
                calls += 1
                review = {
                    "schema_version": 1,
                    "chapter_number": 2,
                    "mode": pipeline.REVIEW_MODE,
                    "narrative_sha256": original_chapter_hash,
                    "decision": "pass",
                    "dimensions": {
                        key: {
                            "verdict": "pass",
                            "assessment": "当前正文给出了明确行动。",
                            "evidence": [
                                "这句是不存在的转述。" if calls == 1 else sentence
                            ],
                        }
                        for key in pipeline.REVIEW_DIMENSIONS
                    },
                    "blocking_issues": [],
                    "most_fragile_passage": {
                        "quote": sentence,
                        "risk": "行动选择出现得较快。",
                        "why_acceptable": "上下文交代了选择依据。",
                    },
                    "strengths_to_preserve": ["人物主动核验来源。"],
                }
                review_path.write_text(
                    json.dumps(review, ensure_ascii=False), encoding="utf-8"
                )
                return SimpleNamespace(returncode=0)

            with (
                mock.patch.object(xiaoshuo_on_demand, "ROOT", root),
                mock.patch.object(
                    xiaoshuo_on_demand.manager, "JOB_DIR", root / ".manager_jobs"
                ),
                mock.patch.object(
                    xiaoshuo_on_demand, "_stage_command", return_value=["codex"]
                ),
                mock.patch.object(
                    xiaoshuo_on_demand.subprocess,
                    "run",
                    side_effect=fake_codex_run,
                ),
            ):
                xiaoshuo_on_demand.run_independent_literary_review(
                    "codex", project, 2, {"id": "job-test-123"}
                )

            self.assertEqual(calls, 2)
            self.assertEqual(narrative_sha256(chapter), original_chapter_hash)
            self.assertEqual(
                pipeline.validate_literary_review(root, project, 2)["decision"],
                "pass",
            )
