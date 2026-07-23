# 本书每日任务说明

由根目录 `novel_manager.py` 按 `manager_config.json` 排期领取 `cosmic-404` 后执行：

1. 读取根目录 `shared/writing_playbook.md`、`shared/quality_scorecard.md`。
2. 读取本书全部核心设定、状态、伏笔账本和最近 3 章。
3. 若 `last_uploaded_chapter < last_completed_chapter` 且本书已绑定番茄，优先重传缺失章节，禁止重写。
4. 否则规划并生成 `next_chapter_number`，进行连续性、质量和平台风险检查。
5. 保存草稿与正式归档，更新状态和伏笔账本。
6. 只有 `fanqie_writer_url` 非 `UNBOUND` 才可进入番茄后台；书名或 book_id 不匹配立即停止。
7. 写入本书日志，并使用总管理器 `finish` 释放运行锁。

