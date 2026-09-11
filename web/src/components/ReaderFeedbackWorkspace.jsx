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
  const [categories, setCategories] = useState({});
  const [category, setCategory] = useState("uncomfortable");
  const [quote, setQuote] = useState("");
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const readerRef = useRef(null);

  useEffect(() => {
    const available = book.chapters.filter((item) => item.number <= book.last_completed_chapter);
    setChapterNumber(available[0]?.number || 0);
    setChapter(null); setItems([]); setQuote(""); setComment("");
  }, [book.id, book.last_completed_chapter]);

  const load = useCallback(async () => {
    if (!chapterNumber) return;
    try {
      const [nextChapter, feedback] = await Promise.all([
        api(`/api/chapter?book_id=${encodeURIComponent(book.id)}&number=${chapterNumber}`),
        api(`/api/reader-feedback?book_id=${encodeURIComponent(book.id)}&chapter=${chapterNumber}`),
      ]);
      setChapter(nextChapter); setItems(feedback.items); setCategories(feedback.categories);
    } catch (error) { onNotice(error.message); }
  }, [book.id, chapterNumber, onNotice]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!chapterNumber) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const feedback = await api(`/api/reader-feedback?book_id=${encodeURIComponent(book.id)}&chapter=${chapterNumber}`);
        setItems(feedback.items); setCategories(feedback.categories);
      } catch { /* 下一次轮询继续 */ }
    }, 3000);
    return () => window.clearInterval(timer);
  }, [book.id, chapterNumber]);

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
      await api("/api/reader-feedback/apply", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ book_id: book.id, feedback_id: item.id }),
      });
      onNotice("候选修订已应用到本地章节，原文已备份"); await load();
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

  const lines = chapter?.content.split(/\r?\n/) || [];
  if (!readableChapters.length) return <section className="panel feedback-empty"><p className="eyebrow">CO-AUTHOR REVIEW</p><h2>副作者审稿</h2><p>这本小说还没有归档章节。生成并归档章节后，就可以在这里圈选原文留言。</p></section>;

  return <section className="reader-feedback-page">
    <article className="panel feedback-intro">
      <div><p className="eyebrow">CO-AUTHOR REVIEW</p><h2>副作者审稿与成长闭环</h2><p>你指出阅读问题，绑定作者先独立判断并修订当前章；可复用的意见会提炼成长期经验候选，等你确认后进入本书或作者知识库。</p></div>
      <label>当前章节<select value={chapterNumber} onChange={(event) => { setChapterNumber(Number(event.target.value)); setQuote(""); }}>{readableChapters.map((item) => <option key={item.number} value={item.number}>第 {item.number} 章 · {item.title}</option>)}</select></label>
    </article>

    <div className="feedback-workspace">
      <article className="panel feedback-reader" onMouseUp={captureSelection} ref={readerRef}>
        <div className="feedback-reader-head"><span>阅读正文</span><small>拖动选中文字，即可带入右侧反馈</small></div>
        <h2>{lines[0]?.replace(/^#\s*/, "")}</h2>
        {lines.slice(1).filter((line) => line.trim() && !/^---$/.test(line.trim()) && !/^## Metadata/.test(line)).map((line, index) => <p key={index}>{line.replace(/^#+\s*/, "")}</p>)}
      </article>

      <aside className="feedback-side">
        <form className="panel feedback-form" onSubmit={submit}>
          <div className="panel-head"><div><p className="eyebrow">MARK</p><h2>哪里不舒服</h2></div></div>
          <label>问题感觉<select value={category} onChange={(event) => setCategory(event.target.value)}>{Object.entries(categories).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label>
          <label>选中的原文<textarea value={quote} onChange={(event) => setQuote(event.target.value.slice(0, 3000))} placeholder="可直接留言，也可以先在左侧圈选原文" rows="5" /></label>
          <label>你的真实感受<textarea value={comment} onChange={(event) => setComment(event.target.value.slice(0, 5000))} placeholder="例如：我看到这里突然不相信这个人物了，但我不确定为什么。" rows="7" required /></label>
          <div className="author-boundary"><strong>双重确认边界</strong><span>当前章修订和长期经验分开确认；主作者负责提炼，你决定是否让它永久学习。</span></div>
          <button className="button primary" disabled={busy || !comment.trim()}>{busy ? "正在提交" : "提交并让作者分析"}<b>→</b></button>
        </form>
      </aside>
    </div>

    <article className="panel feedback-history">
      <div className="panel-head"><div><p className="eyebrow">AUTHOR REVIEW</p><h2>作者判断记录</h2></div><span className="count-label">{items.length} 条</span></div>
      <div className="feedback-cards">{items.length ? items.map((item) => <section className={`feedback-card ${item.analysis?.decision || item.status}`} key={item.id}>
        <header><div><strong>第 {item.chapter} 章 · {item.category_label}</strong><small>{new Date(item.created_at).toLocaleString("zh-CN")}</small></div><span>{item.analysis ? decisionLabel[item.analysis.decision] : statusLabel[item.status] || item.status}</span></header>
        {item.quote && <blockquote>{item.quote}</blockquote>}
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
          {item.status === "applied" && <div className="applied-note">已应用；原文备份保留在本条反馈目录中，未自动发布。</div>}
        </div>}
      </section>) : <div className="empty">本章还没有真实读者反馈</div>}</div>
    </article>
  </section>;
}
