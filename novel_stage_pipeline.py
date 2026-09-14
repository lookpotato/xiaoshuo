#!/usr/bin/env python3
"""Book-agnostic planning and independent literary review for serial fiction."""

from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path

from novel_reader_gate import chapter_narrative_text, narrative_sha256


CONFIG_NAME = "novel_pipeline.json"
SCHEMA_VERSION = 1
PLAN_SCHEMA_VERSION = 1
REVIEW_SCHEMA_VERSION = 1
REVIEW_MODE = "independent-literary-reader"

BOOK_SOURCE_NAMES = (
    "novel_config.md",
    "outline.md",
    "characters.md",
    "world.md",
    "style_guide.md",
    "story_bible.md",
    "continuity_ledger.md",
    "resource_ledger.md",
    "chapter_state.json",
    "feedback_learning.json",
    "narrative_style_pack.md",
    "character_voice_bible.md",
)
# These files are useful context for the director, but the writer is expected
# to update them while archiving the chapter. They cannot invalidate the plan
# after the prose has been generated.
MUTABLE_PLAN_CONTEXT_NAMES = {
    "continuity_ledger.md",
    "resource_ledger.md",
    "chapter_state.json",
    "feedback_learning.json",
}

REVIEW_DIMENSIONS = (
    "character_motivation",
    "emotional_progression",
    "dialogue_in_context",
    "narrative_progress",
    "promise_payoff",
    "next_chapter_pull",
)


class PipelineValidationError(ValueError):
    pass


