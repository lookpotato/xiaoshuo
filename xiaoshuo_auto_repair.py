"""One bounded Codex handoff after a novel batch command fails."""

from __future__ import annotations

import os
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

import novel_codex_model


ROOT = Path(__file__).resolve().parent
REPAIR_ROOT = ROOT / ".auto_repairs"


def repair_failed_book(book_id: str, command: list[str], failure_log: Path) -> bool:
    """Ask a fresh coding agent to fix the cause; the caller performs the retry."""
    if os.environ.get("XIAOSHUO_AUTO_REPAIR_ACTIVE") == "1":
        return False
    from xiaoshuo_on_demand import resolve_codex

    codex = resolve_codex()
    folder = REPAIR_ROOT / f"{datetime.now():%Y%m%d-%H%M%S}-{book_id}-{uuid.uuid4().hex[:8]}"
    folder.mkdir(parents=True, exist_ok=False)
    summary = folder / "summary.md"
    agent_log = folder / "codex.log"
    prompt = f"""你是本地小说流水线的自动故障修复代理。用户已授权本次失败后自动接力，不需要向用户索取提示词。

先读仓库 AGENTS.md、失败日志 `{failure_log.resolve()}`、入口代码和相关运行包，找出本次 `{book_id}` 失败的具体根因。原命令为 {command!r}。
只修复有证据支持的代码、配置或本书当前运行工件；保留其他小说及用户未提交的改动。不能放松章节质量、时间线和发布校验，不能为了让命令成功而伪造章节或跳过检查。运行相关测试或校验。
不要再次执行原命令，也不要直接上传番茄；外层会在你成功退出后只重试原命令一次，由原发布配置决定是否上传。不得更改上传开关或凭据。按 AGENTS.md 只提交本次修复文件并尝试推送；若推送失败，在总结里说明。
若无法安全修复，清楚报告根因并以非零退出码结束。最后简短说明改了什么、验证结果和剩余限制。
"""
    env = os.environ.copy()
    env["XIAOSHUO_AUTO_REPAIR_ACTIVE"] = "1"
    args = [
        *novel_codex_model.exec_prefix(codex, book_id, ROOT),
        "--ephemeral", "-C", str(ROOT), "--sandbox", "workspace-write",
        "--config", 'approval_policy="never"',
        "--output-last-message", str(summary), "-",
    ]
    with agent_log.open("w", encoding="utf-8") as output:
        result = subprocess.run(
            args, cwd=ROOT, input=prompt, text=True, encoding="utf-8",
            errors="replace", stdout=output, stderr=subprocess.STDOUT, env=env,
            timeout=1200,
        )
    print(f"自动修复日志：{agent_log}", flush=True)
    if summary.is_file():
        print(summary.read_text(encoding="utf-8-sig")[-2000:], flush=True)
    return result.returncode == 0
