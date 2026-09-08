#!/usr/bin/env python3
"""CLI entry point for the author-led V2 novel engine."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from novel_engine_v2 import NovelEngine, ValidationError
from novel_engine_v2.runner import run_book


ROOT = Path(__file__).resolve().parent


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="新时代自动小说系统 V2")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="验证作品和 V2 配置")
    validate.add_argument("--book", required=True)
    prepare = sub.add_parser("prepare", help="生成隔离的四阶段写作包")
    prepare.add_argument("--book", required=True)
    prepare.add_argument("--signal", action="append", default=[])
    run = sub.add_parser("run", help="用隔离上下文自动生成、审阅并本地归档一章")
    run.add_argument("--book", required=True)
    run.add_argument("--signal", action="append", default=[])
    run.add_argument("--max-revisions", type=int, default=2)
    args = parser.parse_args()

    try:
        engine = NovelEngine(ROOT)
        if args.command == "validate":
            errors = engine.validate_project(args.book)
            print(json.dumps({"book": args.book, "errors": errors}, ensure_ascii=False, indent=2))
            return int(bool(errors))
        if args.command == "prepare":
            run_dir = engine.prepare(args.book, set(args.signal))
            print(json.dumps({"status": "prepared", "run_dir": str(run_dir)}, ensure_ascii=False, indent=2))
            return 0
        if args.max_revisions < 0 or args.max_revisions > 3:
            raise ValidationError("max-revisions 必须在 0 到 3 之间")
        chapter = run_book(engine, args.book, set(args.signal), args.max_revisions)
        print(json.dumps({"status": "archived", "chapter": str(chapter)}, ensure_ascii=False, indent=2))
        return 0
    except ValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
