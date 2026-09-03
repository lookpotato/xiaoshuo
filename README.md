# 番茄小说管理器

本目录是“番茄小说管理器”的自动创作与发布总控工作区。当前启用《404修理站》和
《道友你这天命与我有缘》；两本作品分别保留独立设定、章节、发布地址和运行状态，
禁止交叉复用番茄书号。

## 快速命令速查

先在 PowerShell 进入项目根目录：

```powershell
cd C:\Users\16007\Desktop\小说\xiaoshuo
```

书籍 ID：

- `cosmic-404`：《404修理站》
- `free-sky`：《道友你这天命与我有缘》

### 本地前后端工作台

直接启动网页管理界面：

```powershell
python .\web_app.py
```

Windows 下也可以直接双击根目录的 `启动小说工作台.cmd`。

浏览器会打开 `http://127.0.0.1:8765`。工作台使用 React + Vite 前端和 Python 后端，可以查看作品状态、质量门禁、下一章接力点、章节归档和最近任务，也可以启动单本/全部作品生成或从失败 job 断点续跑。每次运行都能独立配置章数、是否同步 Git、是否更新番茄正式环境；两个外部交付开关默认关闭，未绑定番茄书号的作品不能开启正式环境。失败任务会保存本次配置，续跑时不会改变交付范围。网页后端不会绕开原有串行锁、人物线、读者验收、发布和安全规则。

前端通过 `POST /api/run` 提交完整配置，例如：

```json
{
  "book_id": "cosmic-404",
  "count": 10,
  "sync_git": true,
  "publish_fanqie": false
}
```

如果不希望自动打开浏览器，或默认端口被占用：

```powershell
python .\web_app.py --no-browser
python .\web_app.py --port 8877
```

服务仅监听本机 `127.0.0.1`；运行记录保存在被 Git 忽略的 `.web_runtime/`，不会保存密码、Cookie、Token或小说正文。
点击任务记录里的“查看后端日志”会打开置顶日志窗口；新任务启动时自动打开，任务失败时也会自动弹出并单独标出最终失败原因。窗口通过 `/api/run-log` 在运行中每 2 秒刷新，任务结束后日志仍会保留并可反复查看。接口只返回日志尾部，自动过滤小说正文、隐藏常见凭据字段并截断异常超长单行。

需要修改 React 页面时，在另一个终端运行开发服务器：

```powershell
cd .\web
npm install
npm run dev
```

Vite 开发地址为 `http://127.0.0.1:5173`，会把 `/api` 转发到 Python 后端。修改完成后运行 `npm run build`，正式工作台仍由 `web_app.py` 一体化提供。

### 检查与安全预演

```powershell
# 检查 Codex、专用 Chrome 和书籍绑定
python xiaoshuo --check

# 只显示计划，不写作、不上传
python xiaoshuo 1 --book free-sky --dry-run
python xiaoshuo 1 --book cosmic-404 --dry-run

# 校验全部本地小说项目
python .\fanqie_novel_manager.py validate
```

### 正常更新

```powershell
# 指定作品更新 N 章；每章严格串行完成写作、质检、上传、排期和平台核验
python xiaoshuo 2 --book free-sky
python xiaoshuo 2 --book cosmic-404

# 所有已启用作品各更新 2 章；按优先级串行，不并发
python xiaoshuo 2 --all

# 不指定章节数时，每本按 manager_config.json 的 daily_chapter_target 更新
python xiaoshuo --all

# 不写 --book 时，使用 manager_config.json 的 default_book_id
python xiaoshuo 1
```

### 查看任务与断点续跑

```powershell
# 查看全部任务或指定任务
python .\fanqie_novel_manager.py job-status
python .\fanqie_novel_manager.py job-status --job <job-id>

# 继续原批次；不会重传已经进入审核中、待发布或已发布的章节
python xiaoshuo --resume <job-id>

# 调试续跑：出错时保留可见 Chrome 窗口
python xiaoshuo --resume <job-id> --debug-browser
```

### 首次登录与浏览器恢复

```powershell
# 打开项目专用 Chrome，由用户本人登录番茄
python xiaoshuo --setup-browser

# 登录后重新检查
python xiaoshuo --check
```

图片生成使用另一套独立 Chrome 登录会话。首次配置并检查：

```powershell
python xiaoshuo --setup-image-browser
python xiaoshuo --check-image-browser
```

