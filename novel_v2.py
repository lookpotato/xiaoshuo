#!/usr/bin/env python3
"""Compatibility CLI forwarding former V2 commands to the production pipeline."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRODUCTION = ROOT / "xiaoshuo.py"
MANAGER = ROOT / "fanqie_novel_manager.py"


def production_command(command: str, book_id: str) -> list[str]:
    if command == "validate":
        return [sys.executable, str(MANAGER), "doctor", "--book", book_id]
    base = [
        sys.executable, str(PRODUCTION), "1", "--book", book_id,
        "--no-sync-git", "--no-publish-fanqie",
    ]
    if command == "prepare":
        base.append("--dry-run")
    return base


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="统一小说生产系统兼容入口；所有命令转交工作台同一流程"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="验证统一生产配置")
    validate.add_argument("--book", required=True)
    prepare = sub.add_parser("prepare", help="预览统一生产流程，不创建第二套写作包")
    prepare.add_argument("--book", required=True)
    prepare.add_argument("--signal", action="append", default=[])
    run = sub.add_parser("run", help="通过统一生产流程生成、审阅并本地归档一章")
    run.add_argument("--book", required=True)
    run.add_argument("--signal", action="append", default=[])
    run.add_argument("--max-revisions", type=int, default=2)
    args = parser.parse_args()

    if getattr(args, "max_revisions", 0) < 0 or getattr(args, "max_revisions", 0) > 3:
        parser.error("max-revisions 必须在 0 到 3 之间")
    if getattr(args, "signal", []):
        print("提示：统一生产流程由小说设置自动装配能力，--signal 已不再单独生效。")
    if args.command == "run" and args.max_revisions != 2:
        print("提示：统一生产流程使用自己的自动修复上限，--max-revisions 已不再单独生效。")
    print("已统一到小说工作台生产流程，不会启动独立 V2 生成器。", flush=True)
    return subprocess.run(production_command(args.command, args.book), cwd=ROOT).returncode


if __name__ == "__main__":
    raise SystemExit(main())
