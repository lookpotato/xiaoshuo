"""Small-context orchestration for author-led serial fiction production."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


class ValidationError(ValueError):
    pass


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"无法读取配置 {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"配置必须是 JSON 对象：{path}")
    return value


def _require_text(data: dict, key: str, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} 缺少 {key}")
    return value.strip()


@dataclass(frozen=True)
class Book:
    id: str
    title: str
    project: Path
    author: str


class NovelEngine:
    """Compile bounded, stage-specific contexts instead of one giant prompt."""

    STAGES = ("director", "writer", "reader", "finalizer")

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.home = self.root / "novel_engine_v2"
        self.config = read_json(self.home / "system.json")
        self.catalog = read_json(self.home / "modules.json")
        self._validate()

    def _validate(self) -> None:
        if self.config.get("schema_version") != 2:
            raise ValidationError("system.json schema_version 必须为 2")
        if self.catalog.get("schema_version") != 2:
            raise ValidationError("modules.json schema_version 必须为 2")
        if not isinstance(self.config.get("books"), dict):
            raise ValidationError("system.json 缺少 books")
        limits = self.config.get("limits", {})
        for key in (
            "max_writer_modules", "max_reader_modules", "recent_chapters",
            "max_author_context_chars",
        ):
            if type(limits.get(key)) is not int or limits[key] < 1:
                raise ValidationError(f"limits.{key} 必须为正整数")
        modules = self.catalog.get("modules")
        if not isinstance(modules, dict):
            raise ValidationError("modules.json 缺少 modules")
        for module_id, module in modules.items():
            if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", module_id):
                raise ValidationError(f"非法模块 ID：{module_id}")
            if module.get("stage") not in ("writer", "reader"):
                raise ValidationError(f"{module_id}.stage 无效")
            for key in ("title", "purpose", "instruction"):
                _require_text(module, key, module_id)

    def book(self, book_id: str) -> Book:
        raw = self.config["books"].get(book_id)
        if not isinstance(raw, dict):
            raise ValidationError(f"未知作品：{book_id}")
        project = (self.root / _require_text(raw, "project", book_id)).resolve()
        if self.root != project and self.root not in project.parents:
            raise ValidationError(f"作品目录越界：{project}")
        return Book(
            id=book_id,
            title=_require_text(raw, "title", book_id),
            project=project,
            author=_require_text(raw, "author", book_id),
        )

    def author(self, author_id: str) -> dict:
        path = self.home / "authors" / f"{author_id}.json"
        data = read_json(path)
        if data.get("schema_version") != 1 or data.get("id") != author_id:
            raise ValidationError(f"作者配置无效：{path}")
        for key in ("creative_identity", "reader_contract", "language_principles"):
            if not isinstance(data.get(key), list) or not all(
                isinstance(item, str) and item.strip() for item in data[key]
            ):
                raise ValidationError(f"作者配置 {key} 必须为非空文本数组")
        for key in ("author_method", "book_application"):
            if key in data and (
                not isinstance(data[key], list)
                or not data[key]
                or not all(isinstance(item, str) and item.strip() for item in data[key])
            ):
                raise ValidationError(f"作者配置 {key} 必须为非空文本数组")
        self._compile_author_context(data)
        return data

    def _compile_author_context(self, author: dict) -> str:
        """Render one bounded author hierarchy instead of a flat prompt pile."""
        sections = (
            ("作者身份与最高取舍", author["creative_identity"]),
            ("创作方法", author.get("author_method", [])),
            ("本书应用", author.get("book_application", [])),
            ("读者承诺", author["reader_contract"]),
            ("语言边界", author["language_principles"]),
        )
        rendered = "\n\n".join(
            f"### {title}\n" + "\n".join(f"- {item}" for item in items)
            for title, items in sections if items
        )
        limit = self.config["limits"]["max_author_context_chars"]
        if len(rendered) > limit:
            raise ValidationError(
                f"作者上下文超过 {limit} 字；请合并取舍，不得继续堆提示词"
            )
        return rendered

    def next_chapter(self, book: Book) -> int:
        state = read_json(book.project / "chapter_state.json")
        number = state.get("next_chapter_number")
        if type(number) is not int or number < 1:
            raise ValidationError("chapter_state.json 缺少有效 next_chapter_number")
        return number

    def recent_chapters(self, book: Book, number: int) -> list[Path]:
        found: list[tuple[int, Path]] = []
        for path in (book.project / "chapters").glob("*.md"):
            match = re.match(r"^(\d+)-", path.name)
            if match and int(match.group(1)) < number:
                found.append((int(match.group(1)), path.resolve()))
        count = self.config["limits"]["recent_chapters"]
        return [path for _, path in sorted(found, reverse=True)[:count]][::-1]

    def select_modules(self, stage: str, signals: set[str]) -> list[dict]:
        if stage not in ("writer", "reader"):
            return []
        candidates = []
        for module_id, raw in self.catalog["modules"].items():
            if raw["stage"] != stage:
                continue
            triggers = set(raw.get("triggers", []))
            mandatory = bool(raw.get("mandatory"))
            score = int(raw.get("priority", 0)) + len(triggers & signals) * 100
            if mandatory or triggers & signals:
                candidates.append((not mandatory, -score, module_id, raw))
        limit = self.config["limits"][f"max_{stage}_modules"]
        selected = sorted(candidates)[:limit]
        return [{"id": module_id, **raw} for _, _, module_id, raw in selected]

    def context_manifest(self, book: Book, number: int) -> dict:
        existing = []
        for name in self.config["legacy_adapter"]["book_sources"]:
            path = (book.project / name).resolve()
            if path.is_file():
                existing.append(str(path))
        planning_sources = self.config["books"][book.id].get("planning_sources", [])
        if not isinstance(planning_sources, list) or not all(
            isinstance(name, str) and name.strip() for name in planning_sources
        ):
            raise ValidationError(f"{book.id}.planning_sources 必须为文本数组")
        for name in planning_sources:
            path = (self.root / name).resolve()
            if self.root != path and self.root not in path.parents:
                raise ValidationError(f"规划资料越界：{path}")
            if not path.is_file():
                raise ValidationError(f"缺少规划资料：{path}")
            existing.append(str(path))
        return {
            "book": book.id,
            "chapter": number,
            "author_profile": str(
                (self.home / "authors" / f"{book.author}.json").resolve()
            ),
            "book_sources": existing,
            "recent_chapters": [str(path) for path in self.recent_chapters(book, number)],
        }

    def prepare(self, book_id: str, signals: set[str] | None = None) -> Path:
        book = self.book(book_id)
        author = self.author(book.author)
        number = self.next_chapter(book)
        signals = set(signals or ())
        if author.get("default_signals"):
            signals.update(author["default_signals"])
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = self.root / ".novel_runs_v2" / book.id / f"{number:04d}-{run_id}"
        run_dir.mkdir(parents=True, exist_ok=False)
        manifest = self.context_manifest(book, number)
        manifest.update({
            "run_id": run_id,
            "run_dir": str(run_dir.resolve()),
            "signals": sorted(signals),
            "writer_modules": self.select_modules("writer", signals),
            "reader_modules": self.select_modules("reader", signals),
            "stage_order": list(self.STAGES),
        })
        (run_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        prompts = self._compile_prompts(book, author, manifest)
        for stage, content in prompts.items():
            (run_dir / f"{stage}.md").write_text(content, encoding="utf-8")
        return run_dir

    def _compile_prompts(self, book: Book, author: dict, manifest: dict) -> dict[str, str]:
        run = Path(manifest["run_dir"])
        author_text = self._compile_author_context(author)
        sources = "\n".join(f"- `{path}`" for path in manifest["book_sources"])
        recent = "\n".join(f"- `{path}`" for path in manifest["recent_chapters"])
        writer_modules = "\n".join(
            f"### {m['title']}\n{m['instruction']}" for m in manifest["writer_modules"]
        ) or "本章没有额外写作模块。"
        reader_modules = "\n".join(
            f"### {m['title']}\n{m['instruction']}" for m in manifest["reader_modules"]
        ) or "只执行基础陌生读者检查。"
        director = f"""# 导演阶段：只定本章，不写正文

