# 每日自动任务 Prompt 草稿

每日 12:00 使用 `$fanqie-auto-novel`，在 `C:\Users\16007\Desktop\小说项目` 中执行每日自动更新：

1. 先读取共享规则 `shared/character_engine.md`、`shared/parallel_character_pipeline.md`，再读取 `novel_config.md`、`outline.md`、`characters.md`、`world.md`、`style_guide.md`、`chapter_state.json` 和最近 3 章正文。
2. 写作前更新所有有戏份人物的运行卡，分别生成私线并建立交织表，模拟主角未介入时各自会做什么，再以单一主视角汇总生成下一章标题与正文，默认 2000-3000 字。人物必须拥有独立目标、误解、底线和下一步行动，不能只服务主角。
3. 做连续性检查、平台风险检查、错别字和重复段落检查；另做人物独立性检查、视角边界检查、全体人物状态回写和反解释编辑，删除替人物总结情绪与意义的句子，保留停顿、误解、答非所问、无关动作和不完整表达。
4. 将草稿保存到 `drafts/`，通过后复制到 `chapters/`。
5. 打开 `publish_config.md` 中的番茄工作台链接，填入标题与正文。
6. 若 `submit_publish: false`，点击“存草稿”；若 `submit_publish: true`，按用户授权继续提交发布。
7. 更新 `chapter_state.json`，并将执行结果写入 `logs/`。
8. 若已连接 Gmail 且 `email_report_enabled: true`，发送成功/失败报告邮件。

遇到登录失效、验证码、风控、网页控件变化、二次确认、上传失败，停止操作，保留草稿和日志。