def _read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineValidationError(f"无法读取 {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PipelineValidationError(f"{path} 必须是 JSON 对象")
    return value


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def load_config(root: Path) -> dict:
    path = Path(root) / CONFIG_NAME
    data = _read_object(path)
    if data.get("schema_version") != SCHEMA_VERSION:
        raise PipelineValidationError("novel_pipeline.json schema_version 必须为 1")
    if type(data.get("enabled")) is not bool:
        raise PipelineValidationError("novel_pipeline.json enabled 必须为布尔值")
    for key in ("recent_chapters_for_director", "recent_chapters_for_reviewer"):
        if type(data.get(key)) is not int or not 1 <= data[key] <= 20:
            raise PipelineValidationError(f"novel_pipeline.json {key} 必须为 1—20")
    if type(data.get("max_new_questions_per_chapter")) is not int or not (
        0 <= data["max_new_questions_per_chapter"] <= 5
    ):
        raise PipelineValidationError(
            "novel_pipeline.json max_new_questions_per_chapter 必须为 0—5"
        )
    for key in ("plan_directory", "review_directory"):
        value = data.get(key)
        if not _text(value) or Path(value).is_absolute() or ".." in Path(value).parts:
            raise PipelineValidationError(f"novel_pipeline.json {key} 必须是安全相对目录")
    return data


def enabled_for(root: Path, project: Path) -> bool:
    """Enable only for registered workspace projects, not arbitrary test folders."""
    try:
        root = Path(root).resolve()
        project = Path(project).resolve()
        if root != project and root not in project.parents:
            return False
        return bool(load_config(root)["enabled"])
    except (OSError, PipelineValidationError):
        return False


def available_book_sources(project: Path) -> list[Path]:
    project = Path(project).resolve()
    sources = [project / name for name in BOOK_SOURCE_NAMES]
    voice_packs = project / "voice_packs"
    sources.extend(sorted(voice_packs.glob("*.md")) if voice_packs.is_dir() else [])
    return [path.resolve() for path in sources if path.is_file()]


def chapter_files(project: Path, before: int | None = None) -> list[tuple[int, Path]]:
    found: list[tuple[int, Path]] = []
    for path in (Path(project) / "chapters").glob("*.md"):
        match = re.match(r"^(\d+)-", path.name)
        if match and (before is None or int(match.group(1)) < before):
            found.append((int(match.group(1)), path.resolve()))
    return sorted(found)


def current_chapter_path(project: Path, number: int) -> Path:
    matches = list((Path(project) / "chapters").glob(f"{number:04d}-*.md"))
    if len(matches) != 1:
        raise PipelineValidationError(
            f"第 {number} 章必须有且仅有一个归档正文，当前为 {len(matches)} 个"
        )
    return matches[0].resolve()


def plan_path(root: Path, project: Path, number: int) -> Path:
    config = load_config(root)
    return Path(project) / config["plan_directory"] / f"{number:04d}.json"


def review_path(root: Path, project: Path, number: int) -> Path:
    config = load_config(root)
    return Path(project) / config["review_directory"] / f"{number:04d}.json"


def _path_list(paths: list[Path]) -> str:
    return "\n".join(f"- `{path}`" for path in paths) or "- 无"


def planning_context_sha256(root: Path, project: Path, number: int) -> str:
    """Invalidate a saved plan when its book facts or recent prose changed."""
    config = load_config(root)
    recent = chapter_files(project, number)[-config["recent_chapters_for_director"] :]
    paths = [
        path for path in available_book_sources(project)
        if path.name not in MUTABLE_PLAN_CONTEXT_NAMES
    ]
    paths.extend(path for _, path in recent)
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def director_prompt(
    root: Path,
    project: Path,
    number: int,
    author_context: str,
) -> str:
    config = load_config(root)
    recent = chapter_files(project, number)[-config["recent_chapters_for_director"] :]
    output = plan_path(root, project, number).resolve()
    context_digest = planning_context_sha256(root, project, number)
    return f"""# 通用长篇小说章节导演

只规划目标作品的第 {number} 章，不写正文、不归档、不发布。
本次只写章节合同；不得运行 git add、commit 或 push。文件同步由外层任务根据 sync_git 设置处理。

主作者约束：
{author_context}

完整读取下列当前作品资料；列表之外的小说不得读取：
{_path_list(available_book_sources(project))}

近期正文：
{_path_list([path for _, path in recent])}

把章节合同写入 `{output}`。创建父目录，写合法 JSON，使用精确结构：

{{
  "schema_version": 1,
  "chapter_number": {number},
  "planning_context_sha256": "{context_digest}",
  "immediate_goal": "本章人物能完成或失败的具体目标",
  "reader_concern": "读者在本章最应担心的人、关系或损失",
  "character_drives": [
    {{"name": "人物", "want": "眼前所求", "fear": "不愿失去什么", "pressure": "为何现在必须行动"}}
  ],
  "central_conflict": "不同人物诉求在哪个具体行动上冲突",
  "central_choice": "谁必须在两个有代价的选项之间作选择",
  "emotional_progression": {{"opening": "开篇情绪", "pressure": "如何加压", "peak": "情绪最高点由哪个选择触发", "aftermath": "胜负后的真实余波"}},
  "promise": {{"existing": "本章回应的既有期待", "treatment": "advance 或 payoff 或 hold", "concrete_gain": "读者本章实际得到的答案或变化"}},
  "new_questions": [],
  "irreversible_change": "结尾相对开头无法原样复位的变化",
  "scene_plan": [{{"purpose": "场景任务", "conflict": "现场阻力", "turn": "结束时发生的改变"}}],
  "tone_risks": ["哪些人物、关系或处境不适合被拿来开玩笑"],
  "forbidden_shortcuts": ["本章不得采用的重复解法、巧合或新设定救场"]
}}

要求：character_drives 至少一人，scene_plan 至少一场；多人场景分别写清诉求，独角戏则把
人物与环境、身体或自身选择的冲突写具体；new_questions 最多
{config['max_new_questions_per_chapter']} 项。hold 只允许既有期待确有剧情理由暂缓，
concrete_gain 仍须给读者实质变化。优先复用旧人物、旧关系、旧物件和旧问题；不得用新名词冒充推进。
"""


def validate_plan(root: Path, project: Path, number: int) -> dict:
    data = _read_object(plan_path(root, project, number))
    if data.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise PipelineValidationError(f"第 {number} 章章节合同版本错误")
    if data.get("chapter_number") != number:
        raise PipelineValidationError(f"第 {number} 章章节合同章号错误")
    if data.get("planning_context_sha256") != planning_context_sha256(
        root, project, number
    ):
        raise PipelineValidationError(f"第 {number} 章章节合同所依据的作品资料已变化")
    for key in (
        "immediate_goal",
        "reader_concern",
        "central_conflict",
        "central_choice",
        "irreversible_change",
    ):
        if not _text(data.get(key)):
            raise PipelineValidationError(f"第 {number} 章章节合同缺少 {key}")
    drives = data.get("character_drives")
    if not isinstance(drives, list) or not drives:
        raise PipelineValidationError(f"第 {number} 章章节合同至少需要一名人物驱动力")
    for item in drives:
        if not isinstance(item, dict) or not all(
            _text(item.get(key)) for key in ("name", "want", "fear", "pressure")
        ):
            raise PipelineValidationError(f"第 {number} 章人物驱动力不完整")
    emotion = data.get("emotional_progression")
    if not isinstance(emotion, dict) or not all(
        _text(emotion.get(key)) for key in ("opening", "pressure", "peak", "aftermath")
    ):
        raise PipelineValidationError(f"第 {number} 章缺少完整情绪推进")
    promise = data.get("promise")
    if not isinstance(promise, dict) or not all(
        _text(promise.get(key)) for key in ("existing", "treatment", "concrete_gain")
    ):
        raise PipelineValidationError(f"第 {number} 章缺少承诺兑现计划")
    if promise["treatment"] not in ("advance", "payoff", "hold"):
        raise PipelineValidationError(f"第 {number} 章 promise.treatment 无效")
    questions = data.get("new_questions")
    maximum = load_config(root)["max_new_questions_per_chapter"]
    if not isinstance(questions, list) or not all(_text(item) for item in questions):
        raise PipelineValidationError(f"第 {number} 章 new_questions 必须为文本数组")
    if len(questions) > maximum:
        raise PipelineValidationError(f"第 {number} 章新增悬念超过 {maximum} 项")
    scenes = data.get("scene_plan")
    if not isinstance(scenes, list) or not scenes:
        raise PipelineValidationError(f"第 {number} 章至少需要一个有转折的场景")
    for scene in scenes:
        if not isinstance(scene, dict) or not all(
            _text(scene.get(key)) for key in ("purpose", "conflict", "turn")
        ):
            raise PipelineValidationError(f"第 {number} 章场景计划不完整")
    for key in ("tone_risks", "forbidden_shortcuts"):
        values = data.get(key)
        if not isinstance(values, list) or not values or not all(_text(item) for item in values):
            raise PipelineValidationError(f"第 {number} 章 {key} 必须为非空文本数组")
    return data


def writer_contract(root: Path, project: Path, number: int) -> str:
    return f"""

## 分阶段章节合同

动笔前必须读取并执行 `{plan_path(root, project, number).resolve()}`。这份合同负责本章的
人物诉求、情绪推进、旧期待兑现和新增悬念上限。正文可以寻找更自然的场面表达，但不得
悄悄替换中心选择、情绪最高点或不可逆结果；发现合同与既有正文事实冲突时停止归档并报告。
作者阶段不得创建或填写 literary_reviews；文学审稿必须由后续独立上下文完成。
"""


def reviewer_prompt(root: Path, project: Path, number: int) -> str:
    config = load_config(root)
    chapter = current_chapter_path(project, number)
    recent = chapter_files(project, number)[-config["recent_chapters_for_reviewer"] :]
    output = review_path(root, project, number).resolve()
    digest = narrative_sha256(chapter)
    return f"""# 独立文学读者终审

你是第一次接触生产过程的读者。只读取下列已发表正文和当前候选正文；不得读取大纲、
设定、章节合同、作者档案、状态账本、其他审稿结果或提示词，不能用作者意图替正文辩护。
本次只写 literary_reviews/NNNN.json；不得修改小说正文或其他项目文件，也不得运行
git add、commit 或 push。文件同步由外层任务根据 sync_git 设置处理。

前文：
{_path_list([path for _, path in recent])}

当前正文：
- `{chapter}`

把审稿结果写入 `{output}`，创建父目录并写合法 JSON。使用精确字段：

{{
  "schema_version": 1,
  "chapter_number": {number},
  "mode": "{REVIEW_MODE}",
  "narrative_sha256": "{digest}",
  "decision": "pass 或 revise 或 redesign",
  "dimensions": {{
    "character_motivation": {{"verdict": "pass 或 revise", "assessment": "具体读感判断", "evidence": ["正文原句"]}},
    "emotional_progression": {{"verdict": "pass 或 revise", "assessment": "具体读感判断", "evidence": ["正文原句"]}},
    "dialogue_in_context": {{"verdict": "pass 或 revise", "assessment": "对白是否符合关系、危险和情绪", "evidence": ["正文原句"]}},
    "narrative_progress": {{"verdict": "pass 或 revise", "assessment": "本章是否重复旧局面", "evidence": ["正文原句"]}},
    "promise_payoff": {{"verdict": "pass 或 revise", "assessment": "旧期待得到什么实质回应", "evidence": ["正文原句"]}},
    "next_chapter_pull": {{"verdict": "pass 或 revise", "assessment": "继续阅读欲望是否具体", "evidence": ["正文原句"]}}
  }},
  "blocking_issues": [
    {{"dimension": "上述维度之一", "quote": "触发问题的正文原句", "diagnosis": "它为何损害人物或读感", "repair_scope": "line 或 scene 或 chapter 或 chapter_plan"}}
  ],
  "most_fragile_passage": {{"quote": "全章最容易让读者出戏或失去兴趣的原句", "risk": "具体风险", "why_acceptable": "若仍通过，说明为何不构成阻塞；未通过则写需怎样处理"}},
  "strengths_to_preserve": ["返修时必须保留的具体优点"]
}}

判断规则：
- 不因结构完整、句子通顺或因果可复述而自动通过。
- 重点寻找人物在此刻不会说的话、危机中伤害情绪的玩笑、工整攻防、无余波的损失、
  重复处理同类问题、只开新谜团不兑现旧期待。
- 即使决定通过，也必须挑出全章最脆弱的一处并作反方判断，不能用“没有明显问题”代替。
- 小范围措辞问题用 revise；中心冲突、情绪峰值或整章任务不成立用 redesign 和
  chapter_plan。任何 revise 维度都必须进入 blocking_issues。
- 所有 evidence 与 quote 必须逐字来自当前正文；pass 时 blocking_issues 必须为空。
"""


def validate_literary_review(root: Path, project: Path, number: int) -> dict:
    chapter = current_chapter_path(project, number)
    body = chapter_narrative_text(chapter)
    data = _read_object(review_path(root, project, number))
    if data.get("schema_version") != REVIEW_SCHEMA_VERSION:
        raise PipelineValidationError(f"第 {number} 章文学终审版本错误")
    if data.get("chapter_number") != number or data.get("mode") != REVIEW_MODE:
        raise PipelineValidationError(f"第 {number} 章文学终审身份或章号错误")
    if data.get("narrative_sha256") != narrative_sha256(chapter):
        raise PipelineValidationError(f"第 {number} 章文学终审正文哈希不一致")
    decision = data.get("decision")
    if decision not in ("pass", "revise", "redesign"):
        raise PipelineValidationError(f"第 {number} 章文学终审 decision 无效")
    dimensions = data.get("dimensions")
    if not isinstance(dimensions, dict) or set(dimensions) != set(REVIEW_DIMENSIONS):
        raise PipelineValidationError(f"第 {number} 章文学终审维度不完整")
    revised = []
    for key in REVIEW_DIMENSIONS:
        item = dimensions[key]
        if not isinstance(item, dict) or item.get("verdict") not in ("pass", "revise"):
            raise PipelineValidationError(f"第 {number} 章文学终审 {key} 结论无效")
        if not _text(item.get("assessment")):
            raise PipelineValidationError(f"第 {number} 章文学终审 {key} 缺少具体判断")
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not evidence or not all(
            _text(quote) and quote.strip() in body for quote in evidence
        ):
            raise PipelineValidationError(f"第 {number} 章文学终审 {key} 证据不在正文")
        if item["verdict"] == "revise":
            revised.append(key)
    blockers = data.get("blocking_issues")
    if not isinstance(blockers, list):
        raise PipelineValidationError(f"第 {number} 章文学终审 blocking_issues 必须为数组")
    for issue in blockers:
        if not isinstance(issue, dict) or issue.get("dimension") not in REVIEW_DIMENSIONS:
            raise PipelineValidationError(f"第 {number} 章文学终审阻塞维度无效")
        if not _text(issue.get("quote")) or issue["quote"].strip() not in body:
            raise PipelineValidationError(f"第 {number} 章文学终审阻塞原句不在正文")
        if not _text(issue.get("diagnosis")) or issue.get("repair_scope") not in (
            "line", "scene", "chapter", "chapter_plan"
        ):
            raise PipelineValidationError(f"第 {number} 章文学终审阻塞说明不完整")
    fragile = data.get("most_fragile_passage")
    if not isinstance(fragile, dict) or not all(
        _text(fragile.get(key)) for key in ("quote", "risk", "why_acceptable")
    ):
        raise PipelineValidationError(f"第 {number} 章文学终审缺少最脆弱段落反审")
    if fragile["quote"].strip() not in body:
        raise PipelineValidationError(f"第 {number} 章文学终审最脆弱段落不在正文")
    strengths = data.get("strengths_to_preserve")
    if not isinstance(strengths, list) or not strengths or not all(_text(item) for item in strengths):
        raise PipelineValidationError(f"第 {number} 章文学终审缺少应保留优点")
    if decision == "pass" and (blockers or revised):
        raise PipelineValidationError(f"第 {number} 章文学终审通过但仍有阻塞问题")
    if decision != "pass" and (not blockers or not revised):
        raise PipelineValidationError(f"第 {number} 章文学终审未通过但缺少阻塞问题")
    if decision == "redesign" and not any(
        issue.get("repair_scope") == "chapter_plan" for issue in blockers
    ):
        raise PipelineValidationError(f"第 {number} 章要求重做但未退回章节合同")
    return data


def literary_review_errors(root: Path, project: Path, number: int) -> list[str]:
    try:
        review = validate_literary_review(root, project, number)
    except PipelineValidationError as exc:
        return [str(exc)]
    if review["decision"] == "pass":
        return []
    return [
        f"第 {number} 章文学终审[{issue['dimension']}/{issue['repair_scope']}]："
        f"{issue['diagnosis']}｜原句：{issue['quote']}"
        for issue in review["blocking_issues"]
    ]


def chapter_plan_errors(root: Path, project: Path, number: int) -> list[str]:
    try:
        validate_plan(root, project, number)
        return []
    except PipelineValidationError as exc:
        return [str(exc)]