作品：{book.title}；目标章节：{manifest['chapter']}。

作者长期取舍：
{author_text}

按需读取作品资料：
{sources}

最近正文：
{recent}

输出两个文件：

1. `{run / 'chapter_contract.json'}`：必须使用精确字段 continuation、single_mission、protagonist_want、opposition、choice、visible_result、next_reason、cast、required_context、protected_unknowns。cast、required_context、protected_unknowns 为非空数组，其余为非空文本。
2. `{run / 'writer_context.md'}`：只摘录本章出场人物的既有事实、关系、当前状态，以及本章行动确实依赖的世界规则。每条注明来自哪个作品文件，总长度不得超过 6000 字；不得加入建议、文风要求或审稿规则。

每项必须具体；不要写正文，不要做文风检查，不要调用发布流程。
"""
        writer = f"""# 作者阶段：只写候选正文与状态增量

你不是通用写作助手。你必须按作者长期取舍写作：
{author_text}

只读取 `{run / 'chapter_contract.json'}`、`{run / 'writer_context.md'}`，以及以下最近正文：
{recent}

本次最多装配这些写作模块：
{writer_modules}

写作时不要读取 reader.md、旧版共享门禁或跨书经验文件。不要边写边自评。完成后保存 `{run / 'candidate.md'}`，格式为 `# 第 {manifest['chapter']} 章 标题` 加正文；另存 `{run / 'state_delta.json'}`，使用精确字段 characters、resources、revealed_facts、unresolved、next_opening，前四项为数组，next_opening 为非空文本，只记录正文实际发生的变化。不得更新作品状态，不得归档或发布。
"""
        reader = f"""# 陌生读者阶段：只读候选稿

