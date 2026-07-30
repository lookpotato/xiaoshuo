# 番茄小说管理器

本目录是“番茄小说管理器”的自动创作与发布总控工作区。当前只启用《404修理站》；
作品保留独立设定、章节、发布地址和运行状态，禁止复用其他作品的番茄书号。

## 目录

- `manager_config.json`：书籍注册表、时区、全局互斥与默认策略。
- `fanqie_novel_manager.py`：番茄小说管理器主入口，负责排期、会话交接、任务领取、
  发布恢复和运行结果。
- `shared/`：跨作品复用的写作方法、质量门槛和复盘记录。
- `测试本小说/`：已有测试作品，保持独立。
- `404修理站/`：第一本正式长期作品。

## 常用命令

### 一键批量创作、上传与排期

在 VS Code 终端进入本项目根目录后，只需输入命令和本次章节数：

```powershell
python xiaoshuo 1
python xiaoshuo 3
python xiaoshuo 5
```

也可以使用带扩展名的等价命令：

```powershell
python .\xiaoshuo.py 5
```

数字表示本批需要严格串行完成的章节数，不是新建书籍数。管理器会先恢复已有的
待发布番茄草稿；每个成功恢复的章节占一个名额。待发布草稿清空后，才继续写新章。
每章都必须完成“写作或恢复 → 质检 → 上传 → 排期 → 章节列表核验 → 状态回写”，
上一章没有显示“待发布”“审核中”或“已发布”时不会进入下一章。
默认会打开可见的专用 Chrome，但不暂停人工确认；程序固定选择“是否使用AI=是”，
打开定时发布，按排期选择日期并设置为中午 12:00。全部完成后立即退出，不创建后台
任务，也不按分钟轮询。只有需要逐步观察页面时才加 `--debug-browser`。

首次使用前确认：

```powershell
codex login status
python xiaoshuo --setup-browser
python xiaoshuo --check
python .\xiaoshuo.py 1 --dry-run
```

`--setup-browser` 会打开一个只供本项目使用的 Chrome 窗口。首次由用户亲自登录番茄；
登录资料由 Chrome 保存在 `%LOCALAPPDATA%\xiaoshuo\fanqie-chrome-profile-v2`，不会写入
仓库、日志或 Git。必须从用户自己的 VS Code 终端运行该命令，后台执行环境无法把
Chrome 窗口显示到用户桌面。管理器不会读取或导出 Cookie、Token、密码和验证码。

实际运行只在命令执行期间工作，不创建定时任务、不轮询队列。命令会依次恢复待发布
章节或调用一次 Codex 写一章，再用专用 Chrome 上传、排期并核验；达到指定章数后进程
立即退出。任务中断后可查看和续跑：

```powershell
python .\fanqie_novel_manager.py job-status
python .\fanqie_novel_manager.py job-status --job <job-id>
python xiaoshuo --resume <job-id>
```

浏览器不可用、登录失效、验证码、风控或政策警告会安全停止并保留恢复点。再次登录或
临时故障恢复后，使用 `python xiaoshuo --resume <job-id>` 继续同一个批次。
浏览器或程序意外退出后，按需进程锁会在下次命令启动时自动识别并清理，不必等待
180 分钟；普通失败也会在退出前主动释放锁。
需要观察浏览器步骤时使用：

```powershell
python xiaoshuo --resume <job-id> --debug-browser
```

调试模式遇到错误会保留 Chrome 窗口，查看页面后回到终端按 Enter 才会关闭。

