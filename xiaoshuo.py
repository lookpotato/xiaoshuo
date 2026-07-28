#!/usr/bin/env python3
"""一键启动番茄小说批量创作、上传与排期。"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MANAGER = ROOT / "fanqie_novel_manager.py"


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="xiaoshuo",
        description="调用 Codex 严格串行完成指定数量的小说章节并发布到番茄。",
    )
    parser.add_argument("count", type=int, nargs="?", help="本批要完成的章节数，例如 5")
    parser.add_argument("--book", default="cosmic-404", help="manager_config.json 中的书籍 id")
    parser.add_argument("--dry-run", action="store_true", help="只显示计划，不启动 Codex")
    parser.add_argument("--check", action="store_true", help="检查 Codex、浏览器组件与书籍绑定")
    parser.add_argument("--resume", help="续跑 `.manager_jobs` 中已有的 job id")
    args = parser.parse_args()
    if args.check:
        return subprocess.run(
            [
                sys.executable,
                str(MANAGER),
                "doctor",
                "--book",
                args.book,
            ],
            cwd=ROOT,
        ).returncode
    if args.count is None and not args.resume:
        parser.error("请提供章节数、`--resume <job-id>` 或 `--check`")
    command = [
        sys.executable,
        str(MANAGER),
        "run",
    ]
    if args.count is not None:
        command.append(str(args.count))
    command.extend(["--book", args.book])
    if args.resume:
        command.extend(["--resume", args.resume])
    if args.dry_run:
        command.append("--dry-run")
    return subprocess.run(command, cwd=ROOT).returncode


if __name__ == "__main__":
    raise SystemExit(main())
