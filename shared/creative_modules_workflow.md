# 创作能力模块运行协议

这是共享能力协议。各书 creative_modules.json 决定实际装配；模块库本身不代表全部启用。
现有人物线、读者门禁、文风和资源账本继续作为事实来源，模块不得维护相互矛盾的副本。

## 配置与调度

模块定义位于 shared/creative_modules.json，每项都有 purpose/input/plan/review/limits。
单书配置格式：

```json
{
  "schema_version": 1,
  "modules": {
    "world_presentation": {
      "mode": "enforce",
      "from_chapter": 74,
      "review_interval": 5,
      "focus": "原创世界；读者没有预备设定知识；优先空间、生活和社会秩序"
    }
  }
}
```

- off：不注入、不要求报告。
- review：参与规划和验收，允许报告文学问题；报告与证据仍须真实完整。
- enforce：参与规划和验收，有待修问题时阻止归档流程完成和后续上传。
- from_chapter：此章起生效。修改既有生效范围会重新要求该范围的报告；不要无意把新模块应用到全部旧章。
- review_interval：世界观模块从首次启用章开始，每隔此数量章节进行跨章复盘。
- focus：本书重点、读者预备知识、保密边界和风格偏好。不得将世界设定或读者已知事实仅写在此处。

协调顺序：既有事实和人物知识边界 → 当前情节必需的理解 → 本章目标与人物选择 → 悬念、情绪与节奏 → 局部修辞。
信息交代与保密冲突时，交代眼前用途和代价，保留深层来源。节奏与理解冲突时，保留最小理解桥梁。
不设统一对白比例、不强制每章展示所有模块的戏剧效果。非世界观模块可以 not_applicable，但必须说明具体原因。

## 写前

1. 读取本书配置和当前装配模块的输入。获取上一次已归档章节的 module_reports，按模块读取最近可用状态；失败草稿的状态不得视为已发生。
2. 世界观模块首次启用时，回查本书前几章及涉及当前规则的历史正文，至少审视空间、社会秩序、普通人生活、主角位置、特殊规则五个维度。看不到证据就记 unknown，不能把设定文件当成正文。
3. 后续累计认知继承上次快照；本章更新、修正或增加时要给新证据。旧正文修订后重新核验证据及哈希。认知条目允许多条，避免把多条规则压缩为一个空泛摘要。
4. 建立 module_plans/NNNN.json，先写本章任务、模块冲突取舍，再为每个启用模块写具体安排。不能复制泛泛规则冒充本章计划。

```json
{
  "schema_version": 1,
  "chapter_number": 74,
  "chapter_goal": "本章具体目标",
  "coordination": "模块之间的取舍及原因",
  "modules": {
    "world_presentation": "缺少什么理解；在哪个具体场景，通过什么事件或叙述补足；哪些秘密仍保留"
  }
}
```

## 写作与验收

按照计划写作，世界通过人物生活、工作、交通、交易、制度和实际后果呈现。允许简明旁白直接说明常识；避免所有背景都让人物说出来。
定稿后，读者验收阶段只依据已发表范围内的正文与本章正文形成判断。AI已经读过设定时，不可声称完成了真正隔离的盲读；应尽可能由独立上下文审阅，并明确当前审阅方式。
世界观验收既问本章能否理解，也问累计读到此处能否说出世界怎样运转。不是每章重新介绍世界；五维度记录是后台认知状态。

写 module_reports/NNNN.json。所有启用模块都应包含以下字段：

```json
{
  "schema_version": 1,
  "chapter_number": 74,
  "narrative_sha256": "用 novel_reader_gate.narrative_sha256(正文路径) 计算",
  "plan_sha256": "用 hashlib.sha256(计划文件.read_bytes()).hexdigest() 计算",
  "review_method": "same-context-evidence-review 或 independent-context-review，按实际填写",
  "modules": {
    "world_presentation": {
      "status": "passed",
      "assessment": "基于正文的具体分析，不写空泛自评",
      "evidence": [{"chapter": 74, "quote": "至少六字且逐字存在的正文原句", "narrative_sha256": "该证据章节正文哈希"}],
      "issues": [],
      "cross_chapter_review": "首次及到期复盘：读者目前能复述的世界、仍缺什么、准备怎样补足；比较前次改善与退化",
      "state": {
        "reader_knowledge": [
          {"dimension": "空间", "status": "known", "summary": "具体已知内容", "evidence": [{"chapter": 1, "quote": "历史正文原句", "narrative_sha256": "历史正文哈希"}]},
          {"dimension": "社会秩序", "status": "unknown", "summary": "目前缺乏正文依据", "evidence": [], "resolve_by": 76, "next_action": "在办理通行的场景补足；不涉及幕后谜底"},
          {"dimension": "普通人生活", "status": "unknown", "summary": "目前缺乏正文依据", "evidence": [], "resolve_by": 76, "next_action": "借任务相关的交易呈现"},
          {"dimension": "主角位置", "status": "unknown", "summary": "需回查主角身份呈现", "evidence": [], "resolve_by": 75, "next_action": "回查正文或补足身份影响"},
          {"dimension": "特殊规则", "status": "unknown", "summary": "需核实眼前规则的解释", "evidence": [], "resolve_by": 75, "next_action": "在规则发挥作用前交代用途与限制"}
        ]
      }
    }
  }
}
```

示例中的 unknown 不是默认通过：影响当前理解的缺口必须列入 issues 并标记 needs_revision；仅允许把尚不影响当前情节的信息安排到后续。不能反复推迟期限掩盖问题。
其他模块 state 用于记录本模块延续信息，如情绪余波、未兑现承诺、关系变化、下一次检查点；人物和资源事实应引用原账本，避免重复维护。
引用证据不得来自未来章节、设定集或其他小说。known 与 hinted 都需原句；hinted 不能作为后续行动无须解释的常识。

运行 `python creative_modules.py validate <本书目录>`，并运行既有项目门禁。机器验证计划、正文哈希、证据及报告结构；AI判断证据是否真正支持结论。机器通过不等于文学质量已获真实读者认可。
出现错误时交回现有自动修复流程，修正文后重新生成读者门禁和模块报告。每章快照只有在对应正文及全部门禁通过后才能被后续写作采用。

## 反馈与扩展

真实反馈保存在本书 reader_feedback.md：原话、章节范围、发现日期、问题假设、处理动作、回访结果。不能虚构读者反馈或回访通过。
新增模块在共享库登记完整定义后即可在单书页面装配，通用计划与证据门禁自动适用；特殊结构或自动化指标需另外扩展代码与测试。
模块之间尽量复用事实数据。产品质量需要持续真实阅读验证，不能用更多提示词或更高自评分代替。
