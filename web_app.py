#!/usr/bin/env python3
"""Local full-stack dashboard for the Fanqie novel manager."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import uuid
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import fanqie_novel_manager as manager


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web" / "dist"
RUNTIME_ROOT = ROOT / ".web_runtime"
RUNS_ROOT = RUNTIME_ROOT / "runs"
SAFE_JOB_ID = re.compile(r"^[0-9A-Za-z_-]{8,80}$")
RUN_LOCK = threading.Lock()
RUN_PROCESSES: dict[str, subprocess.Popen[str]] = {}
MAX_LOG_BYTES = 256 * 1024
MAX_LOG_LINE_LENGTH = 1600
OPERATIONAL_LOG_PREFIXES = (
    "[",
    "本批进度",
    "正在调用",
    "正在排期",
    "当天",
    "已完成本地",
    "已记录",
    "本次运行失败",
    "项目校验失败",
    "本地归档门禁",
    "人物线门禁",
    "并行人物线门禁",
    "批量执行",
    "警告",
    "错误",
    "失败",
    "字数：",
    "校验：",
    "Traceback",
    "File \"",
    "usage:",
    "fatal:",
    "error:",
)
SAFE_JSON_LOG_FIELDS = re.compile(
    r'^\s*"(?:type|at|chapter|platform_status|status|result|message|finished_at|'
    r'completed|target|git_push_pending|publish_fanqie|sync_git)"\s*:'
)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temp, path)


def config() -> dict:
    return manager.config()


def registered_book(book_id: str) -> dict:
    return manager.find_book(config(), book_id)


def project_for(book: dict) -> Path:
    project = (ROOT / str(book["path"])).resolve()
    if ROOT != project and ROOT not in project.parents:
        raise ValueError("书籍目录越界")
    return project


def chapter_rows(project: Path, limit: int = 80) -> list[dict]:
    rows: list[dict] = []
    for path in (project / "chapters").glob("*.md"):
        match = re.match(r"^(\d+)-(.+)\.md$", path.name)
        if not match:
            continue
        number = int(match.group(1))
        title = match.group(2)
        try:
            first = path.read_text(encoding="utf-8").splitlines()[0].strip()
            title_match = re.match(r"^#\s*第\s*\d+\s*章\s*(.*)$", first)
            if title_match and title_match.group(1).strip():
                title = title_match.group(1).strip()
        except (OSError, IndexError):
            pass
        rows.append(
            {
                "number": number,
                "title": title,
                "filename": path.name,
                "updated_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
            }
        )
    rows.sort(key=lambda item: item["number"], reverse=True)
    return rows[: max(1, min(limit, 300))]


def compact_job(job: dict) -> dict:
    completed = job.get("completed_chapters", [])
    return {
        "id": job.get("id"),
        "book_id": job.get("book_id"),
        "target": int(job.get("target_chapters", 0) or 0),
        "completed": len(completed) if isinstance(completed, list) else 0,
        "status": job.get("status", "unknown"),
        "result": job.get("result"),
        "message": str(job.get("message", ""))[:1200],
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "finished_at": job.get("finished_at"),
        "events": job.get("events", [])[-12:],
        "options": job.get("run_options", {}),
    }


def recent_jobs(limit: int = 16) -> list[dict]:
    if not manager.JOB_DIR.is_dir():
        return []
    jobs = []
    paths = sorted(
        manager.JOB_DIR.glob("*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in paths[: max(1, min(limit, 100))]:
        job = read_json(path)
        if isinstance(job, dict):
            jobs.append(compact_job(job))
    return jobs


def book_summary(book: dict, include_chapters: bool = True) -> dict:
    project = project_for(book)
    state = read_json(project / "chapter_state.json", {}) or {}
    errors = manager.validate_book(book, require_publish_complete=False)
    chapters = chapter_rows(project, 60) if include_chapters else []
    publish_text = ""
    try:
        publish_text = (project / "publish_config.md").read_text(encoding="utf-8")
    except OSError:
        pass
    writer_url = re.search(
        r"(?m)^\s*fanqie_writer_url\s*:\s*(\S+)\s*$", publish_text
    )
    platform_book_id = re.search(r"(?m)^\s*book_id\s*:\s*(\S+)\s*$", publish_text)
    fanqie_ready = bool(
        writer_url
        and writer_url.group(1).upper() != "UNBOUND"
        and platform_book_id
        and platform_book_id.group(1).upper() != "UNBOUND"
    )
    return {
        "id": book["id"],
        "title": book.get("title", book["id"]),
        "mode": book.get("mode", "unknown"),
        "enabled": bool(book.get("enabled", True)),
        "daily_target": int(book.get("daily_chapter_target", 1)),
        "publish_times": book.get("default_publish_times", []),
        "last_completed_chapter": int(state.get("last_completed_chapter", 0) or 0),
        "next_chapter_number": int(state.get("next_chapter_number", 1) or 1),
        "last_uploaded_chapter": int(state.get("last_uploaded_chapter", 0) or 0),
        "upload_status": state.get("upload_status", state.get("last_uploaded_status")),
        "fanqie_ready": fanqie_ready,
        "notes_for_next_chapter": state.get("notes_for_next_chapter", ""),
        "validation": {"ok": not errors, "errors": errors[:40]},
        "chapter_count": len(list((project / "chapters").glob("*.md"))),
        "chapters": chapters,
    }


def overview() -> dict:
    data = config()
    books = [book_summary(book) for book in data.get("books", []) if book.get("enabled")]
    return {
        "name": "番茄小说工作台",
        "generated_at": datetime.now().astimezone().isoformat(),
        "default_book_id": data.get("default_book_id"),
        "books": books,
        "jobs": recent_jobs(),
        "runs": list_runs(),
    }


def chapter_document(book_id: str, number: int) -> dict:
    book = registered_book(book_id)
    project = project_for(book)
    matches = list((project / "chapters").glob(f"{number:04d}-*.md"))
    if len(matches) != 1:
        raise ValueError(f"找不到第 {number} 章")
    text = matches[0].read_text(encoding="utf-8")
    metadata_marker = re.search(r"\n---\s*\n+\s*##\s+Metadata\b", text, re.I)
    narrative = text[: metadata_marker.start()] if metadata_marker else text
    return {
        "book_id": book_id,
        "number": number,
        "filename": matches[0].name,
        "content": narrative.strip(),
    }


def run_metadata_path(run_id: str) -> Path:
    return RUNS_ROOT / f"{run_id}.json"


def refresh_run(meta: dict) -> dict:
    run_id = str(meta.get("id", ""))
    with RUN_LOCK:
        process = RUN_PROCESSES.get(run_id)
        if process is not None:
            code = process.poll()
            if code is not None and meta.get("status") == "running":
                meta["status"] = "success" if code == 0 else "failed"
                meta["exit_code"] = code
                meta["finished_at"] = datetime.now().astimezone().isoformat()
                write_json(run_metadata_path(run_id), meta)
                RUN_PROCESSES.pop(run_id, None)
        elif meta.get("status") == "running":
            pid = int(meta.get("pid", 0) or 0)
            if pid and not manager.process_is_running(pid):
                log_path = RUNS_ROOT / f"{run_id}.log"
                log_tail = ""
                if log_path.is_file():
                    with log_path.open("rb") as handle:
                        handle.seek(max(0, log_path.stat().st_size - 64 * 1024))
                        log_tail = handle.read().decode("utf-8", errors="replace")
                if '"result": "failed"' in log_tail or "本次运行失败" in log_tail:
                    meta["status"] = "failed"
                elif '"result": "success"' in log_tail:
                    meta["status"] = "success"
                else:
                    meta["status"] = "finished"
                meta["finished_at"] = datetime.now().astimezone().isoformat()
                meta["recovered_after_restart"] = True
                write_json(run_metadata_path(run_id), meta)
    return meta


def list_runs(limit: int = 12) -> list[dict]:
    if not RUNS_ROOT.is_dir():
        return []
    rows = []
    paths = sorted(
        RUNS_ROOT.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True
    )
    for path in paths[:limit]:
        meta = read_json(path)
        if isinstance(meta, dict):
            rows.append(refresh_run(meta))
    return rows


def operational_log_lines(content: str) -> tuple[list[str], bool]:
    """Keep diagnostics and progress while removing model prose and file diffs."""
    kept: list[str] = []
    hidden = False
    in_diff = False
    for raw_line in content.splitlines():
        line = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", raw_line).rstrip()
        stripped = line.strip()
        if stripped.startswith("diff --git "):
            in_diff = True
            hidden = True
            continue
        operational = (
            stripped.startswith(OPERATIONAL_LOG_PREFIXES)
            or bool(SAFE_JSON_LOG_FIELDS.match(line))
            or "退出码" in stripped
            or "Exception:" in stripped
            or re.search(r"\b(?:ERROR|WARN)\b", stripped) is not None
            or re.match(
                r"(?i)^(?:password|passwd|token|cookie|authorization)\b", stripped
            )
            is not None
        )
        if in_diff and not operational:
            hidden = True
            continue
        if operational:
            in_diff = False
            kept.append(line)
        elif stripped:
            hidden = True
    if hidden:
        kept.insert(0, "[已隐藏小说正文和补丁内容，仅显示进度与报错]")
    return kept, hidden


def run_log(run_id: str, tail_lines: int = 240) -> dict:
    if not SAFE_JOB_ID.fullmatch(run_id):
        raise ValueError("run id 格式不正确")
    tail_lines = max(20, min(int(tail_lines), 500))
    path = RUNS_ROOT / f"{run_id}.log"
    if not path.is_file():
        raise ValueError("找不到该运行日志")
    with path.open("rb") as handle:
        size = path.stat().st_size
        start = max(0, size - MAX_LOG_BYTES)
        handle.seek(start)
        raw = handle.read()
    content = raw.decode("utf-8", errors="replace")
    if start:
        content = content.split("\n", 1)[-1]
    lines, content_hidden = operational_log_lines(content)
    filtered_line_count = len(lines)
    lines = lines[-tail_lines:]
    hidden_notice = "[已隐藏小说正文和补丁内容，仅显示进度与报错]"
    if content_hidden and hidden_notice not in lines:
        lines.insert(0, hidden_notice)
    clipped_lines = []
    for line in lines:
        line = re.sub(
            r"(?i)\b(password|passwd|token|cookie|authorization)\b\s*[:=]\s*\S+",
            r"\1=<已隐藏>",
            line,
        )
        if len(line) > MAX_LOG_LINE_LENGTH:
            line = line[:MAX_LOG_LINE_LENGTH] + "… [本行过长，已截断]"
        clipped_lines.append(line)
    meta = read_json(run_metadata_path(run_id), {}) or {}
    return {
        "id": run_id,
        "status": refresh_run(meta).get("status", "unknown") if meta else "unknown",
        "content": "\n".join(clipped_lines),
        "truncated": bool(start or filtered_line_count > tail_lines),
        "content_hidden": content_hidden,
        "updated_at": datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(),
    }


def launch_command(command: list[str], kind: str, label: str) -> dict:
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    log_path = RUNS_ROOT / f"{run_id}.log"
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["PYTHONUTF8"] = "1"
    log_handle = log_path.open("w", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
            env=child_env,
        )
    finally:
        log_handle.close()
    meta = {
        "id": run_id,
        "kind": kind,
        "label": label,
        "status": "running",
        "pid": process.pid,
        "started_at": datetime.now().astimezone().isoformat(),
        "finished_at": None,
        "exit_code": None,
    }
    write_json(run_metadata_path(run_id), meta)
    with RUN_LOCK:
        RUN_PROCESSES[run_id] = process
    return meta


def launch_generation(payload: dict) -> dict:
    count = int(payload.get("count", 1))
    if not 1 <= count <= 20:
        raise ValueError("单次生成章数必须在 1—20 之间")
    scope = str(payload.get("book_id", ""))
    sync_git = payload.get("sync_git", False)
    publish_fanqie = payload.get("publish_fanqie", False)
    if not isinstance(sync_git, bool) or not isinstance(publish_fanqie, bool):
        raise ValueError("sync_git 和 publish_fanqie 必须是布尔值")
    selected_books: list[dict]
    if scope == "all":
        selected_books = [
            book for book in config().get("books", []) if book.get("enabled", True)
        ]
    else:
        selected_books = [registered_book(scope)]
    if publish_fanqie:
        unbound = [
            book.get("title", book["id"])
            for book in selected_books
            if not book_summary(book, include_chapters=False)["fanqie_ready"]
        ]
        if unbound:
            raise ValueError(
                "以下作品尚未绑定番茄正式环境：" + "、".join(unbound)
            )
    command = [sys.executable, str(ROOT / "xiaoshuo.py"), str(count)]
    command.append("--sync-git" if sync_git else "--no-sync-git")
    command.append(
        "--publish-fanqie" if publish_fanqie else "--no-publish-fanqie"
    )
    if scope == "all":
        command.append("--all")
        label = f"全部作品各生成 {count} 章"
    else:
        book = selected_books[0]
        command.extend(["--book", book["id"]])
        label = f"《{book.get('title', book['id'])}》生成 {count} 章"
    deliveries = ["本地归档"]
    if publish_fanqie:
        deliveries.append("番茄正式环境")
    if sync_git:
        deliveries.append("Git")
    label += " · " + " + ".join(deliveries)
    return launch_command(command, "generate", label)


def launch_resume(payload: dict) -> dict:
    job_id = str(payload.get("job_id", ""))
    if not SAFE_JOB_ID.fullmatch(job_id):
        raise ValueError("job id 格式不正确")
    job = manager.read_job(job_id)
    book_id = str(job.get("book_id", ""))
    registered_book(book_id)
    command = [
        sys.executable,
        str(ROOT / "xiaoshuo.py"),
        "--resume",
        job_id,
        "--book",
        book_id,
    ]
    return launch_command(command, "resume", f"续跑任务 {job_id}")


class AppHandler(BaseHTTPRequestHandler):
    server_version = "FanqieWorkbench/1.0"

    def log_message(self, format_string: str, *args) -> None:
        sys.stdout.write("[web] " + format_string % args + "\n")

    def send_json(self, data, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, message: str, status: int) -> None:
        self.send_json({"ok": False, "error": message}, status)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/overview":
                self.send_json(overview())
                return
            if parsed.path == "/api/jobs":
                limit = int(parse_qs(parsed.query).get("limit", ["30"])[0])
                self.send_json({"jobs": recent_jobs(limit)})
                return
            if parsed.path == "/api/runs":
                self.send_json({"runs": list_runs()})
                return
            if parsed.path == "/api/run-log":
                query = parse_qs(parsed.query)
                run_id = query.get("run_id", [""])[0]
                tail_lines = int(query.get("tail", ["240"])[0])
                self.send_json(run_log(run_id, tail_lines))
                return
            if parsed.path == "/api/chapter":
                query = parse_qs(parsed.query)
                book_id = query.get("book_id", [""])[0]
                number = int(query.get("number", ["0"])[0])
                self.send_json(chapter_document(book_id, number))
                return
            self.serve_static(parsed.path)
        except (ValueError, KeyError) as exc:
            self.send_error_json(str(exc), HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.send_error_json(f"服务器处理失败：{exc}", HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        origin = self.headers.get("Origin")
        if origin and not re.match(r"^http://(?:127\.0\.0\.1|localhost)(?::\d+)?$", origin):
            self.send_error_json("拒绝非本机页面请求", HTTPStatus.FORBIDDEN)
            return
        if "application/json" not in self.headers.get("Content-Type", ""):
            self.send_error_json("请求必须使用 application/json", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 64 * 1024)
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if parsed.path == "/api/run":
                self.send_json({"ok": True, "run": launch_generation(payload)}, HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/resume":
                self.send_json({"ok": True, "run": launch_resume(payload)}, HTTPStatus.ACCEPTED)
                return
            self.send_error_json("接口不存在", HTTPStatus.NOT_FOUND)
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self.send_error_json(str(exc), HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.send_error_json(f"启动失败：{exc}", HTTPStatus.INTERNAL_SERVER_ERROR)

    def serve_static(self, request_path: str) -> None:
        if not WEB_ROOT.is_dir():
            self.send_error_json(
                "React 前端尚未构建；请在 web 目录运行 npm install 和 npm run build",
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        candidate = (WEB_ROOT / relative).resolve()
        if WEB_ROOT != candidate and WEB_ROOT not in candidate.parents:
            self.send_error_json("路径越界", HTTPStatus.FORBIDDEN)
            return
        if not candidate.is_file():
            candidate = WEB_ROOT / "index.html"
        body = candidate.read_bytes()
        mime = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if mime.startswith("text/") or mime in {"application/javascript", "application/json"}:
            mime += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    parser = argparse.ArgumentParser(description="启动番茄小说本地前后端工作台")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("端口必须在 1024—65535 之间")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), AppHandler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"番茄小说工作台已启动：{url}")
    print("按 Ctrl+C 停止；服务只监听本机。")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
