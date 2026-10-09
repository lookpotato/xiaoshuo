"""The production health check excludes retired books unless requested."""

import argparse
import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest import TestCase, mock

import fanqie_novel_manager as manager


class ManagerValidateActiveTest(TestCase):
    def setUp(self):
        self.data = {"books": [
            {"id": "active", "path": "active", "enabled": True},
            {"id": "retired", "path": "retired", "enabled": False},
        ]}

    def test_default_checks_only_active_book(self):
        with mock.patch.object(manager, "validate_book", side_effect=lambda book, **_: ["旧错误"] if book["id"] == "retired" else []) as validate, \
                mock.patch.object(manager, "project_path", side_effect=lambda book: Path(book["path"])), \
                mock.patch.object(manager, "read_json", return_value={}), \
                mock.patch.object(manager, "publish_requires_submission", return_value=False), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(manager.cmd_validate(self.data, argparse.Namespace(all=False, book=None)), 0)
        self.assertEqual(validate.call_count, 1)
        self.assertIn("[ARCHIVED] retired", output.getvalue())

    def test_explicit_all_or_book_checks_retired_book(self):
        for args in (argparse.Namespace(all=True, book=None), argparse.Namespace(all=False, book="retired")):
            with self.subTest(args=args), mock.patch.object(manager, "validate_book", side_effect=lambda book, **_: ["旧错误"] if book["id"] == "retired" else []), \
                    mock.patch.object(manager, "project_path", side_effect=lambda book: Path(book["path"])), \
                    mock.patch.object(manager, "read_json", return_value={}), \
                    mock.patch.object(manager, "publish_requires_submission", return_value=False), \
                    redirect_stdout(io.StringIO()) as output:
                self.assertEqual(manager.cmd_validate(self.data, args), 1)
                self.assertIn("[FAIL] retired", output.getvalue())
