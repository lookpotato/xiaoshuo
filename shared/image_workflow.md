# 小说图片资产工作流

## 目标与硬限制

- 每本小说只使用本书目录内的 `images/`，禁止跨书复用或写入其他作品目录。
- 图片用来解释会持续影响阅读理解的具名人物、关键道具、重要地点、异兽、组织视觉标志或确有必要的场景。一次性杯子、普通门窗等背景物不算新实体。
- 每章最多 1 张图。只选择本章最需要视觉解释的新人物、关键道具、地点、异兽、组织标志或场景；同章其他新实体必须用正文白话解释，不能假装它们已经配图。
- 同名、同设定实体只生成一次，以后沿用 `images/catalog.json` 中的参考图。外观发生剧情性永久变化时，新增版本实体或版本化文件，不覆盖旧图。
- 图片是章节归档的一部分。生图失败、视觉核验失败、文件未落盘、目录未登记或正文未引用时，只能保留草稿，不得推进 `chapter_state.json`。

## 目录与命名

按实体类型保存到以下目录，目录在首次使用时创建：

- `images/characters/`
- `images/items/`
- `images/locations/`
- `images/creatures/`
- `images/organizations/`
- `images/scenes/`

文件名使用稳定、可读的 ASCII slug，例如 `gu-changye-v1.png`、`soul-lock-v1.png`。不得覆盖已有文件；修改图使用 `-v2`、`-v3`。章节中的引用统一写成：

```markdown
![顾长夜：黑衣青年，左眼有银色裂纹](../images/characters/gu-changye-v1.png)
```

## 每章执行顺序

1. 先读人物表、世界观、连续性账本、最近三章和本书 `images/catalog.json`，列出本章候选新实体。
2. 按“若没有图，哪一个最妨碍读者理解”排序，只选择第一名生成本章唯一图片。其余实体靠正文解释；必要时把过于复杂的出场延后。
3. 首次启用图片体系且目录为空时，如果本章没有更重要的新实体，可用唯一图片名额补齐主角参考图。
4. 为每个实体先从首次出现章节抄录不少于 8 个字的 `source_excerpt`，再冻结 `canonical_description`、`distinctive_features` 和不得出现的矛盾特征；不得靠猜测补完正文没有说明的外观。随后先确定生成画幅和番茄裁剪比例，再按本书 `style_bible` 组织网页生图提示词。
5. 把完整提示词写入本任务临时文件，运行 `python browser_image_worker.py --prompt-file <提示词文件> --output <本书 images 分类目录的新文件> --ratio <画幅>`。脚本使用独立、已登录的 Google Chrome 会话打开 `image_browser_config.json` 指定的网页，生成并下载原图。
6. 输出文件必须直接落在本书分类目录，文件名使用新版本号且禁止覆盖旧图。目录中记录 `generated_with: chrome-web` 和实际 `web_provider`。网页失败时不得改用 Codex `imagegen`；验证码、登录失效、风控、政策提示或控件变化时停止并保留草稿。
7. 使用 `view_image` 打开最终文件，逐项检查：主体身份、标志特征、颜色材质、形状与部件数量、是否违反设定、是否有未要求文字或水印，以及按目标比例裁剪后主体和关键特征是否仍完整。
8. 任一关键项不符时，只针对该问题重新生成，再完整复检。精确数量或复杂结构连续失败时，改用正投影、俯视、孤立道具或更清楚的结构展示重试，不得降低正确性标准；仍不正确时停止归档并明确报告失败原因，绝不能把失败项写成 `verified`。
9. 全部通过后计算文件 SHA-256，写入实体记录与本章 `chapter_images`；核验者固定记为 `codex-visual-review`。
10. 在正文首次解释后插入图片，随后运行测试及 `python .\fanqie_novel_manager.py validate`。图片、目录、正文、状态和日志一起提交。

## 番茄“作者有话说”上传

番茄正文不直接接收本地 Markdown 图片。上传章节时，程序从正文中提取唯一的 `../images/...` 图片路径，正文编辑器只粘贴纯文字；图片按以下固定入口单独上传：

