from __future__ import annotations

import unittest
from unittest.mock import patch

import web_app


class WebAppDataTests(unittest.TestCase):
    def test_react_frontend_is_configured(self) -> None:
        package = web_app.read_json(web_app.ROOT / "web" / "package.json", {})
        self.assertIn("react", package.get("dependencies", {}))
        self.assertEqual(web_app.WEB_ROOT, web_app.ROOT / "web" / "dist")

    def test_overview_exposes_registered_books_without_chapter_body(self) -> None:
        payload = web_app.overview()
        ids = {book["id"] for book in payload["books"]}
        self.assertIn("cosmic-404", ids)
        self.assertIn("free-sky", ids)
        self.assertNotIn("content", payload["books"][0])

    def test_chapter_document_is_explicit_and_excludes_metadata(self) -> None:
        document = web_app.chapter_document("cosmic-404", 1)
        self.assertEqual(document["number"], 1)
        self.assertIn("第 1 章", document["content"])
        self.assertNotIn("## Metadata", document["content"])

    def test_generation_reuses_existing_cli(self) -> None:
        fake = {"id": "run-id", "status": "running"}
        with patch.object(web_app, "launch_command", return_value=fake) as launch:
            result = web_app.launch_generation({"book_id": "cosmic-404", "count": 3})
        self.assertEqual(result, fake)
        command = launch.call_args.args[0]
        self.assertEqual(command[-2:], ["--book", "cosmic-404"])
        self.assertIn("3", command)
        self.assertTrue(command[1].endswith("xiaoshuo.py"))

    def test_generation_rejects_unsafe_count(self) -> None:
        with self.assertRaisesRegex(ValueError, "1—20"):
            web_app.launch_generation({"book_id": "cosmic-404", "count": 21})

    def test_generation_passes_delivery_options_to_worker(self) -> None:
        fake = {"id": "run-id", "status": "running"}
        with patch.object(web_app, "launch_command", return_value=fake) as launch:
            web_app.launch_generation(
                {
                    "book_id": "free-sky",
                    "count": 10,
                    "sync_git": True,
                    "publish_fanqie": True,
                }
            )
        command = launch.call_args.args[0]
        self.assertIn("--sync-git", command)
        self.assertIn("--publish-fanqie", command)

    def test_generation_defaults_both_delivery_options_off(self) -> None:
        fake = {"id": "run-id", "status": "running"}
        with patch.object(web_app, "launch_command", return_value=fake) as launch:
            web_app.launch_generation({"book_id": "cosmic-404", "count": 2})
        command = launch.call_args.args[0]
        self.assertIn("--no-sync-git", command)
        self.assertIn("--no-publish-fanqie", command)

    def test_generation_rejects_unbound_fanqie_production(self) -> None:
        with self.assertRaisesRegex(ValueError, "尚未绑定番茄正式环境"):
            web_app.launch_generation(
                {
                    "book_id": "cosmic-404",
                    "count": 1,
                    "sync_git": False,
                    "publish_fanqie": True,
                }
            )

    def test_generation_rejects_non_boolean_delivery_boolean(self) -> None:
        with self.assertRaisesRegex(ValueError, "必须是布尔值"):
            web_app.launch_generation(
                {"book_id": "cosmic-404", "count": 1, "sync_git": "yes"}
            )

    def test_resume_rejects_bad_job_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "格式"):
            web_app.launch_resume({"job_id": "../secret"})

    def test_resume_uses_the_job_book_and_saved_options(self) -> None:
        fake = {"id": "run-id", "status": "running"}
        with (
            patch.object(
                web_app.manager,
                "read_job",
                return_value={"id": "12345678", "book_id": "free-sky"},
            ),
            patch.object(web_app, "launch_command", return_value=fake) as launch,
        ):
            web_app.launch_resume({"job_id": "12345678"})
        command = launch.call_args.args[0]
        self.assertEqual(command[-2:], ["--book", "free-sky"])
        self.assertIn("--resume", command)


if __name__ == "__main__":
    unittest.main()
