import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import SettingsWorkspace from "./components/SettingsWorkspace";

const statusLabel = (status, result) => ({
  running: "运行中", queued: "排队中", finished: result === "success" ? "已完成" : "已结束",
  success: "已完成", failed: "失败", blocked: "需处理",
}[status] || status || "未知");

function Sidebar({ books, selected, onSelect, view, onView }) {
  return <aside className="sidebar">
    <div className="brand"><div className="brand-mark">番</div><div><strong>小说工作台</strong><span>REACT STUDIO</span></div></div>
    <nav className="section-nav"><button className={view === "workbench" ? "active" : ""} onClick={() => onView("workbench")}>创作工作台</button><button className={view === "book-settings" ? "active" : ""} onClick={() => onView("book-settings")}>小说设置</button><button className={view === "system-settings" ? "active" : ""} onClick={() => onView("system-settings")}>系统设置</button></nav>
    <nav className="book-nav">{books.map((book) => <button className={`book-link ${book.id === selected ? "active" : ""}`} key={book.id} title={book.title} onClick={() => { onSelect(book.id); if (view === "system-settings") onView("book-settings"); }}><strong className="book-full-title">{book.title}</strong><strong className="book-abbr">{book.id === "cosmic-404" ? "404" : "道友"}</strong><small>完成 {book.last_completed_chapter} 章</small></button>)}</nav>
    <div className="sidebar-foot"><span className="live-dot" /><div><strong>本地服务</strong><small>React 前端 · Python 后端</small></div></div>
  </aside>;
}

function Metrics({ book }) {
  const valid = book.validation.ok;
  return <section className="hero-grid">
    <article className="metric hero-metric"><span>已完成</span><strong>{String(book.last_completed_chapter).padStart(2, "0")}</strong><small>本地归档章节</small></article>
    <article className="metric"><span>下一章</span><strong>{String(book.next_chapter_number).padStart(2, "0")}</strong><small>{book.mode === "write_only" ? "仅本地创作" : "创作后处理发布"}</small></article>
    <article className="metric"><span>项目门禁</span><strong style={{ color: valid ? "var(--green)" : "var(--red)" }}>{valid ? "通过" : `${book.validation.errors.length} 项`}</strong><small>{valid ? "项目结构与门禁正常" : "需要处理后再运行"}</small></article>
    <article className="metric"><span>平台进度</span><strong>{String(book.last_uploaded_chapter || 0).padStart(2, "0")}</strong><small>最近上传章</small></article>
  </section>;
}

