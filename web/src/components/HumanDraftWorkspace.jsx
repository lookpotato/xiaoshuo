import { useCallback, useEffect, useState } from "react";
import { api } from "../api";

export default function HumanDraftWorkspace({ book, apiVersion, onNotice, onRunStarted, mode = "experiment" }) {
  const [chapter, setChapter] = useState(book.last_completed_chapter || book.next_chapter_number);
  const [workspace, setWorkspace] = useState(null);
  const [draft, setDraft] = useState("");
  const [savedDraft, setSavedDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [activeRun, setActiveRun] = useState(null);
  const [error, setError] = useState("");
  const [reference, setReference] = useState("");

  useEffect(() => { setChapter(book.last_completed_chapter || book.next_chapter_number); setReference(""); }, [book.id]);
  useEffect(() => {
    if (mode === "analysis") setChapter(book.last_completed_chapter || book.next_chapter_number);
  }, [mode, book.id, book.last_completed_chapter, book.next_chapter_number]);
  const load = useCallback(async () => {
    try {
      const result = await api(`/api/human-draft?book_id=${encodeURIComponent(book.id)}&chapter=${chapter}`);
      setWorkspace(result); setDraft(result.draft); setSavedDraft(result.draft); setError("");
    } catch (nextError) { setError(nextError.message); }
  }, [book.id, chapter]);
  useEffect(() => { setWorkspace(null); setReference(""); load(); }, [load]);

  const refreshPlan = useCallback(async () => {
    try {
      const result = await api(`/api/human-draft?book_id=${encodeURIComponent(book.id)}&chapter=${chapter}`);
      setWorkspace((current) => current?.book_id === book.id && current.chapter === chapter ? {
        ...current, has_plan: result.has_plan, plan_revision: result.plan_revision,
        guide: result.guide, guide_stale: result.guide_stale,
        scene_explanation: result.scene_explanation, existing_chapter: result.existing_chapter,
        final_review: result.final_review, final_review_stale: result.final_review_stale,
      } : current);
    } catch (nextError) { setError(nextError.message); }
  }, [book.id, chapter]);
  useEffect(() => {
    const timer = window.setInterval(refreshPlan, 15000);
    return () => window.clearInterval(timer);
  }, [refreshPlan]);

  useEffect(() => {
    if (!activeRun) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const result = await api("/api/runs");
        const run = result.runs.find((item) => item.id === activeRun.id);
        if (run && run.status !== "running") {
          window.clearInterval(timer); setActiveRun(null);
          if (run.status === "success") {
            await load();
            onNotice(activeRun.kind === "plan" ? "章节规划已生成，请阅读场景说明后写手稿" : "润色预览已生成，原稿未被替换");
          } else setError(activeRun.kind === "plan" ? "规划未完成，请在后端控制台查看原因" : "润色未完成，请在后端控制台查看原因");
        }
      } catch (nextError) { setError(nextError.message); }
    }, 3000);
    return () => window.clearInterval(timer);
  }, [activeRun, load, onNotice]);

  async function plan() {
    if (busy || activeRun || chapter !== book.next_chapter_number) return;
    setBusy(true); setError("");
    try {
      const result = await api("/api/human-draft/plan", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ book_id: book.id, chapter }),
      });
      setActiveRun({ id: result.run.id, kind: "plan" });
      onRunStarted(result.run); onNotice(`第 ${chapter} 章规划已启动，完成后这里会显示场景说明`);
    } catch (nextError) { setError(nextError.message); }
    finally { setBusy(false); }
  }

  async function save() {
    if (!workspace || busy) return;
    setBusy(true); setError("");
    try {
      const result = await api("/api/human-draft/save", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ book_id: book.id, chapter, content: draft, revision: workspace.draft_revision }),
      });
      setWorkspace((current) => ({ ...current, draft_revision: result.draft_revision, preview: "", preview_stale: result.preview_stale }));
      setSavedDraft(draft); onNotice("手写稿已保存；正式章节未改变");
    } catch (nextError) { setError(nextError.message); }
    finally { setBusy(false); }
  }

  async function polish() {
    if (busy || activeRun || !workspace || draft !== savedDraft) return;
    setBusy(true); setError("");
    try {
      const result = await api("/api/human-draft/polish", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ book_id: book.id, chapter }),
      });
      setActiveRun({ id: result.run.id, kind: "polish" }); onRunStarted(result.run); onNotice("润色已启动；完成后在这里对照原稿与预览");
    } catch (nextError) { setError(nextError.message); }
    finally { setBusy(false); }
  }

  async function showReference() {
    try { const result = await api(`/api/chapter?book_id=${encodeURIComponent(book.id)}&number=${chapter}`); setReference(result.content); }
    catch (nextError) { setError(nextError.message); }
  }

  const available = [...book.chapters.map((item) => ({ number: item.number, title: item.title })),
    { number: book.next_chapter_number, title: "下一章" }]
    .filter((item, index, items) => items.findIndex((other) => other.number === item.number) === index)
    .sort((a, b) => b.number - a.number);
  const guide = workspace?.guide;
  return <div className="human-workspace">
    <article className="panel human-intro">
      <div className="panel-head"><div><p className="eyebrow">{mode === "analysis" ? "CHAPTER REVIEW" : "HUMAN FIRST"}</p><h2>{mode === "analysis" ? "章节分析" : "手写章节接力"}</h2></div><span className="local-badge">{mode === "analysis" ? "随归档更新" : "试验功能"}</span></div>
      <p>{mode === "analysis" ? "先看写前规划，再看成稿后的读者判断与未解决问题，核对故事是否按预期推进。" : "先看场景说明，再由你写正文。润色只生成预览，保留原稿和现有归档；你可以逐段比较，决定哪些改动值得采用。"}</p>
      <label>选择章节 <select value={chapter} disabled={!!activeRun} onChange={(event) => setChapter(Number(event.target.value))}>{available.map((item) => <option value={item.number} key={item.number}>第 {item.number} 章 · {item.title}</option>)}</select></label>
      {apiVersion < 11 && <p className="feedback">请重启 Python 工作台服务，启用章节分析接口。</p>}
    </article>
    {guide ? <article className="panel human-guide">
      <div className="panel-head"><div><p className="eyebrow">SCENE GUIDE</p><h2>第 {chapter} 章写前规划</h2></div><div><button className="button ghost" onClick={refreshPlan}>刷新章节分析</button>{workspace.existing_chapter && <button className="button ghost" onClick={showReference}>查看现有章节</button>}</div></div>
      {workspace.guide_stale && <p className="feedback" role="note">旧场景说明与当前规划不一致，已隐藏；下方显示的是最新章节规划。</p>}
      {guide.scenes.length > 2 && <p className="feedback" role="note">这份旧规划列了 {guide.scenes.length} 场，超过现在的单章场景上限；适合先收束主线，再用它指导手稿。</p>}
      {workspace.scene_explanation && <div className="human-explanation">{workspace.scene_explanation.split(/\n\s*\n/).map((paragraph, index) => <p key={index}>{paragraph.replace(/^#\s*/, "")}</p>)}</div>}
      <p><strong>这一章要办成什么：</strong>{guide.goal}</p>
      <p><strong>真正的冲突：</strong>{guide.conflict}</p>
      <p><strong>关键选择：</strong>{guide.choice}</p>
      <p><strong>读者该亲眼看见：</strong>{guide.payoff}</p>
      <p><strong>结尾落点：</strong>{guide.ending}</p>
      {guide.clock && <p><strong>时间边界：</strong>故事第 {guide.clock.start_day}—{guide.clock.end_day} 天，最高 {guide.clock.end_floor} 楼；跨过 23:47 {guide.clock.crossed_2347} 次。</p>}
      <h3>场景顺序</h3>
      <ol>{guide.scenes.map((scene, index) => <li key={index}><strong>{scene.purpose}</strong><span>阻力：{scene.conflict}</span><span>场景结束时的变化：{scene.turn}</span></li>)}</ol>
      <p className="human-boundary">场景说明记录写前设想；成稿可能改变具体动作。请用下方成稿审读和正文核对实际结果。</p>
      {reference && <details open><summary>现有归档参考</summary><pre>{reference}</pre></details>}
    </article> : <article className="panel human-guide"><h2>第 {chapter} 章尚无规划</h2><p>{mode === "analysis" ? "这章尚无写前规划；请选择已归档章节查看成稿分析。" : "点击下方按钮生成章节目标和场景说明，完成后直接在本页写手稿。生成可能需要几分钟。"}</p><button className="button ghost" onClick={refreshPlan}>刷新章节分析</button>{mode === "experiment" && chapter === book.next_chapter_number ? <button className="button primary" disabled={busy || !!activeRun || apiVersion < 10} onClick={plan}>{activeRun?.kind === "plan" ? "正在生成规划……" : `生成第 ${chapter} 章规划`}</button> : mode === "experiment" ? <p>目前只能为下一章（第 {book.next_chapter_number} 章）生成规划；可在上方选择已有规划的章节。</p> : null}</article>}
    {mode === "analysis" && workspace?.existing_chapter && <article className="panel human-guide"><div className="panel-head"><div><p className="eyebrow">AFTER WRITING</p><h2>第 {chapter} 章成稿审读</h2></div></div>{workspace.final_review ? <><p><strong>读者判断：</strong>{workspace.final_review.reader.summary || "审读已完成"}</p>{!!workspace.final_review.reader.nonblocking?.length && <><h3>仍值得留意</h3><ul>{workspace.final_review.reader.nonblocking.map((item, index) => <li key={index}>{item.quote && <strong>“{item.quote}” </strong>}{item.reason}</li>)}</ul></>}{workspace.final_review.continuity.next_hook && <p><strong>本章实际留下的下一步：</strong>{workspace.final_review.continuity.next_hook}</p>}</> : <p>{workspace.final_review_stale ? "正文已变动，旧审读记录已隐藏。" : "这章尚无成稿审读记录；旧章节可先查看正文。"}</p>}</article>}
    {mode === "analysis" && error && <p className="feedback" role="alert">{error}</p>}
    {mode === "experiment" && <article className="panel human-editor">
      <div className="panel-head"><div><p className="eyebrow">YOUR DRAFT</p><h2>你的原稿</h2></div><span>{draft.length} 字符</span></div>
      <textarea aria-label="手写章节正文" value={draft} onChange={(event) => setDraft(event.target.value)} placeholder={`# 第 ${chapter} 章 标题\n\n从你认为最有生命力的现场写起……`} />
      <div className="human-actions"><button className="button primary" disabled={busy || !workspace || draft === savedDraft} onClick={save}>保存手写稿</button><button className="button ghost" disabled={busy || !!activeRun || !workspace?.has_plan || !savedDraft || draft !== savedDraft} onClick={polish}>{activeRun?.kind === "polish" ? "润色中……" : "生成润色预览"}</button><button className="button ghost" onClick={load}>重新载入</button></div>
      {draft !== savedDraft && <p className="feedback">有未保存修改；先保存再润色。</p>}
      {workspace?.preview_stale && <p className="feedback">旧预览对应之前的手稿，重新润色后才能对照当前稿。</p>}
      {error && <p className="feedback" role="alert">{error}</p>}
    </article>}
    {mode === "experiment" && workspace?.preview && <article className="panel human-preview"><div className="panel-head"><div><p className="eyebrow">COMPARE</p><h2>润色预览</h2></div></div><p>以下文本尚未替换你的原稿或正式章节。</p><div className="human-compare"><section><h3>你的原稿</h3><pre>{workspace.draft}</pre></section><section><h3>润色预览</h3><pre>{workspace.preview}</pre></section></div></article>}
  </div>;
}
