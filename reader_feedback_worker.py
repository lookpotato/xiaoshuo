#!/usr/bin/env python3
"""Run the user-selected blind-reader and/or bound-author feedback review."""

from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import author_registry
import reader_feedback_service as service
from novel_engine_v2.runner import resolve_codex


ROOT = Path(__file__).resolve().parent
REVISION_SCOPES = set(service.REVISION_SCOPES)


def _evidence_in_text(chapter_text: str, evidence: str) -> bool:
    """Accept exact quotations with harmless punctuation/label differences."""
    def normalize(value: str) -> str:
        value = re.sub(r"^\s*(?:证据|原文|正文)\s*[:：]\s*", "", value.strip())
        value = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", value)
        return value

    source = normalize(service._narrative(chapter_text))
    raw = str(evidence).strip()
    candidates = [normalize(raw)]
    if "：" in raw or ":" in raw:
        candidates.append(normalize(re.split(r"[：:]", raw, maxsplit=1)[1]))
    if any(candidate and candidate in source for candidate in candidates):
        return True
    # Models sometimes prepend a speaker label or a short explanation. Accept
    # a long contiguous Chinese fragment only when it is still verbatim text.
    fragments = re.findall(r"[\u4e00-\u9fff]{3,}", raw)
    return any(fragment in source for fragment in fragments)


def _evidence_segments(chapter_text: str) -> list[str]:
    """Return short, user-visible source segments suitable for evidence repair."""
    narrative = service._narrative(chapter_text)
    return [
        segment.strip()
        for segment in re.split(r"(?<=[。！？；\n])", narrative)
        if len(re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", segment)) >= 3
    ]


def _repair_evidence(chapter_text: str, evidence: str) -> tuple[str, bool]:
    """Keep exact evidence, or replace a model-composed quote with a close source segment."""
    raw = str(evidence).strip()
    if _evidence_in_text(chapter_text, raw):
        return raw, False
    compact_raw = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", raw)
    if len(compact_raw) < 3:
        return raw, False
    best: tuple[float, str] | None = None
    for segment in _evidence_segments(chapter_text):
        compact_segment = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", segment)
        matcher = difflib.SequenceMatcher(None, compact_raw, compact_segment)
        longest = max((block.size for block in matcher.get_matching_blocks()), default=0)
        if longest < 3:
            continue
        score = longest * 2 + matcher.ratio()
        if best is None or score > best[0]:
            best = (score, segment)
    return (best[1], True) if best else (raw, False)


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


def parse_chapter_interview_result(text: str) -> dict:
    """Validate a whole-chapter author-led language review."""
    stripped = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.S | re.I)
    if fenced:
        stripped = fenced.group(1)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"整章提问没有返回有效JSON：{exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("整章提问必须返回JSON对象")
    for key in ("chapter_promise", "reading_summary"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise ValueError(f"整章提问缺少 {key}")
    questions = value.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("整章提问 questions 不能为空")
    cleaned_questions = []
    for index, question in enumerate(questions, 1):
        if not isinstance(question, dict):
            raise ValueError("整章提问 questions 必须为对象数组")
        for key in ("question", "why_it_matters"):
            if not isinstance(question.get(key), str) or not question[key].strip():
                raise ValueError(f"整章提问第{index}题缺少 {key}")
        level = question.get("level")
        if level not in {"wording", "scene", "foundation"}:
            raise ValueError("整章提问 level 必须为 wording、scene 或 foundation")
        evidence = question.get("evidence", [])
        if not isinstance(evidence, list) or not all(
            isinstance(item, str) and item.strip() for item in evidence
        ):
            raise ValueError(f"整章提问第{index}题 evidence 必须为文本数组")
        cleaned_questions.append({
            "id": str(question.get("id") or f"Q{index}"),
            "level": level,
            "question": question["question"].strip(),
            "why_it_matters": question["why_it_matters"].strip(),
            # Keep old interview records readable while requiring the new prompt
            # to provide a concrete natural-language repair direction.
            "suggested_fix": str(question.get("suggested_fix") or "请从人物关系、动作和语气出发，给出更自然的写法。").strip(),
            "evidence": evidence,
        })
    risks = value.get("foundation_risks", [])
    if not isinstance(risks, list):
        raise ValueError("整章提问 foundation_risks 必须为数组")
    cleaned_risks = []
    for risk in risks:
        if not isinstance(risk, dict):
            raise ValueError("整章提问 foundation_risks 必须为对象数组")
        for key in ("risk", "question", "evidence"):
            if not isinstance(risk.get(key), str) or not risk[key].strip():
                raise ValueError(f"底层设计风险缺少 {key}")
        if risk.get("severity") not in {"low", "medium", "high"}:
            raise ValueError("底层设计风险 severity 必须为 low、medium 或 high")
        cleaned_risks.append({
            "risk": risk["risk"].strip(),
            "severity": risk["severity"],
            "question": risk["question"].strip(),
            "evidence": risk["evidence"].strip(),
        })
    return {
        "chapter_promise": value["chapter_promise"].strip(),
        "reading_summary": value["reading_summary"].strip(),
        "questions": cleaned_questions,
        "foundation_risks": cleaned_risks,
    }


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


def parse_follow_up_result(text: str) -> dict:
    stripped = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.S | re.I)
    if fenced:
        stripped = fenced.group(1)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"作者回复没有返回有效JSON：{exc}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("reply"), str) or not value["reply"].strip():
        raise ValueError("作者回复缺少 reply")
    changed = value.get("changed_judgment")
    if not isinstance(changed, bool):
        raise ValueError("作者回复 changed_judgment 必须为布尔值")
    if not changed:
        return {"reply": value["reply"].strip(), "changed_judgment": False}
    analysis_value = value.get("analysis")
    if not isinstance(analysis_value, dict):
        raise ValueError("修改判断时必须返回完整 analysis")
    analysis_value = dict(analysis_value)
    analysis_value["proposed_revision"] = value.get("proposed_revision")
    parsed = parse_result(json.dumps(analysis_value, ensure_ascii=False))
    return {
        "reply": value["reply"].strip(),
        "changed_judgment": True,
        **parsed,
    }


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


