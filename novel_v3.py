#!/usr/bin/env python3
"""V3 author-room workflow entry point.

This command intentionally writes to .novel_runs_v3 and does not archive a
chapter.  V3 must earn the right to replace the existing production chain by
passing a human read first.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

from novel_engine_v3 import AuthorEngine, V3ValidationError


ROOT = Path(__file__).resolve().parent


def execute(prompt: Path, output: Path) -> None:
    codex = shutil.which("codex")
    if not codex:
        raise V3ValidationError("找不到 codex CLI；请先安装并登录")
    result = subprocess.run(
        [codex, "exec", "--ephemeral", "-C", str(ROOT), "--sandbox", "workspace-write",
         "--config", 'approval_policy="never"', "--output-last-message", str(output), "-"],
        cwd=ROOT,
        input=prompt.read_text(encoding="utf-8"),
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        raise V3ValidationError(f"阶段失败：{prompt.name}，退出码 {result.returncode}")


def main() -> int:
    parser = argparse.ArgumentParser(description="V3 作者房间长篇生成链")
    parser.add_argument("command", choices=("prepare", "run"))
    parser.add_argument("--book", required=True)
    parser.add_argument("--chapter", type=int)
    args = parser.parse_args()
    engine = AuthorEngine(ROOT)
    run = engine.prepare(args.book, args.chapter)
    print(f"V3 运行包：{run}")
    if args.command == "prepare":
        return 0
    for stage in ("author_room", "draft", "deep_edit", "reader"):
        execute(run / f"{stage}.md", run / f"{stage}.result.md")
    print("V3 已完成作者房间、初稿、深层编辑和读者检查；未自动覆盖正文。")
    print("请先阅读 candidate.md 与 reader_review.json，再决定是否接入归档流程。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except V3ValidationError as exc:
        raise SystemExit(f"V3 错误：{exc}")
