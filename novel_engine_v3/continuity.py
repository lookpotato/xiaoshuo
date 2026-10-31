"""Required artifacts and chronology gate for the unified V3 workflow."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from novel_stage_pipeline import PipelineValidationError, plan_path, validate_plan

from .engine import V3ValidationError, WORKFLOW, LEGACY_WORKFLOW


def _object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise V3ValidationError(f"连续性材料缺失或无效：{path}") from exc
    if not isinstance(value, dict):
        raise V3ValidationError(f"连续性材料必须是 JSON 对象：{path}")
    return value


def validate_director(root: Path, project: Path, chapter: int) -> None:
    try:
        plan = validate_plan(root, project, chapter)
    except (OSError, PipelineValidationError) as exc:
        raise V3ValidationError(f"剧情总设计师章节合同未通过：{exc}") from exc
    state_path = project / "timeline_state.json"
    if state_path.is_file():
        previous = _object(state_path)
        clock = plan.get("story_clock")
        if not isinstance(clock, dict):
            raise V3ValidationError("章节合同缺少 story_clock")
        _validate_clock(clock, previous, chapter)


def validate_timeline_head(project: Path, chapter: int) -> None:
    """Reject a stale chapter reset before the expensive director stage."""
    state_path = project / "timeline_state.json"
    if not state_path.is_file():
        return
    timeline = _object(state_path)
    chapter_state = _object(project / "chapter_state.json")
    if timeline.get("last_chapter") != chapter - 1 or chapter_state.get("last_completed_chapter") != chapter - 1:
        raise V3ValidationError(
            f"第 {chapter} 章开始前，章节状态与时间线未同步；请先回退 timeline_state.json 到第 {chapter - 1} 章终点"
        )


def _validate_clock(clock: dict, previous: dict, chapter: int) -> None:
    if previous.get("last_chapter") != chapter - 1:
        raise V3ValidationError("时间线状态没有接上上一章")
    fields = ("start_day", "end_day", "start_floor", "end_floor", "crossed_2347")
    if any(type(clock.get(key)) is not int for key in fields):
        raise V3ValidationError("时间线缺少整数故事日、楼层或跨夜次数")
    if any(type(clock.get(key)) is not bool for key in ("start_after_2347", "end_after_2347")):
        raise V3ValidationError("时间线缺少 23:47 前后状态")
    start_day, end_day = clock["start_day"], clock["end_day"]
    if start_day < previous["day"] or end_day < start_day:
        raise V3ValidationError("时间线故事日倒退")
    for point in ("start", "end"):
        expected_floor = 6 + clock[f"{point}_day"] - 1 + int(clock[f"{point}_after_2347"])
        if clock[f"{point}_floor"] != expected_floor:
            raise V3ValidationError(f"{point} 楼层与每日 23:47 增一层的规则不符")
    if clock["start_floor"] < previous["floor"] or clock["end_floor"] < clock["start_floor"]:
        raise V3ValidationError("楼层数倒退")
    if clock["crossed_2347"] != clock["end_floor"] - previous["floor"]:
        raise V3ValidationError("跨过的 23:47 次数与新增楼层数不一致")


def validate_characters(project: Path, chapter: int, *, final: bool) -> None:
    directory = project / "character_threads" / f"{chapter:04d}"
    cast = directory / "00-cast.md"
    interaction = directory / "interaction_map.md"
    required = [cast, interaction]
    if final:
        required.append(directory / "state_update.md")
    missing = [p.name for p in required if not p.is_file() or not p.read_text(encoding="utf-8").strip()]
    if missing:
        raise V3ValidationError(f"第 {chapter} 章人物私线缺少：{', '.join(missing)}")
    names = re.findall(r"^-\s*([^：:\n]+)\s*[：:]", cast.read_text(encoding="utf-8"), re.M)
    if not names:
        raise V3ValidationError(f"第 {chapter} 章人物名单没有有效人物")
    for name in names:
        files = [p for p in directory.glob("[0-9][0-9]-*.md") if p.name != "00-cast.md" and name.strip() in p.stem]
        if not files:
            raise V3ValidationError(f"第 {chapter} 章缺少 {name.strip()} 的人物私线")
        if final and name.strip() not in (directory / "state_update.md").read_text(encoding="utf-8"):
            raise V3ValidationError(f"第 {chapter} 章未回写 {name.strip()} 的状态")


def validate_viewpoint_contract(root: Path, project: Path, chapter: int) -> None:
    """Catch a plan/map viewpoint disagreement before drafting starts."""
    plan = _object(plan_path(root, project, chapter))
    if plan.get("schema_version", 1) < 3:
        return  # Archived plans predate the explicit viewpoint contract.
    contract = plan.get("viewpoint_contract")
    if not isinstance(contract, str) or not contract.strip():
        raise V3ValidationError(f"第 {chapter} 章章节合同缺少视角约定")
    interaction = project / "character_threads" / f"{chapter:04d}" / "interaction_map.md"
    if f"**视角合同**：{contract}" not in interaction.read_text(encoding="utf-8"):
        raise V3ValidationError(
            f"第 {chapter} 章交织表与章节合同的视角约定不一致，请在写正文前修正 interaction_map.md"
        )


def stamp_candidate(run: Path) -> None:
    digest = hashlib.sha256((run / "candidate.md").read_bytes()).hexdigest()
    (run / "candidate.sha256").write_text(digest + "\n", encoding="ascii")


def validate_review(run: Path, chapter: int) -> None:
    review = _object(run / "continuity_review.json")
    if review.get("chapter") != chapter or not isinstance(review.get("blocking"), list):
        raise V3ValidationError(f"第 {chapter} 章连续性审查格式无效")
    if review["blocking"]:
        raise V3ValidationError(f"第 {chapter} 章连续性审查未通过：{review['blocking'][:3]}")
    if not isinstance(review.get("next_hook"), str) or not review["next_hook"].strip():
        raise V3ValidationError(f"第 {chapter} 章连续性审查缺少下一章事实")
    if not isinstance(review.get("unresolved"), list) or not all(
        isinstance(item, str) for item in review["unresolved"]
    ):
        raise V3ValidationError(f"第 {chapter} 章连续性审查待解事项格式无效")
    expected = (run / "candidate.sha256").read_text(encoding="ascii").strip()
    actual = hashlib.sha256((run / "candidate.md").read_bytes()).hexdigest()
    if actual != expected:
        raise V3ValidationError(f"第 {chapter} 章审查后正文发生变化，须重新审查")


def validate_chronology(run: Path, project: Path, chapter: int) -> dict | None:
    state_path = project / "timeline_state.json"
    if not state_path.is_file():
        return None
    before = _object(state_path)
    after = _object(run / "chronology.json")
    if after.get("chapter") != chapter:
        raise V3ValidationError("时间线章号没有接上上一章")
    _validate_clock(after, before, chapter)
    plan_clock = _object(plan_path(run.parents[2], project, chapter)).get("story_clock")
    if not isinstance(plan_clock, dict):
        raise V3ValidationError("章节合同缺少 story_clock")
    if any(after.get(key) != plan_clock.get(key) for key in (
        "start_day", "end_day", "start_floor", "end_floor", "crossed_2347",
        "start_after_2347", "end_after_2347",
    )):
        raise V3ValidationError("成稿时间线与总设计师章节合同不一致，需重新规划")
    events = after.get("events")
    if not isinstance(events, list) or len(events) != after["crossed_2347"]:
        raise V3ValidationError("新增楼层事件数量与跨夜次数不一致")
    body = (run / "candidate.md").read_text(encoding="utf-8-sig")
    for key in ("start_quote", "end_quote"):
        quote = after.get(key)
        if not isinstance(quote, str) or not quote.strip() or quote not in body:
            raise V3ValidationError(f"时间线 {key} 未引用当前正文")
    for index, event in enumerate(events, 1):
        floor = before["floor"] + index
        if not isinstance(event, dict) or event.get("floor") != floor:
            raise V3ValidationError("新增楼层事件顺序错误")
        if event.get("day") != floor - 6:
            raise V3ValidationError(f"第 {floor} 楼出现的故事日错误")
        quote = event.get("quote")
        if not isinstance(quote, str) or not quote.strip() or quote not in body:
            raise V3ValidationError(f"第 {floor} 楼事件未引用当前正文")
    return {"last_chapter": chapter, "day": after["end_day"], "floor": after["end_floor"],
            "after_2347": after["end_after_2347"]}


def validate_unified_run(root: Path, run: Path, project: Path, chapter: int) -> dict | None:
    manifest = _object(run / "manifest.json")
    if manifest.get("workflow") not in (WORKFLOW, LEGACY_WORKFLOW):
        raise V3ValidationError("旧运行包没有完整统一流程，请重新准备章节")
    validate_director(root, project, chapter)
    validate_characters(project, chapter, final=True)
    validate_viewpoint_contract(root, project, chapter)
    validate_review(run, chapter)
    if manifest.get("book_id") == "night-floor":
        state = validate_chronology(run, project, chapter)
        if state is None:
            raise V3ValidationError("《我家旧楼每晚多一层》缺少 timeline_state.json")
        return state
    return None
