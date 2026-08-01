#!/usr/bin/env python3
"""一键启动番茄小说批量创作、上传与排期。"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MANAGER = ROOT / "fanqie_novel_manager.py"
ON_DEMAND = ROOT / "xiaoshuo_on_demand.py"
REWARD = ROOT / "xiaoshuo_reward.py"
BROWSER_WORKER = ROOT / "fanqie_browser_worker.py"


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="xiaoshuo",
        description="调用 Codex 严格串行完成指定数量的小说章节并发布到番茄。",
    )
    parser.add_argument("count", type=int, nargs="?", help="本批要完成的章节数，例如 5")
    parser.add_argument(
        "--reward",
        type=int,
        metavar="N",
        help="打赏加更：把最早的 N 个未来排期章节提前到今天",
    )
    parser.add_argument(
        "--reward-time",
        metavar="HH:MM",
        help="加更发布时间；省略时使用当前时间后 30 分钟并向上取整",
    )
    parser.add_argument("--book", default="cosmic-404", help="manager_config.json 中的书籍 id")
    parser.add_argument("--dry-run", action="store_true", help="只显示计划，不启动 Codex")
    parser.add_argument("--check", action="store_true", help="检查 Codex、Chrome 与书籍绑定")
    parser.add_argument(
        "--setup-browser",
        action="store_true",
        help="首次配置番茄专用 Chrome 登录会话",
    )
    parser.add_argument(
        "--debug-browser",
        action="store_true",
        help="报错时保留 Chrome 窗口，便于人工查看页面",
    )
    parser.add_argument("--resume", help="续跑 `.manager_jobs` 中已有的 job id")
    args = parser.parse_args()
    if args.setup_browser:
        return subprocess.run(
            [
                sys.executable,
                str(BROWSER_WORKER),
                "--project",
                str(ROOT / "404修理站"),
                "--setup",
            ],
            cwd=ROOT,
        ).returncode
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
    if args.reward is not None:
        if args.count is not None or args.resume:
            parser.error("--reward 不能与普通章节数或 --resume 同时使用")
        command = [
            sys.executable,
            str(REWARD),
            str(args.reward),
            "--book",
            args.book,
        ]
        if args.reward_time:
            command.extend(["--time", args.reward_time])
        if args.dry_run:
            command.append("--dry-run")
        if args.debug_browser:
            command.append("--debug-browser")
        return subprocess.run(command, cwd=ROOT).returncode
    if args.reward_time:
        parser.error("--reward-time 只能与 --reward 一起使用")
    if args.count is None and not args.resume:
        parser.error("请提供章节数、`--resume <job-id>` 或 `--check`")
    command = [sys.executable, str(ON_DEMAND)]
    if args.count is not None:
        command.append(str(args.count))
    command.extend(["--book", args.book])
    if args.resume:
        command.extend(["--resume", args.resume])
    if args.dry_run:
        command.append("--dry-run")
    if args.debug_browser:
        command.append("--debug-browser")
    return subprocess.run(command, cwd=ROOT).returncode


if __name__ == "__main__":
    raise SystemExit(main())
