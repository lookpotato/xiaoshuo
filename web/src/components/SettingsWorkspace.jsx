import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import CharacterStorylines from "./CharacterStorylines";

const clone = (value) => JSON.parse(JSON.stringify(value));

function Field({ label, hint, children }) {
  return <label className="setting-field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>;
}

function DocumentEditor({ documents, activeId, onSelect, onChange }) {
  const active = documents.find((item) => item.id === activeId) || documents[0];
  if (!active) return <div className="empty">当前没有可编辑的规则文件</div>;
  return <div className="document-workspace">
    <nav className="document-list" aria-label="配置文件">
      {documents.map((document) => <button type="button" className={document.id === active.id ? "active" : ""} key={document.id} onClick={() => onSelect(document.id)}><strong>{document.title}</strong><small>{document.id}</small><i>{document.exists ? "已启用" : "可新建"}</i></button>)}
    </nav>
    <section className="document-editor">
      <div className="document-title"><div><strong>{active.title}</strong><small>{active.description}</small></div><code>{active.id}</code></div>
      <textarea aria-label={`${active.title}内容`} spellCheck="false" value={active.content} onChange={(event) => onChange(active.id, event.target.value)} />
      <p>保存后，下一次章节任务会直接读取这个模块。单文件最大 20 万字。</p>
    </section>
  </div>;
}

function SystemGeneral({ value, books, onChange }) {
  const update = (key, next) => onChange({ ...value, [key]: next });
  return <div className="settings-form-grid">
    <Field label="默认小说"><select value={value.default_book_id || ""} onChange={(event) => update("default_book_id", event.target.value)}>{books.map((book) => <option key={book.id} value={book.id}>{book.title}</option>)}</select></Field>
    <Field label="时区"><input value={value.timezone || ""} onChange={(event) => update("timezone", event.target.value)} /></Field>
    <Field label="全局锁时长（分钟）"><input type="number" min="10" max="1440" value={value.global_lock_minutes} onChange={(event) => update("global_lock_minutes", Number(event.target.value))} /></Field>
    <Field label="每日最大尝试"><input type="number" min="1" max="20" value={value.max_daily_attempts} onChange={(event) => update("max_daily_attempts", Number(event.target.value))} /></Field>
    <Field label="失败重试间隔（分钟）"><input type="number" min="0" max="1440" value={value.retry_delay_minutes} onChange={(event) => update("retry_delay_minutes", Number(event.target.value))} /></Field>
    <Field label="默认失败策略"><input value={value.default_failure_policy || ""} onChange={(event) => update("default_failure_policy", event.target.value)} /></Field>
  </div>;
}

function BookGeneral({ value, onChange }) {
  const update = (key, next) => onChange({ ...value, [key]: next });
  return <div className="settings-form-grid">
    <Field label="书名"><input value={value.title || ""} onChange={(event) => update("title", event.target.value)} /></Field>
    <Field label="运行模式"><select value={value.mode} onChange={(event) => update("mode", event.target.value)}><option value="write_only">仅本地创作</option><option value="write_then_upload">创作并可上传</option></select></Field>
    <Field label="每日章节数"><input type="number" min="1" max="20" value={value.daily_chapter_target} onChange={(event) => update("daily_chapter_target", Number(event.target.value))} /></Field>
    <Field label="任务优先级"><input type="number" min="0" max="10000" value={value.priority} onChange={(event) => update("priority", Number(event.target.value))} /></Field>
    <Field label="读者门禁起始章"><input type="number" min="1" value={value.reader_gate_from_chapter} onChange={(event) => update("reader_gate_from_chapter", Number(event.target.value))} /></Field>
    <Field label="默认发布时间"><input value={value.schedule_time || ""} onChange={(event) => update("schedule_time", event.target.value)} /></Field>
    <Field label="每日发布时间" hint="多个时间用逗号分隔"><input value={(value.default_publish_times || []).join(", ")} onChange={(event) => update("default_publish_times", event.target.value.split(/[,，]/).map((item) => item.trim()).filter(Boolean))} /></Field>
    <Field label="启用本书"><label className="switch-line"><input type="checkbox" checked={value.enabled} onChange={(event) => update("enabled", event.target.checked)} />参与批量任务</label></Field>
    <Field label="备注"><textarea className="short-textarea" value={value.note || ""} onChange={(event) => update("note", event.target.value)} /></Field>
  </div>;
}