def build_chapter_interview_prompt(book_id: str, feedback_id: str) -> tuple[Path, Path]:
    item = service.feedback_item(ROOT, book_id, feedback_id)
    _, project = service._book(ROOT, book_id)
    folder = project / "reader_feedback" / feedback_id
    author = author_registry.book_author(ROOT, book_id)
    author_path = ROOT / author["document_id"]
    chapter_path = Path(item["chapter_file"])
    chapter_number = int(item["chapter"])
    context_paths = [
        project / "novel_config.md",
        project / "style_guide.md",
        project / "characters.md",
        project / "story_bible.md",
        project / "continuity_ledger.md",
    ]
    context = "\n".join(f"- `{path}`" for path in context_paths if path.is_file())
    prompt = folder / "chapter_interview_prompt.md"
    result = folder / "chapter_interview_model_result.json"
    prompt.write_text(
        f"""# 整章写法审校

你是绑定本书的副作者和中文语用审校员。主作者已经写完第 {chapter_number} 章，你的任务是审核成稿是否像真实的中国人会说、会做，而不是盘问主作者脑中的隐藏设定。请完整阅读本章，指出不自然、关系依据不足、像提纲或系统提示的地方，并给出可执行的改写方向。

必须读取：
- 作者档案：`{author_path}`
- 当前整章：`{chapter_path}`
- 本书设定与连续性资料：
{context}

审校原则：
1. 先说明整章读感和本章承诺，再列出最值得修改的写法。不要把作者没有写出的幕后设定当成作者必须解释的答案。
2. 每条都要以“这段成稿是否像真人会这样说/做”为核心，不能只问“作者为什么这样设计”。例如不要只写“孟阿婆为什么强硬”，要写清“正文没有给出关系依据时，陌生长辈这样命令式说话是否自然；若不自然，建议改成怎样的试探、拒绝或提醒”。
3. 每题标记层级：wording（局部说法）、scene（完整场景推进）、foundation（人物关系、核心冲突、世界规则、目标承诺或结局逻辑）。
4. 每题必须给出 suggested_fix：可以是更自然的说话动作、语气方向、关系铺垫或一小段示例；不要只说“加强人物感情”。不要替主作者重写整章。
5. 特别检查首次出现的人物，尤其客户、长辈、邻居和陌生人：称呼、语气、强硬程度是否有得罪、欠账、权力差、熟人默契或现场压力的依据。没有依据时，明确指出这是写法问题，并给出符合中国语用的替代方式。
6. 重点检查对白是否被写成电报、操作口令、合同摘要或作者替人物总结。中文口语允许省略，但应有关系动作、停顿、改口、指代、回避或态度变化。
7. foundation_risks 只填写有正文证据的风险；不要臆测世界观漏洞。证据必须是当前章节中的原文短句或明确事件。
8. evidence 必须从当前章节复制原文，不得改写、总结、拼接两处原文或补充说话人。尤其不能把“先查值班表”和“下一步却已经不在书里了”合并成正文不存在的新句子；需要解释影响时放在 why_it_matters。

最终只输出一个 JSON 对象，不要代码围栏：
{{
  "chapter_promise": "本章对读者做出的核心承诺",
  "reading_summary": "只凭本章读完后的真实阅读结果，指出最清楚和最不稳的地方",
  "questions": [
    {{
      "id": "Q1",
      "level": "wording|scene|foundation",
      "question": "给主作者的审校问题：这段写法像真实中国人会这样说/做吗？",
      "why_it_matters": "为什么不回答这个问题，就不能判断本章是否成立",
      "suggested_fix": "如果不自然，建议怎样改写或补足关系动作；可给一到两句示例",
      "evidence": ["当前章节中的原文短句或明确事件"]
    }}
  ],
  "foundation_risks": [
    {{
      "risk": "可能存在的底层设计风险",
      "severity": "low|medium|high",
      "question": "这处写法是否自然；如果不自然，建议如何改",
      "evidence": "当前章节证据"
    }}
  ]
}}
""",
        encoding="utf-8",
    )
    return prompt, result


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
    blind_report = folder / "blind_reader_analysis.json"
    if blind_report.is_file():
        blind_context = (
            f"- 陌生读者试读报告：`{blind_report}`\n"
            "先读取陌生读者报告，确认一个不知道作者设定的人实际读到了什么；"
            "不得用作者本意抹掉其阅读事实。"
        )
        blind_scope_rule = (
            "若推翻陌生读者建议的层级，必须在 scope_rationale 说明正文证据。"
        )
    else:
        blind_context = (
            "本次选择的是单独作者审稿，没有陌生读者报告。"
            "请直接根据正文证据、作者档案、设定与连续性判断。"
        )
        blind_scope_rule = "在 scope_rationale 中说明修改层级所依据的正文证据。"
    prompt = folder / "analysis_prompt.md"
    prompt.write_text(
        f"""# 真实读者反馈：作者判断阶段

你是这本小说已经绑定的作者，不是顺从读者的客服。读者的“不舒服”是真实阅读事实，但读者对病因和改法的判断可能正确、部分正确或错误。

只读取：
- 作者档案：`{author_path}`
- 当前章节：`{source}`
- 结构化反馈：`{folder / 'feedback.json'}`
{blind_context}
{context}

判断顺序：
1. 先做真人开口测试：暂时不看人物设定，只问一个当代中国人在这个可见现场、面对这个关系对象时，会不会自然地这样开口。读着像作者概括、功能清单、系统提示或翻译腔，就必须承认语言问题；人物目的正确不能替不自然的说法辩护。
2. 再区分“症状”和“读者猜测的病因”。用户对真实中文口语的直接纠正，优先级高于作者为既有台词寻找合理解释；不同意时必须给出正文与现实语用证据，不能只说“符合人设”。
3. 在 wording、scene、chapter 中明确选择修改层级。若问题涉及开篇承诺、正常世界参照、中心冲突、信息顺序或整章任务，必须选择 chapter，不能用局部补句掩盖。
4. 用作者档案、人物当下目的、相邻章节、连续性台账和作品读者承诺判断如何修改；这些资料用于保连续性，不能用来否定已经成立的阅读不适。{blind_scope_rule}
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


def build_follow_up_prompt(book_id: str, feedback_id: str) -> tuple[Path, Path]:
    item = service.feedback_item(ROOT, book_id, feedback_id)
    _, project = service._book(ROOT, book_id)
    folder = project / "reader_feedback" / feedback_id
    author = author_registry.book_author(ROOT, book_id)
    author_path = ROOT / author["document_id"]
    dialogue = service.read_json(folder / "author_dialogue.json", {}) or {}
    messages = dialogue.get("messages", [])
    if not isinstance(messages, list) or not messages or messages[-1].get("role") != "user":
        raise ValueError("没有等待作者回复的追问")
    prompt = folder / "author_follow_up_prompt.md"
    result = folder / "author_follow_up_model_result.json"
    prompt.write_text(f"""# 作者判断连续对话

你是本书绑定作者，正在和副作者继续讨论同一条反馈。这里不是一次新的审稿，不要忘记前文，也不要维护面子。副作者指出你分析错了时，必须重新检查现实中文语用；人物目的、熟人关系和剧情功能只能解释“为什么要说”，不能自动证明“这句话会这样说”。

读取：
- 作者档案：`{author_path}`
- 当前章节：`{item['chapter_file']}`
- 原始反馈：`{folder / 'feedback.json'}`
- 当前作者判断：`{folder / 'analysis.json'}`
- 完整连续对话：`{folder / 'author_dialogue.json'}`
- 本书文风：`{project / 'style_guide.md'}`
- 中国口语基础：`{ROOT / 'shared' / 'chinese_dialogue_foundation.md'}`

先直接回应副作者最新一句，再判断原结论是否需要改。若副作者纠正的是“现实中不会这样说”，先把台词还原成它在现场真正想完成的动作，检查抽象概括、清单结构、书面词和过度完整；不得用“符合人设”“目的成立”“其余部分没问题”回避该句本身。

只输出一个 JSON 对象，不要代码围栏：
{{
  "reply": "直接、具体地回应最新追问；承认或反驳都要给出理由",
  "changed_judgment": true或false,
  "analysis": null或完整的新判断对象（字段与首次作者判断一致，不含 schema_version、analyzed_at、policy），
  "proposed_revision": null或修改判断后对应的完整候选章节
}}

changed_judgment 为 true 时，analysis 必须完整包含 decision、revision_scope、scope_rationale、author_judgment、valid_observations、misdiagnoses、revision_strategy、learning_candidate；采纳或部分采纳时必须同时返回完整 proposed_revision。为 false 时 analysis 与 proposed_revision 均为 null。
""", encoding="utf-8")
    return prompt, result


def run_follow_up(book_id: str, feedback_id: str) -> None:
    _, project = service._book(ROOT, book_id)
    folder = project / "reader_feedback" / feedback_id
    dialogue = service.update_author_dialogue(
        ROOT, book_id, feedback_id, status="responding", error=None
    )
    pending_id = dialogue.get("pending_message_id")
    prompt, result_path = build_follow_up_prompt(book_id, feedback_id)
    command = [
        resolve_codex(), "exec", "--ephemeral", "-C", str(ROOT),
        "--sandbox", "read-only", "--config", 'approval_policy="never"',
        "--output-last-message", str(result_path), "-",
    ]
    result = subprocess.run(command, cwd=ROOT, input=prompt.read_text(encoding="utf-8"),
                            text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError(f"作者连续对话进程退出码 {result.returncode}")
    parsed = parse_follow_up_result(result_path.read_text(encoding="utf-8"))
    changed = bool(parsed["changed_judgment"])
    if changed:
        previous = service.read_json(folder / "analysis.json")
        history = folder / "analysis_history"
        history.mkdir(parents=True, exist_ok=True)
        service.atomic_json(history / f"{datetime.now():%Y%m%d-%H%M%S}.json", previous)
        analysis = {
            "schema_version": 3, **parsed["analysis"],
            "analyzed_at": datetime.now().astimezone().isoformat(),
            "policy": "副作者可通过连续对话纠正作者判断；现实中文语用优先于事后人设辩护",
        }
        service.atomic_json(folder / "analysis.json", analysis)
        proposal = folder / "proposed_revision.md"
        if parsed["revision"] is None:
            proposal.unlink(missing_ok=True)
        else:
            service.atomic_text(proposal, parsed["revision"].strip() + "\n")
    latest = service.read_json(folder / "author_dialogue.json", {}) or {}
    messages = latest.get("messages", [])
    messages.append({
        "id": uuid.uuid4().hex[:12], "role": "author",
        "content": parsed["reply"], "changed_judgment": changed,
        "reply_to": pending_id, "created_at": datetime.now().astimezone().isoformat(),
    })
    service.update_author_dialogue(
        ROOT, book_id, feedback_id, messages=messages, status="idle",
        pending_message_id=None, error=None,
    )


def run(book_id: str, feedback_id: str) -> None:
    item = service.feedback_item(ROOT, book_id, feedback_id)
    review_mode = str(item.get("review_mode", "combined"))
    if review_mode not in service.REVIEW_MODES:
        raise ValueError("审稿方式无效")
    if review_mode == "chapter_interview":
        service.update_status(
            ROOT, book_id, feedback_id, status="analyzing",
            message="正在完整阅读本章，审校人物说话与行动是否自然",
        )
        prompt, result_path = build_chapter_interview_prompt(book_id, feedback_id)
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
            raise RuntimeError(f"整章提问进程退出码 {result.returncode}")
        interview = parse_chapter_interview_result(
            result_path.read_text(encoding="utf-8")
        )
        chapter_text = Path(item["chapter_file"]).read_text(encoding="utf-8")
        repaired_evidence = []
        for question in interview["questions"]:
            repaired = []
            for original in question["evidence"]:
                current, changed = _repair_evidence(chapter_text, original)
                if changed:
                    repaired_evidence.append({"from": original, "to": current})
                repaired.append(current)
            question["evidence"] = repaired
        for risk in interview["foundation_risks"]:
            current, changed = _repair_evidence(chapter_text, risk["evidence"])
            if changed:
                repaired_evidence.append({"from": risk["evidence"], "to": current})
            risk["evidence"] = current
        evidence = [
            *[e for question in interview["questions"] for e in question["evidence"]],
            *[risk["evidence"] for risk in interview["foundation_risks"]],
        ]
        if not all(_evidence_in_text(chapter_text, text) for text in evidence):
            invalid = next(text for text in evidence if not _evidence_in_text(chapter_text, text))
            raise ValueError(f"整章提问的 evidence 必须逐字存在于当前正文：{invalid}")
        service.atomic_json(
            result_path.parent / "chapter_interview.json",
            {
                "schema_version": 1,
                **interview,
                "reviewed_at": datetime.now().astimezone().isoformat(),
                "context_policy": "完整阅读当前章节，并结合绑定作者与本书设计资料进行写法审校",
                "evidence_repairs": repaired_evidence,
            },
        )
        service.update_status(
            ROOT, book_id, feedback_id, status="interview_ready",
            message="整章写法审校完成；请作者判断是否采纳建议，并沉淀有效中文经验",
        )
        return
    if review_mode in {"blind", "combined"}:
        service.update_status(
            ROOT, book_id, feedback_id, status="analyzing", message="陌生读者正在只看正文试读"
        )
        blind_prompt, blind_result_path = build_blind_reader_prompt(book_id, feedback_id)
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
        if review_mode == "blind":
            service.update_status(
                ROOT, book_id, feedback_id, status="blind_reviewed",
                message="陌生读者试读完成；本次未进入作者审稿",
            )
            return
    service.update_status(
        ROOT, book_id, feedback_id, status="analyzing",
        message=(
            "陌生读者报告完成，作者正在结合连续性审稿"
            if review_mode == "combined" else "作者正在结合设定与连续性审稿"
        ),
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
    parser.add_argument("--follow-up", action="store_true")
    args = parser.parse_args()
    try:
        if args.follow_up:
            run_follow_up(args.book, args.feedback_id)
        else:
            run(args.book, args.feedback_id)
        print("真实读者反馈分析完成")
        return 0
    except Exception as exc:
        if getattr(args, "follow_up", False):
            try:
                service.update_author_dialogue(
                    ROOT, args.book, args.feedback_id,
                    status="failed", error=str(exc), pending_message_id=None,
                )
            except Exception:
                pass
            print(f"作者连续对话失败：{exc}", file=sys.stderr)
            return 1
        try:
            service.update_status(
                ROOT,
                args.book,
                args.feedback_id,
                status="failed",
                message=f"审稿任务失败：{exc}",
            )
        except Exception:
            pass
        print(f"真实读者反馈审稿失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
