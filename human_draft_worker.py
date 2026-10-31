#!/usr/bin/env python3
"""Create a reviewable polish preview from a saved human chapter draft."""

from __future__ import annotations

import argparse

import human_draft_service
from novel_v3 import ROOT


def main() -> int:
    parser = argparse.ArgumentParser(description="作者手写稿轻润色预览")
    parser.add_argument("--book", required=True)
    parser.add_argument("--chapter", type=int, required=True)
    args = parser.parse_args()
    path = human_draft_service.polish(ROOT, args.book, args.chapter)
    print(f"润色预览已保存：{path}")
    print("原手写稿和正式归档未被替换。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
