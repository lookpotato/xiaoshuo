# 小说图片资产工作流

## 目标与硬限制

- 每本小说只使用本书目录内的 `images/`，禁止跨书复用或写入其他作品目录。
- 图片用来解释会持续影响阅读理解的具名人物、关键道具、重要地点、异兽、组织视觉标志或确有必要的场景。一次性杯子、普通门窗等背景物不算新实体。
- 新实体首次出现时必须配图，并在正文第一次解释完成后就近插入 Markdown 图片；每章最多 3 张。因此一章不得引入超过 3 个需要配图的新实体。
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
2. 按“若没有图会不会妨碍读者理解”筛选；若超过 3 个，先调整正文，把多余实体延后出场，不可漏图硬写。
3. 首次启用图片体系且目录为空时，可把尚无参考图的主角作为补图实体，但新实体优先，合计仍不得超过 3 张。
4. 为每个实体先从首次出现章节抄录不少于 8 个字的 `source_excerpt`，再冻结 `canonical_description`、`distinctive_features` 和不得出现的矛盾特征；不得靠猜测补完正文没有说明的外观。随后按本书 `style_bible` 组织 imagegen 提示词。
5. 使用内置 `imagegen` 技能逐张生成。不要使用需要 API Key 的 CLI 后备方式；若内置工具不可用，本章停在草稿状态并如实记录。
6. 从 `$CODEX_HOME/generated_images/` 选择输出，复制到本书分类目录。项目引用的最终图不得只留在 Codex 默认目录。
7. 使用 `view_image` 打开最终文件，逐项检查：主体身份、标志特征、颜色材质、形状与部件数量、是否违反设定、是否有未要求文字或水印。
8. 任一关键项不符时，只针对该问题重新生成，再完整复检。最多保留最终通过版本；连续尝试仍不正确时停止归档，绝不能把失败项写成 `verified`。
9. 全部通过后计算文件 SHA-256，写入实体记录与本章 `chapter_images`；核验者固定记为 `codex-visual-review`。
10. 在正文首次解释后插入图片，随后运行测试及 `python .\fanqie_novel_manager.py validate`。图片、目录、正文、状态和日志一起提交。

## 生图提示词最低内容

每张图的最终提示词必须包含：用途为小说设定参考图、实体名称与类型、正文已经明确的外观、所有标志特征、材质与颜色、部件数量或空间关系、本书统一画风，以及“不得新增设定、不得用相似替代物、无文字、无水印”。不要为了画面好看擅自增添武器、纹章、人物或能力特效。

## 核验记录

只有六项检查全部为 `true` 才能设为 `verified`：

- `subject_identity`
- `canonical_features`
- `colors_and_materials`
- `shape_and_parts`
- `no_contradictions`
- `no_unrequested_text_or_watermark`

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
      "generated_with": "codex-imagegen",
      "prompt": "实际使用的完整提示词",
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
          "no_unrequested_text_or_watermark": true
        }
      }
    }
  }
}
```

同一章最终成图实体必须登记到 `chapter_images`，例如：`"7": {"entity_ids": ["item:soul-lock"]}`。`entity_ids` 必须与各实体的 `image_created_chapter` 完全一致。