export default function SettingsWorkspace({ scope, books, bookId, onBookChange, onNotice, onSaved }) {
  const [settings, setSettings] = useState(null);
  const [draft, setDraft] = useState(null);
  const [activeDocument, setActiveDocument] = useState("");
  const [policyText, setPolicyText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const requestVersion = useRef(0);
  const query = scope === "system" ? "scope=system" : `scope=book&book_id=${encodeURIComponent(bookId)}`;

  async function load() {
    const version = ++requestVersion.current;
    setBusy(true); setError("");
    try {
      const next = await api(`/api/settings?${query}`);
      if (version !== requestVersion.current) return;
      setSettings(next); setDraft(clone(next));
      setPolicyText(next.writing_policy ? JSON.stringify(next.writing_policy, null, 2) : "");
      setActiveDocument((current) => next.documents.some((item) => item.id === current) ? current : next.documents[0]?.id || "");
    } catch (nextError) {
      if (version === requestVersion.current) setError(nextError.message);
    } finally {
      if (version === requestVersion.current) setBusy(false);
    }
  }

  useEffect(() => {
    load();
    return () => { requestVersion.current += 1; };
  }, [scope, bookId]);
  const dirty = useMemo(() => settings && draft && (JSON.stringify(settings) !== JSON.stringify(draft) || (scope === "system" && policyText !== JSON.stringify(settings.writing_policy, null, 2))), [settings, draft, policyText, scope]);

  function updateDocument(id, content) {
    setDraft((current) => ({ ...current, documents: current.documents.map((item) => item.id === id ? { ...item, content } : item) }));
  }

  async function save() {
    if (!draft || busy) return;
    setBusy(true); setError("");
    try {
      let writingPolicy;
      if (scope === "system") {
        try { writingPolicy = JSON.parse(policyText); }
        catch { throw new Error("底层模块 JSON 格式不正确"); }
      }
      const payload = {
        scope,
        book_id: scope === "book" ? bookId : undefined,
        config_revision: draft.config_revision,
        general: draft.general,
        registry: draft.registry,
        writing_policy: writingPolicy,
        documents: draft.documents.filter((item) => item.exists || item.content.trim()).map(({ id, revision, content }) => ({ id, revision, content })),
      };
      const result = await api("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      setSettings(result.settings); setDraft(clone(result.settings));
      setPolicyText(result.settings.writing_policy ? JSON.stringify(result.settings.writing_policy, null, 2) : "");
      onNotice("设置已保存，将从下一次章节任务开始生效"); onSaved();
    } catch (nextError) { setError(nextError.message); onNotice(nextError.message); }
    finally { setBusy(false); }
  }

  const draftMatchesScope = draft?.scope === scope && (scope === "system" || draft.book_id === bookId);
  if (!draftMatchesScope) return <article className="panel settings-loading">{error || "正在读取模块化配置……"}</article>;
  return <section className="settings-page">
    <article className="panel settings-hero">
      <div><p className="eyebrow">{scope === "system" ? "SYSTEM SETTINGS" : "BOOK SETTINGS"}</p><h2>{scope === "system" ? "番茄系统设置" : `${draft.registry.title} · 小说设置`}</h2><p>{scope === "system" ? "控制所有小说共享的底层能力。每项规则按模块装配，不绑定某一本书。" : "控制这本书独有的文风、人物组织方式、世界观和系统提示词，不影响其他小说。"}</p></div>
      <div className="settings-actions"><button className="button ghost" onClick={load} disabled={busy}>重新读取</button><button className="button save-settings" onClick={save} disabled={busy || !dirty || draft.locked}>{busy ? "处理中" : "保存设置"}</button></div>
    </article>
    {draft.locked && <div className="settings-lock"><strong>设置已锁定</strong><span>{draft.locked.message} · {draft.locked.book_id || "当前任务"}</span></div>}
    {error && <div className="settings-error">{error}</div>}
    {scope === "book" && <div className="book-settings-switch"><label>当前小说<select value={bookId} onChange={(event) => onBookChange(event.target.value)}>{books.map((book) => <option key={book.id} value={book.id}>{book.title}</option>)}</select></label><code>{draft.registry.id} · {draft.registry.path}</code></div>}
    <article className="panel settings-section">
      <div className="panel-head"><div><p className="eyebrow">BASICS</p><h2>{scope === "system" ? "运行底座" : "作品运行参数"}</h2></div></div>
      {scope === "system" ? <SystemGeneral value={draft.general} books={books} onChange={(general) => setDraft({ ...draft, general })} /> : <BookGeneral value={draft.registry} onChange={(registry) => setDraft({ ...draft, registry })} />}
    </article>
    {scope === "book" && <CharacterStorylines bookId={bookId} onNotice={onNotice} />}
    {scope === "system" && <article className="panel settings-section">
      <div className="panel-head"><div><p className="eyebrow">MODULE REGISTRY</p><h2>底层能力模块</h2></div><span className="count-label">{draft.modules.length} 个模块</span></div>
      <div className="module-grid">{draft.modules.map((module) => <div className="module-card" key={module.id}><span className={module.enabled ? "module-dot enabled" : "module-dot"} /><div><strong>{module.id}</strong><small>{module.rules.length ? module.rules.join(" · ") : `${module.field_count} 个内联规则`}</small></div></div>)}</div>
      <Field label="模块注册表（JSON）" hint="可增加新模块或调整规则；共享文档仍保持独立文件"><textarea className="policy-editor" spellCheck="false" value={policyText} onChange={(event) => setPolicyText(event.target.value)} /></Field>
    </article>}
    <article className="panel settings-section">
      <div className="panel-head"><div><p className="eyebrow">PROMPT MODULES</p><h2>{scope === "system" ? "共享底层提示模块" : "单书创作模块"}</h2></div><span className="count-label">{draft.documents.filter((item) => item.exists).length}/{draft.documents.length}</span></div>
      <DocumentEditor documents={draft.documents} activeId={activeDocument} onSelect={setActiveDocument} onChange={updateDocument} />
    </article>
  </section>;
}