function CreatePanel({ books, selectedBookId, onNotice, onRefresh, onRunStarted }) {
  const [count, setCount] = useState(1);
  const [scope, setScope] = useState(selectedBookId);
  const [syncGit, setSyncGit] = useState(false);
  const [publishFanqie, setPublishFanqie] = useState(false);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState("");
  useEffect(() => setScope(selectedBookId), [selectedBookId]);
  const selectedBooks = scope === "all" ? books : books.filter((book) => book.id === scope);
  const fanqieReady = selectedBooks.length > 0 && selectedBooks.every((book) => book.fanqie_ready);
  useEffect(() => { if (!fanqieReady) setPublishFanqie(false); }, [fanqieReady]);

  async function start() {
    if (busy) return;
    setBusy(true); setFeedback("正在创建后台任务……");
    try {
      const result = await api("/api/run", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ book_id: scope, count, sync_git: syncGit, publish_fanqie: publishFanqie }) });
      setFeedback(`已启动：${result.run.label}`); onNotice("任务已启动，后端控制台已切换到本次运行"); onRunStarted(result.run); onRefresh();
    } catch (error) { setFeedback(error.message); onNotice(error.message); }
    finally { setBusy(false); }
  }

  return <article className="panel action-panel">
    <div className="panel-head"><div><p className="eyebrow">CREATE</p><h2>运行配置</h2></div><span className="local-badge">API 任务</span></div>
    <p className="panel-copy">一次配置本批章数和交付位置。正文始终先通过本地门禁，再按开关处理 Git 与番茄正式环境。</p>
    <div className="run-controls">
      <label>本次章数<div className="stepper"><button aria-label="减少章数" onClick={() => setCount(Math.max(1, count - 1))}>−</button><input aria-label="本次章数" type="number" min="1" max="20" value={count} onChange={(e) => setCount(Math.max(1, Math.min(20, Number(e.target.value) || 1)))} /><button aria-label="增加章数" onClick={() => setCount(Math.min(20, count + 1))}>＋</button></div></label>
      <label>执行范围<select aria-label="执行范围" value={scope} onChange={(e) => setScope(e.target.value)}>{books.map((book) => <option key={book.id} value={book.id}>{book.title}</option>)}<option value="all">全部作品（各自生成）</option></select></label>
    </div>
    <div className="delivery-options">
      <label className={`delivery-option ${syncGit ? "selected" : ""}`}>
        <span><strong>同步 Git</strong><small>本批完成后提交并推送本批改动</small></span>
        <input aria-label="同步 Git" type="checkbox" checked={syncGit} onChange={(event) => setSyncGit(event.target.checked)} />
      </label>
      <label className={`delivery-option production ${publishFanqie ? "selected" : ""} ${!fanqieReady ? "disabled" : ""}`}>
        <span><strong>更新番茄正式环境</strong><small>{fanqieReady ? "上传并按现有发布规则提交" : "所选作品尚未绑定番茄账号或书号"}</small></span>
        <input aria-label="更新番茄正式环境" type="checkbox" checked={publishFanqie} disabled={!fanqieReady} onChange={(event) => setPublishFanqie(event.target.checked)} />
      </label>
    </div>
    <div className="run-summary"><span>本批结果</span><strong>本地 {count} 章{publishFanqie ? " + 番茄正式环境" : ""}{syncGit ? " + Git" : ""}</strong></div>
    <button className="button primary" disabled={busy} onClick={start}><span>{busy ? "正在启动" : "启动生成"}</span><b>→</b></button>
    <p className="feedback">{feedback}</p>
  </article>;
}

function ChapterList({ book, onOpen }) {
  return <article className="panel chapter-panel">
    <div className="panel-head"><div><p className="eyebrow">ARCHIVE</p><h2>章节归档</h2></div><span className="count-label">{book.chapter_count} 章</span></div>
    <div className="chapter-list">{book.chapters.length ? book.chapters.map((chapter) => <button className="chapter-row" key={chapter.number} onClick={() => onOpen(book.id, chapter.number)}><span className="chapter-number">{String(chapter.number).padStart(4, "0")}</span><span className="chapter-title">{chapter.title}</span><span className="chapter-arrow">↗</span></button>) : <div className="empty">还没有归档章节</div>}</div>
  </article>;
}

function Activity({ data, bookId, onResume, onOpenLog, selectedRunId }) {
  const jobs = data.jobs.filter((job) => job.book_id === bookId).slice(0, 5);
  return <article className="panel jobs-panel">
    <div className="panel-head"><div><p className="eyebrow">ACTIVITY</p><h2>任务进度</h2></div></div>
    <div className="run-list">{data.runs.length ? data.runs.slice(0, 4).map((run) => <div className={`activity ${run.id === selectedRunId ? "active-run" : ""}`} key={run.id}><div className="activity-top"><strong>{run.label}</strong><span className={`status ${run.status}`}>{statusLabel(run.status)}</span></div><p>{run.started_at}{run.exit_code != null ? ` · 退出码 ${run.exit_code}` : ""}</p><button className="log-button" onClick={() => onOpenLog(run.id)}>在控制台查看</button></div>) : <div className="empty">暂无网页启动记录</div>}</div>
    <div className="job-list">{jobs.map((job) => { const ratio = job.target ? Math.min(100, Math.round(job.completed / job.target * 100)) : 0; const resumable = ["failed", "partial", "blocked"].includes(job.result); const delivery = [job.options?.publish_fanqie && "番茄", job.options?.sync_git && "Git"].filter(Boolean).join(" + ") || "仅本地"; return <div className="activity" key={job.id}><div className="activity-top"><strong>{job.id}</strong><span className={`status ${job.result || job.status}`}>{statusLabel(job.status, job.result)}</span></div><div className="progress"><i style={{ width: `${ratio}%` }} /></div><p>{job.completed}/{job.target} 章 · {delivery}{job.message ? ` · ${job.message}` : ""}</p>{resumable && <button className="resume" onClick={() => onResume(job.id)}>按原配置续跑</button>}</div>; })}</div>
  </article>;
}

