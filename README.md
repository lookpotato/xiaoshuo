# 多小说创作与发布总管理器

本目录是多本小说的总控工作区。每本书保留独立设定、章节、发布地址和运行状态；根目录只负责注册、排期、质量沉淀与调用，禁止在不同书之间复用番茄书号。

## 目录

- `manager_config.json`：书籍注册表、时区、全局互斥与默认策略。
- `novel_manager.py`：查看排期、校验项目、领取到期任务、记录运行结果。
- `shared/`：跨作品复用的写作方法、质量门槛和复盘记录。
- `测试本小说/`：已有测试作品，保持独立。
- `宇宙不予解析/`：第一本正式长期作品。

## 常用命令

```powershell
python .\novel_manager.py list
python .\novel_manager.py validate
python .\novel_manager.py due
python .\novel_manager.py claim --book cosmic-404
python .\novel_manager.py finish --book cosmic-404 --result success
python .\novel_manager.py finish --book cosmic-404 --result failed --message "登录失效"
```

`claim` 使用根目录锁避免两次任务同时写同一本书；锁默认 180 分钟过期。它只领取任务，不会擅自生成或上传内容。实际每日任务应先读取目标书的 `publish_config.md`，并遵守其中授权。

## 调度原则

1. 每本书只在自己的计划时间运行。
2. 同一本书前一章上传失败时，优先重传，不生成重复章节。
3. 不同书的番茄链接必须逐本绑定；未绑定的书只生成并归档本地草稿。
4. 验证码、登录、风控、审核警告与意外确认一律停止并记日志。
5. 章节完成后，把有效经验写入 `shared/learning_log.md`；只有反复验证有效的做法才升级到正式写作手册。

