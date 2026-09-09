#!/usr/bin/env python3
"""Ask the bound author to judge reader feedback and propose a safe revision."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import author_registry
import reader_feedback_service as service
from novel_engine_v2.runner import resolve_codex


ROOT = Path(__file__).resolve().parent


def parse_result(text: str) -> dict:
    stripped = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.S | re.I)
    if fenced:
        stripped = fenced.group(1)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"作者分析没有返回有效JSON：{exc}") from exc
    if not isinstance(value, dict) or value.get("decision") not in {
        "accept", "partial", "reject",
    }:
        raise ValueError("作者分析 decision 必须为 accept、partial 或 reject")
    for key in ("valid_observations", "misdiagnoses", "revision_strategy"):
        if not isinstance(value.get(key), list) or not all(
            isinstance(item, str) for item in value[key]
        ):
            raise ValueError(f"作者分析 {key} 必须为文本数组")
    if not isinstance(value.get("author_judgment"), str) or not value["author_judgment"].strip():
        raise ValueError("作者分析缺少 author_judgment")
    revision = value.pop("proposed_revision", None)
    if value["decision"] in {"accept", "partial"}:
        if not isinstance(revision, str) or not revision.strip():
            raise ValueError("采纳反馈时必须提供完整候选修订")
    else:
        revision = None
    return {"analysis": value, "revision": revision}


def build_prompt(book_id: str, feedback_id: str) -> tuple[Path, Path]:
    item = service.feedback_item(ROOT, book_id, feedback_id)
    _, project = service._book(ROOT, book_id)
    folder = project / "reader_feedback" / feedback_id
    author = author_registry.book_author(ROOT, book_id)
    author_path = ROOT / author["document_id"]
    source = Path(item["chapter_file"])
    chapter_number = int(item["chapter"])
    adjacent_chapters = []
    for number in (chapter_number - 1, chapter_number + 1):
        matches = list((project / "chapters").glob(f"{number:04d}-*.md"))
        if len(matches) == 1:
            adjacent_chapters.append(matches[0])
    context_paths = [
        project / "novel_config.md",
        project / "style_guide.md",
        project / "characters.md",
        project / "story_bible.md",
        project / "continuity_ledger.md",
        *adjacent_chapters,
    ]
    context = "\n".join(f"- `{path}`" for path in context_paths if path.is_file())
    prompt = folder / "analysis_prompt.md"
    prompt.write_text(
        f"""# 真实读者反馈：作者判断阶段

你是这本小说已经绑定的作者，不是顺从读者的客服。读者的“不舒服”是真实阅读事实，但读者对病因和改法的判断可能正确、部分正确或错误。

只读取：
- 作者档案：`{author_path}`
- 当前章节：`{source}`
- 结构化反馈：`{folder / 'feedback.json'}`
{context}

判断顺序：
1. 先定位读者产生不适的正文证据，不否认真实感受。
2. 区分“症状”和“读者猜测的病因”。
3. 用作者档案、人物当下目的、相邻章节、连续性台账和作品读者承诺判断是否采纳。
4. 不得因为一条意见永久新增作者规则，不得把人物棱角磨平，不得迎合到破坏伏笔、人物性格或作品辨识度。
5. 若采纳或部分采纳，只解决被证据支持的问题，保留已经成立的情节、人物选择、信息边界和章节元数据。

最终只输出一个JSON对象，不要代码围栏，不要修改任何文件：
{{
  "decision": "accept|partial|reject",
  "author_judgment": "作者为何这样判断",
  "valid_observations": ["读者确实指出的问题"],
  "misdiagnoses": ["读者意见中不准确或不应照做的部分"],
  "revision_strategy": ["若需修改，具体改什么以及保留什么"],
  "proposed_revision": "采纳或部分采纳时返回含原章节标题与元数据的完整修订稿；不采纳时为null"
}}
""",
        encoding="utf-8",
    )
    return prompt, folder / "model_result.json"


def run(book_id: str, feedback_id: str) -> None:
    service.update_status(
        ROOT, book_id, feedback_id, status="analyzing", message="作者正在判断反馈"
    )
    prompt, result_path = build_prompt(book_id, feedback_id)
    command = [
        resolve_codex(), "exec", "--ephemeral", "-C", str(ROOT),
        "--sandbox", "read-only", "--config", 'approval_policy="never"',
        "--output-last-message", str(result_path), "-",
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        input=prompt.read_text(encoding="utf-8"),
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        raise RuntimeError(f"作者分析进程退出码 {result.returncode}")
    parsed = parse_result(result_path.read_text(encoding="utf-8"))
    folder = result_path.parent
    analysis = {
        "schema_version": 1,
        **parsed["analysis"],
        "analyzed_at": datetime.now().astimezone().isoformat(),
        "policy": "真实感受作为证据，修改方案由绑定作者裁决",
    }
    service.atomic_json(folder / "analysis.json", analysis)
    if parsed["revision"] is not None:
        service.atomic_text(folder / "proposed_revision.md", parsed["revision"].strip() + "\n")
    service.update_status(
        ROOT,
        book_id,
        feedback_id,
        status="reviewed",
        message="作者判断完成，等待读者查看或确认采用",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="分析真实读者反馈")
    parser.add_argument("--book", required=True)
    parser.add_argument("--feedback-id", required=True)
    args = parser.parse_args()
    try:
        run(args.book, args.feedback_id)
        print("真实读者反馈分析完成")
        return 0
    except Exception as exc:
        try:
            service.update_status(
                ROOT,
                args.book,
                args.feedback_id,
                status="failed",
                message=f"作者分析失败：{exc}",
            )
        except Exception:
            pass
        print(f"真实读者反馈分析失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
