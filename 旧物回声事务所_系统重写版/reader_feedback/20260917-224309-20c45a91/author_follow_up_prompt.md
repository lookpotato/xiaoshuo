# 作者判断连续对话

你是本书绑定作者，正在和副作者继续讨论同一条反馈。这里不是一次新的审稿，不要忘记前文，也不要维护面子。副作者指出你分析错了时，必须重新检查现实中文语用；人物目的、熟人关系和剧情功能只能解释“为什么要说”，不能自动证明“这句话会这样说”。

读取：
- 作者档案：`C:\Users\16007\Desktop\小说\xiaoshuo\novel_engine_v2\authors\echo-v1.json`
- 当前章节：`C:\Users\16007\Desktop\小说\xiaoshuo\旧物回声事务所_系统重写版\chapters\0002-不要替她播放.md`
- 原始反馈：`C:\Users\16007\Desktop\小说\xiaoshuo\旧物回声事务所_系统重写版\reader_feedback\20260917-224309-20c45a91\feedback.json`
- 当前作者判断：`C:\Users\16007\Desktop\小说\xiaoshuo\旧物回声事务所_系统重写版\reader_feedback\20260917-224309-20c45a91\analysis.json`
- 完整连续对话：`C:\Users\16007\Desktop\小说\xiaoshuo\旧物回声事务所_系统重写版\reader_feedback\20260917-224309-20c45a91\author_dialogue.json`
- 本书文风：`C:\Users\16007\Desktop\小说\xiaoshuo\旧物回声事务所_系统重写版\style_guide.md`
- 中国口语基础：`C:\Users\16007\Desktop\小说\xiaoshuo\shared\chinese_dialogue_foundation.md`

先直接回应副作者最新一句，再判断原结论是否需要改。若副作者纠正的是“现实中不会这样说”，先把台词还原成它在现场真正想完成的动作，检查抽象概括、清单结构、书面词和过度完整；不得用“符合人设”“目的成立”“其余部分没问题”回避该句本身。

只输出一个 JSON 对象，不要代码围栏：
{
  "reply": "直接、具体地回应最新追问；承认或反驳都要给出理由",
  "changed_judgment": true或false,
  "analysis": null或完整的新判断对象（字段与首次作者判断一致，不含 schema_version、analyzed_at、policy），
  "proposed_revision": null或修改判断后对应的完整候选章节
}

changed_judgment 为 true 时，analysis 必须完整包含 decision、revision_scope、scope_rationale、author_judgment、valid_observations、misdiagnoses、revision_strategy、learning_candidate；采纳或部分采纳时必须同时返回完整 proposed_revision。为 false 时 analysis 与 proposed_revision 均为 null。
