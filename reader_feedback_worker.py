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
from tempfile import TemporaryDirectory

import author_registry
import reader_feedback_service as service
from novel_engine_v2.runner import resolve_codex


ROOT = Path(__file__).resolve().parent
REVISION_SCOPES = set(service.REVISION_SCOPES)


def parse_blind_reader_result(text: str) -> dict:
    stripped = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.S | re.I)
    if fenced:
        stripped = fenced.group(1)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"陌生读者报告没有返回有效JSON：{exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("陌生读者报告必须为JSON对象")
    for key in ("reader_experience", "scope_rationale"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError(f"陌生读者报告缺少 {key}")
    for key in ("visible_facts", "missing_or_late_information", "evidence"):
        if not isinstance(value.get(key), list) or not all(
            isinstance(item, str) and item.strip() for item in value[key]
        ):
            raise ValueError(f"陌生读者报告 {key} 必须为文本数组")
    if value.get("recommended_scope") not in REVISION_SCOPES:
        raise ValueError("陌生读者报告 recommended_scope 无效")
    return value


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
    if value.get("revision_scope") not in REVISION_SCOPES:
        raise ValueError("作者分析 revision_scope 必须为 wording、scene 或 chapter")
    if not isinstance(value.get("scope_rationale"), str) or not value[
        "scope_rationale"
    ].strip():
        raise ValueError("作者分析缺少 scope_rationale")
    for key in ("valid_observations", "misdiagnoses", "revision_strategy"):
        if not isinstance(value.get(key), list) or not all(
            isinstance(item, str) for item in value[key]
        ):
            raise ValueError(f"作者分析 {key} 必须为文本数组")
    if not isinstance(value.get("author_judgment"), str) or not value["author_judgment"].strip():
        raise ValueError("作者分析缺少 author_judgment")
    revision = value.pop("proposed_revision", None)
    learning = value.get("learning_candidate")
    if learning is not None:
        if not isinstance(learning, dict):
            raise ValueError("作者分析 learning_candidate 必须为对象或null")
        for key in ("principle", "applies_when", "avoid", "rationale"):
            if not isinstance(learning.get(key), str) or not learning[key].strip():
                raise ValueError(f"作者分析 learning_candidate.{key} 缺少文本")
        if learning.get("recommended_scope") not in {"book", "author"}:
            raise ValueError("作者分析 learning_candidate.recommended_scope 无效")
        if learning.get("confidence") not in {"medium", "high"}:
            raise ValueError("作者分析 learning_candidate.confidence 必须为 medium 或 high")
    if value["decision"] in {"accept", "partial"}:
        if not isinstance(revision, str) or not revision.strip():
            raise ValueError("采纳反馈时必须提供完整候选修订")
    else:
        revision = None
    return {"analysis": value, "revision": revision}


def build_blind_reader_prompt(book_id: str, feedback_id: str) -> tuple[Path, Path]:
    item = service.feedback_item(ROOT, book_id, feedback_id)
    _, project = service._book(ROOT, book_id)
    folder = project / "reader_feedback" / feedback_id
    prompt = folder / "blind_reader_prompt.md"
    prompt.write_text(
        f"""# 陌生读者试读

你是第一次看到这一章的普通读者。你不知道作者设定、大纲、人物档案、相邻章节、长期规则或作者本意，也不得读取它们。

只读取：
- 当前章节：`chapter.md`
- 读者本次留下的原始反馈：`feedback.json`

你的任务不是替作者改稿，而是说明仅凭眼前正文实际读到了什么、缺了什么，以及问题影响到多大范围。

修改层级只能三选一：
- wording：个别词句、语序、口吻或少量前后衔接有问题，正文场景任务仍成立。
- scene：一个完整场景的进入、关系、行动因果或情绪推进有问题，需要重写该场景，但整章任务仍成立。
- chapter：开篇承诺、正常世界参照、中心冲突、信息顺序或章末结果整体不成立，局部修句无法解决。

最终只输出一个JSON对象，不要代码围栏，不要修改任何文件：
{{
  "reader_experience": "第一次阅读时具体在哪里失去理解、相信或兴趣",
  "visible_facts": ["只凭正文已经能够确认的事实"],
  "missing_or_late_information": ["读者完成当前理解所缺少或出现过晚的信息"],
  "recommended_scope": "wording|scene|chapter",
  "scope_rationale": "为什么这个问题必须在该层级处理，局部修句是否足够",
  "evidence": ["逐字摘录当前正文中的短句作为依据"]
}}
""",
        encoding="utf-8",
    )
    return prompt, folder / "blind_reader_model_result.json"


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
- 陌生读者试读报告：`{folder / 'blind_reader_analysis.json'}`
{context}

判断顺序：
1. 先读取陌生读者报告，确认一个不知道作者设定的人实际读到了什么；不得用作者本意抹掉其阅读事实。
2. 区分“症状”和“读者猜测的病因”。
3. 在 wording、scene、chapter 中明确选择修改层级。若问题涉及开篇承诺、正常世界参照、中心冲突、信息顺序或整章任务，必须选择 chapter，不能用局部补句掩盖。
4. 再用作者档案、人物当下目的、相邻章节、连续性台账和作品读者承诺判断是否采纳；若推翻陌生读者建议的层级，必须在 scope_rationale 说明正文证据。
5. 不得自行永久新增规则；但若有效意见能跨句、跨场景复用，必须提炼一条等待副作者确认的长期经验候选。一次性措辞、仅服务当前情节的修补或不采纳意见不提炼。
6. 若采纳或部分采纳，只解决被证据支持的问题，保留已经成立的情节、人物选择、信息边界和章节元数据。
7. 修改权限由 revision_scope 决定：wording 只改选中词句和必要衔接；scene 可重写问题所在的完整场景；chapter 可重排、删写或重写整章。读者选中的原文只是问题证据，不再自动限制为局部修改。
8. 长期经验必须写成正向、可执行的创作原则，说明何时适用和怎样避免过度泛化。只影响本书独特文风、人物或设定时推荐 book；属于这个作者跨作品稳定取舍时才推荐 author。
9. 完整候选稿必须满足 `novel_config.md` 的常规章长，按最终正文重新填写 Metadata 的 word_count；不能沿用旧数字。

最终只输出一个JSON对象，不要代码围栏，不要修改任何文件：
{{
  "decision": "accept|partial|reject",
  "revision_scope": "wording|scene|chapter",
  "scope_rationale": "为什么应在这个层级修改，以及是否采纳陌生读者的层级判断",
  "author_judgment": "作者为何这样判断",
  "valid_observations": ["读者确实指出的问题"],
  "misdiagnoses": ["读者意见中不准确或不应照做的部分"],
  "revision_strategy": ["若需修改，具体改什么以及保留什么"],
  "learning_candidate": null或{{
    "principle": "以后写作时可直接执行的一条正向原则",
    "applies_when": "适用的场景、人物关系或文本条件",
    "avoid": "不得机械推广到哪些情况",
    "recommended_scope": "book|author",
    "confidence": "medium|high",
    "rationale": "为什么值得长期保留"
  }},
  "proposed_revision": "采纳或部分采纳时返回含原章节标题与元数据的完整修订稿；不采纳时为null"
}}
""",
        encoding="utf-8",
    )
    return prompt, folder / "model_result.json"


def run(book_id: str, feedback_id: str) -> None:
    service.update_status(
        ROOT, book_id, feedback_id, status="analyzing", message="陌生读者正在只看正文试读"
    )
    blind_prompt, blind_result_path = build_blind_reader_prompt(book_id, feedback_id)
    item = service.feedback_item(ROOT, book_id, feedback_id)
    chapter_source = Path(item["chapter_file"]).read_text(encoding="utf-8")
    chapter_text = service._narrative(chapter_source)
    feedback_source = (blind_result_path.parent / "feedback.json").read_text(
        encoding="utf-8"
    )
    with TemporaryDirectory(prefix="novel-blind-reader-") as temporary:
        isolated = Path(temporary)
        (isolated / "chapter.md").write_text(chapter_source, encoding="utf-8")
        (isolated / "feedback.json").write_text(feedback_source, encoding="utf-8")
        isolated_result = isolated / "result.json"
        blind_command = [
            resolve_codex(), "exec", "--ephemeral", "--skip-git-repo-check",
            "-C", str(isolated),
            "--sandbox", "read-only", "--config", 'approval_policy="never"',
            "--output-last-message", str(isolated_result), "-",
        ]
        blind_process = subprocess.run(
            blind_command,
            cwd=isolated,
            input=blind_prompt.read_text(encoding="utf-8"),
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if blind_process.returncode:
            raise RuntimeError(f"陌生读者试读进程退出码 {blind_process.returncode}")
        blind_result_text = isolated_result.read_text(encoding="utf-8")
    service.atomic_text(blind_result_path, blind_result_text)
    blind = parse_blind_reader_result(blind_result_text)
    if not blind["evidence"] or not all(
        evidence.strip() in chapter_text for evidence in blind["evidence"]
    ):
        raise ValueError("陌生读者报告的 evidence 必须逐字存在于当前正文")
    folder = blind_result_path.parent
    service.atomic_json(folder / "blind_reader_analysis.json", {
        "schema_version": 1,
        **blind,
        "reviewed_at": datetime.now().astimezone().isoformat(),
        "context_policy": "只读当前章节与原始反馈，不读取作者设定或相邻章节",
    })
    service.update_status(
        ROOT, book_id, feedback_id, status="analyzing", message="陌生读者报告完成，作者正在结合连续性审稿"
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
        "schema_version": 2,
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
