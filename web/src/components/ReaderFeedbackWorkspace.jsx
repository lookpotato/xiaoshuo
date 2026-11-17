import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";

const decisionLabel = { accept: "采纳", partial: "部分采纳", reject: "不采纳" };
const scopeLabel = { wording: "措辞级", scene: "场景级", chapter: "整章结构级", shared_language: "中文语言库" };
const statusLabel = {
  queued: "已排队", analyzing: "分析中", reviewed: "作者已判断",
  blind_reviewed: "试读完成", interview_ready: "提问完成", applied: "已采用", failed: "分析失败",
};
const runStatusLabel = { running: "运行中", success: "已完成", finished: "已结束", failed: "失败" };
const reviewModes = [
  { id: "blind", title: "陌生读者试读", note: "只看当前章，不读取设定；诊断真实读感，不改稿" },
  { id: "author", title: "作者审稿", note: "读取设定与连续性；判断反馈并生成候选修改" },
  { id: "combined", title: "双重审稿", note: "先陌生试读，再由作者裁决与改稿", recommended: true },
  { id: "chapter_interview", title: "整章写法审校", note: "读完整章后审校人物说话与行动；给出更自然的中文写法" },
];

export default function ReaderFeedbackWorkspace({ book, apiVersion, onNotice }) {
  const readableChapters = book.chapters.filter((item) => item.number <= book.last_completed_chapter);
  const [chapterNumber, setChapterNumber] = useState(readableChapters[0]?.number || 0);
  const [chapter, setChapter] = useState(null);
  const [items, setItems] = useState([]);
  const [versions, setVersions] = useState([]);
  const [manualEdits, setManualEdits] = useState([]);
  const [selectedVersion, setSelectedVersion] = useState("current");
  const [receipt, setReceipt] = useState(null);
  const [feedbackRuns, setFeedbackRuns] = useState([]);
  const [selectedFeedbackRun, setSelectedFeedbackRun] = useState(null);
  const [feedbackRunLog, setFeedbackRunLog] = useState(null);
  const [categories, setCategories] = useState({});
  const [category, setCategory] = useState("uncomfortable");
  const [quote, setQuote] = useState("");
  const [comment, setComment] = useState("");
  const [reviewMode, setReviewMode] = useState("chapter_interview");
  const [dialogueDrafts, setDialogueDrafts] = useState({});
  const [learningDrafts, setLearningDrafts] = useState({});
  const [interviewAnswers, setInterviewAnswers] = useState({});
  const [applyErrors, setApplyErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState("");
  const [editSha, setEditSha] = useState("");
  const [editError, setEditError] = useState("");
  const readerRef = useRef(null);

  useEffect(() => {
    const available = book.chapters.filter((item) => item.number <= book.last_completed_chapter);
    setChapterNumber(available[0]?.number || 0);
    setChapter(null); setItems([]); setVersions([]); setManualEdits([]); setSelectedVersion("current");
    setReceipt(null); setQuote(""); setComment(""); setFeedbackRuns([]); setSelectedFeedbackRun(null); setFeedbackRunLog(null);
    setInterviewAnswers({});
    setLearningDrafts({});
    setEditing(false); setEditText(""); setEditError("");
  }, [book.id, book.last_completed_chapter]);

  const load = useCallback(async () => {
    if (!chapterNumber) return;
    try {
      const [latestChapter, feedback] = await Promise.all([
        api(`/api/chapter?book_id=${encodeURIComponent(book.id)}&number=${chapterNumber}`),
        api(`/api/reader-feedback?book_id=${encodeURIComponent(book.id)}&chapter=${chapterNumber}`),
      ]);
      setChapter(feedback.current || latestChapter); setItems(feedback.items); setVersions(feedback.versions || []); setManualEdits(feedback.manual_edits || []); setCategories(feedback.categories); setFeedbackRuns(feedback.runs || []);
    } catch (error) { onNotice(error.message); }
  }, [book.id, chapterNumber, onNotice]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!chapterNumber) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const feedback = await api(`/api/reader-feedback?book_id=${encodeURIComponent(book.id)}&chapter=${chapterNumber}`);
        setItems(feedback.items); setVersions(feedback.versions || []); setManualEdits(feedback.manual_edits || []); setCategories(feedback.categories); setFeedbackRuns(feedback.runs || []);
        if (selectedVersion === "current") setChapter(feedback.current);
      } catch { /* 下一次轮询继续 */ }
    }, 3000);
    return () => window.clearInterval(timer);
  }, [book.id, chapterNumber, selectedVersion]);

  const openFeedbackRun = useCallback(async (run) => {
    setSelectedFeedbackRun(run);
    try {
      setFeedbackRunLog(await api(`/api/run-log?run_id=${encodeURIComponent(run.id)}&tail=120`));
    } catch (error) { onNotice(error.message); }
  }, [onNotice]);

  useEffect(() => {
    if (!selectedFeedbackRun) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const next = await api(`/api/run-log?run_id=${encodeURIComponent(selectedFeedbackRun.id)}&tail=120`);
        setFeedbackRunLog(next);
        setFeedbackRuns((current) => current.map((item) => item.id === next.id ? { ...item, status: next.status } : item));
      } catch { /* 下次刷新继续 */ }
    }, selectedFeedbackRun.status === "running" ? 2000 : 5000);
    return () => window.clearInterval(timer);
  }, [selectedFeedbackRun, onNotice]);

  function captureSelection() {
    if (reviewMode === "chapter_interview") return;
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !readerRef.current) return;
    const range = selection.getRangeAt(0);
    if (!readerRef.current.contains(range.commonAncestorContainer)) return;
    const selected = selection.toString().trim();
    if (selected) setQuote(selected.slice(0, 3000));
  }

  async function submit(event) {
    event.preventDefault();
    if (!comment.trim() || busy) return;
    setBusy(true);
    try {
      await api("/api/reader-feedback", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ book_id: book.id, chapter: chapterNumber, category, quote, comment, review_mode: reviewMode }),
      });
      const label = reviewModes.find((item) => item.id === reviewMode)?.title || "审稿";
      setComment(""); setQuote(""); onNotice(`反馈已收到，已启动${label}`); await load();
    } catch (error) { onNotice(error.message); }
    finally { setBusy(false); }
  }

  async function retryFeedback(item) {
    try {
      await api("/api/reader-feedback/retry", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ book_id: book.id, feedback_id: item.id }),
      });
      onNotice("已重新启动这条审稿任务"); await load();
    } catch (error) { onNotice(error.message); }
  }

  async function apply(item) {
    if (!window.confirm("采用后会修改本地章节，并在反馈目录保存原文备份；不会自动发布。继续吗？")) return;
    try {
      const result = await api("/api/reader-feedback/apply", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ book_id: book.id, feedback_id: item.id }),
      });
      if (result.current) setChapter(result.current);
      if (result.versions) setVersions(result.versions);
      const nextReceipt = result.receipt || {
        chapter: chapterNumber, applied_at: new Date().toISOString(),
        word_count: null, invalidated_checks: ["旧验收"],
      };
      setSelectedVersion("current"); setReceipt(nextReceipt);
      setApplyErrors((current) => ({ ...current, [item.id]: "" }));
      onNotice(`第 ${nextReceipt.chapter} 章更新完成，当前显示最新正式稿`); await load();
    } catch (error) {
      setApplyErrors((current) => ({ ...current, [item.id]: error.message }));
      onNotice(error.message);
    }
  }

  function beginEdit() {
    if (!chapter || selectedVersion !== "current") return;
    setEditText(chapter.content); setEditSha(chapter.sha256);
    setEditError(""); setEditing(true);
  }

  async function saveEdit() {
    if (busy || !editing || apiVersion < 14 || editText === chapter?.content) return;
    setBusy(true); setEditError("");
    try {
      const result = await api("/api/reader-feedback/manual-edit", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ book_id: book.id, chapter: chapterNumber, content: editText, sha256: editSha }),
      });
      setChapter(result.current); setVersions(result.versions || []); setManualEdits(result.manual_edits || []);
      setReceipt(result.receipt); setSelectedVersion("current"); setEditing(false);
      onNotice(result.analysis_error ? `正文已保存，AI 分析未启动：${result.analysis_error}` : `第 ${chapterNumber} 章已保存，AI 正在对比修改前后正文`);
      await load();
    } catch (error) { setEditError(error.message); }
    finally { setBusy(false); }
  }

  async function retryManualAnalysis(edit) {
    if (busy) return;
    setBusy(true);
    try {
      const result = await api("/api/reader-feedback/manual-edit/retry", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ book_id: book.id, chapter: chapterNumber, edit_id: edit.id }),
      });
      setManualEdits(result.manual_edits || []);
      onNotice("已重新启动这次修改的 AI 分析");
    } catch (error) { onNotice(error.message); }
    finally { setBusy(false); }
  }

  async function promoteManualLearning(edit, index) {
    const candidate = edit.learning_candidates[index];
    const destination = candidate.recommended_scope === "shared_language" ? "中文语言库" : "作者规则";
    if (!window.confirm(`确认把这条写法沉淀到${destination}吗？后续创作会读取它。`)) return;
    setBusy(true);
    try {
      const result = await api("/api/reader-feedback/manual-edit/promote", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ book_id: book.id, chapter: chapterNumber, edit_id: edit.id, candidate_index: index }),
      });
      setManualEdits((current) => current.map((item) => item.id === edit.id ? result.record : item));
      onNotice(`已加入${destination}`);
    } catch (error) { onNotice(error.message); }
    finally { setBusy(false); }
  }

  async function requestChapterRewrite(item) {
    const error = applyErrors[item.id] || "当前候选修订不符合整章要求";
    if (["queued", "responding"].includes(item.author_dialogue_status)) return;
    try {
      await api("/api/reader-feedback/dialogue", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          book_id: book.id,
          feedback_id: item.id,
          content: `【请按整章重写候选修订】应用时校验失败：${error}。请保留已经成立的情节、人物选择和章节标题，按整章结构级重写完整候选稿，恢复本书正常章长，并返回可直接应用的完整正文。`,
        }),
      });
      setApplyErrors((current) => ({ ...current, [item.id]: "" }));
      onNotice("已要求作者按整章重写候选修订，请等待作者回复"); await load();
    } catch (error) { onNotice(error.message); }
  }

  async function promote(item) {
    if (!window.confirm("确认沉淀这条长期经验吗？作者已经判断了它应当影响的范围，后续生成和审稿会自动读取。")) return;
    try {
      await api("/api/reader-feedback/promote", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ book_id: book.id, feedback_id: item.id }),
      });
      onNotice("长期经验已沉淀，后续生成和审稿会自动读取"); await load();
    } catch (error) { onNotice(error.message); }
  }

  async function continueAuthorDialogue(item, target = "analysis") {
    const content = (dialogueDrafts[item.id] || "").trim();
    if (!content || item.author_dialogue_status === "queued" || item.author_dialogue_status === "responding") return;
    try {
      await api("/api/reader-feedback/dialogue", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ book_id: book.id, feedback_id: item.id, content: target === "learning_candidate" ? `【请重提炼长期经验候选】${content}` : content }),
      });
      setDialogueDrafts((current) => ({ ...current, [item.id]: "" }));
      onNotice("已把纠正意见发给作者，会在同一条记录中继续回复"); await load();
    } catch (error) { onNotice(error.message); }
  }

  async function guideLearningCandidate(item) {
    const content = (learningDrafts[item.id] || "").trim();
    if (!content || ["queued", "responding"].includes(item.author_dialogue_status)) return;
    try {
      await api("/api/reader-feedback/dialogue", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ book_id: book.id, feedback_id: item.id, content: `【请重提炼长期经验候选】${content}` }),
      });
      setLearningDrafts((current) => ({ ...current, [item.id]: "" }));
      onNotice("已要求作者重新提炼长期经验候选"); await load();
    } catch (error) { onNotice(error.message); }
  }

  async function saveInterviewAnswers(item) {
    const response = (interviewAnswers[item.id] ?? item.chapter_interview_response ?? "").trim();
    if (!response) return;
    try {
      await api("/api/reader-feedback/interview-answers", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ book_id: book.id, feedback_id: item.id, response }),
      });
      onNotice("作者正在理解你的判断并生成候选修订稿，请稍候"); await load();
    } catch (error) { onNotice(error.message); }
  }

  const archivedVersion = versions.find((item) => item.id === selectedVersion);
  const displayedChapter = selectedVersion === "current" ? chapter : archivedVersion;
  const lines = displayedChapter?.content.split(/\r?\n/) || [];
  const isCurrent = selectedVersion === "current";
  if (!readableChapters.length) return <section className="panel feedback-empty"><p className="eyebrow">CO-AUTHOR REVIEW</p><h2>副作者审稿</h2><p>这本小说还没有归档章节。生成并归档章节后，就可以在这里圈选原文留言。</p></section>;

  return <section className="reader-feedback-page">
    <article className="panel feedback-intro">
      <div><p className="eyebrow">CO-AUTHOR REVIEW</p><h2>读者反馈</h2><p>你可以直接修改正文；保存后会自动对比原文，由 AI 分析修改效果和可吸收的写法。也可以留言，让作者给出修订建议。</p></div>
      <label>当前章节<select value={chapterNumber} onChange={(event) => { setChapterNumber(Number(event.target.value)); setSelectedVersion("current"); setReceipt(null); setQuote(""); setEditing(false); setEditError(""); }}>{readableChapters.map((item) => <option key={item.number} value={item.number}>第 {item.number} 章 · {item.title}</option>)}</select></label>
    </article>

    {receipt && <article className="revision-receipt" role="status">
      <div><strong>第 {receipt.chapter} 章更新完成</strong><span>{new Date(receipt.applied_at).toLocaleString("zh-CN")} · 当前已显示最新正式稿</span></div>
      <p>旧版本已收入草稿箱；{receipt.word_count ? `正文现为 ${receipt.word_count} 字。` : "最新正式稿已经重新载入。"}{receipt.invalidated_checks?.length ? `已撤销过期的${receipt.invalidated_checks.join("、")}，重新验收前不会发布。` : "本次没有需要撤销的旧验收。"}</p>
    </article>}
    {apiVersion < 14 && <div className="stale-version-warning" role="alert">请重启工作台服务，启用保存后的自动对比分析。</div>}
    {apiVersion >= 14 && apiVersion < 16 && <div className="stale-version-warning" role="alert">请重启工作台服务，启用手动改稿的作者规则和中文语言库候选。</div>}

    {manualEdits.length > 0 && <article className="panel manual-edit-history"><div className="panel-head"><div><p className="eyebrow">YOUR EDITS</p><h2>手动修改与 AI 复盘</h2></div><span className="count-label">{manualEdits.length} 次</span></div><p>每次保存都保留原文和修改稿。AI 会逐项对比，并提出适合作者或中文语言库的候选；由你确认后才会入库。</p>{manualEdits.slice(0, 6).map((edit, index) => <details key={edit.id} defaultOpen={index === 0}><summary>{new Date(edit.receipt.applied_at).toLocaleString("zh-CN")} · {edit.status === "completed" ? "分析完成" : edit.status === "failed" ? "分析失败" : edit.status === "unavailable" ? "旧记录无分析" : "AI 正在分析"}</summary><div className="manual-edit-analysis"><h3>原文对比</h3><pre>{edit.comparison || "此记录保存时尚未生成逐行对比。"}{edit.comparison_truncated ? "\n…对比过长，已截取前段" : ""}</pre><h3>写法复盘</h3>{edit.status === "completed" ? <pre>{edit.analysis}</pre> : <p>{edit.status === "failed" || edit.status === "unavailable" ? edit.error || "分析失败，可在运行记录查看日志。" : "AI 正在阅读本次修改，完成后会显示在这里。"}</p>}{edit.status === "completed" && edit.structured_analysis && !edit.learning_candidates?.length && <p>本次改动没有足够可推广的写法，保留为本章经验。</p>}{edit.status === "completed" && edit.learning_candidates?.map((candidate, candidateIndex) => <div className="manual-learning-candidate" key={candidateIndex}><strong>{candidate.principle}</strong><p>建议范围：{candidate.recommended_scope === "shared_language" ? "中文语言库" : "作者规则"} · {candidate.confidence === "high" ? "高把握" : "中等把握"}</p><p>适用：{candidate.applies_when}</p><p>边界：{candidate.avoid}</p><p>依据：{candidate.rationale}</p>{edit.promotions?.[String(candidateIndex)] ? <span>已加入{edit.promotions[String(candidateIndex)].scope_label}</span> : <button className="button ghost" disabled={busy || apiVersion < 16} onClick={() => promoteManualLearning(edit, candidateIndex)}>确认加入{candidate.recommended_scope === "shared_language" ? "中文语言库" : "作者规则"}</button>}</div>)}{(["failed", "unavailable"].includes(edit.status) || (edit.status === "completed" && !edit.structured_analysis)) && edit.can_analyze && <button className="button ghost" disabled={busy || (edit.status === "completed" && apiVersion < 16)} onClick={() => retryManualAnalysis(edit)}>{edit.status === "completed" ? "补做经验候选" : edit.status === "unavailable" ? "补做分析" : "重新分析"}</button>}</div></details>)}</article>}

    <div className="feedback-workspace">
      <article className="panel feedback-reader" onMouseUp={captureSelection} ref={readerRef}>
        <div className="feedback-reader-head"><span>{editing ? "正在编辑最新正式稿" : isCurrent ? "最新正式稿 · 可继续评审" : `草稿箱 · ${displayedChapter?.label || "历史版本"}`}</span><div className="feedback-reader-tools"><small>{isCurrent ? (reviewMode === "chapter_interview" ? "整章阅读，不针对单句圈选" : "拖动选中文字，即可带入右侧反馈") : "历史版本仅供对照，不能在旧稿上继续留言"}</small>{isCurrent && !editing && <button className="button ghost" disabled={apiVersion < 14} onClick={beginEdit}>编辑正文</button>}</div></div>
        {editing ? <div className="feedback-manual-editor"><p>直接修改正文并保存。章节标题保持原样；修改前版本会放入草稿箱，过期验收会撤销。</p><textarea aria-label="编辑章节正文" value={editText} onChange={(event) => setEditText(event.target.value)} /><div className="feedback-manual-actions"><button className="button primary" disabled={busy || editText === chapter?.content} onClick={saveEdit}>{busy ? "保存中…" : "保存修改"}</button><button className="button ghost" disabled={busy} onClick={() => { setEditing(false); setEditError(""); }}>取消</button></div>{editError && <p className="feedback-error" role="alert">{editError}</p>}</div> : <><h2>{lines[0]?.replace(/^#\s*/, "")}</h2>{lines.slice(1).filter((line) => line.trim() && !/^---$/.test(line.trim()) && !/^## Metadata/.test(line)).map((line, index) => <p key={index}>{line.replace(/^#+\s*/, "")}</p>)}</>}
      </article>

      <aside className="feedback-side">
        <section className="panel feedback-runs">
          <div className="panel-head"><div><p className="eyebrow">FEEDBACK RUNS</p><h2>读者反馈运行记录</h2></div><span className="count-label">{feedbackRuns.length} 条</span></div>
          <p className="panel-copy">这里显示本章的整章提问、作者判断和连续对话正在运行什么。</p>
          <div className="feedback-run-list">{feedbackRuns.length ? feedbackRuns.map((run) => <div className={`feedback-run-row ${run.id === selectedFeedbackRun?.id ? "active" : ""}`} key={run.id}>
            <div className="feedback-run-top"><strong>{run.kind === "reader_feedback" ? "首次审稿" : run.kind === "reader_feedback_dialogue" ? "作者连续对话" : run.kind === "manual_edit_analysis" ? "手动修改分析" : "作者生成修订"}</strong><span className={`status ${run.status}`}>{runStatusLabel[run.status] || run.status}</span></div>
            <small>{run.started_at}{run.exit_code != null ? ` · 退出码 ${run.exit_code}` : ""}</small>
            <button className="log-button" onClick={() => openFeedbackRun(run)}>{run.id === selectedFeedbackRun?.id ? "刷新日志" : "查看日志"}</button>
          </div>) : <div className="empty">本章暂无后台运行记录</div>}</div>
          {feedbackRunLog && <pre className="feedback-run-log">{feedbackRunLog.content || feedbackRunLog.error_summary || "暂无日志输出"}</pre>}
        </section>
        <section className="panel draft-vault">
          <div className="panel-head"><div><p className="eyebrow">VERSIONS</p><h2>草稿箱</h2></div><span className="count-label">{versions.length} 份</span></div>
          <p>正式稿永远优先显示；生成草稿、候选稿和每次应用前的原文留在这里对照。</p>
          <div className="version-list">
            <button className={isCurrent ? "active" : ""} onClick={() => { setSelectedVersion("current"); setQuote(""); }}>最新正式稿<span>继续评审此版本</span></button>
            {versions.map((version) => <button className={selectedVersion === version.id ? "active" : ""} key={version.id} onClick={() => { setSelectedVersion(version.id); setQuote(""); }}>{version.label}<span>{new Date(version.created_at).toLocaleString("zh-CN")}</span></button>)}
          </div>
        </section>
        <form className="panel feedback-form" onSubmit={submit}>
          <div className="panel-head"><div><p className="eyebrow">MARK</p><h2>哪里不舒服</h2></div></div>
          <details className="review-mode-picker">
            <summary>审稿方式：{reviewModes.find((mode) => mode.id === reviewMode)?.title}（可选）</summary>
            <div className="review-mode-options">{reviewModes.map((mode) => <button type="button" className={reviewMode === mode.id ? "active" : ""} aria-pressed={reviewMode === mode.id} key={mode.id} onClick={() => { setReviewMode(mode.id); if (mode.id === "chapter_interview") setQuote(""); }}>
              <span>{mode.title}{mode.recommended && <em>推荐</em>}</span><small>{mode.note}</small>
            </button>)}</div>
            <label>问题感觉<select value={category} onChange={(event) => setCategory(event.target.value)}>{Object.entries(categories).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label>
          </details>
          {reviewMode !== "chapter_interview" && <label>选中的原文<textarea value={quote} onChange={(event) => setQuote(event.target.value.slice(0, 3000))} placeholder="可直接留言，也可以先在左侧圈选原文" rows="5" /></label>}
          <label>你想说的问题<textarea value={comment} onChange={(event) => setComment(event.target.value.slice(0, 5000))} placeholder="例如：十三楼此前限行，周寻怎么突然提着工具包上去了？" rows="5" required /></label>
          {!isCurrent && <div className="stale-version-warning">你正在查看历史版本。请先切回“最新正式稿”再继续评审。</div>}
          <button className="button primary" disabled={busy || editing || !comment.trim() || !isCurrent}>{busy ? "正在提交" : "发送反馈"}<b>→</b></button>
        </form>
      </aside>
    </div>

    <article className="panel feedback-history">
      <div className="panel-head"><div><p className="eyebrow">AUTHOR REVIEW</p><h2>作者判断记录</h2></div><span className="count-label">{items.length} 条</span></div>
      <div className="feedback-cards">{items.length ? items.map((item) => <section className={`feedback-card ${item.analysis?.decision || item.status}`} key={item.id}>
        <header><div><strong>第 {item.chapter} 章 · {item.category_label}</strong><small>{item.review_mode_label || "旧版审稿"} · {new Date(item.created_at).toLocaleString("zh-CN")}</small></div><span>{item.analysis ? decisionLabel[item.analysis.decision] : statusLabel[item.status] || item.status}</span></header>
        {item.quote && <><small className="quote-label">当时选中的原文（留档，不随正文变化）</small><blockquote>{item.quote}</blockquote></>}
        <p className="reader-comment">“{item.comment}”</p>
        {item.status === "analyzing" && <p className="feedback-progress">{item.status_message}</p>}
        {item.status === "failed" && <><p className="feedback-error">{item.status_message}</p><button className="button" onClick={() => retryFeedback(item)}>重新审稿</button></>}
        {item.blind_reader && <div className="blind-reader-review">
          <div className="review-stage-head"><div><small>FIRST-TIME READER</small><h3>陌生读者试读</h3></div><span>{scopeLabel[item.blind_reader.recommended_scope] || item.blind_reader.recommended_scope}</span></div>
          <p>{item.blind_reader.reader_experience}</p>
          {!!item.blind_reader.missing_or_late_information?.length && <><h4>缺少或出现过晚的信息</h4><ul>{item.blind_reader.missing_or_late_information.map((text, index) => <li key={index}>{text}</li>)}</ul></>}
          <p className="scope-reason">{item.blind_reader.scope_rationale}</p>
        </div>}
        {item.chapter_interview && <div className="chapter-interview-review">
          <div className="review-stage-head"><div><small>AUTHOR REPLY</small><h3>作者回复</h3></div></div>
          <p>{item.chapter_interview.reading_summary}</p>
          <ol>{item.chapter_interview.questions?.map((question) => <li key={question.id}><strong>{question.question}</strong></li>)}</ol>
          {item.status === "interview_ready" && <><label>一次回复以上问题<textarea rows="7" maxLength="12000" value={interviewAnswers[item.id] ?? item.chapter_interview_response ?? ""} onChange={(event) => setInterviewAnswers((current) => ({ ...current, [item.id]: event.target.value }))} placeholder="1. ……\n2. ……\n3. ……\n4. ……（按实际问题数回答即可）" /></label><button className="button primary" disabled={!(interviewAnswers[item.id] ?? item.chapter_interview_response ?? "").trim()} onClick={() => saveInterviewAnswers(item)}>发送回答</button></>}
          {item.chapter_interview_response && item.status !== "interview_ready" && <p><b>你的回答：</b>{item.chapter_interview_response}</p>}
        </div>}
        {item.analysis && <div className="author-review">
          <div className="review-stage-head"><div><small>AUTHOR REVIEW</small><h3>作者判断</h3></div><span>{scopeLabel[item.analysis.revision_scope] || "旧版局部判断"}</span></div>
          <p>{item.analysis.author_judgment}</p>
          {item.analysis.scope_rationale && <p className="scope-reason"><b>修改范围：</b>{item.analysis.scope_rationale}</p>}
          {!!item.analysis.valid_observations?.length && <><h4>有效观察</h4><ul>{item.analysis.valid_observations.map((text, index) => <li key={index}>{text}</li>)}</ul></>}
          {!!item.analysis.misdiagnoses?.length && <><h4>不照单全收的部分</h4><ul>{item.analysis.misdiagnoses.map((text, index) => <li key={index}>{text}</li>)}</ul></>}
          {!!item.analysis.revision_strategy?.length && <><h4>候选改法</h4><ul>{item.analysis.revision_strategy.map((text, index) => <li key={index}>{text}</li>)}</ul></>}
          <section className="author-dialogue">
            <div className="author-dialogue-head"><h4>继续和作者讨论</h4><small>同一条记录保留上下文；可以指出误判、追问原因或要求重新判断</small></div>
            {!!item.author_dialogue?.length && <div className="author-dialogue-messages">{item.author_dialogue.map((message) => <div className={`author-dialogue-message ${message.role}`} key={message.id}>
              <strong>{message.role === "user" ? "你" : "作者"}{message.changed_judgment ? " · 已修正判断" : ""}</strong>
              <p>{message.content}</p>
            </div>)}</div>}
            {item.author_dialogue_status === "failed" && <p className="feedback-error">作者回复失败，可以重新发送一条消息。</p>}
            <div className="author-dialogue-compose">
              <textarea rows="3" maxLength="5000" value={dialogueDrafts[item.id] || ""} onChange={(event) => setDialogueDrafts((current) => ({ ...current, [item.id]: event.target.value }))} placeholder="例如：你判断错了。问题不是三个短句连在一起，而是‘临时起意’根本不像她此刻会说的话，请按现实中文口语重新判断。" />
              <button className="button" disabled={!dialogueDrafts[item.id]?.trim() || ["queued", "responding"].includes(item.author_dialogue_status)} onClick={() => continueAuthorDialogue(item)}>{["queued", "responding"].includes(item.author_dialogue_status) ? "作者正在回复" : "继续讨论"}</button>
            </div>
          </section>
          {item.analysis.learning_candidate && <div className="learning-candidate">
            <div className="learning-head"><div><small>LONG-TERM LEARNING</small><h4>长期经验候选</h4></div><span>{item.analysis.learning_candidate.confidence === "high" ? "高把握" : "中等把握"}</span></div>
            <strong>{item.analysis.learning_candidate.principle}</strong>
            <p><b>适用：</b>{item.analysis.learning_candidate.applies_when}</p>
            <p><b>边界：</b>{item.analysis.learning_candidate.avoid}</p>
            <p className="learning-reason">{item.analysis.learning_candidate.rationale}</p>
            <div className="learning-candidate-editor">
              <div className="learning-candidate-editor-head"><strong>不满意这条候选？</strong><span>先指导，再沉淀</span></div>
              <p>把你认为不准确的地方直接写出来，作者会按你的边界重新提炼。</p>
              <textarea rows="3" maxLength="5000" value={learningDrafts[item.id] || ""} onChange={(event) => setLearningDrafts((current) => ({ ...current, [item.id]: event.target.value }))} placeholder="例如：这不是所有对白都要变短，而是不能把作者的总结塞进人物嘴里；请按这个边界重新提炼。" />
              <button className="button learning-guide-button" disabled={!learningDrafts[item.id]?.trim() || ["queued", "responding"].includes(item.author_dialogue_status)} onClick={() => guideLearningCandidate(item)}>{["queued", "responding"].includes(item.author_dialogue_status) ? "作者正在重提炼…" : "指导作者重提长期经验"}</button>
            </div>
            {item.promotion ? <div className="promoted-note">已沉淀为{item.promotion.scope_label}长期规则 · 证据 {item.promotion.evidence_count} 条</div> : <div className="learning-actions">
              <button className="button recommended" onClick={() => promote(item)}>确认沉淀长期经验</button>
            </div>}
          </div>}
          {applyErrors[item.id] && <div className="apply-error-actions"><p className="feedback-error">{applyErrors[item.id]}</p><button className="button" disabled={["queued", "responding"].includes(item.author_dialogue_status)} onClick={() => requestChapterRewrite(item)}>{["queued", "responding"].includes(item.author_dialogue_status) ? "作者正在重写…" : "要求作者整章重写"}</button></div>}
          {item.has_revision && item.status !== "applied" && <button className="button apply-revision" onClick={() => apply(item)}>确认采用{scopeLabel[item.analysis.revision_scope] || "本地"}修订</button>}
          {item.status === "applied" && <div className="applied-note"><strong>更新完成</strong>{item.applied_at && <> · {new Date(item.applied_at).toLocaleString("zh-CN")}</>}<br />正式稿已更新，应用前原文可在草稿箱查看；重新验收前不会发布。</div>}
        </div>}
      </section>) : <div className="empty">本章还没有真实读者反馈</div>}</div>
    </article>
  </section>;
}
