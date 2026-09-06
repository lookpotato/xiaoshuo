"""Per-book creative capabilities: configuration, prompt assembly and evidence gates."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = "creative_modules.json"
DIMENSIONS = ("空间", "社会秩序", "普通人生活", "主角位置", "特殊规则")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def catalog():
    return validate_catalog(read_json(ROOT / "shared" / CONFIG))


def validate_catalog(data):
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("modules"), dict):
        raise ValueError("创作模块库格式错误")
    for key, value in data["modules"].items():
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) or not isinstance(value, dict):
            raise ValueError("创作模块 ID 或定义无效")
        for field in ("title", "purpose", "input", "plan", "review", "limits"):
            if not isinstance(value.get(field), str) or not value[field].strip():
                raise ValueError(f"{key} 缺少 {field}")
        guide = value.get("guide")
        if guide is not None:
            if not isinstance(guide, dict) or not isinstance(guide.get("steps"), list) or not all(_text(step) for step in guide["steps"]):
                raise ValueError(f"{key} 操作步骤无效")
            checks = guide.get("checks")
            if not isinstance(checks, list) or not checks:
                raise ValueError(f"{key} 缺少具体检查题")
            seen = set()
            for check in checks:
                if not isinstance(check, dict) or not isinstance(check.get("id"), str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", check["id"]) or not _text(check.get("question")) or check["id"] in seen:
                    raise ValueError(f"{key} 检查题 ID 或内容无效")
                seen.add(check["id"])
    return data


def validate_config(data, library=None):
    library = library or catalog()
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("modules"), dict):
        raise ValueError("单书创作模块配置格式错误")
    if type(data.get("report_contract", 1)) is not int or data.get("report_contract", 1) not in (1, 2):
        raise ValueError("report_contract 必须为 1 或 2")
    for key, value in data["modules"].items():
        if key not in library["modules"] or not isinstance(value, dict):
            raise ValueError(f"未知创作模块：{key}")
        if value.get("mode") not in ("off", "review", "enforce"):
            raise ValueError(f"{key} mode 必须为 off/review/enforce")
        for field in ("from_chapter", "review_interval"):
            number = value.get(field, 1 if field == "from_chapter" else 5)
            if type(number) is not int or not 1 <= number <= 100000:
                raise ValueError(f"{key} {field} 必须为正整数")
        if not isinstance(value.get("focus", ""), str):
            raise ValueError(f"{key} focus 必须为文本")
    return data


def load_config(project):
    path = Path(project) / CONFIG
    return validate_config(read_json(path)) if path.exists() else {"schema_version": 1, "modules": {}}


def active_modules(config, number):
    return {key: value for key, value in config["modules"].items()
            if value["mode"] != "off" and number >= value.get("from_chapter", 1)}


def chapter_files(project):
    result = {}
    for path in sorted((Path(project) / "chapters").glob("*.md")):
        match = re.match(r"^(\d+)-", path.name)
        if match:
            number = int(match[1])
            if number in result:
                raise ValueError(f"第 {number} 章存在多个正文文件")
            result[number] = path
    return result


def prompt(project, number=None):
    project = Path(project)
    config = load_config(project)
    if not any(value["mode"] != "off" for value in config["modules"].values()):
        return ""
    if number is None:
        number = read_json(project / "chapter_state.json").get("next_chapter_number", 1)
    active = active_modules(config, number)
    if not active:
        return ""
    definitions = catalog()["modules"]
    selected = {key: {**definitions[key], **value} for key, value in active.items()}
    return (f"\n创作能力装配：目标目录 {project}，第 {number} 章。\n"
            f"报告协议 report_contract={config.get('report_contract', 1)}；协议2须逐项回答所选模块 guide.checks，不能用一句总评代替。\n"
            "完整读取 shared/creative_modules_workflow.md，并执行其中的规划、写作、证据验收和跨章状态流程。\n"
            "允许更新本书 module_plans/、module_reports/ 配套文件；不得将报告或计划上传为小说。\n"
            "模块与现有规范共同生效：事实连续性、视角和读者理解优先，再协调悬念、情绪与节奏；冲突写入计划。\n"
            + json.dumps(selected, ensure_ascii=False, indent=2))


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def activity(project):
    """Read bounded report metadata, never equate self-reported passes with quality."""
    result = {}
    candidates = sorted(Path(project).glob("module_reports/[0-9][0-9][0-9][0-9].json"), reverse=True)[:50]
    for path in candidates:
        try:
            report = read_json(path)
            if not isinstance(report, dict) or not isinstance(report.get("modules"), dict):
                continue
            for key, item in report["modules"].items():
                if key not in result and isinstance(item, dict):
                    result[key] = {"chapter": int(path.stem), "status": str(item.get("status", "unknown")),
                                   "assessment": str(item.get("assessment", ""))[:600],
                                   "review_method": str(report.get("review_method", "unknown"))}
        except (OSError, ValueError, TypeError):
            continue
    return result


def validate_reports(project, only_number=None):
    """Validate provenance mechanically; literary judgements remain AI/human reviews."""
    from novel_reader_gate import chapter_narrative_text, narrative_sha256
    project = Path(project)
    errors = []
    try:
        config = load_config(project)
        if not any(v["mode"] != "off" for v in config["modules"].values()):
            return []
        files = chapter_files(project)
        definitions = catalog()["modules"]
        bodies = {}

        def evidence_valid(ref, current):
            if not isinstance(ref, dict):
                return False
            n = ref.get("chapter")
            if type(n) is not int or n > current or n not in files:
                return False
            if n not in bodies:
                bodies[n] = chapter_narrative_text(files[n])
            quote = ref.get("quote")
            return (_text(quote) and len(quote.strip()) >= 6 and quote in bodies[n]
                    and ref.get("narrative_sha256") == narrative_sha256(files[n]))

        for number, path in files.items():
            if only_number is not None and number != only_number:
                continue
            active = active_modules(config, number)
            if not active:
                continue
            label = f"第 {number} 章创作模块"
            report_path = project / "module_reports" / f"{number:04d}.json"
            plan_path = project / "module_plans" / f"{number:04d}.json"
            try:
                report, plan = read_json(report_path), read_json(plan_path)
                if not isinstance(report, dict) or not isinstance(plan, dict):
                    raise ValueError("报告与计划必须为对象")
                if report.get("chapter_number") != number or report.get("schema_version") != 1:
                    raise ValueError("报告章节号或版本错误")
                if report.get("review_method") not in ("same-context-evidence-review", "independent-context-review"):
                    raise ValueError("须如实记录 review_method")
                if plan.get("chapter_number") != number or not isinstance(plan.get("modules"), dict):
                    raise ValueError("计划章节号或模块字段错误")
                if report.get("narrative_sha256") != narrative_sha256(path):
                    raise ValueError("正文变更，需重新验收")
                if report.get("plan_sha256") != hashlib.sha256(plan_path.read_bytes()).hexdigest():
                    raise ValueError("计划变更，需重新验收")
                reviews = report.get("modules")
                if not isinstance(reviews, dict):
                    raise ValueError("缺少模块验收")
                for key, options in active.items():
                    item = reviews.get(key)
                    if not _text(plan["modules"].get(key)) or not isinstance(item, dict):
                        raise ValueError(f"{key} 缺少规划或验收")
                    if item.get("status") not in ("passed", "needs_revision", "not_applicable") or not _text(item.get("assessment")):
                        raise ValueError(f"{key} 缺少有效结论与分析")
                    if not isinstance(item.get("state"), dict) or not isinstance(item.get("issues"), list):
                        raise ValueError(f"{key} 缺少状态或问题列表")
                    refs = item.get("evidence")
                    if not isinstance(refs, list) or not all(evidence_valid(ref, number) for ref in refs):
                        raise ValueError(f"{key} 证据不存在、越界或已过期")
                    if item["status"] != "not_applicable" and not refs:
                        raise ValueError(f"{key} 必须提供正文证据")
                    if config.get("report_contract", 1) == 2:
                        checks = item.get("checks")
                        if not isinstance(checks, dict):
                            raise ValueError(f"{key} 缺少逐项 checks")
                        for definition in definitions[key].get("guide", {}).get("checks", []):
                            answer = checks.get(definition["id"])
                            if not isinstance(answer, dict) or answer.get("verdict") not in ("met", "gap", "not_applicable") or not _text(answer.get("answer")):
                                raise ValueError(f"{key}/{definition['id']} 缺少具体回答")
                            evidence = answer.get("evidence")
                            if not isinstance(evidence, list) or not all(evidence_valid(ref, number) for ref in evidence):
                                raise ValueError(f"{key}/{definition['id']} 证据无效")
                            if answer["verdict"] == "met" and (not evidence or not _text(answer.get("support"))):
                                raise ValueError(f"{key}/{definition['id']} 须解释原句怎样支持回答")
                            if answer["verdict"] == "gap" and (not _text(answer.get("fix")) or item["status"] != "needs_revision"):
                                raise ValueError(f"{key}/{definition['id']} 有缺口须给出修订位置与动作并标记 needs_revision")
                    if key == "world_presentation":
                        if item["status"] == "not_applicable":
                            raise ValueError("世界观呈现需逐章维护认知，不可跳过")
                        knowledge = item["state"].get("reader_knowledge")
                        if not isinstance(knowledge, list):
                            raise ValueError("缺少累计 reader_knowledge")
                        dimensions = set()
                        for fact in knowledge:
                            if not isinstance(fact, dict) or fact.get("dimension") not in DIMENSIONS or not _text(fact.get("summary")):
                                raise ValueError("读者认知维度或摘要错误")
                            dimensions.add(fact["dimension"])
                            if fact.get("status") not in ("known", "hinted", "unknown"):
                                raise ValueError("认知状态错误")
                            evidence = fact.get("evidence", [])
                            if not isinstance(evidence, list) or not all(evidence_valid(ref, number) for ref in evidence):
                                raise ValueError("累计认知证据错误或正文已修订")
                            if fact["status"] != "unknown" and not evidence:
                                raise ValueError("已知或线索必须有正文证据")
                            if config.get("report_contract", 1) == 2:
                                if fact.get("support_kind") not in ("explicit", "inferred", "missing"):
                                    raise ValueError("认知须区分明示、推断与缺失")
                                if fact["status"] == "known" and fact["support_kind"] != "explicit":
                                    raise ValueError("推断不能登记为读者已知")
                                if type(fact.get("required_now")) is not bool:
                                    raise ValueError("认知须说明是否为本章必需信息")
                                if fact["status"] != "known" and fact["required_now"] and item["status"] != "needs_revision":
                                    raise ValueError("本章必需认知尚未明确，须标记 needs_revision")
                            if fact["status"] == "unknown":
                                due = fact.get("resolve_by")
                                if not _text(fact.get("next_action")) or type(due) is not int or due < number:
                                    raise ValueError("未知认知须有未逾期补足计划")
                        if dimensions != set(DIMENSIONS):
                            raise ValueError("读者认知须覆盖五个维度，缺失项标记 unknown")
                        interval = options.get("review_interval", 5)
                        if (number - options.get("from_chapter", 1)) % interval == 0 and not _text(item.get("cross_chapter_review")):
                            raise ValueError("到期须完成跨章世界认知复盘")
                    if options["mode"] == "enforce" and (item["status"] == "needs_revision" or item["issues"]):
                        raise ValueError(f"{key} 存在待修订问题")
            except (OSError, ValueError, TypeError, KeyError) as exc:
                errors.append(f"{label}: {exc}")
    except (OSError, ValueError, TypeError) as exc:
        errors.append(f"创作模块配置: {exc}")
    return errors


def main():
    parser = argparse.ArgumentParser(description="创作模块装配与证据验收")
    parser.add_argument("command", choices=("catalog", "prompt", "validate"))
    parser.add_argument("project", nargs="?", default=".")
    parser.add_argument("--chapter", type=int)
    args = parser.parse_args()
    if args.command == "catalog":
        print(json.dumps(catalog(), ensure_ascii=False, indent=2))
    elif args.command == "prompt":
        print(prompt(Path(args.project), args.chapter))
    else:
        errors = validate_reports(Path(args.project), args.chapter)
        print(json.dumps({"errors": errors}, ensure_ascii=False, indent=2))
        return int(bool(errors))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