你没有作者设定知识。只读取 `{run / 'candidate.md'}` 和 `{run / 'chapter_contract.json'}`；合同只能用于核对承诺，不能替正文补充信息。

检查模块：
{reader_modules}

把结果写入 `{run / 'reader_review.json'}`，使用精确字段 decision、blocking_issues、nonblocking_notes、evidence；decision 只能是 pass 或 revise，其他三项必须为数组。只有读者无法确认人物、对象、眼前因果、关键选择或结果时才阻塞；措辞偏好只能提醒。不得修改正文。
"""
        finalizer = f"""# 系统归档说明：不创作

归档由 V2 程序确定性执行。它只在 reader_review decision=pass、候选稿章号正确且 state_delta 结构有效时复制正文、保存连续性增量并推进 chapter_state。此阶段不得润色、扩写、发布、生成图片或运行 Git。
"""
        return {"director": director, "writer": writer, "reader": reader, "finalizer": finalizer}

    def validate_project(self, book_id: str) -> list[str]:
        errors = []
        try:
            book = self.book(book_id)
            self.author(book.author)
            self.next_chapter(book)
            for name in ("chapters", "drafts", "logs"):
                if not (book.project / name).is_dir():
                    errors.append(f"缺少目录：{book.project / name}")
            if (
                not self.recent_chapters(book, self.next_chapter(book))
                and not (book.project / "opening_contract.md").is_file()
            ):
                errors.append("没有可供承接的历史章节；新书需先提供开篇合同")
        except ValidationError as exc:
            errors.append(str(exc))
        return errors

    def validate_contract(self, run_dir: Path) -> dict:
        data = read_json(run_dir / "chapter_contract.json")
        required = (
            "continuation", "single_mission", "protagonist_want", "opposition",
            "choice", "visible_result", "next_reason", "cast",
            "required_context", "protected_unknowns",
        )
        for key in required:
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                continue
            if isinstance(value, list) and value:
                continue
            raise ValidationError(f"章节合同缺少具体字段：{key}")
        context_path = run_dir / "writer_context.md"
        try:
            context = context_path.read_text(encoding="utf-8-sig").strip()
        except OSError as exc:
            raise ValidationError(f"缺少受控作者上下文：{exc}") from exc
        if not context:
            raise ValidationError("writer_context.md 不能为空")
        if len(context) > 6000:
            raise ValidationError("writer_context.md 超过 6000 字限制")
        return data

    def validate_candidate(self, run_dir: Path, number: int) -> tuple[str, str]:
        path = run_dir / "candidate.md"
        try:
            text = path.read_text(encoding="utf-8-sig").strip()
        except OSError as exc:
            raise ValidationError(f"候选正文不存在：{exc}") from exc
        lines = text.splitlines()
        match = re.fullmatch(rf"#\s*第\s*{number}\s*章\s+(.+)", lines[0] if lines else "")
        if not match:
            raise ValidationError(f"候选正文标题必须是第 {number} 章")
        body = "\n".join(lines[1:]).strip()
        if len(re.sub(r"\s+", "", body)) < 800:
            raise ValidationError("候选正文不足 800 字，不能进入读者阶段")
        return match.group(1).strip(), text

    def validate_review(self, run_dir: Path) -> dict:
        data = read_json(run_dir / "reader_review.json")
        if data.get("decision") not in ("pass", "revise"):
            raise ValidationError("reader_review.decision 必须为 pass 或 revise")
        for key in ("blocking_issues", "nonblocking_notes", "evidence"):
            if not isinstance(data.get(key), list):
                raise ValidationError(f"reader_review.{key} 必须为数组")
        if data["decision"] == "pass" and data["blocking_issues"]:
            raise ValidationError("读者结论为 pass 时不能存在 blocking_issues")
        if data["decision"] == "revise" and not data["blocking_issues"]:
            raise ValidationError("读者结论为 revise 时必须给出 blocking_issues")
        return data

    def finalize(self, book_id: str, run_dir: Path) -> Path:
        book = self.book(book_id)
        number = self.next_chapter(book)
        title, text = self.validate_candidate(run_dir, number)
        review = self.validate_review(run_dir)
        if review["decision"] != "pass":
            raise ValidationError("陌生读者尚未通过，不能归档")
        delta = read_json(run_dir / "state_delta.json")
        for key in ("characters", "resources", "revealed_facts", "unresolved"):
            if not isinstance(delta.get(key), list):
                raise ValidationError(f"state_delta.{key} 必须为数组")
        if not isinstance(delta.get("next_opening"), str) or not delta["next_opening"].strip():
            raise ValidationError("state_delta.next_opening 必须为非空文本")
        safe_title = re.sub(r'[<>:"/\\|?*]', "", title).strip().rstrip(".")
        if not safe_title:
            raise ValidationError("章节标题清理后为空")
        chapter_path = book.project / "chapters" / f"{number:04d}-{safe_title}.md"
        if chapter_path.exists() or list((book.project / "chapters").glob(f"{number:04d}-*.md")):
            raise ValidationError(f"第 {number} 章已经存在，拒绝覆盖")
        draft_path = book.project / "drafts" / f"{datetime.now():%Y-%m-%d}-chapter-{number:04d}.md"
        continuity_dir = book.project / "continuity_v2"
        continuity_dir.mkdir(exist_ok=True)
        draft_path.write_text(text + "\n", encoding="utf-8")
        chapter_path.write_text(text + "\n", encoding="utf-8")
        (continuity_dir / f"{number:04d}.json").write_text(
            json.dumps(delta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        state_path = book.project / "chapter_state.json"
        state = read_json(state_path)
        state.update({
            "last_completed_chapter": number,
            "next_chapter_number": number + 1,
            "notes_for_next_chapter": str(delta["next_opening"]),
            "last_uploaded_status": "not_uploaded",
        })
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log_path = book.project / "logs" / f"{datetime.now():%Y-%m-%d}-v2-{number:04d}.md"
        log_path.write_text(
            f"# V2 第 {number} 章\n\n- run: `{run_dir}`\n- result: archived\n- reader: pass\n- chapter: `{chapter_path.name}`\n",
            encoding="utf-8",
        )
        return chapter_path