系统会通过已登录的 ChatGPT Plus 网页生成并下载图片，Codex 只负责提示词、设定一致性和视觉质量验收；网页失败时不会自动消耗 Codex 图片额度。详细步骤见 `docs/图片浏览器配置.md`。

### 《404修理站》打赏加更

```powershell
# 建议先预演
python xiaoshuo --reward 3 --book cosmic-404 --dry-run

# 把未来最早的 3 章提前到今天，并自动补齐后续排期
python xiaoshuo --reward 3 --book cosmic-404

# 明确指定今天的加更时间
python xiaoshuo --reward 3 --book cosmic-404 --reward-time 18:30
```

### 查看单本书状态

```powershell
python .\fanqie_novel_manager.py session --book free-sky
python .\fanqie_novel_manager.py pending --book free-sky
python .\fanqie_novel_manager.py notes --book free-sky

python .\fanqie_novel_manager.py session --book cosmic-404
python .\fanqie_novel_manager.py pending --book cosmic-404
python .\fanqie_novel_manager.py notes --book cosmic-404
```

安全边界：验证码、登录失效、二维码、风控、政策警告或陌生确认框会使任务停止并保留恢复点；
不要重新创建同章，修复问题后使用原 `job-id` 续跑。

## 目录

- `manager_config.json`：书籍注册表、时区、全局互斥与默认策略。
- `fanqie_novel_manager.py`：番茄小说管理器主入口，负责排期、会话交接、任务领取、
  发布恢复和运行结果。
- `shared/`：跨作品复用的写作方法、质量门槛和复盘记录。
- `测试本小说/`：已有测试作品，保持独立。
- `404修理站/`：第一本正式长期作品。
- `道友你这天命与我有缘/`：《道友你这天命与我有缘》的独立项目目录；已绑定独立番茄书号，默认每日 18:30、20:30 排期。

## 常用命令

### 一键批量创作、上传与排期

在 VS Code 终端进入本项目根目录后，只需输入命令和本次章节数：

```powershell
python xiaoshuo 1
python xiaoshuo 3
python xiaoshuo 5
```

默认更新 `manager_config.json` 的 `default_book_id`。也可以指定一本，或让全部已启用小说按优先级严格串行执行（每本各更新 N 章）：

```powershell
python xiaoshuo 2 --book cosmic-404
python xiaoshuo 2 --book free-sky
python xiaoshuo 2 --all
python xiaoshuo 2 --all --dry-run
```

`--all` 不并发写作：全局运行锁要求逐本完成，某本失败时会安全停止，尚未开始的小说不受影响。`mode: write_only` 的新书只在本地写作、质检、归档并同步 Git，不会访问或误用其他作品的番茄后台。

管理器通过 `manager_config.json` 的 `writing_policy.new_item_explanation` 统一约束新道具写法：首次出现先直说用途并尽快触发效果，隔章再用时先做一句情境化回顾；只把来源、上限和隐藏代价留作悬念。该规则会进入 `session` 交接包并由通用质量评分卡复核。

### 每本书独立的图片资产体系

两个正式作品现在各自维护 `images/catalog.json`，图片只保存在对应小说的 `images/characters`、`items`、`locations`、`creatures`、`organizations` 或 `scenes` 分类目录中，禁止跨书复用。新增作品注册到 `manager_config.json` 时也必须建立自己的 `images/catalog.json`，否则 `validate` 会直接报错。

Codex 写每章前会先查本书图片目录。每章只选择一个最需要视觉解释的新人物、关键道具、地点、异兽、组织形象或场景配图；普通背景物不登记，同章其他新实体靠正文白话解释。同一实体已有参考图时直接沿用，不会再次生图。图片会插在正文首次解释之后，Markdown 使用 `../images/...` 的本书相对路径。

生图固定通过 `browser_image_worker.py` 使用独立的已登录 Chrome 网页 GPT 完成，不消耗 Codex 图片生成额度，也不要求 API Key。网页成图直接下载、登记并上传，不再调用 `view_image` 做 Codex 内容复核。程序只复核文件头、SHA-256、像素画幅、分类目录、单图上限、正文引用和平台上传回显；这些机械项失败时才保留草稿。完整规范见 `shared/image_workflow.md`。

