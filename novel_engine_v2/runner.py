"""Run each creative stage in a fresh Codex context."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .engine import NovelEngine, ValidationError


def resolve_codex() -> str:
    path = shutil.which("codex")
    if not path:
        raise ValidationError("找不到 codex CLI；请先安装并登录")
    return path


def execute_prompt(root: Path, prompt_path: Path, result_path: Path) -> None:
    command = [
        resolve_codex(), "exec", "--ephemeral", "-C", str(root),
        "--sandbox", "workspace-write", "--config", 'approval_policy="never"',
        "--output-last-message", str(result_path), "-",
    ]
    result = subprocess.run(
        command,
        cwd=root,
        input=prompt_path.read_text(encoding="utf-8"),
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        raise ValidationError(f"阶段执行失败：{prompt_path.name}，退出码 {result.returncode}")


def revision_prompt(run_dir: Path, attempt: int) -> Path:
    path = run_dir / f"revision-{attempt}.md"
    path.write_text(
        "# 作者返修阶段\n\n"
        f"读取 `{run_dir / 'manifest.json'}`、`{run_dir / 'chapter_contract.json'}`、"
        f"`{run_dir / 'candidate.md'}`、`{run_dir / 'reader_review.json'}`，"
        "再读取 manifest 中明确列出的 author_profile。\n"
        "只处理 blocking_issues。保留已经成立的情节、人物选择和语言特点；"
        "nonblocking_notes 不得触发重写。修改 candidate.md 后同步修正 state_delta.json。"
        "不要读取旧版共享提示、设定大全或跨书经验，不归档、不发布。\n",
        encoding="utf-8",
    )
    return path


def run_book(
    engine: NovelEngine,
    book_id: str,
    signals: set[str],
    max_revisions: int = 2,
) -> Path:
    run_dir = engine.prepare(book_id, signals)
    execute_prompt(engine.root, run_dir / "director.md", run_dir / "director.result.md")
    engine.validate_contract(run_dir)
    execute_prompt(engine.root, run_dir / "writer.md", run_dir / "writer.result.md")
    number = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))["chapter"]
    engine.validate_candidate(run_dir, number)
    for attempt in range(max_revisions + 1):
        execute_prompt(engine.root, run_dir / "reader.md", run_dir / f"reader-{attempt}.result.md")
        review = engine.validate_review(run_dir)
        if review["decision"] == "pass":
            return engine.finalize(book_id, run_dir)
        if attempt == max_revisions:
            raise ValidationError(
                f"陌生读者连续 {max_revisions + 1} 次未通过；候选稿保留在 {run_dir}"
            )
        prompt = revision_prompt(run_dir, attempt + 1)
        execute_prompt(engine.root, prompt, run_dir / f"revision-{attempt + 1}.result.md")
        engine.validate_candidate(run_dir, number)
    raise AssertionError("unreachable")
