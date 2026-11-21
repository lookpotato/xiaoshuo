import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";

function ChangeSummary({ summary, title = "保存后的对比总结", note = "这里只对比总览文本。剧情规划与后续接力是否已同步，请看下面的运行进度和完成结果。" }) {
  if (!summary) return null;
  const changes = summary.changes || [];
  return <section className="overview-comparison" aria-label="总览修改对比">
    <h3>{title}</h3>
    {summary.baseline && <p>{summary.baseline}</p>}
    <p>{changes.length ? `本次共 ${changes.length} 处变化：` + ["新增", "修改", "删除"].map((kind) => `${kind} ${changes.filter((item) => item.kind === kind).length} 处`).join("，") : "本次保存与上一版内容相同。"}</p>
    {changes.map((item, index) => <details key={index} open={changes.length <= 3}>
      <summary>{index + 1}. {item.kind}</summary>
      <div className="workspace-grid"><div><strong>保存前</strong><p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{item.before || "（无）"}</p></div><div><strong>保存后</strong><p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{item.after || "（已删除）"}</p></div></div>
    </details>)}
    {note && <p>{note}</p>}
  </section>;
}

export default function OverviewHandoffWorkspace({ book, apiVersion, onNotice, onRunStarted }) {
  const chapter = book.next_chapter_number;
  const [data, setData] = useState(null);
  const [text, setText] = useState("");
  const [question, setQuestion] = useState("");
  const [pendingQuestion, setPendingQuestion] = useState("");
  const [run, setRun] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const chatRef = useRef(null);
  const editorRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const result = await api(`/api/overview-handoff?book_id=${encodeURIComponent(book.id)}&chapter=${chapter}`);
      setData(result); setText(result.overview || ""); setError("");
    } catch (failure) { setError(failure.message); }
  }, [book.id, chapter]);
  useEffect(() => { setData(null); setRun(null); load(); }, [load]);
  useEffect(() => {
    if (!run && data?.learning?.status !== "running" && data?.last_report?.learning?.status !== "running" && data?.status !== "expanding" && !data?.questions?.some((item) => item.status === "running")) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const result = await api(`/api/overview-handoff?book_id=${encodeURIComponent(book.id)}&chapter=${chapter}`);
        setData((current) => current ? { ...current, questions: result.questions, status: result.status,
          progress: result.progress, change_summary: result.change_summary, result_summary: result.result_summary,
          plan_change_summary: result.plan_change_summary, understanding: result.understanding, learning: result.learning, last_report: result.last_report,
          proposal: result.proposal,
          plan_stale: result.plan_stale } : current);
      } catch (failure) { setError(failure.message); }
    }, 3000);
    return () => window.clearInterval(timer);
  }, [book.id, chapter, data?.questions?.some((item) => item.status === "running"), data?.status, data?.learning?.status, data?.last_report?.learning?.status, run?.id]);
  useEffect(() => {
    if (!run) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const result = await api("/api/runs");
        const current = result.runs.find((item) => item.id === run.id);
        if (current && current.status !== "running") {
          window.clearInterval(timer); setRun(null);
          await load();
          if (current.status === "success") onNotice(run.action === "prepare" ? "写前总览已生成，请先审阅和修改" : run.action === "summarize" ? "AI 理解总结已生成，请检查是否理解正确" : run.action === "ask" ? "作者已回答问题" : run.action === "revise" ? "总览修改稿已生成，请审阅后决定是否采用" : "章节扩写、归档与后续接力更新已完成");
          else {
            const failedLog = await api(`/api/run-log?run_id=${encodeURIComponent(run.id)}&tail=100`);
            setError(failedLog.error_summary || "任务未完成，请在控制台查看失败原因");
          }
        }
      } catch (failure) { setError(failure.message); }
    }, 3000);
    return () => window.clearInterval(timer);
  }, [run, load, onNotice]);

  async function launch(action, questionIndex) {
    if (busy || run) return;
    setBusy(true); setError("");
    try {
      let revision = data?.revision;
      if ((action === "expand" || action === "summarize") && text !== data.overview) {
        const saved = await api("/api/overview-handoff/save", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ book_id: book.id, chapter, content: text, revision }),
        });
        revision = saved.revision;
        setData((current) => ({ ...current, overview: text, revision, change_summary: saved.change_summary, understanding: saved.understanding }));
      }
      const result = await api(`/api/overview-handoff/${action}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ book_id: book.id, chapter, revision, question, question_index: questionIndex }),
      });
      setRun({ id: result.run.id, action }); onRunStarted(result.run);
      if (action === "ask") setQuestion("");
      onNotice(action === "prepare" ? "正在生成写前总览" : action === "summarize" ? "AI 正在总结你的修改意图与后续影响" : action === "ask" ? "作者正在回答" : action === "revise" ? "正在根据对话生成总览修改稿" : "正在按你确认的总览扩写");
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
      setData((current) => ({ ...current, overview: text, revision: result.revision, change_summary: result.change_summary, understanding: result.understanding }));
      onNotice("写前总览已保存；点击保存并扩写即可直接写章并总结长期经验");
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }

  async function askAuthor() {
    if (busy || run || question.trim().length < 2 || data?.plan_stale) return;
    setBusy(true); setError("");
    try {
      let revision = data.revision;
      if (text !== data.overview) {
        const saved = await api("/api/overview-handoff/save", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ book_id: book.id, chapter, content: text, revision }),
        });
        revision = saved.revision;
        setData((current) => ({ ...current, overview: text, revision, change_summary: saved.change_summary, understanding: saved.understanding }));
      }
      const result = await api("/api/overview-handoff/ask", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ book_id: book.id, chapter, revision, question }),
      });
      setRun({ id: result.run.id, action: "ask" }); onRunStarted(result.run);
      setPendingQuestion(question.trim());
      setQuestion(""); onNotice("当前总览已保存，作者正在回答");
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }

  function adoptProposal() {
    if (!data?.proposal || text !== data.overview) return;
    setText(data.proposal.content);
    editorRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    editorRef.current?.focus();
    onNotice("修改稿已放入总览编辑框；请审阅后保存，作者回答本身不会自动改动总览");
  }

  const dirty = data?.status === "pending" && text !== data.overview;
  const learning = data?.learning || data?.last_report?.learning;
  const exchanges = data?.questions || [];
  const lastExchange = exchanges[exchanges.length - 1];
  const showLocalPending = pendingQuestion && run?.action === "ask" &&
    !(lastExchange?.question === pendingQuestion && lastExchange?.status === "running");
  useEffect(() => {
    if (chatRef.current) chatRef.current.scrollTop = chatRef.current.scrollHeight;
  }, [exchanges.length, lastExchange?.status, showLocalPending]);
  return <div className="human-workspace overview-handoff">
    <article className="panel human-intro">
      <div className="panel-head"><div><p className="eyebrow">AUTHOR HANDOFF</p><h2>写前总览接力</h2></div><span className="local-badge">第 {chapter} 章</span></div>
      <p>先生成章节规划、人物私线和场景粗稿，扩写会暂停在这里。你可以重写总览，向内置作者 AI 询问不合理之处；对话后可生成修改稿，审阅并保存后再扩写正文。</p>
      {apiVersion < 15 && <p className="feedback" role="alert">请重启 Python 工作台服务，加载长期经验总结功能。已保存的总览会保留。</p>}
    </article>
    {(busy || run || data?.progress || data?.change_summary || data?.result_summary || data?.last_report) && <article className="panel human-guide" aria-label="总览接力进度与对比">
      <div role="status" aria-live="polite"><strong>{busy ? "正在保存或提交任务，请稍候……" : run || data?.status === "expanding" ? (data?.progress?.stage || (run?.action === "ask" ? "作者正在回答……" : run?.action === "prepare" ? "正在生成总览……" : "正在启动扩写与剧情同步……")) : data?.progress?.stage || "总览已保存，等待确认扩写"}</strong>
      {(run || data?.status === "expanding") && <p>任务运行中，进度每 3 秒更新。当前阶段可能需要几分钟，请勿重复提交。</p>}</div>
      <details><summary>查看原文差异</summary><ChangeSummary summary={data?.change_summary || data?.last_report?.change_summary} /></details>
      <ChangeSummary summary={data?.plan_change_summary || data?.last_report?.plan_change_summary} title="本章规划同步前后对比" note="这是实际写入章节规划的变化。" />
      {(data?.result_summary || data?.last_report) && (() => { const result = data.result_summary || data.last_report; return <section><h3>第 {result.chapter} 章后续剧情更新结果</h3><p style={{ whiteSpace: "pre-wrap" }}>{result.next_hook || "未返回后续接力内容。"}</p><ChangeSummary summary={result.handoff_change_summary} title="后续接力更新前后对比" note="" /><p>已备份并撤销 {(result.invalidated || []).length} 份后续旧材料。</p>{result.invalidated?.length > 0 && <details><summary>查看已撤销材料</summary><ul>{result.invalidated.map((path) => <li key={path}>{path}</li>)}</ul></details>}</section>; })()}
    </article>}
    {learning && <article className="panel human-guide" aria-label="本次修改的长期总结">
      <h2>本次修改吸收的长期经验</h2>
      {learning.status === "running" ? <p role="status">正文已归档，AI 正在总结修改原因、可复用原则和后续适用条件……</p> : learning.status === "failed" ? <p role="alert">扩写归档已完成，长期总结未写入：{learning.error}</p> : <><p style={{ whiteSpace: "pre-wrap" }}>{learning.summary}</p>{learning.lessons?.map((item, index) => <section key={index}><h3>{index + 1}. {item.problem}</h3><p>{item.principle}</p><p>适用：{item.applies_when}</p><p>避免误用：{item.avoid}</p></section>)}<p>已记入本书经验库，下次生成总览会读取这些原则。</p></>}
    </article>}
    {data?.status === "none" ? <article className="panel human-guide"><h2>准备第 {chapter} 章</h2><p>生成写前材料可能需要几分钟，完成后会在本页显示可修改的总览。</p><button className="button primary" disabled={busy || !!run || apiVersion < 12} onClick={() => launch("prepare")}>{run?.action === "prepare" ? "正在生成……" : "生成写前总览"}</button></article> : data && <>
      <article className="panel human-editor"><div className="panel-head"><div><p className="eyebrow">YOUR OVERVIEW</p><h2>修改本章总览</h2></div><span>{text.length} 字符</span></div>
        {data.plan_stale && <p className="feedback" role="alert">底层章节规划已变动，请重新生成总览后再扩写。<button className="button ghost" disabled={busy || !!run} onClick={() => launch("prepare")}>重新生成总览</button></p>}
        {(data.term_note || (book.id === "night-floor" && text.includes("原表"))) && <div className="overview-term-note"><strong>这里的“原表”是什么？</strong><p>{data.term_note || "街道手里的二零二旧登记表：二零二一栏写着‘已退出’，但刘桂芳仍实际居住。街道已暂停按空户处理，原表还没改；旧凭据号与郭冬梅母亲的收条末四位相同，错记原因仍未查清。"}</p></div>}
        <textarea ref={editorRef} aria-label="写前总览" value={text} disabled={data.status !== "pending" || !!run} onChange={(event) => setText(event.target.value)} />
        <div className="human-actions"><button className="button primary" disabled={busy || !!run || apiVersion < 15 || data.status !== "pending" || data.plan_stale} onClick={save}>保存总览</button><button className="button ghost" disabled={busy || !!run || apiVersion < 15 || data.status !== "pending" || data.plan_stale} onClick={() => launch("expand")}>保存总览并扩写</button><button className="button ghost" disabled={!!run} onClick={load}>重新载入</button></div>
        <p className="feedback">扩写前会同步本章规划与人物线；归档后按最终正文更新后续接力，后续旧规划将备份并重新生成。</p>
        {dirty && <p className="feedback">有未保存修改，点击保存总览并扩写会自动保存后直接扩写。</p>}
        {data.status === "expanding" && <p className="feedback">已交给 AI 扩写。若任务已停止或失败，可用保存的总览重试。<button className="button ghost" disabled={busy || !!run} onClick={() => launch("expand")}>重试扩写</button></p>}
        {data.status === "archived" && <p className="feedback">本章已按确认的总览完成归档，后续接力已按最终正文更新。</p>}
      </article>
      {(data.status === "pending" || exchanges.length > 0 || pendingQuestion) && <article className="panel human-guide"><h2>与作者讨论</h2><p>指出逻辑、人物动机或场景安排的问题。提问时会自动保存当前总览；回复会直接显示在下面，修改稿需由你审阅后采用。</p>
        {data.status === "pending" && <><textarea className="overview-question" aria-label="向作者提问" placeholder="例如：这个人物为什么此时愿意交出钥匙？" value={question} onChange={(event) => setQuestion(event.target.value)} />
        <div className="human-actions"><button className="button ghost" disabled={busy || !!run || data.plan_stale || question.trim().length < 2} onClick={askAuthor}>询问作者</button></div></>}
        <div className="overview-answers" aria-live="polite" ref={chatRef}>{exchanges.map((item, index) => <div className="overview-exchange" key={index}><p className="overview-user"><strong>你</strong><span>{item.question}</span></p><p className="overview-author"><strong>作者 AI</strong><span>{item.status === "running" ? "正在思考并组织回答……" : item.status === "failed" ? "回答失败，请重新提问或查看控制台。" : item.answer}</span></p>{data.status === "pending" && item.status === "answered" && <button className="button ghost" disabled={busy || !!run || apiVersion < 17 || data.plan_stale || dirty} onClick={() => launch("revise", index)}>按这条答复修改总览</button>}</div>)}{showLocalPending && <div className="overview-exchange"><p className="overview-user"><strong>你</strong><span>{pendingQuestion}</span></p><p className="overview-author"><strong>作者 AI</strong><span>正在思考并组织回答……</span></p></div>}</div>
        {dirty && exchanges.some((item) => item.status === "answered") && <p className="feedback">先保存编辑框里尚未保存的改动，再根据作者答复生成修改稿。</p>}
        {apiVersion < 17 && exchanges.some((item) => item.status === "answered") && <p className="feedback" role="alert">请重启 Python 工作台服务，加载对话修改功能。</p>}
        {data.status === "pending" && data.proposal && <section className="overview-proposal"><h3>根据对话生成的总览修改稿</h3><ChangeSummary summary={data.proposal.comparison} title="建议修改的内容" note="修改稿尚未保存，不会自动进入章节扩写。" /><button className="button primary" disabled={busy || !!run || dirty || !data.proposal.comparison?.count} onClick={adoptProposal}>放入总览编辑框</button></section>}
      </article>}
    </>}
    {error && <p className="feedback" role="alert">{error}</p>}
  </div>;
}
