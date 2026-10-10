#!/usr/bin/env python3
"""V3 author-room chapter generation and local archiving entry point."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime

from novel_engine_v3 import AuthorEngine, V3ValidationError


ROOT = Path(__file__).resolve().parent


def accept_deep_edit(run_dir: Path, output: Path) -> None:
    """Validate a complete replacement before atomically updating the candidate."""
    candidate = run_dir / "candidate.md"
    original = candidate.read_text(encoding="utf-8-sig").strip()
    revised = output.read_text(encoding="utf-8-sig").strip()
    if not revised.startswith("# ") or "\n" not in revised:
        raise V3ValidationError("深层编辑未返回完整的 Markdown 章节，原候选稿已保留")
    if len(revised) < len(original) * 0.6 or revised.startswith("```"):
        raise V3ValidationError("深层编辑返回的正文不完整，原候选稿已保留")
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=run_dir,
        prefix=".candidate-", suffix=".tmp", delete=False,
    ) as handle:
        handle.write(revised + "\n")
        temporary = Path(handle.name)
    try:
        os.replace(temporary, candidate)
    finally:
        temporary.unlink(missing_ok=True)


def execute(prompt: Path, output: Path, run_dir: Path, *, capture_candidate: bool = False) -> None:
    codex = shutil.which("codex")
    if not codex:
        raise V3ValidationError("找不到 codex CLI；请先安装并登录")
    # Keep the subprocess outside the repository.  It can read/write only the
    # explicitly added project and run directories, so project-level Git and
    # publishing instructions cannot hijack an internal creative stage.
    isolated_cwd = Path(tempfile.mkdtemp(prefix="xiaoshuo-v3-"))
    try:
        result = subprocess.run(
            [codex, "exec", "--ephemeral", "--skip-git-repo-check",
             "-C", str(isolated_cwd),
             "--add-dir", str(ROOT), "--add-dir", str(run_dir),
             "--sandbox", "read-only" if capture_candidate else "workspace-write",
             "--config", 'approval_policy="never"',
             "--output-last-message", str(output), "-"],
            cwd=isolated_cwd,
            input=prompt.read_text(encoding="utf-8"),
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    finally:
        shutil.rmtree(isolated_cwd, ignore_errors=True)
    if result.returncode:
        raise V3ValidationError(f"阶段失败：{prompt.name}，退出码 {result.returncode}")
    if capture_candidate:
        accept_deep_edit(run_dir, output)


def archive_chapter(run: Path, project: Path, chapter: int) -> Path:
    candidate = run / "candidate.md"
    text = candidate.read_text(encoding="utf-8-sig").strip()
    lines = text.splitlines()
    if not lines or not lines[0].startswith("# "):
        raise V3ValidationError("候选稿缺少章节标题")
    title = re.sub(r"^第\s*[0-9零〇一二三四五六七八九十百千两]+\s*章\s*", "", lines[0][2:].strip()).strip()
    if not title:
        raise V3ValidationError("候选稿缺少章节标题")
    body = text.split("\n---", 1)[0].strip()
    body_lines = body.splitlines()
    body = "\n".join(body_lines[1:]).strip()
    if not body:
        raise V3ValidationError("候选稿正文为空")
    word_count = sum(1 for char in body if "\u4e00" <= char <= "\u9fff")
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    final = (
        f"# 第 {chapter} 章 {title}\n\n{body}\n\n---\n\n## Metadata\n\n"
        f"- chapter_number: {chapter}\n- word_count: {word_count}\n"
        f"- generated_at: {now}\n- upload_status: not_uploaded\n"
    )
    safe_title = re.sub(r'[\\/:*?"<>|]', "", title).strip() or f"第{chapter}章"
    final_path = project / "chapters" / f"{chapter:04d}-{safe_title}.md"
    draft_path = project / "drafts" / f"{now[:10]}-chapter-{chapter:04d}.md"
    existing = list((project / "chapters").glob(f"{chapter:04d}-*.md"))
    if existing:
        raise V3ValidationError(f"第 {chapter} 章已有归档，已保留现有正文")
    delta_path = run / "state_delta.json"
    delta = json.loads(delta_path.read_text(encoding="utf-8-sig")) if delta_path.is_file() else {}
    next_hook = str(delta.get("next_hook", "")).strip()
    unresolved = delta.get("unresolved", [])
    if isinstance(unresolved, list) and unresolved:
        next_hook += " 待解问题：" + "；".join(str(item) for item in unresolved if item)
    project.joinpath("chapters").mkdir(parents=True, exist_ok=True)
    project.joinpath("drafts").mkdir(parents=True, exist_ok=True)
    final_path.write_text(final, encoding="utf-8")
    draft_path.write_text(final, encoding="utf-8")
    state_path = project / "chapter_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["last_completed_chapter"] = max(int(state.get("last_completed_chapter", 0)), chapter)
    state["next_chapter_number"] = max(int(state.get("next_chapter_number", 1)), chapter + 1)
    state["last_uploaded_status"] = "not_uploaded"
    state["last_uploaded_at"] = None
    state["notes_for_next_chapter"] = next_hook.strip() or "请从本章结尾和未解决问题承接。"
    state["status"] = "ready"
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return final_path


def review_and_archive(run: Path, project: Path, chapter: int) -> Path:
    review_path = run / "reader_review.json"
    review = json.loads(review_path.read_text(encoding="utf-8-sig"))
    if review.get("blocking"):
        prior_reviews = list(run.glob("reader_review.before_repair.*.json"))
        attempt = len(prior_reviews) + 1
        saved_review = run / f"reader_review.before_repair.{attempt}.json"
        shutil.copy2(review_path, saved_review)
        repair_prompt = run / f"repair.{attempt}.md"
        repair_output = run / f"repair.{attempt}.result.md"
        repair_prompt.write_text(
            "# 阻塞问题返修\n\n"
            f"读取 `{run / 'candidate.md'}`、`{saved_review}` 和 `{project / 'world.md'}`。\n"
            "只处理读者检查的 blocking 问题，保持人物选择、已发生事件和其他正文不变。"
            "若设备状态造成因果冲突，须让动作和物理状态前后一致，不要凭空添加新能力。\n\n"
            "不要调用工具修改任何文件。最终回复只输出完整修订后的 Markdown 章节，"
            "从 `# 标题` 开始，不加说明或代码围栏。程序会校验并一次性替换候选稿。\n",
            encoding="utf-8",
        )
        execute(repair_prompt, repair_output, run, capture_candidate=True)
        execute(run / "reader.md", run / f"reader.after_repair.{attempt}.result.md", run)
        review = json.loads(review_path.read_text(encoding="utf-8-sig"))
    if review.get("blocking"):
        raise V3ValidationError(f"第 {chapter:04d} 章读者检查仍有阻塞问题，已保留运行包供继续返修")
    return archive_chapter(run, project, chapter)


def main() -> int:
    parser = argparse.ArgumentParser(description="V3 作者房间长篇生成链")
    parser.add_argument("command", choices=("prepare", "run", "resume"))
    parser.add_argument("--book", required=True)
    parser.add_argument("--chapter", type=int)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--run-dir", type=Path, help="resume 使用的 V3 运行包路径")
    args = parser.parse_args()
    engine = AuthorEngine(ROOT)
    if args.count < 1 or args.count > 20:
        parser.error("count 必须在 1 到 20 之间")
    project, _ = engine.book(args.book)
    if args.command == "resume":
        if not args.run_dir:
            parser.error("resume 必须指定 --run-dir")
        run = args.run_dir.resolve()
        expected_parent = (ROOT / ".novel_runs_v3" / args.book).resolve()
        if run.parent != expected_parent:
            parser.error("运行包不属于所选作品")
        manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("book_id") != args.book or Path(manifest.get("project", "")).resolve() != project:
            parser.error("运行包清单与所选作品不一致")
        archived = review_and_archive(run, project, int(manifest["chapter"]))
        print(f"V3 已归档：{archived}")
        return 0
    chapter = args.chapter
    for _ in range(args.count):
        run = engine.prepare(args.book, chapter)
        print(f"V3 运行包：{run}")
        if args.command == "prepare":
            chapter = (chapter or 1) + 1
            continue
        for stage in ("author_room", "draft", "deep_edit", "reader"):
            execute(run / f"{stage}.md", run / f"{stage}.result.md", run,
                    capture_candidate=stage == "deep_edit")
        archived = review_and_archive(run, project, int(run.name[:4]))
        print(f"V3 已归档：{archived}")
        chapter = None
    if args.command == "prepare":
        print("V3 已生成准备包，未写正文、未归档。")
    else:
        print("V3 已完成作者房间、初稿、深层编辑、读者检查和本地归档。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except V3ValidationError as exc:
        raise SystemExit(f"V3 错误：{exc}")
