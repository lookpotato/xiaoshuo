import { useState } from "react";

const modes = { off: "关闭", review: "观察", enforce: "强制" };

export default function CreativeModules({ draft, onChange }) {
  const [selected, setSelected] = useState("world_presentation");
  const [filter, setFilter] = useState("");
  const [tab, setTab] = useState("guide");
  const document = draft.documents.find((item) => item.id === "creative_modules.json");
  if (!document || !draft.creative_catalog) return null;
  let config;
  try {
    config = document.content.trim() ? JSON.parse(document.content) : { schema_version: 1, report_contract: 2, modules: {} };
    if (!config || !config.modules || typeof config.modules !== "object" || Array.isArray(config.modules)) throw new Error();
  } catch {
    return <div className="settings-error">创作能力配置格式错误，请在下方文件编辑器修正 JSON。</div>;
  }
  const entries = Object.entries(draft.creative_catalog.modules);
  const visible = entries.filter(([, m]) => `${m.title} ${m.purpose}`.includes(filter.trim()));
  const [id, module] = entries.find(([key]) => key === selected) || entries[0] || [];
  if (!module) return <div className="empty">能力库暂时为空</div>;
  const defaultValue = { mode: "off", from_chapter: draft.creative_next_chapter || 1, review_interval: 5, focus: "" };
  const value = { ...defaultValue, ...config.modules[id] };
  const guide = module.guide;
  const run = draft.creative_activity?.[id];
  const enabled = entries.filter(([key]) => config.modules[key]?.mode && config.modules[key].mode !== "off").length;
  const saveConfig = (next) => onChange(document.id, JSON.stringify(next, null, 2) + "\n");
  const update = (patch) => saveConfig({ ...config, modules: { ...config.modules, [id]: { ...value, ...patch } } });
  return <article className="panel creative-workspace">
    <header className="creative-header"><div><p className="eyebrow">WRITING LAB</p><h2>创作能力装配</h2><p>先选一个阅读问题，再看具体做法与检查依据。</p></div><span className="count-label">已配置启用 {enabled} / {entries.length}</span></header>
    <div className="creative-disclaimer"><strong>阅读效果待验证</strong><span>这些能力会参与写作与审稿。程序核对报告和引用，AI判断文字含义；目前没有读者对照实验能证明开关后的提升。</span></div>
    <div className="creative-layout">
      <nav className="creative-list" aria-label="创作能力列表">
        <input aria-label="搜索创作能力" placeholder="搜索名称或问题…" value={filter} onChange={(e) => setFilter(e.target.value)} />
        {visible.map(([key, m]) => <button type="button" key={key} aria-pressed={id === key} className={id === key ? "selected" : ""} onClick={() => { setSelected(key); setTab("guide"); }}><span><strong>{m.title}</strong><i className={`creative-mode ${config.modules[key]?.mode || "off"}`}>{modes[config.modules[key]?.mode || "off"]}</i></span><small>{m.purpose}</small></button>)}
        {!visible.length && <p className="empty">没有匹配的能力</p>}
      </nav>
      <section className="creative-detail" aria-label="能力详情">
        <header><span className="creative-kicker">{id === "world_presentation" ? "有专用认知检查 · 效果未验证" : "写作与审稿提示 · 效果未验证"}</span><h3>{module.title}</h3><p>{guide?.symptom || module.purpose}</p></header>
        <div className="creative-tabs" role="tablist" aria-label="能力内容">
          {[["guide", "怎么起作用"], ["checks", "怎样判断有用"], ["settings", "本书配置"]].map(([key, label]) => <button type="button" role="tab" aria-selected={tab === key} aria-controls={`creative-panel-${key}`} id={`creative-tab-${key}`} tabIndex={tab === key ? 0 : -1} key={key} onClick={() => setTab(key)} onKeyDown={(e) => { const keys = ["guide", "checks", "settings"]; let next; if (e.key === "ArrowRight") next = keys[(keys.indexOf(key) + 1) % 3]; if (e.key === "ArrowLeft") next = keys[(keys.indexOf(key) + 2) % 3]; if (e.key === "Home") next = keys[0]; if (e.key === "End") next = keys[2]; if (next) { e.preventDefault(); setTab(next); globalThis.document.getElementById(`creative-tab-${next}`)?.focus(); } }}>{label}</button>)}
        </div>
        <div className="creative-tab-content" role="tabpanel" id={`creative-panel-${tab}`} aria-labelledby={`creative-tab-${tab}`}>
          {tab === "guide" && <>
            <h4>写作时具体做什么</h4>
            <ol className="creative-steps">{(guide?.steps || [module.input, module.plan, module.review]).map((step, i) => <li key={i}>{step}</li>)}</ol>
            {guide?.example && <><h4>改写示意</h4><div className="creative-example"><section><span>信息不足</span><p>{guide.example.before}</p></section><section><span>补足理解的一种写法</span><p>{guide.example.after}</p></section></div><p className="creative-caption">{guide.example.note}</p></>}
            <div className="creative-boundary"><h4>使用边界</h4><p>{module.limits}</p></div>
          </>}
          {tab === "checks" && <>
            <h4>审稿必须逐项回答</h4>
            <ul className="creative-questions">{(guide?.checks || [{ id: "review", question: module.review }]).map((check) => <li key={check.id}>{check.question}</li>)}</ul>
            <p>每项要写结论、原句，以及原句如何支持结论。有缺口就标明修订位置和动作。逐项回答在报告协议2下由程序检查。</p>
            <div className="creative-boundary"><h4>程序能查什么</h4><p>报告是否缺失、正文是否变更、引用是否真的存在。世界观另查五个认知维度与复盘周期。</p><h4>仍需阅读判断</h4><p>引用是否足以支持结论、场景是否自然、读者是否更愿意读。填了“通过”不代表这些问题已经解决。</p></div>
            <section className="creative-run"><h4>本书最近记录</h4>{run ? <><p>第 {run.chapter} 章 · {run.chapter < value.from_chapter ? "配置起始章之前的记录" : "已保存报告"} · {run.review_method === "same-context-evidence-review" ? "同一上下文自审" : run.review_method === "independent-context-review" ? "报告声明独立审阅" : "审阅方式未标明"}</p><p>{run.assessment || "没有具体分析"}</p><small>仅展示报告中的自述，未据此判定文学效果或当前验收状态。</small></> : <p>最近50份报告中没有此能力的记录，尚无可展示的运行依据。</p>}</section>
          </>}
          {tab === "settings" && <div className="creative-config">
            <label>参与方式<select value={value.mode} disabled={!!draft.locked} onChange={(e) => update({ mode: e.target.value })}><option value="off">关闭：不参与生成</option><option value="review">观察：记录问题，允许继续</option><option value="enforce">强制：报告的问题需修复</option></select></label>
            <label>从第几章生效<input type="number" min="1" max="100000" value={value.from_chapter} disabled={!!draft.locked} onChange={(e) => update({ from_chapter: Number(e.target.value) })} /><small>提前起始章会要求对应历史章节补交报告。</small></label>
            {id === "world_presentation" && <label>每隔几章复盘<input type="number" min="1" max="100000" value={value.review_interval} disabled={!!draft.locked} onChange={(e) => update({ review_interval: Number(e.target.value) })} /></label>}
            <label className="creative-full">本书重点与边界<textarea rows="4" placeholder="例如：先交代居民靠什么生活，保留灾难来源的谜底。" value={value.focus} disabled={!!draft.locked} onChange={(e) => update({ focus: e.target.value })} /></label>
            <label className="creative-full">本书报告协议（适用于所有已启用能力）<select value={config.report_contract || 1} disabled={!!draft.locked} onChange={(e) => saveConfig({ ...config, report_contract: Number(e.target.value) })}><option value={1}>协议1：总评与引用</option><option value={2}>协议2：逐项回答、支撑解释与修订动作</option></select><small>升级会重新检查生效范围内的已有报告。观察模式同样要求报告和证据完整。</small></label>
            <p className="creative-full creative-caption">修改后点击页面顶部“保存设置”。{draft.locked ? "当前任务运行中，暂时只读。" : ""}</p>
          </div>}
        </div>
      </section>
    </div>
  </article>;
}
