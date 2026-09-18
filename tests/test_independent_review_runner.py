import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock

import novel_stage_pipeline as pipeline
import xiaoshuo_on_demand
from novel_reader_gate import narrative_sha256


class IndependentReviewRunnerTests(TestCase):
    def test_dialogue_repair_uses_isolated_full_chapter_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "book"
            for folder in ("chapters", "drafts", "dialogue_reviews", "reader_checks", "literary_reviews", "chapter_plans"):
                (project / folder).mkdir(parents=True, exist_ok=True)
            (root / pipeline.CONFIG_NAME).write_text(json.dumps({
                "schema_version": 1, "enabled": True,
                "recent_chapters_for_director": 5,
                "recent_chapters_for_reviewer": 2,
                "max_new_questions_per_chapter": 1,
                "plan_directory": "chapter_plans",
                "review_directory": "literary_reviews",
            }), encoding="utf-8")
            filler = "他守在门边等人回来。" * 120
            old_line = "“你别看，别摸，别临时起意。”"
            new_line = "“先别碰，等她回来再说。”"
            original = (
                f"# 第 2 章 查证\n\n{filler}\n\n{old_line}\n\n"
                "---\n\n## Metadata\n\n- chapter_number: 2\n"
                "- word_count: 1200\n- generated_at: 2026-09-18 12:00\n"
                "- upload_status: not_uploaded\n"
            )
            revised = original.replace(old_line, new_line)
            chapter = project / "chapters" / "0002-second.md"
            chapter.write_text(original, encoding="utf-8")
            draft = project / "drafts" / "2026-09-18-chapter-0002.md"
            draft.write_text(original, encoding="utf-8")
            review = {
                "issues": [{"quote": old_line, "diagnosis": "像规则清单"}],
                "strengths_to_preserve": ["动作明确"],
            }
            (project / "dialogue_reviews" / "0002.json").write_text(
                json.dumps(review, ensure_ascii=False), encoding="utf-8"
            )
            (project / "reader_checks" / "0002.json").write_text("{}", encoding="utf-8")
            (project / "literary_reviews" / "0002.json").write_text("{}", encoding="utf-8")

            def fake_codex_run(*args, **kwargs):
                command = args[0]
                self.assertIn("read-only", command)
                self.assertNotEqual(Path(kwargs["cwd"]).resolve(), project.resolve())
                output = Path(command[command.index("--output-last-message") + 1])
                output.write_text(revised, encoding="utf-8")
                return SimpleNamespace(returncode=0)

            with (
                mock.patch.object(xiaoshuo_on_demand, "ROOT", root),
                mock.patch.object(
                    xiaoshuo_on_demand.manager, "JOB_DIR", root / ".manager_jobs"
                ),
                mock.patch.object(
                    xiaoshuo_on_demand.author_registry,
                    "book_author",
                    side_effect=xiaoshuo_on_demand.author_registry.AuthorConfigError("none"),
                ),
                mock.patch.object(
                    xiaoshuo_on_demand.subprocess, "run", side_effect=fake_codex_run
                ),
            ):
                xiaoshuo_on_demand.run_isolated_dialogue_repair(
                    "codex", "demo", project, 2, {"id": "job-repair"}, 1, review
                )

            self.assertIn(new_line, chapter.read_text(encoding="utf-8"))
            self.assertEqual(chapter.read_text(encoding="utf-8"), draft.read_text(encoding="utf-8"))
            self.assertFalse((project / "dialogue_reviews" / "0002.json").exists())
            self.assertFalse((project / "reader_checks" / "0002.json").exists())
            self.assertFalse((project / "literary_reviews" / "0002.json").exists())

    def test_dialogue_repair_retries_connection_without_consuming_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "book"
            (project / "chapters").mkdir(parents=True)
            (project / "dialogue_reviews").mkdir()
            (root / pipeline.CONFIG_NAME).write_text(json.dumps({
                "schema_version": 1, "enabled": True,
                "recent_chapters_for_director": 5,
                "recent_chapters_for_reviewer": 2,
                "max_new_questions_per_chapter": 1,
                "plan_directory": "chapter_plans",
                "review_directory": "literary_reviews",
            }), encoding="utf-8")
            filler = "他守在门边等人回来。" * 120
            old_line = "“不许动，不许问。”"
            new_line = "“先放下。”"
            original = (
                f"# 第 2 章 查证\n\n{filler}\n\n{old_line}\n\n---\n\n"
                "## Metadata\n\n- chapter_number: 2\n- word_count: 1200\n"
            )
            chapter = project / "chapters" / "0002-second.md"
            chapter.write_text(original, encoding="utf-8")
            review = {"issues": [{"quote": old_line}], "strengths_to_preserve": []}
            (project / "dialogue_reviews" / "0002.json").write_text(
                json.dumps(review, ensure_ascii=False), encoding="utf-8"
            )
            calls = 0

            def flaky_run(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return SimpleNamespace(returncode=1)
                command = args[0]
                output = Path(command[command.index("--output-last-message") + 1])
                output.write_text(original.replace(old_line, new_line), encoding="utf-8")
                return SimpleNamespace(returncode=0)

            with (
                mock.patch.object(xiaoshuo_on_demand, "ROOT", root),
                mock.patch.object(
                    xiaoshuo_on_demand.manager, "JOB_DIR", root / ".manager_jobs"
                ),
                mock.patch.object(
                    xiaoshuo_on_demand.author_registry,
                    "book_author",
                    side_effect=xiaoshuo_on_demand.author_registry.AuthorConfigError("none"),
                ),
                mock.patch.object(xiaoshuo_on_demand.subprocess, "run", side_effect=flaky_run),
            ):
                xiaoshuo_on_demand.run_isolated_dialogue_repair(
                    "codex", "demo", project, 2, {"id": "job-retry"}, 1, review
                )
            self.assertEqual(calls, 2)
            self.assertIn(new_line, chapter.read_text(encoding="utf-8"))

    def test_dialogue_runner_reuses_pass_for_unchanged_chapter(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "book"
            (project / "chapters").mkdir(parents=True)
            chapter = project / "chapters" / "0002-second.md"
            chapter.write_text("# 第 2 章 查证\n\n“先别碰，等人来。”\n", encoding="utf-8")
            review = {
                "schema_version": 1, "chapter_number": 2,
                "mode": pipeline.DIALOGUE_REVIEW_MODE,
                "narrative_sha256": narrative_sha256(chapter),
                "decision": "pass", "assessment": "台词指向现场动作。",
                "issues": [], "strengths_to_preserve": ["先拦动作再给条件。"],
            }
            path = pipeline.dialogue_review_path(project, 2)
            path.parent.mkdir()
            path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(xiaoshuo_on_demand.subprocess, "run") as run:
                result = xiaoshuo_on_demand.run_independent_dialogue_review(
                    "codex", project, 2, {"id": "job-dialogue"}
                )
            self.assertEqual(result["decision"], "pass")
            run.assert_not_called()

    def test_runner_reuses_existing_review_for_unchanged_chapter(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "book"
            (project / "chapters").mkdir(parents=True)
            chapter = project / "chapters" / "0002-second.md"
            sentence = "林澄合上账册，决定先查清来源。"
            chapter.write_text(f"# 第 2 章 查证\n\n{sentence}\n", encoding="utf-8")
            (root / pipeline.CONFIG_NAME).write_text(json.dumps({
                "schema_version": 1, "enabled": True,
                "recent_chapters_for_director": 5,
                "recent_chapters_for_reviewer": 2,
                "max_new_questions_per_chapter": 1,
                "plan_directory": "chapter_plans",
                "review_directory": "literary_reviews",
            }), encoding="utf-8")
            review = {
                "schema_version": 1, "chapter_number": 2,
                "mode": pipeline.REVIEW_MODE,
                "narrative_sha256": narrative_sha256(chapter),
                "decision": "pass",
                "dimensions": {
                    key: {"verdict": "pass", "assessment": "判断具体且有依据。", "evidence": [sentence]}
                    for key in pipeline.REVIEW_DIMENSIONS
                },
                "blocking_issues": [],
                "most_fragile_passage": {
                    "quote": sentence, "risk": "推进较快。",
                    "why_acceptable": "人物行动和目标仍然清楚。",
                },
                "strengths_to_preserve": ["人物主动核验来源。"],
            }
            review_path = pipeline.review_path(root, project, 2)
            review_path.parent.mkdir()
            review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
            with (
                mock.patch.object(xiaoshuo_on_demand, "ROOT", root),
                mock.patch.object(xiaoshuo_on_demand.subprocess, "run") as run,
            ):
                xiaoshuo_on_demand.run_independent_literary_review(
                    "codex", project, 2, {"id": "job-reuse-123"}
                )
            run.assert_not_called()

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
                command = args[0]
                self.assertIn("--skip-git-repo-check", command)
                self.assertIn("read-only", command)
                output_path = Path(command[command.index("--output-last-message") + 1])
                review = {
                    "schema_version": 1,
                    "chapter_number": 2,
                    "mode": pipeline.REVIEW_MODE,
                    "narrative_sha256": "whole-markdown-hash-from-model",
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
                output_path.write_text(
                    json.dumps(review, ensure_ascii=False), encoding="utf-8"
                )
                return SimpleNamespace(returncode=0)

            with (
                mock.patch.object(xiaoshuo_on_demand, "ROOT", root),
                mock.patch.object(
                    xiaoshuo_on_demand.manager, "JOB_DIR", root / ".manager_jobs"
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
