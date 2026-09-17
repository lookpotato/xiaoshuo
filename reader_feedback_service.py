"""Structured real-reader feedback with author-governed revision proposals."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path


CATEGORIES = {
    "uncomfortable": "读着不舒服",
    "confusing": "看不懂",
    "character_voice": "人物不像本人",
    "ai_flavor": "AI味明显",
    "pacing": "节奏不对",
    "emotion": "情绪不对",
    "other": "其他",
}
SAFE_ID = re.compile(r"^[0-9A-Za-z_-]{8,80}$")
MAX_QUOTE_CHARS = 3000
MAX_COMMENT_CHARS = 5000
PROMOTION_SCOPES = {"book": "本书", "author": "作者"}
PROMOTION_LOCK = threading.RLock()


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temp, path)


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(value, encoding="utf-8")
    os.replace(temp, path)


def _book(root: Path, book_id: str) -> tuple[dict, Path]:
    config = read_json(root / "manager_config.json", {})
    books = config.get("books", []) if isinstance(config, dict) else []
    book = next((item for item in books if item.get("id") == book_id), None)
    if not isinstance(book, dict):
        raise ValueError(f"未知书籍 id: {book_id}")
    project = (root / str(book.get("path", ""))).resolve()
    root = root.resolve()
    if root != project and root not in project.parents:
        raise ValueError("书籍路径越界")
    return book, project


def _chapter(project: Path, number: int) -> Path:
    matches = list((project / "chapters").glob(f"{number:04d}-*.md"))
    if len(matches) != 1:
        raise ValueError(f"找不到唯一的第 {number} 章")
    return matches[0]


def _narrative(text: str) -> str:
    marker = re.search(r"\n---\s*\n+\s*##\s+Metadata\b", text, re.I)
    return (text[: marker.start()] if marker else text).strip()


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _body_text(value: str) -> str:
    narrative = _narrative(value)
    return re.sub(r"^\s*#\s*第\s*\d+\s*章\s+.*?(?:\r?\n|$)", "", narrative, count=1).strip()


def _chapter_length_range(project: Path) -> tuple[int, int] | None:
    config_path = project / "novel_config.md"
    if not config_path.is_file():
        return None
    config = config_path.read_text(encoding="utf-8")
    match = re.search(
        r"常规章[^\d]{0,12}(\d{3,5})\s*[—–~～-]\s*(\d{3,5})\s*字", config
    )
    if not match:
        return None
    minimum, maximum = (int(match.group(1)), int(match.group(2)))
    return (minimum, maximum) if 0 < minimum <= maximum else None


def _validate_revision_scope(current: str, revision: str, quote: str) -> None:
    """Keep a selected-passage revision close to the passage the reader marked."""
    marked = _compact(quote)
    if not marked:
        return
    before = _compact(_narrative(current))
    after = _compact(_narrative(revision))
    quote_start = before.find(marked)
    if quote_start < 0:
        raise ValueError("反馈选中原文已经不属于当前章节")
    prefix = 0
    prefix_limit = min(len(before), len(after))
    while prefix < prefix_limit and before[prefix] == after[prefix]:
        prefix += 1
    suffix = 0
    suffix_limit = min(len(before) - prefix, len(after) - prefix)
    while suffix < suffix_limit and before[-1 - suffix] == after[-1 - suffix]:
        suffix += 1
    changed_end = len(before) - suffix
    quote_end = quote_start + len(marked)
    margin = 300
    if prefix < max(0, quote_start - margin) or changed_end > min(
        len(before), quote_end + margin
    ):
        raise ValueError("局部反馈的候选稿改动超出选中段落附近，请改用整章重写")


def _refresh_word_count(revision: str, count: int) -> str:
    pattern = r"(?m)^(\s*-\s*word_count\s*:\s*).*$"
    if not re.search(pattern, revision):
        raise ValueError("候选修订缺少 Metadata word_count")
    return re.sub(pattern, rf"\g<1>{count}", revision, count=1)


def _feedback_dir(root: Path, book_id: str, feedback_id: str) -> Path:
    if not SAFE_ID.fullmatch(feedback_id):
        raise ValueError("feedback id 格式不正确")
    _, project = _book(root, book_id)
    return project / "reader_feedback" / feedback_id


def create_feedback(root: Path, payload: dict) -> dict:
    book_id = str(payload.get("book_id", "")).strip()
    try:
        chapter = int(payload.get("chapter", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("章节号无效") from exc
    category = str(payload.get("category", "uncomfortable")).strip()
    quote = str(payload.get("quote", "")).strip()
    comment = str(payload.get("comment", "")).strip()
    if category not in CATEGORIES:
        raise ValueError("反馈类型无效")
    if not 1 <= chapter <= 100000:
        raise ValueError("章节号无效")
    if not comment or len(comment) > MAX_COMMENT_CHARS:
        raise ValueError(f"读者留言必须为1—{MAX_COMMENT_CHARS}字")
    if len(quote) > MAX_QUOTE_CHARS:
        raise ValueError(f"选中原文不能超过{MAX_QUOTE_CHARS}字")
    book, project = _book(root, book_id)
    chapter_path = _chapter(project, chapter)
    source = chapter_path.read_text(encoding="utf-8")
    if quote and _compact(quote) not in _compact(_narrative(source)):
        raise ValueError("选中原文不属于当前章节，请重新选择")
    feedback_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    folder = project / "reader_feedback" / feedback_id
    data = {
        "schema_version": 1,
        "id": feedback_id,
        "book_id": book_id,
        "book_title": str(book.get("title", book_id)),
        "chapter": chapter,
        "chapter_file": str(chapter_path.resolve()),
        "chapter_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "category": category,
        "category_label": CATEGORIES[category],
        "quote": quote,
        "comment": comment,
        "created_at": datetime.now().astimezone().isoformat(),
    }
    atomic_json(folder / "feedback.json", data)
    atomic_json(folder / "status.json", {
        "status": "queued",
        "message": "已收到真实读者反馈，等待作者判断",
        "updated_at": datetime.now().astimezone().isoformat(),
    })
    return feedback_item(root, book_id, feedback_id)


def update_status(root: Path, book_id: str, feedback_id: str, **changes) -> dict:
    folder = _feedback_dir(root, book_id, feedback_id)
    status = read_json(folder / "status.json", {}) or {}
    status.update(changes)
    status["updated_at"] = datetime.now().astimezone().isoformat()
    atomic_json(folder / "status.json", status)
    return status


def feedback_item(root: Path, book_id: str, feedback_id: str) -> dict:
    folder = _feedback_dir(root, book_id, feedback_id)
    feedback = read_json(folder / "feedback.json")
    if not isinstance(feedback, dict) or feedback.get("book_id") != book_id:
        raise ValueError("找不到反馈记录")
    status = read_json(folder / "status.json", {}) or {}
    analysis = read_json(folder / "analysis.json")
    promotion = read_json(folder / "promotion.json")
    return {
        **feedback,
        "status": status.get("status", "queued"),
        "status_message": status.get("message", ""),
        "run_id": status.get("run_id"),
        "analysis": analysis if isinstance(analysis, dict) else None,
        "promotion": promotion if isinstance(promotion, dict) else None,
        "has_revision": (folder / "proposed_revision.md").is_file(),
        "applied_at": status.get("applied_at"),
        "receipt": status.get("receipt") if isinstance(status.get("receipt"), dict) else None,
    }


def list_feedback(root: Path, book_id: str, chapter: int | None = None) -> list[dict]:
    _, project = _book(root, book_id)
    base = project / "reader_feedback"
    if not base.is_dir():
        return []
    rows = []
    for folder in sorted(base.iterdir(), key=lambda item: item.name, reverse=True):
        if not folder.is_dir() or not SAFE_ID.fullmatch(folder.name):
            continue
        try:
            item = feedback_item(root, book_id, folder.name)
        except ValueError:
            continue
        if chapter is None or item.get("chapter") == chapter:
            rows.append(item)
        if len(rows) >= 100:
            break
    return rows


def _version_payload(path: Path, *, version_id: str, kind: str, label: str,
                     created_at: str | None = None) -> dict:
    text = path.read_text(encoding="utf-8")
    return {
        "id": version_id,
        "kind": kind,
        "label": label,
        "created_at": created_at or datetime.fromtimestamp(
            path.stat().st_mtime
        ).astimezone().isoformat(),
        "content": _narrative(text),
    }


def chapter_versions(root: Path, book_id: str, chapter: int) -> dict:
    """Return the live chapter plus read-only drafts and feedback snapshots."""
    _, project = _book(root, book_id)
    chapter_path = _chapter(project, chapter)
    current_text = chapter_path.read_text(encoding="utf-8")
    versions = []

    draft_pattern = f"*-chapter-{chapter:04d}.md"
    for path in sorted((project / "drafts").glob(draft_pattern), reverse=True):
        versions.append(_version_payload(
            path,
            version_id=f"draft:{path.name}",
            kind="generated_draft",
            label=f"生成草稿 · {path.stem.split('-chapter-')[0]}",
        ))

    for item in list_feedback(root, book_id, chapter):
        folder = _feedback_dir(root, book_id, str(item["id"]))
        applied_at = item.get("applied_at") or item.get("created_at")
        original = folder / "original_before_apply.md"
        if original.is_file():
            versions.append(_version_payload(
                original,
                version_id=f"before:{item['id']}",
                kind="before_apply",
                label=f"应用前备份 · {item['category_label']}",
                created_at=applied_at,
            ))
        proposal = folder / "proposed_revision.md"
        if proposal.is_file():
            versions.append(_version_payload(
                proposal,
                version_id=f"proposal:{item['id']}",
                kind="proposal",
                label=f"候选修订 · {item['category_label']}",
                created_at=item.get("created_at"),
            ))

    versions.sort(key=lambda value: str(value.get("created_at", "")), reverse=True)
    return {
        "current": {
            "book_id": book_id,
            "number": chapter,
            "filename": chapter_path.name,
            "content": _narrative(current_text),
            "sha256": hashlib.sha256(current_text.encode("utf-8")).hexdigest(),
        },
        "versions": versions[:60],
    }


def _learning_candidate(analysis: dict) -> dict:
    candidate = analysis.get("learning_candidate")
    if not isinstance(candidate, dict):
        raise ValueError("作者没有为这条反馈提出可沉淀的长期经验")
    cleaned = {}
    for key in ("principle", "applies_when", "avoid", "rationale"):
        value = re.sub(r"\s+", " ", str(candidate.get(key, ""))).strip()
        if not value or len(value) > 1000:
            raise ValueError(f"长期经验 {key} 无效")
        cleaned[key] = value
    scope = str(candidate.get("recommended_scope", ""))
    confidence = str(candidate.get("confidence", ""))
    if scope not in PROMOTION_SCOPES or confidence not in {"medium", "high"}:
        raise ValueError("长期经验候选的范围或置信度无效")
    return {**cleaned, "recommended_scope": scope, "confidence": confidence}


def _rule_id(category: str, principle: str) -> str:
    normalized = re.sub(r"[\W_]+", "", principle, flags=re.UNICODE).casefold()
    if not normalized:
        raise ValueError("长期经验原则不能只包含标点")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
    return f"{category}-{digest}"


def _render_rule(record: dict) -> str:
    return (
        f"{record['principle']} 适用：{record['applies_when']} "
        f"避免误用：{record['avoid']}"
    )


def _append_markdown_rule(path: Path, marker: str, record: dict) -> None:
    if not path.is_file():
        raise ValueError(f"缺少长期规则载体：{path}")
    current = path.read_text(encoding="utf-8")
    if marker in current:
        return
    heading = "## 副作者确认的长期反馈规则"
    addition = (
        f"\n\n{heading}\n" if heading not in current else "\n"
    ) + f"\n<!-- {marker} -->\n- {_render_rule(record)}\n"
    atomic_text(path, current.rstrip() + addition)


def _book_learning_registry(project: Path) -> tuple[Path, dict]:
    path = project / "feedback_learning.json"
    data = read_json(path, {"schema_version": 1, "rules": []})
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError(f"长期反馈知识库格式无效：{path}")
    if not isinstance(data.get("rules"), list):
        raise ValueError(f"长期反馈知识库 rules 无效：{path}")
    return path, data


def _upsert_learning(records: list, record: dict, feedback_id: str) -> dict:
    existing = next(
        (item for item in records if isinstance(item, dict) and item.get("id") == record["id"]),
        None,
    )
    if existing is None:
        records.append(record)
        return record
    evidence = existing.setdefault("evidence_feedback_ids", [])
    if feedback_id not in evidence:
        evidence.append(feedback_id)
        existing["evidence_count"] = len(evidence)
        existing["updated_at"] = datetime.now().astimezone().isoformat()
    return existing


def _author_projects(root: Path, author_id: str) -> list[Path]:
    import author_registry

    system = author_registry.load_system(root)
    book_ids = {
        book_id for book_id, value in system["books"].items()
        if isinstance(value, dict) and value.get("author") == author_id
    }
    config = read_json(root / "manager_config.json", {}) or {}
    projects = []
    root_resolved = root.resolve()
    for book in config.get("books", []):
        if not isinstance(book, dict) or book.get("id") not in book_ids:
            continue
        project = (root / str(book.get("path", ""))).resolve()
        if root_resolved != project and root_resolved not in project.parents:
            raise ValueError("作者绑定的书籍路径越界")
        if project not in projects:
            projects.append(project)
    return projects


def promote_learning(root: Path, book_id: str, feedback_id: str, scope: str) -> dict:
    """Promote one author-proposed lesson after explicit co-author confirmation."""
    scope = str(scope).strip()
    if scope not in PROMOTION_SCOPES:
        raise ValueError("长期经验范围必须是本书或作者")
    folder = _feedback_dir(root, book_id, feedback_id)
    feedback = read_json(folder / "feedback.json")
    analysis = read_json(folder / "analysis.json")
    if not isinstance(feedback, dict) or not isinstance(analysis, dict):
        raise ValueError("作者分析尚未完成")
    if analysis.get("decision") not in {"accept", "partial"}:
        raise ValueError("未采纳的反馈不能升级为长期规则")
    candidate = _learning_candidate(analysis)
    _, project = _book(root, book_id)
    now = datetime.now().astimezone().isoformat()
    rule_id = _rule_id(str(feedback.get("category", "other")), candidate["principle"])
    record = {
        "id": rule_id,
        "scope": scope,
        "book_id": book_id if scope == "book" else None,
        "category": feedback.get("category", "other"),
        **candidate,
        "evidence_feedback_ids": [feedback_id],
        "evidence_count": 1,
        "confirmed_at": now,
        "updated_at": now,
    }

    with PROMOTION_LOCK:
        existing_promotion = read_json(folder / "promotion.json")
        if isinstance(existing_promotion, dict):
            if existing_promotion.get("scope") != scope:
                raise ValueError("这条经验已经按其他范围沉淀，不能重复改换范围")
            return {
                "item": feedback_item(root, book_id, feedback_id),
                "promotion": existing_promotion,
            }
        if scope == "book":
            registry_path, registry = _book_learning_registry(project)
            promoted = _upsert_learning(registry["rules"], record, feedback_id)
            _append_markdown_rule(
                project / "style_guide.md", f"feedback-learning:{rule_id}", promoted
            )
            atomic_json(registry_path, registry)
            destinations = [str(registry_path), str(project / "style_guide.md")]
        else:
            import author_registry
            from novel_engine_v2.engine import NovelEngine

            author = author_registry.book_author(root, book_id)
            author_path = (root / author["document_id"]).resolve()
            profile = author_registry.read_object(author_path)
            records = profile.setdefault("feedback_learning", [])
            if not isinstance(records, list):
                raise ValueError("作者档案 feedback_learning 必须为数组")
            promoted = _upsert_learning(records, record, feedback_id)
            field = {
                "confusing": "reader_contract",
                "character_voice": "language_principles",
                "ai_flavor": "language_principles",
            }.get(str(feedback.get("category")), "author_method")
            principles = profile.setdefault(field, [])
            rendered = _render_rule(promoted)
            if rendered not in principles:
                principles.append(rendered)
            author_registry.validate_author_profile(profile, author["id"])
            NovelEngine(root)._compile_author_context(profile)
            bound_projects = _author_projects(root, author["id"])
            for bound_project in bound_projects:
                if not (bound_project / "style_guide.md").is_file():
                    raise ValueError(f"缺少长期规则载体：{bound_project / 'style_guide.md'}")
            atomic_json(author_path, profile)
            destinations = [str(author_path)]
            for bound_project in bound_projects:
                style_path = bound_project / "style_guide.md"
                _append_markdown_rule(style_path, f"author-feedback-learning:{rule_id}", promoted)
                destinations.append(str(style_path))

        promotion = {
            "rule_id": rule_id,
            "scope": scope,
            "scope_label": PROMOTION_SCOPES[scope],
            "promoted_at": now,
            "destinations": destinations,
            "evidence_count": promoted["evidence_count"],
        }
        atomic_json(folder / "promotion.json", promotion)
    return {"item": feedback_item(root, book_id, feedback_id), "promotion": promotion}


def apply_revision(root: Path, book_id: str, feedback_id: str) -> dict:
    folder = _feedback_dir(root, book_id, feedback_id)
    feedback = read_json(folder / "feedback.json")
    analysis = read_json(folder / "analysis.json")
    if not isinstance(feedback, dict) or not isinstance(analysis, dict):
        raise ValueError("作者分析尚未完成")
    if analysis.get("decision") not in {"accept", "partial"}:
        raise ValueError("作者判断为不采纳，不能应用修订")
    revision_path = folder / "proposed_revision.md"
    if not revision_path.is_file():
        raise ValueError("没有可应用的候选修订")
    chapter_path = Path(str(feedback.get("chapter_file", ""))).resolve()
    _, project = _book(root, book_id)
    if project.resolve() not in chapter_path.parents:
        raise ValueError("章节路径越界")
    current = chapter_path.read_text(encoding="utf-8")
    current_hash = hashlib.sha256(current.encode("utf-8")).hexdigest()
    if current_hash != feedback.get("chapter_sha256"):
        raise ValueError("章节在分析期间已经变化，请重新提交反馈")
    revision = revision_path.read_text(encoding="utf-8").strip() + "\n"
    number = int(feedback["chapter"])
    first = revision.splitlines()[0] if revision else ""
    if not re.fullmatch(rf"#\s*第\s*{number}\s*章\s+.+", first):
        raise ValueError("候选修订的章节标题不匹配")
    current_count = len(_compact(_body_text(current)))
    revision_count = len(_compact(_body_text(revision)))
    if revision_count < 500:
        raise ValueError("候选修订正文过短")
    length_range = _chapter_length_range(project)
    if length_range and not length_range[0] <= revision_count <= length_range[1]:
        raise ValueError(
            f"候选修订正文为 {revision_count} 字，不符合本书 "
            f"{length_range[0]}—{length_range[1]} 字要求"
        )
    if not length_range and revision_count < int(current_count * 0.85):
        raise ValueError("候选修订删减超过原正文 15%，请改用整章重写")
    _validate_revision_scope(current, revision, str(feedback.get("quote", "")))
    revision = _refresh_word_count(revision, revision_count)
    backup = folder / "original_before_apply.md"
    if not backup.exists():
        atomic_text(backup, current)
    atomic_text(chapter_path, revision)
    invalidated = []
    stale_artifacts = (
        (project / "reader_checks" / f"{number:04d}.json", "reader_check_before_apply.json", "读者验收"),
        (project / "literary_reviews" / f"{number:04d}.json", "literary_review_before_apply.json", "文学终审"),
        (project / "module_reports" / f"{number:04d}.json", "module_report_before_apply.json", "创作能力报告"),
    )
    for artifact, backup_name, label in stale_artifacts:
        if not artifact.is_file():
            continue
        artifact_backup = folder / backup_name
        if not artifact_backup.exists():
            atomic_text(artifact_backup, artifact.read_text(encoding="utf-8"))
        artifact.unlink()
        invalidated.append(label)
    applied_at = datetime.now().astimezone().isoformat()
    current_hash = hashlib.sha256(revision.encode("utf-8")).hexdigest()
    receipt = {
        "chapter": number,
        "filename": chapter_path.name,
        "applied_at": applied_at,
        "word_count": revision_count,
        "previous_sha256": hashlib.sha256(current.encode("utf-8")).hexdigest(),
        "current_sha256": current_hash,
        "backup": str(backup.relative_to(project)),
        "invalidated_checks": invalidated,
        "message": "最新正式稿已更新，页面已切回新正文；旧版本已收入草稿箱。",
    }
    status = update_status(
        root,
        book_id,
        feedback_id,
        status="applied",
        message="最新正式稿已更新；旧版本已收入草稿箱，过期验收已撤销，重新验收前不得发布",
        applied_at=applied_at,
        receipt=receipt,
    )
    return {
        "item": feedback_item(root, book_id, feedback_id),
        "status": status,
        "receipt": receipt,
        **chapter_versions(root, book_id, number),
    }
