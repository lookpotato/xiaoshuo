# 2026-07-28 固定命令批量启动器升级

## 目标

- 在 VS Code 项目终端通过 `python xiaoshuo 5` 启动独立批次任务。
- 不依赖旧聊天记录，由项目 session、排期文件和 job 文件恢复状态。
- 严格串行执行草稿恢复、写作、上传、排期、平台核验和本地回写。

## 实现

- 新增 `xiaoshuo` 与 `xiaoshuo.py` 两个等价入口。
- 管理器新增 `run`、`doctor`、`job-progress`、`job-finish`、`job-status`。
- `.manager_jobs/` 保存本地任务、提示和最终报告，并从 Git 排除。
- 新任务优先使用 Codex 桌面版配置登记的 CLI，避免旧 PATH 版本不兼容当前模型。
- 启动器持有全局运行锁；Codex 正常结束、失败或被中断时均兜底释放。
- 支持 `python xiaoshuo --resume <job-id>` 从同一 job 的剩余章节继续。

## 验证

- Python 语法编译通过。
- 项目结构校验通过。
- `python xiaoshuo --check` 全部通过。
- 两个入口的 dry-run 均未创建 job、未访问番茄。
- job 进度、成功门槛、启动失败解锁和 `batch_success` 解锁测试通过。
- 独立 `codex exec` 使用桌面版 `codex-cli 0.146.0-alpha.3.1` 返回 `READY`。

## 安全边界

- 仍以番茄章节管理页的待发布、审核中或已发布为成功信号。
- 登录失效、验证码、二维码、风控、政策警告或陌生确认框立即停止。
- 不读取、保存或提交 Cookie、Token、密码、验证码。
