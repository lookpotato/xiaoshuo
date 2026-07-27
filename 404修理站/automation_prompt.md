# 本书每日任务说明

每天 12:00 由 Codex 自动任务唤醒，在仓库根目录执行。每日目标为 2 章，必须
串行执行两轮“写一章→校验归档→上传一章→确认结果”，不得先写两章再集中上传。根目录
`novel_manager.py` 负责排期、原子领取、运行状态和失败重试；Codex 负责写作与浏览器上传。

1. 执行 `python .\novel_manager.py next`。返回空对象时当天无任务，立即结束；领取成功后只处理返回的 `cosmic-404`。
2. 读取根目录 `shared/writing_playbook.md`、`shared\quality_scorecard.md` 和
   `shared\learning_log.md`，再读取本书全部核心设定、`chapter_state.json`、
   `continuity_ledger.md`、最近 3 章及最近一次运行日志。
3. 领取成功后按顺序执行最多两轮。每轮开始时，若
   `last_uploaded_chapter < last_completed_chapter` 且本书已经绑定番茄，
   优先重传最早的缺失章节，禁止重写或推进章节号；该章确认上传成功后，才进入
   下一轮。
4. 若没有待上传章节，则规划并生成 `next_chapter_number`，进行连续性、章节结构、文风、
   平台风险和 100 分质量评分；低于 80 分修订一次，仍不合格则仅保留草稿。
5. 质量通过后保存草稿与正式章节；同步更新 `chapter_state.json` 和
   `continuity_ledger.md`。核对归档最高章节、状态章节号、人物伤势、位置、
   装备、资源、承诺及伏笔状态一致后，执行：
   `python .\novel_manager.py progress --book cosmic-404 --phase chapter_archived`。
6. 只有 `fanqie_writer_url`、`book_id` 均非 `UNBOUND` 才能进入番茄后台，
   并必须核对页面作品名和 book_id。`submit_publish: false` 时只存番茄草稿；
   `submit_publish: true` 时必须提交发布并设置定时发布；保存到番茄草稿箱只算中间态，
   不得视为本轮上传成功，也不得以 `success` 结束任务。进入后台前必须完整读取
   `fanqie_ui_workflow.md`，严格遵守其中的章节号、标题、正文定位、固定弹窗和
   发布设置流程。标题必须超过 5 个汉字。正文输入必须串行完成并核对首段、末段、
   平台字数和本地字数；若正文未进入编辑器、字数为 0 或明显偏离，不得点击“下一步”，
   应重贴一次，仍失败则记录 `failed_retryable`。只有章节列表、成功提示或审核状态明确
   证明目标章节已经进入待发布/审核中/已发布，才能更新上传状态并开始第二轮；
   否则立即记录 `publish_pending` 或 `upload_pending` 并停止本次任务。
7. 归档成功但上传尚未完成时记录 `upload_pending`，下次只重传：
   `python .\novel_manager.py finish --book cosmic-404 --result upload_pending --message "具体原因"`。
   若章节已经进入番茄草稿箱但尚未完成“下一步→基础检测→AI=是→定时发布→确认发布”，
   记录 `publish_pending`；下次只从草稿箱继续推进发布，不得重写章节。
8. 登录失效、验证码、风控、政策或审核警告、陌生确认框、作品不匹配等需要
   人工处理的问题，记录 `blocked_manual`；临时网络或页面加载失败记录
   `failed_retryable`。两者都必须写明原因并释放运行锁。
9. 每次运行写入本书日志，并把“最强场景、最弱场景、连续性风险、观察证据、
   下一次实验”追加到 `shared\learning_log.md`。单次观察不得直接改写
   `writing_playbook.md`；至少经过两本书或三个事件链重复验证后才可晋升。
10. 浏览器每次出现文档未覆盖的页面结构或措辞变化时，先识别其含义并保存可核验
    的页面证据。若它只是已授权动作的同义新版入口，可在成功验证后把新结构、定位
    方法和成功信号补入 `fanqie_ui_workflow.md`；若涉及新的政策确认、风险告警、
    身份验证、付费、删除或其他不可逆操作，必须停止，不得自行点击。每次更新规程
    都要写入当日日志并随本批文件提交 Git。
11. 每日最终结果无论成功、失败或阻塞，都按 `publish_config.md` 向配置的邮箱发送
    简短报告；邮件必须包含两轮各自的章节号、标题、番茄状态、日志路径和失败原因。
    若邮件连接不可用或发送失败，写入日志并将本次结果视为 `failed_retryable`，不得
    虚报通知成功。
12. 两轮都确认进入待发布/审核中/已发布后才执行：
    `python .\novel_manager.py finish --book cosmic-404 --result success`。
    无论成功、失败还是阻塞，都必须调用一次 `finish` 释放锁。
