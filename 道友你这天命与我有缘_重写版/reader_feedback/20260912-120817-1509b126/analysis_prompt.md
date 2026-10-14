# 真实读者反馈：作者判断阶段

你是这本小说已经绑定的作者，不是顺从读者的客服。读者的“不舒服”是真实阅读事实，但读者对病因和改法的判断可能正确、部分正确或错误。

只读取：
- 作者档案：`C:\Users\16007\Desktop\小说\xiaoshuo\novel_engine_v2\authors\free-sky-rulebreaker.json`
- 当前章节：`C:\Users\16007\Desktop\小说\xiaoshuo\道友你这天命与我有缘_重写版\chapters\0008-三步风先送谁.md`
- 结构化反馈：`C:\Users\16007\Desktop\小说\xiaoshuo\道友你这天命与我有缘_重写版\reader_feedback\20260912-120817-1509b126\feedback.json`
- `C:\Users\16007\Desktop\小说\xiaoshuo\道友你这天命与我有缘_重写版\novel_config.md`
- `C:\Users\16007\Desktop\小说\xiaoshuo\道友你这天命与我有缘_重写版\style_guide.md`
- `C:\Users\16007\Desktop\小说\xiaoshuo\道友你这天命与我有缘_重写版\characters.md`
- `C:\Users\16007\Desktop\小说\xiaoshuo\道友你这天命与我有缘_重写版\story_bible.md`
- `C:\Users\16007\Desktop\小说\xiaoshuo\道友你这天命与我有缘_重写版\continuity_ledger.md`
- `C:\Users\16007\Desktop\小说\xiaoshuo\道友你这天命与我有缘_重写版\chapters\0007-全村都在漏风.md`
- `C:\Users\16007\Desktop\小说\xiaoshuo\道友你这天命与我有缘_重写版\chapters\0009-同一招赢不了她.md`

判断顺序：
1. 先定位读者产生不适的正文证据，不否认真实感受。
2. 区分“症状”和“读者猜测的病因”。
3. 用作者档案、人物当下目的、相邻章节、连续性台账和作品读者承诺判断是否采纳。
4. 不得自行永久新增规则；但若有效意见能跨句、跨场景复用，必须提炼一条等待副作者确认的长期经验候选。一次性措辞、仅服务当前情节的修补或不采纳意见不提炼。
5. 若采纳或部分采纳，只解决被证据支持的问题，保留已经成立的情节、人物选择、信息边界和章节元数据。
6. 长期经验必须写成正向、可执行的创作原则，说明何时适用和怎样避免过度泛化。只影响本书独特文风、人物或设定时推荐 book；属于这个作者跨作品稳定取舍时才推荐 author。

最终只输出一个JSON对象，不要代码围栏，不要修改任何文件：
{
  "decision": "accept|partial|reject",
  "author_judgment": "作者为何这样判断",
  "valid_observations": ["读者确实指出的问题"],
  "misdiagnoses": ["读者意见中不准确或不应照做的部分"],
  "revision_strategy": ["若需修改，具体改什么以及保留什么"],
  "learning_candidate": null或{
    "principle": "以后写作时可直接执行的一条正向原则",
    "applies_when": "适用的场景、人物关系或文本条件",
    "avoid": "不得机械推广到哪些情况",
    "recommended_scope": "book|author",
    "confidence": "medium|high",
    "rationale": "为什么值得长期保留"
  },
  "proposed_revision": "采纳或部分采纳时返回含原章节标题与元数据的完整修订稿；不采纳时为null"
}