番茄正文保持纯文本。本章唯一图片会自动上传到页面底部“作者有话说”：悬停“添加”→“添加图文”→左下角图片按钮→本地文件上传→“确定”，并在出现图片预览后才允许进入下一步。首次运行升级后的任一本书时，如果本章没有更重要的新实体，可用唯一图片名额补齐尚无参考图的主角。检查所有书的图片目录与章节状态：

```powershell
python .\fanqie_novel_manager.py validate
```

### 无大纲读者反向验收

系统不会再把大纲中的一句关键事件直接扩写成一串“作者觉得理所当然”的动作。所有新章节都必须在正文中补齐“承接→问题→依据→判断→行动→结果”：新事件为什么此刻发生、人物凭什么知道、方案为什么有效、失败会怎样以及最后改变了什么，都要让没有大纲的读者从正文中看见。

完稿后，Codex 会停止读取大纲、设定表、连续性账本和写作提示，只用最终正文回答六个问题：前情承接、眼前问题、判断依据、行动原理、结果代价和结尾钩子。每个答案必须带逐字存在于正文的证据，并检查本章必要的新名词是否已有白话解释。结果写入每本书独立的 `reader_checks/NNNN.json`，同时绑定正文 SHA-256；正文后来改过就必须重新验收。

《404修理站》从第 54 章启用，《道友你这天命与我有缘》从第 17 章启用。验收文件缺失、正文证据不存在、仍有未解释名词、正文哈希变化或任一题需要作者额外解释时，管理器只允许保留草稿，不允许归档、推进章节号或上传。完整规则见 `shared/reader_gate.md`。

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
只要章节在番茄显示“审核中”“待发布”或“已发布”，平台任务即视为成功。若此后
GitHub 暂时断网，本地提交和待推送路径会保留并在后续运行中重试，但不会重复上传
已经进入审核或排期的章节。
需要观察浏览器步骤时使用：

```powershell
python xiaoshuo --resume <job-id> --debug-browser
```

调试模式遇到错误会保留 Chrome 窗口，查看页面后回到终端按 Enter 才会关闭。

### 打赏加更：提前已有排期

老板打赏、需要今天额外多发章节时，使用第二种命令：

```powershell
python xiaoshuo --reward 3
```

加更同样支持选择书籍或全部执行：

```powershell
python xiaoshuo --reward 3 --book cosmic-404
python xiaoshuo --reward 1 --all --dry-run
```

`--reward --all` 只选择注册表中 `manual_extra_chapters_supported: true` 的已启用作品；未绑定平台排期的本地创作书会被跳过。

数字表示从未来排期中提前到今天的章节数。程序按原排期从早到晚选择章节，默认设置为
当前时间后 45 分钟并向上取整到 10 分钟；所有选中章节可使用同一发布时间。需要明确
指定时间时可运行 `python xiaoshuo --reward 3 --reward-time 18:30`。建议先追加
`--dry-run` 查看会调整哪些章节。

加更章节从未来队列抽出后，程序会把其后全部已定时章节按原有发布时间槽位向前补齐，
保证未来每天的常规发布节奏不留空档。例如原来每天两章，加更 1 章后，后续队列整体
前移 1 个章节槽位；只有日期或时间实际发生变化的章节才访问番茄修改。重排计划保存在
本地可恢复任务中，如果平台限额或网络导致中断，再次运行相同的 `--reward N` 会继续
剩余重排，不会再次额外抽取 N 章。

程序从章节管理第 1 页开始，按“章节号 + 完整标题”逐页查找；每页 15 条、首页新增记录
将旧章节挤到后页都不会影响定位。找到后点击该行操作列中的 `span` 编辑入口，不改标题
和正文；已提交章节先关闭“发布时间前30分钟提交修改”的“我知道了”提示，再走
“下一步 → 基础检测 → AI=是 → 定时发布 → 确认发布”。章节管理页再次
显示目标章为“待发布”“审核中”或“已发布”后，才把新日期、时间和原排期历史写回本地
`batch_schedule_*.json`、`chapter_state.json` 与当日日志，并提交、推送 Git。未被选中的
后续章节会按原槽位整体前移，以维持常规排期密度。

如果番茄返回“提交字数超出每日上限”，程序会保留原平台排期和本地数据并安全停止；
该限制属于平台当天额度，命令不会重复确认或尝试绕过。

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
5. 共享层只负责格式、连续性、因果、图片和发布安全等底层门禁；情绪、玩梗、次元壁互动等创作规则与经验只写入各书自己的 `style_guide.md` 和运行日志，不跨书继承。
