# 本书每日任务说明

每天 12:00 由 Codex 自动任务唤醒，在仓库根目录执行。根目录
`novel_manager.py` 负责排期、原子领取、运行状态和失败重试；Codex 负责写作与浏览器上传。

1. 执行 `python .\novel_manager.py next`。返回空对象时当天无任务，立即结束；领取成功后只处理返回的 `cosmic-404`。
2. 读取根目录 `shared/writing_playbook.md`、`shared\quality_scorecard.md` 和
   `shared\learning_log.md`，再读取本书全部核心设定、`chapter_state.json`、
   `continuity_ledger.md`、最近 3 章及最近一次运行日志。
3. 若 `last_uploaded_chapter < last_completed_chapter` 且本书已经绑定番茄，
   优先重传最早的缺失章节，禁止重写或推进章节号。
4. 否则规划并生成 `next_chapter_number`，进行连续性、章节结构、文风、
   平台风险和 100 分质量评分；低于 80 分修订一次，仍不合格则仅保留草稿。
5. 质量通过后保存草稿与正式章节；同步更新 `chapter_state.json` 和
   `continuity_ledger.md`。核对归档最高章节、状态章节号、人物伤势、位置、
   装备、资源、承诺及伏笔状态一致后，执行：
   `python .\novel_manager.py progress --book cosmic-404 --phase chapter_archived`。
6. 只有 `fanqie_writer_url`、`book_id` 均非 `UNBOUND` 才能进入番茄后台，
   并必须核对页面作品名和 book_id。`submit_publish: false` 时只存番茄草稿；
   `submit_publish: true` 时才提交发布。进入后台前必须完整读取
   `fanqie_ui_workflow.md`，严格遵守其中的章节号、标题、正文定位、固定弹窗和
   发布设置流程。标题必须超过 5 个汉字。
7. 归档成功但上传尚未完成时记录 `upload_pending`，下次只重传：
   `python .\novel_manager.py finish --book cosmic-404 --result upload_pending --message "具体原因"`。
8. 登录失效、验证码、风控、政策或审核警告、陌生确认框、作品不匹配等需要
   人工处理的问题，记录 `blocked_manual`；临时网络或页面加载失败记录
   `failed_retryable`。两者都必须写明原因并释放运行锁。
9. 每次运行写入本书日志，并把“最强场景、最弱场景、连续性风险、观察证据、
   下一次实验”追加到 `shared\learning_log.md`。单次观察不得直接改写
   `writing_playbook.md`；至少经过两本书或三个事件链重复验证后才可晋升。
10. 全流程成功后执行：
    `python .\novel_manager.py finish --book cosmic-404 --result success`。
    无论成功、失败还是阻塞，都必须调用一次 `finish` 释放锁。