function BackendLog({ run, runs, onSelect }) {
  const [log, setLog] = useState(null);
  const [error, setError] = useState("");
  const [tailLimit, setTailLimit] = useState(300);
  const outputRef = useRef(null);
  const loadLog = useCallback(async () => {
    if (!run) return;
    try {
      const next = await api(`/api/run-log?run_id=${encodeURIComponent(run.id)}&tail=${tailLimit}`);
      setLog(next); setError("");
    } catch (nextError) { setError(nextError.message); }
  }, [run?.id, tailLimit]);

  useEffect(() => {
    setLog(null); setError("");
    if (!run) return undefined;
    loadLog();
    const timer = window.setInterval(loadLog, run.status === "running" ? 2000 : 5000);
    return () => window.clearInterval(timer);
  }, [run?.id, run?.status, loadLog]);
  useEffect(() => {
    if (outputRef.current) outputRef.current.scrollTop = outputRef.current.scrollHeight;
  }, [log?.content]);

  return <article className="panel backend-log-panel" aria-label="后端实时控制台">
    <div className="panel-head"><div><p className="eyebrow">BACKEND CONSOLE</p><h2>后端实时控制台</h2></div><div className="log-actions">{run && <span className={`status ${run.status}`}>{statusLabel(run.status)}</span>}<label className="log-limit">上下文<select aria-label="日志上下文上限" value={tailLimit} onChange={(event) => setTailLimit(Number(event.target.value))}><option value="100">100 行</option><option value="300">300 行</option><option value="500">500 行</option></select></label><button className="resume" disabled={!run} onClick={loadLog}>立即刷新</button></div></div>
    {runs.length > 0 && <select className="run-selector" aria-label="选择后端运行记录" value={run?.id || ""} onChange={(event) => onSelect(event.target.value)}>{runs.map((item) => <option value={item.id} key={item.id}>{statusLabel(item.status)} · {item.label} · {item.id}</option>)}</select>}
    {run ? <><p className="log-caption">{run.label} · {run.id}{log?.truncated ? " · 已按上下文上限截取" : ""}{log?.content_hidden ? " · 正文已过滤" : ""} · 最大 500 行 / 256 KB</p>{log?.error_summary && <div className="log-error-summary"><strong>失败原因</strong><span>{log.error_summary}</span></div>}<pre className="backend-log" ref={outputRef}>{error || log?.content || "任务刚启动，等待后端输出……"}</pre></> : <div className="empty">启动任务后，这里会持续显示 Python 输出和错误信息</div>}
  </article>;
}

function ValidationPanel({ validation }) {
  return <article className="panel gate-panel"><div className="panel-head"><div><p className="eyebrow">QUALITY</p><h2>校验信息</h2></div></div><div className="gate-details">{validation.ok ? <div className="gate-ok">当前项目校验通过</div> : validation.errors.map((error, index) => <div className="gate-error" key={`${index}-${error}`}>{error}</div>)}</div></article>;
}

