"""Compile the v3 author-room workflow.

V3 is deliberately not a larger checklist.  It gives a fresh context a small
author room: the book's promise, the characters' current lives, the immediate
scene, a few research conclusions, and the failures that must not recur.  The
actual prose remains free to discover its own sentences.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path


class V3ValidationError(ValueError):
    pass


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise V3ValidationError(f"无法读取 {path}: {exc}") from exc


def _json(path: Path) -> dict:
    try:
        value = json.loads(_read(path))
    except json.JSONDecodeError as exc:
        raise V3ValidationError(f"JSON 无效：{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise V3ValidationError(f"配置必须是对象：{path}")
    return value


class AuthorEngine:
    """Prepare a bounded, author-led context for one chapter."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.home = self.root / "novel_engine_v3"
        self.config = _json(self.home / "system.json")
        if self.config.get("schema_version") != 3:
            raise V3ValidationError("novel_engine_v3/system.json 必须是 schema_version 3")

    def book(self, book_id: str) -> tuple[Path, dict]:
        raw = self.config.get("books", {}).get(book_id)
        if not isinstance(raw, dict):
            raise V3ValidationError(f"未知 V3 作品：{book_id}")
        project = (self.root / raw["project"]).resolve()
        if self.root != project and self.root not in project.parents:
            raise V3ValidationError(f"作品目录越界：{project}")
        return project, raw

    def _recent(self, project: Path, number: int) -> list[Path]:
        found = []
        for path in (project / "chapters").glob("*.md"):
            match = re.match(r"^(\d+)-", path.name)
            if match and int(match.group(1)) < number:
                found.append((int(match.group(1)), path))
        return [p for _, p in sorted(found)[-2:]]

    def _existing(self, project: Path, names: list[str]) -> list[Path]:
        return [project / name for name in names if (project / name).is_file()]

    def prepare(self, book_id: str, chapter: int | None = None) -> Path:
        project, book = self.book(book_id)
        if chapter is None:
            state = _json(project / "chapter_state.json")
            chapter = state.get("next_chapter_number")
        if not isinstance(chapter, int) or chapter < 1:
            raise V3ValidationError("缺少有效章节号")

        run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        run = self.root / ".novel_runs_v3" / book_id / f"{chapter:04d}-{run_id}"
        run.mkdir(parents=True, exist_ok=False)
        sources = self._existing(project, book.get("sources", []))
        plan = project / "chapter_plans" / f"{chapter:04d}.json"
        if plan.is_file():
            sources.append(plan)
        recent = self._recent(project, chapter)
        manifest = {
            "schema_version": 3,
            "book_id": book_id,
            "chapter": chapter,
            "run_id": run_id,
            "run_dir": str(run.resolve()),
            "project": str(project),
            "sources": [str(p.resolve()) for p in sources],
            "recent_chapters": [str(p.resolve()) for p in recent],
            "workflow": ["author_room", "draft", "deep_edit", "reader"],
        }
        (run / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        prompts = self._prompts(project, book, manifest, sources, recent, run)
        for name, text in prompts.items():
            (run / f"{name}.md").write_text(text, encoding="utf-8")
        return run

    def _prompts(
        self, project: Path, book: dict, manifest: dict,
        sources: list[Path], recent: list[Path], run: Path,
    ) -> dict[str, str]:
        source_list = "\n".join(f"- `{p.resolve()}`" for p in sources)
        recent_list = "\n".join(f"- `{p.resolve()}`" for p in recent) or "- 无历史正文"
        room = f"""# 作者房间：先活一遍，再决定怎么写

作品：{book['title']}；章节：{manifest['chapter']}。

你不是提示词执行器。你要像一个长期写中国故事的作者，先理解这一章的人为什么此刻来到这里、各自还要过自己的生活、彼此有什么说不出口的顾虑，再决定这章怎样发生。

读取这些资料：
{source_list}

最近正文：
{recent_list}

另读 `shared/writing_playbook.md`、`shared/narrative_prose_foundation.md`、`shared/opening_promise.md`、`shared/chinese_corpus_registry.md`，只提取判断，不复制原文，不把研究结论变成台词规则。

请写 `{run / 'author_room.md'}`。它不是提纲，也不是表格。用自然语言记录：

- 这一章每个人在主线之外本来要做什么；
- 他们带着什么旧经验、关系债和现实压力进入现场；
- 场景里谁想把事情办成，谁想把事情拖住，谁在保护什么；
- 哪些话大家心里明白但不会当面说；
- 场景会怎样在办事、帮忙、推诿、误会或冲突中自然变形；
- 本章结束后，哪些人的生活和关系真的改变了。
- 首章或前三章里，书名和简介究竟许诺了什么独特胜负，本章会先兑现哪一个可见结果；进入新场景时，读者需要知道哪些地点、来由、人物关系和当地限制。

不要设计固定对白，不要列“人物应该说什么”，不要把人物属性写成标签。先让人物在没有主角帮助时各自行动，再让他们相遇。
"""
        draft = f"""# 初稿：像作者写，不像系统填

读取 `{run / 'author_room.md'}`、manifest 中列出的作品资料和最近正文。

先把场景写出来，再让信息从人的行动和关系里出现。中国人的自然感不靠方言词、口头词或短句数量，而来自人物正在办什么事、欠谁人情、怕什么后果、愿不愿意把话说死，以及公开和私下的不同。

第一屏让初读者知道跟着谁、身在何处、正要办什么。首次进入新场景，用适量旁白或行动交代到此的原因、关键人物的关系和眼前限制；前文铺垫过的只需简短承接。前三章让书名和简介的独特承诺在场上发生，并有可见收益或代价；第二章既承接前章，也要提供新的行动和读下去的理由。不要把必要背景硬塞进人物对白。

不要把 author_room 改写成剧情说明。不要逐条兑现资料。允许人物误会、绕开、改口、心里有数却不说全、嘴上不客气但手上帮忙；也允许场景暂时没有效率，只要这种生活残留属于人物，并最终改变行动或关系。

保存 `{run / 'candidate.md'}`，只含标题和正文；另存 `{run / 'state_delta.json'}`，记录本章真实改变。不要读取 reader.md，不要边写边自评。
"""
        edit = f"""# 深层编辑：保留故事，恢复人的生活

读取 `{run / 'candidate.md'}`、`{run / 'author_room.md'}` 和最近正文。

不要做表面口语润色。检查每个关键场景：人物是否真的带着自己的生活进入；是否先处理了眼前的事、物品、面子或边界；是否把只有作者知道的信息提前说完；是否所有人都在高效配合剧情；是否有一句话只是为了传递设定。

只改会破坏中国读者现场感的地方，同时保留情节、人物选择、事实变化和原创表达。把假对白改成真实交涉，把解释还给动作、停顿、关系和后果。检查人物与场景初次出现时读者有没有坐标；缺少必要背景时补短旁白，不要只靠对话和动作让读者猜。不要统一人物口吻，不要强行增加“嗯、啊、您、吧”。

不要调用工具修改任何文件。最终回复只输出完整修订后的 Markdown 章节，从 `# 标题` 开始，保留完整正文，不加说明或代码围栏。系统会检查并一次性替换候选稿。
"""
        reader = f"""# 陌生中国读者：只判断读感，不替作者改写

只读取 `{run / 'candidate.md'}`。

请以一个真正读中国网文的读者判断：人物是否像在过自己的日子，而不是等剧情发问；场景里的办事、交易、帮忙、推诿和冲突是否可信；人物关系是否能从动作、称呼、避答和让步中看出来；对白是否像演员能自然说出来；哪些地方像翻译、客服、会议纪要或作者把信息塞进人物嘴里。还要只凭正文判断开头及换场后的时间、地点、来由和关系是否清楚；前三章有没有独特的具体做法与可见结果，第二章是否给了新的追读理由。缺失必要坐标，或只有危机和悬念而没有实际兑现时列为 blocking；不要为追求悬念把基础信息也藏掉。

把问题分成 blocking 和 nonblocking，并引用具体原句。不要因为个人偏好要求方言、网络梗或更短句。结果写入 `{run / 'reader_review.json'}`。
"""
        return {"author_room": room, "draft": draft, "deep_edit": edit, "reader": reader}