1. 在正文页底部找到“作者有话说”。
2. 鼠标悬停“添加”，在浮出的选择框中点击“添加图文”。
3. 区域变成输入框后，先输入固定边界明确的辅助说明，再点击左下角图片按钮。开头固定为 `【本章辅助说明｜以下内容仅帮助理解配图，不属于小说正文】`，中间写 `本章配图：<图片白话描述>`，结尾固定为 `【辅助说明结束】`。
4. 在“点击或拖拽文件到此上传”弹窗中选择本章本地图片。自动化通过网页文件输入控件提交该绝对路径，等价于在资源管理器选择文件。
5. 等待上传完成后，按图片目录里的 `fanqie_crop_ratio` 点击右侧对应比例，再点击“确定”。
6. 回查“作者有话说”区域出现图片预览或文件名；未回显时不得点击“保存”。
7. 点击右侧“保存”，等待区域恢复为“编辑”状态，再进入右上角正常“下一步”发布流程。

没有本章图片时不创建空的“作者有话说”图文框。已有待发布草稿恢复时先核对图片是否已经存在，禁止不确定状态下重复上传。

“作者有话说”里的配图解释属于阅读辅助，不得冒充剧情、旁白或本章正文，也不得承载正文理解所必需的前因后果。即使读者完全跳过该说明，也必须能只凭正文理解本章行动与因果。

## 生成画幅与番茄裁剪比例

每张图片都必须在 `image.display` 中记录生成画幅与番茄裁剪比例，且两者保持一致。生成提示词必须明确写出目标画幅与安全区，避免先生成一种构图再由番茄强行截断。

- 人物单人半身或全身设定图：默认 `2:3`，人物居中，头顶、双手、脚下和标志性服饰均留边。
- 明确需要超长竖构图的人物或高耸场景：`9:16`；没有充分理由不得代替人物默认的 `2:3`。
- 道具、法宝、徽记、组织标志：默认 `1:1`，物体完整居中，四周至少留约 10% 安全边距。
- 宽阔地点、环境全景、横向多人场景：默认 `16:9`，关键人物和地标不得贴左右边缘。
- 横向异兽、坐骑、动作画面或不宜过宽的景物：默认 `3:2`；高瘦类异兽可按实际构图改用 `2:3`。

生成工具没有独立画幅参数时，也必须在提示词中要求准确画幅，并在落盘后检查像素宽高关系和安全构图；不符合就重生，不能只修改目录数字。

## 生图提示词最低内容

每张图的最终提示词必须包含：用途为小说设定参考图、实体名称与类型、正文已经明确的外观、所有标志特征、材质与颜色、部件数量或空间关系、本书统一画风，以及“不得新增设定、不得用相似替代物、无文字、无水印”。不要为了画面好看擅自增添武器、纹章、人物或能力特效。

## 核验记录

只有七项检查全部为 `true` 才能设为 `verified`：

- `subject_identity`
- `canonical_features`
- `colors_and_materials`
- `shape_and_parts`
- `no_contradictions`
- `no_unrequested_text_or_watermark`
- `crop_safe_composition`

`verification.notes` 要写明肉眼确认到的关键证据，不能只写“正常”或“通过”。

实体记录的最低结构如下（字段值按正文替换）：

```json
{
  "item:soul-lock": {
    "name": "锁魂钉",
    "type": "item",
    "first_chapter": 7,
    "image_created_chapter": 7,
    "source_excerpt": "锁魂钉能固定游魂。",
    "canonical_description": "一枚用于固定游魂的乌黑四棱长钉",
    "distinctive_features": ["四棱钉身", "尾端一圈暗红刻痕"],
    "forbidden_features": ["剑形", "金色"],
    "image": {
      "path": "images/items/soul-lock-v1.png",
      "sha256": "文件的实际 SHA-256",
      "alt_text": "四棱乌黑锁魂钉，尾端有暗红刻痕",
      "generated_with": "chrome-web",
      "web_provider": "chatgpt-plus",
      "prompt": "实际使用的完整提示词",
      "display": {
        "content_kind": "item",
        "generation_aspect_ratio": "1:1",
        "fanqie_crop_ratio": "1:1",
        "safe_area": "道具完整居中，四周保留至少百分之十安全边距"
      },
      "verification": {
        "status": "verified",
        "reviewer": "codex-visual-review",
        "checked_at": "带时区的 ISO 时间",
        "attempts": 1,
        "notes": "肉眼确认到的具体依据",
        "checks": {
          "subject_identity": true,
          "canonical_features": true,
          "colors_and_materials": true,
          "shape_and_parts": true,
          "no_contradictions": true,
          "no_unrequested_text_or_watermark": true,
          "crop_safe_composition": true
        }
      }
    }
  }
}
```

同一章最终成图实体必须登记到 `chapter_images`，例如：`"7": {"entity_ids": ["item:soul-lock"]}`。`entity_ids` 必须与各实体的 `image_created_chapter` 完全一致。
