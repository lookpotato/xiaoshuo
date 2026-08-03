# 《此天任我行》自动创作约束

每次只处理 `chapter_state.json` 指定的下一章，并严格串行：读取设定与最近三章 → 写章纲 → 写作 → 质量校验 → 修订 → 本地归档 → 更新状态与连续性账本 → 写日志 → Git 提交与推送。

## 必读顺序

1. 根目录 `AGENTS.md`
2. `fanqie-auto-novel` 技能及其要求的引用文件
3. 根目录 `shared/writing_playbook.md`、`shared/quality_scorecard.md`、`shared/learning_log.md`
4. 本书 `novel_config.md`、`outline.md`、`characters.md`、`world.md`、`style_guide.md`
5. `publish_config.md`、`chapter_state.json`、`continuity_ledger.md`
6. `chapters/` 中最近三章与当前排期文件

## 当前执行模式

- 本书尚未绑定番茄，`mode=write_only`、`submit_publish=false`。
- 只允许生成、质检、归档和 Git 同步；不得打开浏览器，不得上传，不得借用其他作品的发布地址。
- 新章节 Metadata 的 `upload_status` 写为 `not_uploaded`。
- 默认未来档期为每日 18:30、20:30；本地排期只做计划，不冒充平台状态。

## 章节硬门槛

- 2800—3400 字为常规目标。
- 开头 300 字内出现目标、异常或危险后果。
- 本章至少推进情节、关系、能力、真相中的两项。
- 新能力必须符合青羽“看路而非代打”的限制。
- 结尾钩子必须由本章结果自然产生。
- 不得泄露 AI、提示词、自动化、账号信息或其他书籍发布数据。
