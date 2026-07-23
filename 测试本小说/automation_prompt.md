# 每日自动任务 Prompt 草稿

每日 12:00 使用 `$fanqie-auto-novel`，在 `C:\Users\16007\Desktop\小说项目` 中执行每日自动更新：

1. 读取 `novel_config.md`、`outline.md`、`characters.md`、`world.md`、`style_guide.md`、`chapter_state.json` 和最近 3 章正文。
2. 生成下一章标题与正文，默认 2000-3000 字。
3. 做连续性检查、平台风险检查、错别字和重复段落检查。
4. 将草稿保存到 `drafts/`，通过后复制到 `chapters/`。
5. 打开 `publish_config.md` 中的番茄工作台链接，填入标题与正文。
6. 若 `submit_publish: false`，点击“存草稿”；若 `submit_publish: true`，按用户授权继续提交发布。
7. 更新 `chapter_state.json`，并将执行结果写入 `logs/`。
8. 若已连接 Gmail 且 `email_report_enabled: true`，发送成功/失败报告邮件。

遇到登录失效、验证码、风控、网页控件变化、二次确认、上传失败，停止操作，保留草稿和日志。
