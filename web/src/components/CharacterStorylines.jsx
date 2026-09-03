import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";

export default function CharacterStorylines({ bookId, onNotice }) {
  const [data, setData] = useState(null);
  const [activeEntryId, setActiveEntryId] = useState("");
  const [content, setContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const requestVersion = useRef(0);
  const activeEntry = useMemo(() => data?.entries.find((entry) => entry.id === activeEntryId), [data, activeEntryId]);
  const dirty = Boolean(activeEntry && content !== activeEntry.content);

  async function load(character = "") {
    const version = ++requestVersion.current;
    setBusy(true); setError("");
    try {
      const query = new URLSearchParams({ book_id: bookId });
      if (character) query.set("character", character);
      const next = await api(`/api/character-stories?${query}`);
      if (version !== requestVersion.current) return;
      setData(next);
      const entry = next.entries[0];
      setActiveEntryId(entry?.id || "");
      setContent(entry?.content || "");
    } catch (nextError) {
      if (version === requestVersion.current) setError(nextError.message);
    } finally {
      if (version === requestVersion.current) setBusy(false);
    }
  }

  useEffect(() => {
    setData(null); setActiveEntryId(""); setContent("");
    load();
    return () => { requestVersion.current += 1; };
  }, [bookId]);

  function selectEntry(entry) {
    if (dirty && !window.confirm("当前人物私线还没保存，确定切换吗？")) return;
    setActiveEntryId(entry.id); setContent(entry.content);
  }

  async function save() {
    if (!activeEntry || busy || !dirty) return;
    setBusy(true); setError("");
    try {
      const result = await api("/api/character-story", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ book_id: bookId, character: data.selected_character, entry_id: activeEntry.id, revision: activeEntry.revision, content }),
      });
      setData(result.stories);
      const savedEntry = result.stories.entries.find((entry) => entry.id === activeEntry.id) || result.stories.entries[0];
      setActiveEntryId(savedEntry?.id || ""); setContent(savedEntry?.content || "");
      onNotice(`${data.selected_character}的人物私线已保存`);
    } catch (nextError) { setError(nextError.message); onNotice(nextError.message); }
    finally { setBusy(false); }
  }

  if (!data) return <article className="panel settings-loading">{error || "正在整理人物独立故事线……"}</article>;
  return <article className="panel settings-section character-story-panel">
    <div className="panel-head"><div><p className="eyebrow">CHARACTER STORYLINES</p><h2>人物独立故事线</h2><p className="section-description">按人物聚合每章私线。这里的行动、误解、代价和下一步会参与后续章节生成，但不会上传到番茄正文。</p></div><span className="count-label">{data.characters.length} 人</span></div>
    {data.locked && <div className="settings-lock"><strong>只读</strong><span>{data.locked.message}</span></div>}
    {error && <div className="settings-error">{error}</div>}
    {data.characters.length ? <div className="character-story-layout">
      <nav className="character-roster" aria-label="人物列表">{data.characters.map((character) => <button type="button" className={character.name === data.selected_character ? "active" : ""} key={character.name} onClick={() => load(character.name)}><strong>{character.name}</strong><small>第 {character.first_chapter}—{character.latest_chapter} 章</small><i>{character.entry_count} 条私线</i></button>)}</nav>
      <section className="character-timeline">
        <div className="storyline-head"><div><strong>{data.selected_character}</strong><small>独立行动时间轴 · 最新在前</small></div><button className="button save-settings" disabled={busy || !dirty || data.locked} onClick={save}>{busy ? "处理中" : "保存本条私线"}</button></div>
        <div className="timeline-list">{data.entries.map((entry) => <button type="button" className={entry.id === activeEntryId ? "active" : ""} key={entry.id} onClick={() => selectEntry(entry)}><b>第 {entry.chapter} 章</b><span>{entry.summary}</span></button>)}</div>
        {activeEntry ? <div className="storyline-editor"><div><code>{activeEntry.id}</code><span>{dirty ? "有未保存修改" : "已同步"}</span></div><textarea aria-label={`${data.selected_character}第${activeEntry.chapter}章人物私线`} spellCheck="false" value={content} onChange={(event) => setContent(event.target.value)} /></div> : <div className="empty">这个人物还没有可编辑的章节私线</div>}
      </section>
    </div> : <div className="empty">本书还没有人物私线。生成章节后，系统会按人物自动建立。</div>}
  </article>;
}
