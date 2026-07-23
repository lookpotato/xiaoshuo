# 多小说创作与发布总管理器

本目录是小说自动创作与发布总控工作区。当前只启用《404修理站》；
作品保留独立设定、章节、发布地址和运行状态，禁止复用其他作品的番茄书号。

## 目录

- `manager_config.json`：书籍注册表、时区、全局互斥与默认策略。
- `novel_manager.py`：查看排期、校验项目、领取到期任务、记录运行结果。
- `shared/`：跨作品复用的写作方法、质量门槛和复盘记录。
- `测试本小说/`：已有测试作品，保持独立。
- `404修理站/`：第一本正式长期作品。

## 常用命令

```powershell
python .\novel_manager.py list
python .\novel_manager.py validate
python .\novel_manager.py due
python .\novel_manager.py next
python .\novel_manager.py claim --book cosmic-404
python .\novel_manager.py progress --book cosmic-404 --phase chapter_archived
python .\novel_manager.py finish --book cosmic-404 --result success
python .\novel_manager.py finish --book cosmic-404 --result failed_retryable --message "临时网络失败"
python .\novel_manager.py finish --book cosmic-404 --result blocked_manual --message "登录失效"
```

每天 12:00 由 Codex 自动任务唤醒并执行 `next`。`next` 会选择到期作品并
原子领取任务；根目录锁避免重复写作，默认 180 分钟过期。管理器只负责调度和
运行状态，生成与上传由自动任务按目标书 `automation_prompt.md` 执行。

临时失败或待上传任务由 12:30 的补偿唤醒重试，每天最多 2 次；正常情况下
12:00 成功后，12:30 只检查状态并立即结束。登录、验证码、风控、
政策警告等记为 `blocked_manual`，等待人工处理。已经成功完成当天任务后不再领取。

## 调度原则

1. 《404修理站》每天 12:00 运行。
2. 同一本书前一章上传失败时，优先重传，不生成重复章节。
3. 不同书的番茄链接必须逐本绑定；未绑定的书只生成并归档本地草稿。
4. 验证码、登录、风控、审核警告与意外确认一律停止并记日志。
5. 章节完成后，把有效经验写入 `shared/learning_log.md`；只有反复验证有效的做法才升级到正式写作手册。
