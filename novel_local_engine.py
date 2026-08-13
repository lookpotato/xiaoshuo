"""本地小说预处理引擎：用 CPU 完成上下文裁剪、缓存与机械质检。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT_FILES = (
    "novel_config.md",
    "outline.md",
    "characters.md",
    "world.md",
    "style_guide.md",
    "continuity_ledger.md",
    "chapter_state.json",
)
CHAPTER_RE = re.compile(r"^(\d{4})-(.+)\.md$")
META_RE = re.compile(r"\n---\s*\n\s*## Metadata\b", re.S)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def narrative(text: str) -> str:
    return META_RE.split(text, maxsplit=1)[0].strip()


@dataclass
class LocalCheck:
    chapter: int
    path: str
    characters: int
    paragraphs: int
    issues: list[str]


def chapter_files(book: Path) -> list[tuple[int, Path]]:
    found: list[tuple[int, Path]] = []
    for path in (book / "chapters").glob("*.md"):
        match = CHAPTER_RE.match(path.name)
        if match:
            found.append((int(match.group(1)), path))
    return sorted(found)


def local_check(path: Path) -> LocalCheck:
    text = path.read_text(encoding="utf-8")
    body = narrative(text)
    number = int(path.name[:4])
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    issues: list[str] = []
    if not text.startswith(f"# 第{number}章 "):
        issues.append("标题章号与文件名不一致")
    if "## Metadata" not in text:
        issues.append("缺少 Metadata")
    if len(body) < 2200:
        issues.append("正文少于 2200 字符")
    if any(token in body for token in ("TODO", "待补", "提示词", "作为AI", "作为 AI")):
        issues.append("正文含占位或元创作词")
    normalized = [re.sub(r"\s+", "", p) for p in paragraphs if len(p) >= 20]
    if len(normalized) != len(set(normalized)):
        issues.append("存在完全重复段落")
    return LocalCheck(number, str(path), len(body), len(paragraphs), issues)


def build_bundle(book: Path, recent: int = 3) -> dict:
    files = chapter_files(book)
    selected = files[-max(1, recent):]
    sources: list[dict] = []
    for name in ROOT_FILES:
        path = book / name
        if path.exists():
            text = path.read_text(encoding="utf-8")
            sources.append({"path": name, "sha256": sha256_text(text), "text": text})
    for number, path in selected:
        text = path.read_text(encoding="utf-8")
        sources.append({
            "path": str(path.relative_to(book)),
            "chapter": number,
            "sha256": sha256_text(text),
            "text": text,
        })
    fingerprint = sha256_text("\n".join(item["sha256"] for item in sources))
    return {
        "schema_version": 1,
        "book": book.name,
        "recent_chapters": [number for number, _ in selected],
        "fingerprint": fingerprint,
        "sources": sources,
    }


def prepare(book: Path, recent: int = 3) -> tuple[Path, bool]:
    bundle = build_bundle(book, recent)
    cache_dir = book / ".local_cache"
    cache_dir.mkdir(exist_ok=True)
    current = cache_dir / "context_bundle.json"
    reused = False
    if current.exists():
        try:
            reused = json.loads(current.read_text(encoding="utf-8")).get("fingerprint") == bundle["fingerprint"]
        except (OSError, json.JSONDecodeError):
            pass
    if not reused:
        current.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checks = [asdict(local_check(path)) for _, path in chapter_files(book)]
    (cache_dir / "local_checks.json").write_text(
        json.dumps({"schema_version": 1, "checks": checks}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return current, reused


def main() -> int:
    parser = argparse.ArgumentParser(description="本地生成小说上下文缓存并执行零模型机械质检")
    parser.add_argument("book", type=Path)
    parser.add_argument("--recent", type=int, default=3)
    args = parser.parse_args()
    cache, reused = prepare(args.book.resolve(), args.recent)
    print(json.dumps({"cache": str(cache), "reused": reused}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
