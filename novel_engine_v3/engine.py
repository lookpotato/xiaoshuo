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

from novel_stage_pipeline import director_prompt, plan_path, load_config


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
        limit = load_config(self.root)["recent_chapters_for_director"]
        return [p for _, p in sorted(found)[-limit:]]

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
            "workflow": ["director", "characters", "author_room", "draft", "deep_edit", "reader", "continuity"],
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
        number = manifest["chapter"]
        plan = plan_path(self.root, project, number).resolve()
        thread_dir = project / "character_threads" / f"{number:04d}"
        director = director_prompt(self.root, project, number, (
            f"作品：{book['title']}。保持人物独立行动与既有时间、空间、承诺连续；"
            "本章若跨过固定增层时刻，章节合同须明确增层及现场后果。"
        ))
        if manifest["book_id"] == "night-floor":
            director += f"""
读取 `{project / 'timeline_state.json'}`。章节合同 JSON 另须加入 story_clock 对象：
{{"start_day": 整数, "end_day": 整数, "start_after_2347": true或false,
"end_after_2347": true或false, "start_floor": 整数, "end_floor": 整数,
"crossed_2347": 从上一章终点到本章终点经过的次数}}。
故事日从首次增层当天为第 1 天；当日 23:47 前最高楼层为 6+故事日-1，之后再加一层。
不能用章节号代替故事日；有时间跳跃也要计算其间每个 23:47。
章节合同 JSON 另须加入 unknown_pressure 对象：
{{"anomaly": "本章人物亲眼接触的新增楼层异常或仍在持续的未知事实",
"disrupted_expectation": "它推翻了人物原先确信的什么",
"human_cost": "谁因此改变行动、关系或去处"}}。
这三项必须落在正文可写的场景中；维修、房钱、清册与利益争执不能替代未知楼层带来的情绪压力。
"""
        characters = f"""# 人物独立故事线

先读 `{plan}`、作品资料和最近正文：
{source_list}
{recent_list}

再读 `shared/parallel_character_pipeline.md`。检索本书 `character_threads/` 中每位出场人物最近一次私线，区分人物知道的事实与读者知道的事实。
为本章全部有戏份人物创建 `{thread_dir}`，写 `00-cast.md`、每人独立的 `序号-人物名.md` 和 `interaction_map.md`。名单使用 `## 角色名单`，每人一行 `- 人物名：本章身份与目标`。
每条私线写明故事日与时刻、地点、个人目标、已知与未知、独立行动、代价及下一步；交织表按时间排序。
此阶段不得写正式正文。`state_update.md` 留给成稿后的回写阶段。
"""
        clock_check = (
            "先按 timeline.md 核对本章开始和结束的故事日、跨过的 23:47、当时最高楼层，"
            "并把人物说的‘明天’‘后天’换成故事日；若跨过 23:47，正文必须交代新增楼层。"
            if manifest["book_id"] == "night-floor" else ""
        )
        room = f"""# 作者房间：先活一遍，再决定怎么写

作品：{book['title']}；章节：{manifest['chapter']}。

你不是提示词执行器。你要像一个长期写中国故事的作者，先理解这一章的人为什么此刻来到这里、各自还要过自己的生活、彼此有什么说不出口的顾虑，再决定这章怎样发生。

先读取并遵守章节合同 `{plan}` 与本章人物私线 `{thread_dir}`；再读取这些资料：
{source_list}

最近正文：
{recent_list}

{clock_check}

另读 `shared/writing_playbook.md`、`shared/narrative_prose_foundation.md`、`shared/chinese_corpus_registry.md`，只提取判断，不复制原文，不把研究结论变成台词规则。

请写 `{run / 'author_room.md'}`。它不是提纲，也不是表格。用自然语言记录：

- 这一章每个人在主线之外本来要做什么；
- 他们带着什么旧经验、关系债和现实压力进入现场；
- 场景里谁想把事情办成，谁想把事情拖住，谁在保护什么；
- 哪些话大家心里明白但不会当面说；
- 场景会怎样在办事、帮忙、推诿、误会或冲突中自然变形；
- 本章结束后，哪些人的生活和关系真的改变了。

不要设计固定对白，不要列“人物应该说什么”，不要把人物属性写成标签。先让人物在没有主角帮助时各自行动，再让他们相遇。
"""
        draft = f"""# 初稿：像作者写，不像系统填

读取 `{run / 'author_room.md'}`、`{plan}`、`{thread_dir}`、manifest 中列出的作品资料和最近正文。
{clock_check}

先把场景写出来，再让信息从人的行动和关系里出现。中国人的自然感不靠方言词、口头词或短句数量，而来自人物正在办什么事、欠谁人情、怕什么后果、愿不愿意把话说死，以及公开和私下的不同。

不要把 author_room 改写成剧情说明。不要逐条兑现资料。允许人物误会、绕开、改口、心里有数却不说全、嘴上不客气但手上帮忙；也允许场景暂时没有效率，只要这种生活残留属于人物，并最终改变行动或关系。

保存 `{run / 'candidate.md'}`，只含标题和正文；另存 `{run / 'state_delta.json'}`，记录本章真实改变。不要读取 reader.md，不要边写边自评。
"""
        edit = f"""# 深层编辑：保留故事，恢复人的生活

读取 `{run / 'candidate.md'}`、`{run / 'author_room.md'}`、`{plan}`、`{thread_dir}` 和最近正文。
{clock_check}

不要做表面口语润色。检查每个关键场景：人物是否真的带着自己的生活进入；是否先处理了眼前的事、物品、面子或边界；是否把只有作者知道的信息提前说完；是否所有人都在高效配合剧情；是否有一句话只是为了传递设定。

逐句检查关键对白：这句话是对谁说的，此刻想让对方做什么，删掉后人物反应或关系会不会改变。熟人互相帮忙不等于必须说俏皮话；只为显得熟络的机锋应删掉。人物已看到的动作和结果，不要再由另一人完整口播成维修步骤、办事流程或整齐的三段说明；保留对方此刻确实需要听到的信息即可。

只改会破坏中国读者现场感的地方，同时保留情节、人物选择、事实变化和原创表达。把假对白改成真实交涉，把解释还给动作、停顿、关系和后果。修订后再读一遍新写的对白，确认没有把原句改成操作播报。不要统一人物口吻，不要强行增加“嗯、啊、您、吧”。

不要调用工具修改任何文件。最终回复只输出完整修订后的 Markdown 章节，从 `# 标题` 开始，保留完整正文，不加说明或代码围栏。系统会检查并一次性替换候选稿。
"""
        reader = f"""# 陌生中国读者：只判断读感，不替作者改写

只读取 `{run / 'candidate.md'}`。

请以一个真正读中国网文的读者判断：人物是否像在过自己的日子，而不是等剧情发问；场景里的办事、交易、帮忙、推诿和冲突是否可信；人物关系是否能从动作、称呼、避答和让步中看出来；对白是否像演员能自然说出来；哪些地方像翻译、客服、会议纪要或作者把信息塞进人物嘴里。

特别留意两种出戏对白：没有眼前目的、只为表现熟络而硬造的俏皮话或机锋；把读者已看见的动作与结果重新说成操作说明、流程交接或完整的信息清单。对每处可疑台词，结合说话对象和下一步反应判断：删掉它是否几乎不影响现场？人物会不会真在这个时候对这个人说这句话？也检查同一信息是否在章内反复口播。不要仅因整章其他部分可信，就放过具体的假对白。

把明显破坏人物或现场可信度的具体句子列入 blocking，触发返修；轻微卡顿列入 nonblocking。每项引用原句并说明它为什么不像此人此刻会说的话。不要因为个人偏好要求方言、网络梗或更短句，也不要把必要的安全提醒和现实办事信息一概判错。结果写入 `{run / 'reader_review.json'}`。
"""
        continuity = f"""# 成稿后连续性审查与人物状态回写

读取 `{run / 'candidate.md'}`、`{plan}`、`{thread_dir}`、作品时间线、前一章和 `shared/parallel_character_pipeline.md`。
按正文真实发生的事更新 `{thread_dir / 'state_update.md'}`，逐人记录结尾时间地点、资源、知识、关系、承诺、秘密和下一步；未写进正文的私线标为读者未见。
写 `{run / 'continuity_review.json'}`，只使用以下结构：
{{"chapter": {number}, "blocking": [], "evidence": {{"begin": "正文原句", "end": "正文原句"}},
"next_hook": "依据最终正文写给下一章的事实与待办", "unresolved": ["仍未解决的具体事项"]}}
发现人物、时间、地点、承诺与正文或章节合同矛盾时，把具体问题写入 blocking，不得替正文补事实。
"""
        if manifest["book_id"] == "night-floor":
            continuity += f"""
另写 `{run / 'chronology.json'}`：
{{"chapter": {number}, "start_day": 整数, "end_day": 整数, "start_after_2347": true或false, "end_after_2347": true或false,
"start_floor": 整数, "end_floor": 整数,
"crossed_2347": 整数, "events": [{{"day": 整数, "floor": 整数, "quote": "正文中新增楼层的原句"}}],
"start_quote": "正文开头的时间依据原句", "end_quote": "正文结尾的时间依据原句"}}
故事日从首次增层当天为第 1 天；每天 23:47 必增一层。先读取 `{project / 'timeline_state.json'}` 的上一章终点，crossed_2347 是从上一章终点到本章终点经过的次数；events 应逐次写出正文中明确交代的新楼层。引用必须来自候选正文；不确定故事日或跨夜次数就写入 blocking，不能猜。
"""
        return {"director": director, "characters": characters, "author_room": room,
                "draft": draft, "deep_edit": edit, "reader": reader, "continuity": continuity}
