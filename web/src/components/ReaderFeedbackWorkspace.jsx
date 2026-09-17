import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";

const decisionLabel = { accept: "采纳", partial: "部分采纳", reject: "不采纳" };
const statusLabel = {
  queued: "已排队", analyzing: "分析中", reviewed: "作者已判断",
  applied: "已采用", failed: "分析失败",
};

export default function ReaderFeedbackWorkspace({ book, onNotice }) {
  const readableChapters = book.chapters.filter((item) => item.number <= book.last_completed_chapter);
  const [chapterNumber, setChapterNumber] = useState(readableChapters[0]?.number || 0);
  const [chapter, setChapter] = useState(null);
  const [items, setItems] = useState([]);
  const [versions, setVersions] = useState([]);
  const [selectedVersion, setSelectedVersion] = useState("current");
  const [receipt, setReceipt] = useState(null);
  const [categories, setCategories] = useState({});
  const [category, setCategory] = useState("uncomfortable");
  const [quote, setQuote] = useState("");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const readerRef = useRef(null);

  useEffect(() => {
    const available = book.chapters.filter((item) => item.number <= book.last_completed_chapter);
    setChapterNumber(available[0]?.number || 0);
    setChapter(null); setItems([]); setVersions([]); setSelectedVersion("current");
    setReceipt(null); setQuote(""); setComment("");
  }, [book.id, book.last_completed_chapter]);

  const load = useCallback(async () => {
    if (!chapterNumber) return;
    try {
      const [latestChapter, feedback] = await Promise.all([
        api(`/api/chapter?book_id=${encodeURIComponent(book.id)}&number=${chapterNumber}`),
        api(`/api/reader-feedback?book_id=${encodeURIComponent(book.id)}&chapter=${chapterNumber}`),
      ]);
      setChapter(feedback.current || latestChapter); setItems(feedback.items); setVersions(feedback.versions || []); setCategories(feedback.categories);
    } catch (error) { onNotice(error.message); }
  }, [book.id, chapterNumber, onNotice]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!chapterNumber) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const feedback = await api(`/api/reader-feedback?book_id=${encodeURIComponent(book.id)}&chapter=${chapterNumber}`);
        setItems(feedback.items); setVersions(feedback.versions || []); setCategories(feedback.categories);
        if (selectedVersion === "current") setChapter(feedback.current);
      } catch { /* 下一次轮询继续 */ }
    }, 3000);
    return () => window.clearInterval(timer);
  }, [book.id, chapterNumber, selectedVersion]);

  function captureSelection() {
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
        body: JSON.stringify({ book_id: book.id, chapter: chapterNumber, category, quote, comment }),
      });
      setComment(""); setQuote(""); onNotice("反馈已收到，作者正在独立判断"); await load();
    } catch (error) { onNotice(error.message); }
    finally { setBusy(false); }
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
      onNotice(`第 ${nextReceipt.chapter} 章更新完成，当前显示最新正式稿`); await load();
    } catch (error) { onNotice(error.message); }
  }

  async function promote(item, scope) {
    const scopeLabel = scope === "author" ? "这个作者的长期经验（影响其绑定作品）" : "本书长期规则";
    if (!window.confirm(`确认把这条经验沉淀为${scopeLabel}吗？后续生成和审稿都会读取。`)) return;
    try {
      await api("/api/reader-feedback/promote", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ book_id: book.id, feedback_id: item.id, scope }),
      });
      onNotice(`经验已沉淀到${scope === "author" ? "作者知识库" : "本书规则"}`); await load();
    } catch (error) { onNotice(error.message); }
  }

  const archivedVersion = versions.find((item) => item.id === selectedVersion);
  const displayedChapter = selectedVersion === "current" ? chapter : archivedVersion;
  const lines = displayedChapter?.content.split(/\r?\n/) || [];
  const isCurrent = selectedVersion === "current";
  if (!readableChapters.length) return <section className="panel feedback-empty"><p className="eyebrow">CO-AUTHOR REVIEW</p><h2>副作者审稿</h2><p>这本小说还没有归档章节。生成并归档章节后，就可以在这里圈选原文留言。</p></section>;

  return <section className="reader-feedback-page">
    <article className="panel feedback-intro">
      <div><p className="eyebrow">CO-AUTHOR REVIEW</p><h2>副作者审稿与成长闭环</h2><p>你指出阅读问题，绑定作者先独立判断并修订当前章；可复用的意见会提炼成长期经验候选，等你确认后进入本书或作者知识库。</p></div>
      <label>当前章节<select value={chapterNumber} onChange={(event) => { setChapterNumber(Number(event.target.value)); setSelectedVersion("current"); setReceipt(null); setQuote(""); }}>{readableChapters.map((item) => <option key={item.number} value={item.number}>第 {item.number} 章 · {item.title}</option>)}</select></label>
    </article>

    {receipt && <article className="revision-receipt" role="status">
      <div><strong>第 {receipt.chapter} 章更新完成</strong><span>{new Date(receipt.applied_at).toLocaleString("zh-CN")} · 当前已显示最新正式稿</span></div>
      <p>旧版本已收入草稿箱；{receipt.word_count ? `正文现为 ${receipt.word_count} 字。` : "最新正式稿已经重新载入。"}{receipt.invalidated_checks?.length ? `已撤销过期的${receipt.invalidated_checks.join("、")}，重新验收前不会发布。` : "本次没有需要撤销的旧验收。"}</p>
    </article>}

    <div className="feedback-workspace">
      <article className="panel feedback-reader" onMouseUp={captureSelection} ref={readerRef}>
        <div className="feedback-reader-head"><span>{isCurrent ? "最新正式稿 · 可继续评审" : `草稿箱 · ${displayedChapter?.label || "历史版本"}`}</span><small>{isCurrent ? "拖动选中文字，即可带入右侧反馈" : "历史版本仅供对照，不能在旧稿上继续留言"}</small></div>
        <h2>{lines[0]?.replace(/^#\s*/, "")}</h2>
        {lines.slice(1).filter((line) => line.trim() && !/^---$/.test(line.trim()) && !/^## Metadata/.test(line)).map((line, index) => <p key={index}>{line.replace(/^#+\s*/, "")}</p>)}
      </article>

      <aside className="feedback-side">
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
          <label>问题感觉<select value={category} onChange={(event) => setCategory(event.target.value)}>{Object.entries(categories).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label>
          <label>选中的原文<textarea value={quote} onChange={(event) => setQuote(event.target.value.slice(0, 3000))} placeholder="可直接留言，也可以先在左侧圈选原文" rows="5" /></label>
          <label>你的真实感受<textarea value={comment} onChange={(event) => setComment(event.target.value.slice(0, 5000))} placeholder="例如：我看到这里突然不相信这个人物了，但我不确定为什么。" rows="7" required /></label>
          <div className="author-boundary"><strong>双重确认边界</strong><span>当前章修订和长期经验分开确认；主作者负责提炼，你决定是否让它永久学习。</span></div>
          {!isCurrent && <div className="stale-version-warning">你正在查看历史版本。请先切回“最新正式稿”再继续评审。</div>}
          <button className="button primary" disabled={busy || !comment.trim() || !isCurrent}>{busy ? "正在提交" : "提交并让作者分析"}<b>→</b></button>
        </form>
      </aside>
    </div>

    <article className="panel feedback-history">
      <div className="panel-head"><div><p className="eyebrow">AUTHOR REVIEW</p><h2>作者判断记录</h2></div><span className="count-label">{items.length} 条</span></div>
      <div className="feedback-cards">{items.length ? items.map((item) => <section className={`feedback-card ${item.analysis?.decision || item.status}`} key={item.id}>
        <header><div><strong>第 {item.chapter} 章 · {item.category_label}</strong><small>{new Date(item.created_at).toLocaleString("zh-CN")}</small></div><span>{item.analysis ? decisionLabel[item.analysis.decision] : statusLabel[item.status] || item.status}</span></header>
        {item.quote && <><small className="quote-label">当时选中的原文（留档，不随正文变化）</small><blockquote>{item.quote}</blockquote></>}
        <p className="reader-comment">“{item.comment}”</p>
        {item.status === "failed" && <p className="feedback-error">{item.status_message}</p>}
        {item.analysis && <div className="author-review">
          <h3>作者判断</h3><p>{item.analysis.author_judgment}</p>
          {!!item.analysis.valid_observations?.length && <><h4>有效观察</h4><ul>{item.analysis.valid_observations.map((text, index) => <li key={index}>{text}</li>)}</ul></>}
          {!!item.analysis.misdiagnoses?.length && <><h4>不照单全收的部分</h4><ul>{item.analysis.misdiagnoses.map((text, index) => <li key={index}>{text}</li>)}</ul></>}
          {!!item.analysis.revision_strategy?.length && <><h4>候选改法</h4><ul>{item.analysis.revision_strategy.map((text, index) => <li key={index}>{text}</li>)}</ul></>}
          {item.analysis.learning_candidate && <div className="learning-candidate">
            <div className="learning-head"><div><small>LONG-TERM LEARNING</small><h4>长期经验候选</h4></div><span>{item.analysis.learning_candidate.confidence === "high" ? "高把握" : "中等把握"}</span></div>
            <strong>{item.analysis.learning_candidate.principle}</strong>
            <p><b>适用：</b>{item.analysis.learning_candidate.applies_when}</p>
            <p><b>边界：</b>{item.analysis.learning_candidate.avoid}</p>
            <p className="learning-reason">{item.analysis.learning_candidate.rationale}</p>
            {item.promotion ? <div className="promoted-note">已沉淀为{item.promotion.scope_label}长期规则 · 证据 {item.promotion.evidence_count} 条</div> : <div className="learning-actions">
              <button className={`button ${item.analysis.learning_candidate.recommended_scope === "book" ? "recommended" : ""}`} onClick={() => promote(item, "book")}>用于本书{item.analysis.learning_candidate.recommended_scope === "book" && " · 推荐"}</button>
              <button className={`button ${item.analysis.learning_candidate.recommended_scope === "author" ? "recommended" : ""}`} onClick={() => promote(item, "author")}>教给作者{item.analysis.learning_candidate.recommended_scope === "author" && " · 推荐"}</button>
            </div>}
          </div>}
          {item.has_revision && item.status !== "applied" && <button className="button apply-revision" onClick={() => apply(item)}>确认采用本地修订</button>}
          {item.status === "applied" && <div className="applied-note"><strong>更新完成</strong>{item.applied_at && <> · {new Date(item.applied_at).toLocaleString("zh-CN")}</>}<br />正式稿已更新，应用前原文可在草稿箱查看；重新验收前不会发布。</div>}
        </div>}
      </section>) : <div className="empty">本章还没有真实读者反馈</div>}</div>
    </article>
  </section>;
}
