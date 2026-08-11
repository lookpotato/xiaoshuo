#!/usr/bin/env python3
"""Outline-blind reader-comprehension gate for archived novel chapters."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


SCHEMA_VERSION = 1
REVIEW_MODE = "outline-blind-first-reader"
REVIEWER = "codex-reader-gate"
REQUIRED_QUESTIONS = {
    "previous_context",
    "immediate_problem",
    "decision_basis",
    "action_logic",
    "result_and_cost",
    "reader_hook",
}


def chapter_narrative_text(path: Path) -> str:
    """Return stable reader-visible prose, excluding metadata and local images."""
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    title = re.match(r"^\s*#\s*第\s*\d+\s*章\s+.+?$", text, re.M)
    start = title.end() if title else 0
    metadata = re.search(r"\n---\s*\n+\s*##\s+Metadata\b", text, re.I)
    end = metadata.start() if metadata else len(text)
    body = text[start:end].strip()
    body = re.sub(
        r"(?m)^[ \t]*!\[[^\]\r\n]*\]\(\.\./images/[^)\r\n]+\)[ \t]*\r?\n?",
        "",
        body,
    )
    body = "\n".join(line.rstrip() for line in body.splitlines())
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def narrative_sha256(path: Path) -> str:
    return hashlib.sha256(chapter_narrative_text(path).encode("utf-8")).hexdigest()


def _valid_evidence(body: str, evidence: object) -> bool:
    return (
        isinstance(evidence, str)
        and len(evidence.strip()) >= 6
        and evidence.strip() in body
    )


def validate_reader_checks(project: Path, from_chapter: int) -> list[str]:
    """Validate every archived chapter at or above the configured rollout point."""
    errors: list[str] = []
    checks_dir = project / "reader_checks"
    if not checks_dir.is_dir():
        return ["缺少目录 reader_checks/"]

    chapter_files: dict[int, Path] = {}
    for path in (project / "chapters").glob("*.md"):
        match = re.match(r"^(\d+)-", path.name)
        if match and int(match.group(1)) >= from_chapter:
            chapter_files[int(match.group(1))] = path

    for number, chapter_path in sorted(chapter_files.items()):
        label = f"第 {number} 章无大纲读者验收"
        check_path = checks_dir / f"{number:04d}.json"
        if not check_path.is_file():
            errors.append(f"{label}缺失: reader_checks/{number:04d}.json")
            continue
        try:
            check = json.loads(check_path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"{label}无法解析: {exc}")
            continue
        if check.get("schema_version") != SCHEMA_VERSION:
            errors.append(f"{label} schema_version 必须为 {SCHEMA_VERSION}")
        if check.get("chapter_number") != number:
            errors.append(f"{label} chapter_number 不一致")
        if check.get("mode") != REVIEW_MODE or check.get("outline_blind") is not True:
            errors.append(f"{label}必须在不读取大纲的模式下完成")
        if check.get("reviewer") != REVIEWER:
            errors.append(f"{label} reviewer 必须为 {REVIEWER}")
        if check.get("status") != "passed":
            errors.append(f"{label}尚未通过")
        if not isinstance(check.get("reviewed_at"), str) or not check.get(
            "reviewed_at", ""
        ).strip():
            errors.append(f"{label} reviewed_at 不能为空")
        if not isinstance(check.get("revision_rounds"), int) or check.get(
            "revision_rounds", -1
        ) < 0:
            errors.append(f"{label} revision_rounds 必须是非负整数")

        actual_hash = narrative_sha256(chapter_path)
        if check.get("narrative_sha256") != actual_hash:
            errors.append(f"{label}正文哈希不一致，修改正文后必须重新验收")

        body = chapter_narrative_text(chapter_path)
        questions = check.get("questions")
        if not isinstance(questions, dict):
            errors.append(f"{label} questions 必须是对象")
            questions = {}
        missing_questions = sorted(REQUIRED_QUESTIONS - set(questions))
        extra_questions = sorted(set(questions) - REQUIRED_QUESTIONS)
        if missing_questions:
            errors.append(f"{label}缺少问题: {', '.join(missing_questions)}")
        if extra_questions:
            errors.append(f"{label}包含未知问题: {', '.join(extra_questions)}")
        for key in sorted(REQUIRED_QUESTIONS & set(questions)):
            answer = questions[key]
            question_label = f"{label}.{key}"
            if not isinstance(answer, dict):
                errors.append(f"{question_label} 必须是对象")
                continue
            if not isinstance(answer.get("answer"), str) or len(
                answer.get("answer", "").strip()
            ) < 8:
                errors.append(f"{question_label}.answer 不能是空泛短句")
            evidence = answer.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                errors.append(f"{question_label}.evidence 至少需要一条正文原句")
            elif not all(_valid_evidence(body, value) for value in evidence):
                errors.append(f"{question_label}.evidence 必须逐字存在于正文")

        evidence_positions: dict[str, int] = {}
        for key in REQUIRED_QUESTIONS:
            value = questions.get(key, {}) if isinstance(questions, dict) else {}
            evidence = value.get("evidence", []) if isinstance(value, dict) else []
            if isinstance(evidence, list) and evidence and _valid_evidence(body, evidence[0]):
                evidence_positions[key] = body.index(evidence[0].strip())
        opening_limit = min(len(body), 1200)
        for key in ("previous_context", "immediate_problem"):
            if evidence_positions.get(key, opening_limit + 1) > opening_limit:
                errors.append(f"{label}.{key} 的解释出现过晚，必须在开篇 1200 字内落地")
        ordered_keys = ("decision_basis", "action_logic", "result_and_cost")
        if all(key in evidence_positions for key in ordered_keys) and not (
            evidence_positions["decision_basis"]
            <= evidence_positions["action_logic"]
            <= evidence_positions["result_and_cost"]
        ):
            errors.append(f"{label}因果证据顺序错误，必须先依据、再行动原理、后结果代价")
        if "reader_hook" in evidence_positions and evidence_positions["reader_hook"] < int(
            len(body) * 0.65
        ):
            errors.append(f"{label}.reader_hook 证据必须来自正文后 35%")

        new_terms = check.get("new_terms")
        if not isinstance(new_terms, list):
            errors.append(f"{label} new_terms 必须是数组")
        else:
            seen_terms: set[str] = set()
            for index, term in enumerate(new_terms):
                term_label = f"{label}.new_terms[{index}]"
                if not isinstance(term, dict):
                    errors.append(f"{term_label} 必须是对象")
                    continue
                name = term.get("term")
                if not isinstance(name, str) or not name.strip():
                    errors.append(f"{term_label}.term 不能为空")
                elif name.strip() in seen_terms:
                    errors.append(f"{term_label}.term 重复")
                else:
                    seen_terms.add(name.strip())
                if not isinstance(term.get("plain_explanation"), str) or len(
                    term.get("plain_explanation", "").strip()
                ) < 8:
                    errors.append(f"{term_label}.plain_explanation 不能是空泛短句")
                if not _valid_evidence(body, term.get("evidence")):
                    errors.append(f"{term_label}.evidence 必须逐字存在于正文")
                elif isinstance(name, str) and name.strip():
                    clean_name = name.strip()
                    evidence_text = term["evidence"].strip()
                    if clean_name not in body:
                        errors.append(f"{term_label}.term 未出现在正文")
                    else:
                        first_use = body.index(clean_name)
                        explanation_at = body.index(evidence_text)
                        if (
                            explanation_at + len(evidence_text) < first_use
                            or explanation_at > first_use + 300
                        ):
                            errors.append(
                                f"{term_label} 必须在首次出现后 300 字内完成白话解释"
                            )
        unexplained = check.get("unexplained_terms")
        if unexplained != []:
            errors.append(f"{label}仍有未解释名词，必须修文后清零")

    for check_path in checks_dir.glob("[0-9][0-9][0-9][0-9].json"):
        number = int(check_path.stem)
        if number >= from_chapter and number not in chapter_files:
            errors.append(f"reader_checks/{check_path.name} 没有对应归档章节")
    return errors
