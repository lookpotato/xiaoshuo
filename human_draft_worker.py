#!/usr/bin/env python3
"""Create a chapter plan or a reviewable polish preview."""

from __future__ import annotations

import argparse

import human_draft_service
from novel_v3 import ROOT


def main() -> int:
    parser = argparse.ArgumentParser(description="作者手写稿轻润色预览")
    parser.add_argument("--book", required=True)
    parser.add_argument("--chapter", type=int, required=True)
    parser.add_argument("--plan", action="store_true")
    args = parser.parse_args()
    if args.plan:
        path = human_draft_service.plan_chapter(ROOT, args.book, args.chapter)
        print(f"章节规划已保存：{path}")
        print("现在可在手写章节接力中查看场景说明、写手稿。")
        return 0
    path = human_draft_service.polish(ROOT, args.book, args.chapter)
    print(f"润色预览已保存：{path}")
    print("原手写稿和正式归档未被替换。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