```powershell
python .\fanqie_novel_manager.py list
python .\fanqie_novel_manager.py validate
python .\fanqie_novel_manager.py session --book cosmic-404  # 新会话完整交接包
python .\fanqie_novel_manager.py pending --book cosmic-404  # 批量排期中的待处理章节
python .\fanqie_novel_manager.py notes --book cosmic-404    # 发布流程和成功门槛
python .\fanqie_novel_manager.py due
python .\fanqie_novel_manager.py next
python .\fanqie_novel_manager.py claim --book cosmic-404
python .\fanqie_novel_manager.py progress --book cosmic-404 --phase chapter_archived
python .\fanqie_novel_manager.py progress --book cosmic-404 --phase publish_pending
python .\fanqie_novel_manager.py finish --book cosmic-404 --result success
python .\fanqie_novel_manager.py finish --book cosmic-404 --result publish_pending --message "fanqie draft saved; continue publish confirmation"
python .\fanqie_novel_manager.py finish --book cosmic-404 --result failed_retryable --message "临时网络失败"
python .\fanqie_novel_manager.py finish --book cosmic-404 --result blocked_manual --message "登录失效"
```

## 新会话接管

在新 Codex 会话中，可以直接说：

> 使用番茄小说管理器继续 `cosmic-404` 的批量创作和发布，先读取 session，
> 优先恢复待发布草稿，然后严格串行完成。

新会话应先执行：

```powershell
python .\fanqie_novel_manager.py validate
python .\fanqie_novel_manager.py session --book cosmic-404
python .\fanqie_novel_manager.py pending --book cosmic-404
python .\fanqie_novel_manager.py next
```

定时唤醒使用 `next`；如果用户在新会话里明确要求立即执行，不受当日唤醒时间限制，
可使用：

```powershell
python .\fanqie_novel_manager.py claim --book cosmic-404
```

直接 `claim` 用于用户明确要求立即执行的已启用作品；它不绕过项目结构校验、
全局运行锁、发布成功门槛或浏览器安全停止条件。

`session` 输出机器可读 JSON，包含当前书籍状态、运行状态、批量排期中仍需处理的章节、
必读文件顺序、完整写作与上传闭环、可靠性规则、成功门槛及结束命令。新会话不应依赖
上一段聊天记录来猜恢复点。

`pending` 会读取 `batch_schedule_*.json`，逐章列出尚未进入待发布、审核中或已发布
状态的批量任务。已有番茄草稿时必须从原草稿继续，不得重新写作或创建重复章节。

## 完整批量闭环

每章必须严格串行完成：

1. 读取设定、连续性账本、最近三章和当前排期。
2. 优先恢复待发布草稿；没有待恢复项时才生成 `next_chapter_number`。
3. 写作、质量评分、修订、归档，并更新章节状态与连续性账本。
4. 核对番茄作品名和 `book_id`，串行填写章节号、标题、纯正文。
5. 回读正文首段、末段、平台字数；不一致时禁止进入下一步。
6. 依次完成错别字提示提交、仅基础检测、AI=是、定时发布。
7. 回读日期、时间、AI 和定时开关，再点击一次“确认发布”。
8. 返回章节管理页，按章节号和标题唯一匹配目标行；读取状态和发布时间。
9. 只有“待发布”“审核中”或“已发布”才算该章完成，并立即回写状态和日志。
10. 当前章确认完成后才能进入下一章；整批完成后才能 `finish --result success`。

默认不启用任何定时唤醒。只有用户输入 `python xiaoshuo N` 时才会取得根目录锁并
运行；根目录锁避免重复写作，默认 180 分钟过期。

`notes` 还会输出 `browser_reliability_steps`，用于判断浏览器控制超时、
拆分单步操作、执行只读恢复，并防止不确定动作被重复点击。

临时失败或待上传任务不会后台重试。登录、验证码、风控、政策警告等记为
`blocked_manual`，等待用户处理后手动续跑。

## 调度原则

When `submit_publish: true`, Fanqie draft storage is only an intermediate state.
The manager must not record `success` until the chapter is visibly in pending
publish, review, or published state. Use `publish_pending` when a saved Fanqie
draft still needs the fixed publish-confirmation flow.

1. 《404修理站》每天 12:00 运行。
2. 同一本书前一章上传失败时，优先重传，不生成重复章节。
3. 不同书的番茄链接必须逐本绑定；未绑定的书只生成并归档本地草稿。
4. 验证码、登录、风控、审核警告与意外确认一律停止并记日志。
5. 章节完成后，把有效经验写入 `shared/learning_log.md`；只有反复验证有效的做法才升级到正式写作手册。
