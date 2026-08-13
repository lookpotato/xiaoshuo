#!/usr/bin/env python3
"""一键启动番茄小说批量创作、上传与排期。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MANAGER = ROOT / "fanqie_novel_manager.py"
ON_DEMAND = ROOT / "xiaoshuo_on_demand.py"
REWARD = ROOT / "xiaoshuo_reward.py"
BROWSER_WORKER = ROOT / "fanqie_browser_worker.py"
IMAGE_BROWSER_WORKER = ROOT / "browser_image_worker.py"
CONFIG = ROOT / "manager_config.json"


def load_books() -> tuple[dict, list[dict]]:
    data = json.loads(CONFIG.read_text(encoding="utf-8"))
    books = data.get("books")
    if not isinstance(books, list):
        raise ValueError("manager_config.json 缺少 books 数组")
    return data, books


def selected_books(book_id: str | None, all_books: bool, feature: str) -> list[dict]:
    data, books = load_books()
    if all_books:
        selected = [book for book in books if book.get("enabled", False)]
        if feature == "reward":
            selected = [
                book
                for book in selected
                if book.get("manual_extra_chapters_supported", False)
            ]
        if not selected:
            raise ValueError("没有符合条件的已启用小说")
        return sorted(selected, key=lambda item: -item.get("priority", 0))

    target = book_id or data.get("default_book_id") or "cosmic-404"
    for book in books:
        if book.get("id") == target:
            if feature == "reward" and not book.get(
                "manual_extra_chapters_supported", False
            ):
                raise ValueError(f"书籍 {target} 当前未启用平台加更")
            return [book]
    raise ValueError(f"未知书籍 id: {target}")


def run_commands(commands: list[tuple[dict, list[str]]]) -> int:
    for index, (book, command) in enumerate(commands, 1):
        print(
            f"\n[{index}/{len(commands)}] {book['title']} ({book['id']})",
            flush=True,
        )
        result = subprocess.run(command, cwd=ROOT).returncode
        if result:
            print(
                f"批量执行在 {book['id']} 停止，退出码 {result}；"
                "尚未开始的小说未被修改。",
                file=sys.stderr,
            )
            return result
    return 0


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
        help="加更发布时间；省略时使用当前时间后 45 分钟并向上取整",
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--book", help="只更新 manager_config.json 中指定的书籍 id")
    target.add_argument("--all", action="store_true", help="依优先级更新全部已启用小说")
    parser.add_argument("--dry-run", action="store_true", help="只显示计划，不启动 Codex")
    parser.add_argument("--check", action="store_true", help="检查 Codex、Chrome 与书籍绑定")
    parser.add_argument(
        "--setup-browser",
        action="store_true",
        help="首次配置番茄专用 Chrome 登录会话",
    )
    image_browser = parser.add_mutually_exclusive_group()
    image_browser.add_argument(
        "--setup-image-browser",
        action="store_true",
        help="首次配置网页生图专用 Chrome 登录会话",
    )
    image_browser.add_argument(
        "--check-image-browser",
        action="store_true",
        help="检查网页生图 Chrome 的登录态与页面控件",
    )
    parser.add_argument(
        "--debug-browser",
        action="store_true",
        help="报错时保留 Chrome 窗口，便于人工查看页面",
    )
    parser.add_argument("--resume", help="续跑 `.manager_jobs` 中已有的 job id")
    args = parser.parse_args()
    if args.setup_image_browser or args.check_image_browser:
        if args.count is not None or args.reward is not None or args.resume:
            parser.error("图片浏览器配置命令不能与章节任务同时使用")
        action = "--setup" if args.setup_image_browser else "--check"
        return subprocess.run(
            [sys.executable, str(IMAGE_BROWSER_WORKER), action],
            cwd=ROOT,
        ).returncode
    try:
        feature = "reward" if args.reward is not None else "update"
        books = selected_books(args.book, args.all, feature)
    except ValueError as exc:
        parser.error(str(exc))
    if args.setup_browser:
        if args.all:
            parser.error("--setup-browser 只需配置一次，请用 --book 指定打开哪本书")
        book = books[0]
        return subprocess.run(
            [
                sys.executable,
                str(BROWSER_WORKER),
                "--project",
                str(ROOT / book["path"]),
                "--setup",
            ],
            cwd=ROOT,
        ).returncode
    if args.check:
        return run_commands([
            (book, [
                sys.executable,
                str(MANAGER),
                "doctor",
                "--book",
                book["id"],
            ])
            for book in books
        ])
    if args.reward is not None:
        if args.count is not None or args.resume:
            parser.error("--reward 不能与普通章节数或 --resume 同时使用")
        commands = []
        for book in books:
            command = [
                sys.executable,
                str(REWARD),
                str(args.reward),
                "--book",
                book["id"],
            ]
            if args.reward_time:
                command.extend(["--time", args.reward_time])
            if args.dry_run:
                command.append("--dry-run")
            if args.debug_browser:
                command.append("--debug-browser")
            commands.append((book, command))
        return run_commands(commands)
    if args.reward_time:
        parser.error("--reward-time 只能与 --reward 一起使用")
    if args.count is None and not args.resume:
        parser.error("请提供章节数、`--resume <job-id>` 或 `--check`")
    if args.resume and args.all:
        parser.error("--resume 只能续跑一个 job，请同时使用 --book")
    commands = []
    for book in books:
        command = [sys.executable, str(ON_DEMAND)]
        if args.count is not None:
            command.append(str(args.count))
        command.extend(["--book", book["id"]])
        if args.resume:
            command.extend(["--resume", args.resume])
        if args.dry_run:
            command.append("--dry-run")
        if args.debug_browser:
            command.append("--debug-browser")
        commands.append((book, command))
    return run_commands(commands)


if __name__ == "__main__":
    raise SystemExit(main())
