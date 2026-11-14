import { useCallback, useEffect, useState } from "react";
import { api } from "../api";

export default function OverviewHandoffWorkspace({ book, apiVersion, onNotice, onRunStarted }) {
  const chapter = book.next_chapter_number;
  const [data, setData] = useState(null);
  const [text, setText] = useState("");
  const [question, setQuestion] = useState("");
  const [run, setRun] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const result = await api(`/api/overview-handoff?book_id=${encodeURIComponent(book.id)}&chapter=${chapter}`);
      setData(result); setText(result.overview || ""); setError("");
    } catch (failure) { setError(failure.message); }
  }, [book.id, chapter]);
  useEffect(() => { setData(null); setRun(null); load(); }, [load]);
  useEffect(() => {
    if (!run) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const result = await api("/api/runs");
        const current = result.runs.find((item) => item.id === run.id);
        if (current && current.status !== "running") {
          window.clearInterval(timer); setRun(null);
          await load();
          if (current.status === "success") onNotice(run.action === "prepare" ? "写前总览已生成，请先审阅和修改" : run.action === "ask" ? "作者已回答问题" : "章节扩写与归档已完成");
          else setError("任务未完成，请在控制台查看失败原因");
        }
      } catch (failure) { setError(failure.message); }
    }, 3000);
    return () => window.clearInterval(timer);
  }, [run, load, onNotice]);

  async function launch(action) {
    if (busy || run) return;
    setBusy(true); setError("");
    try {
      const result = await api(`/api/overview-handoff/${action}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ book_id: book.id, chapter, revision: data?.revision, question }),
      });
      setRun({ id: result.run.id, action }); onRunStarted(result.run);
      if (action === "ask") setQuestion("");
      onNotice(action === "prepare" ? "正在生成写前总览" : action === "ask" ? "作者正在回答" : "正在按你确认的总览扩写");
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }

  async function save() {
    setBusy(true); setError("");
    try {
      const result = await api("/api/overview-handoff/save", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ book_id: book.id, chapter, content: text, revision: data.revision }),
      });
      setData((current) => ({ ...current, overview: text, revision: result.revision }));
      onNotice("写前总览已保存");
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }

  const dirty = data?.status === "pending" && text !== data.overview;
  return <div className="human-workspace overview-handoff">
    <article className="panel human-intro">
      <div className="panel-head"><div><p className="eyebrow">AUTHOR HANDOFF</p><h2>写前总览接力</h2></div><span className="local-badge">第 {chapter} 章</span></div>
      <p>先生成章节规划、人物私线和场景粗稿，扩写会暂停在这里。你可以重写总览，向内置作者 AI 询问不合理之处；保存并确认后，AI 再以你的总览扩成正文。</p>
      {apiVersion < 12 && <p className="feedback" role="alert">请重启 Python 工作台服务，启用写前总览接口。</p>}
    </article>
    {data?.status === "none" ? <article className="panel human-guide"><h2>准备第 {chapter} 章</h2><p>生成写前材料可能需要几分钟，完成后会在本页显示可修改的总览。</p><button className="button primary" disabled={busy || !!run || apiVersion < 12} onClick={() => launch("prepare")}>{run?.action === "prepare" ? "正在生成……" : "生成写前总览"}</button></article> : data && <>
      <article className="panel human-editor"><div className="panel-head"><div><p className="eyebrow">YOUR OVERVIEW</p><h2>修改本章总览</h2></div><span>{text.length} 字符</span></div>
        {data.plan_stale && <p className="feedback" role="alert">底层章节规划已变动，请重新生成总览后再扩写。<button className="button ghost" disabled={busy || !!run} onClick={() => launch("prepare")}>重新生成总览</button></p>}
        <textarea aria-label="写前总览" value={text} disabled={data.status !== "pending" || !!run} onChange={(event) => setText(event.target.value)} />
        <div className="human-actions"><button className="button primary" disabled={busy || !!run || !dirty || data.plan_stale} onClick={save}>保存总览</button><button className="button ghost" disabled={busy || !!run || dirty || data.status !== "pending" || data.plan_stale} onClick={() => launch("expand")}>确认总览并扩写</button><button className="button ghost" disabled={!!run} onClick={load}>重新载入</button></div>
        {dirty && <p className="feedback">有未保存修改；保存后才能提问或扩写。</p>}
        {data.status === "expanding" && <p className="feedback">已交给 AI 扩写。若任务已停止或失败，可用保存的总览重试。<button className="button ghost" disabled={busy || !!run} onClick={() => launch("expand")}>重试扩写</button></p>}
        {data.status === "archived" && <p className="feedback">本章已按确认的总览完成归档。</p>}
      </article>
      {data.status === "pending" && <article className="panel human-guide"><h2>问作者 AI</h2><p>指出逻辑、人物动机或场景安排的问题。作者会简短回答；你决定是否把建议写回总览。</p>
        <textarea className="overview-question" aria-label="向作者提问" placeholder="例如：这个人物为什么此时愿意交出钥匙？" value={question} onChange={(event) => setQuestion(event.target.value)} />
        <div className="human-actions"><button className="button ghost" disabled={busy || !!run || dirty || data.plan_stale || question.trim().length < 2} onClick={() => launch("ask")}>询问作者</button></div>
        {!!data.questions?.length && <div className="overview-answers">{data.questions.map((item, index) => <div key={index}><strong>你：{item.question}</strong><p>作者：{item.answer}</p></div>)}</div>}
      </article>}
    </>}
    {error && <p className="feedback" role="alert">{error}</p>}
  </div>;
}