function ReaderDialog({ chapter, onClose }) {
  const ref = useRef(null);
  useEffect(() => { if (chapter && ref.current && !ref.current.open) ref.current.showModal(); if (!chapter && ref.current?.open) ref.current.close(); }, [chapter]);
  const lines = chapter?.content.split(/\r?\n/) || [];
  return <dialog className="reader-dialog" ref={ref} onClose={onClose} onClick={(event) => event.target === ref.current && onClose()}><div className="reader-head"><div><p className="eyebrow">CHAPTER READER</p><h2>{lines[0]?.replace(/^#\s*/, "") || "章节"}</h2></div><button className="icon-button" aria-label="关闭" onClick={onClose}>×</button></div><article className="reader-content">{lines.slice(1).filter((line) => line.trim()).map((line, index) => <p key={index}>{line.replace(/^#+\s*/, "")}</p>)}</article></dialog>;
}

export default function App() {
  const [data, setData] = useState(null);
  const [selectedBookId, setSelectedBookId] = useState(null);
  const [chapter, setChapter] = useState(null);
  const [selectedRunId, setSelectedRunId] = useState(null);
  const [notice, setNotice] = useState("");
  const [view, setView] = useState("workbench");
  const announcedFailures = useRef(new Set());

  const loadOverview = useCallback(async (announce = false) => {
    try {
      const next = await api("/api/overview"); setData(next);
      setSelectedBookId((current) => current && next.books.some((book) => book.id === current) ? current : next.default_book_id || next.books[0]?.id);
      if (announce) setNotice("状态已刷新");
    } catch (error) { setNotice(error.message); }
  }, []);

  useEffect(() => { loadOverview(); const timer = window.setInterval(loadOverview, 5000); return () => window.clearInterval(timer); }, [loadOverview]);
  useEffect(() => { if (!notice) return undefined; const timer = window.setTimeout(() => setNotice(""), 3200); return () => window.clearTimeout(timer); }, [notice]);
  const book = useMemo(() => data?.books.find((item) => item.id === selectedBookId) || data?.books[0], [data, selectedBookId]);
  useEffect(() => {
    if (!data?.runs.length) return;
    setSelectedRunId((current) => data.runs.some((run) => run.id === current) ? current : data.runs[0].id);
  }, [data?.runs]);
  const selectedRun = useMemo(() => data?.runs.find((run) => run.id === selectedRunId) || data?.runs[0], [data, selectedRunId]);
  const openLog = useCallback((runId) => { setSelectedRunId(runId); }, []);
  useEffect(() => {
    const failed = data?.runs.find((run) => run.status === "failed" && !announcedFailures.current.has(run.id));
    data?.runs.forEach((run) => { if (run.status === "failed") announcedFailures.current.add(run.id); });
    if (failed) openLog(failed.id);
  }, [data?.runs, openLog]);

  async function openChapter(bookId, number) { try { setChapter(await api(`/api/chapter?book_id=${encodeURIComponent(bookId)}&number=${number}`)); } catch (error) { setNotice(error.message); } }
  async function resume(jobId) { try { await api("/api/resume", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ job_id: jobId }) }); setNotice("续跑任务已启动"); loadOverview(); } catch (error) { setNotice(error.message); } }

  if (!data || !book) return <div className="loading-screen"><div className="brand-mark">番</div><p>正在连接小说项目……</p></div>;
  return <>
    <div className="noise" />
    <div className="shell"><Sidebar books={data.books} selected={book.id} onSelect={setSelectedBookId} view={view} onView={setView} /><main className="main">
      <header className="topbar"><div><p className="eyebrow">FANQIE NOVEL CONTROL</p><h1>{view === "system-settings" ? "番茄系统" : book.title}</h1></div><div className="top-actions"><button className="button ghost" onClick={() => loadOverview(true)}>刷新状态</button><span className="sync-time">更新 {new Date(data.generated_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span></div></header>
      {view !== "workbench" ? <SettingsWorkspace scope={view === "system-settings" ? "system" : "book"} books={data.books} bookId={book.id} onBookChange={setSelectedBookId} onNotice={setNotice} onSaved={() => loadOverview()} /> : <>
      <Metrics book={book} />
      <section className="workspace-grid"><CreatePanel books={data.books} selectedBookId={book.id} onNotice={setNotice} onRefresh={loadOverview} onRunStarted={(run) => openLog(run.id)} /><article className="panel note-panel"><div className="panel-head"><div><p className="eyebrow">NEXT</p><h2>下一章接力点</h2></div></div><p className="next-notes">{book.notes_for_next_chapter || "暂无下一章备注。"}</p></article></section>
      <BackendLog run={selectedRun} runs={data.runs} onSelect={openLog} />
      <section className="content-grid"><ChapterList book={book} onOpen={openChapter} /><div className="right-stack"><Activity data={data} bookId={book.id} onResume={resume} onOpenLog={openLog} selectedRunId={selectedRunId} /><ValidationPanel validation={book.validation} /></div></section>
      </>}
    </main></div>
    <ReaderDialog chapter={chapter} onClose={() => setChapter(null)} />
    <div className={`toast ${notice ? "show" : ""}`} role="status">{notice}</div>
  </>;
}
