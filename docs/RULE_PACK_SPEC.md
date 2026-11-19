# 规则包（Rule Pack）契约

> 目标：**换一份编码规则文档就能换一个课题**。字段表、校验器、提示词、模板全部由规则包生成，
> 不写死在代码里。当前 `rules/fields.yaml`（28 字段）只是 `meta-wbi` 这个规则包的实例。

## 1. 一次完整运行的四件输入

| 输入 | 形态 | 谁来定 |
|---|---|---|
| 三个 API key | 环境变量，只写 `.env` | 用户在壳里填 |
| 规则文档 | Markdown / PDF / DOCX 正文 | 用户上传 |
| 论文 | PDF 目录或逐篇上传 | 用户 |
| 交付形态 | 字段表 + 工作簿模板（可选） | 规则包生成后由用户确认 |

## 2. 规则包 = 五件产物（`rulepacks/<pack_id>/`）

```text
rulepacks/<pack_id>/
├── pack.json          # 元信息：来源文档哈希、生成时间、模型、版本
├── fields.yaml        # 字段清单：名称/类型/取值域/缺失规则/原文出处
├── decisions.yaml     # 分支决策：分表去向、资格、合并、编号
├── schema/
│   └── record.schema.json   # 由 fields.yaml 生成
└── template_contract.json   # 列位契约（若无现成模板则由字段顺序生成）
```

`pack.json` 必含：

```json
{
  "pack_id": "meta-wbi",
  "source_doc": {"path": "rules/CODING_PLAN.md", "sha256": "fa7c7500…", "pages": null},
  "field_count": 28,
  "generated_by": {"model": "...", "prompt_version": "rulepack_v1", "at": "..."},
  "status": "draft | confirmed",
  "confirmed_by": "human",
  "confirmed_at": "..."
}
```

**只有 `status=confirmed` 的规则包才允许进入编码。** 这是 G1 的通用化版本。

## 3. 生成 → 审阅 → 锁定 三步

1. **生成**（模型）：读规则文档全文，产出 `fields.yaml` 草稿。每字段必须带 `chapter`
   或 `quote`（原文出处），无出处的字段视为模型编造，直接丢弃并记入 `gaps.md`。
2. **审阅**（人）：壳里逐条显示「字段名 / 类型 / 取值 / 缺失规则 / 原文片段」，可改可删可增。
   任何修改都写回 `fields.yaml` 并在 `pack.json` 记 `human_edits`。
3. **锁定**：确认后计算 `pack_hash = sha256(canonical(fields + decisions + schema + contract))`，
   写入所有后续 manifest。规则包从此不可静默变更，改动必须升版本号。

## 4. 规则包如何驱动流水线

| 阶段 | 依赖规则包的什么 |
|---|---|
| 摄取 | 无（与课题无关） |
| 编码提示词 | `fields.yaml` 的字段清单 + `decisions.yaml` 的口径 + 原文摘录 |
| Schema 校验 | `schema/record.schema.json` |
| 校验器 | 通用结构检查（固定）+ 规则包声明的取值域检查（生成） |
| 共识工作簿 | `template_contract.json` 的列位 |
| 分析导出 | `fields.yaml` 的类型决定长表列型 |

**课题专属的硬编码校验器（如 WBI 的 `age_group` 边界）必须迁出到规则包**：
写成 `validators:` 段落里的声明式规则，代码只提供算子库。

## 5. 交付物：三栏对照 + 修改意见

每次运行产出：

```text
runs/<run_id>/
├── results/coder_a.jsonl      # 编码员 A 的逐篇结果
├── results/coder_b.jsonl      # 编码员 B
├── diff/cells.csv             # 逐字段差异（含 match_type）
├── diff/summary.md            # 差异分布：按字段、按论文、按类型
├── advice/adjudication.jsonl  # 第三方裁决：建议值 + 原文证据 + 置信度
├── advice/revisions.md        # 给人看的修改清单：哪篇哪个字段、现值→建议值、依据页码
└── run.json                   # manifest：配置哈希、规则包哈希、成本、耗时
```

`advice/revisions.md` 是用户真正要读的东西，格式：

```markdown
## 6047.pdf · WBIS · Overall sample · scale_mean
- A: 3.54  B: 3.45   差异类型：numeric_conflict
- 建议：3.54（Table 2, p.7：「M = 3.54, SD = 1.69」）
- 理由：B 取到相邻列；A 与表格一致
- 置信度：0.92    严重度：critical
```

## 6. 与现有代码的迁移路径

1. 现有 `rules/` 冻结为规则包 `meta-wbi-v1`，内容逐字节不变。
2. `fields.yaml` 的 `chapter` 字段已有，补齐 `quote` 后即可作为规则包样板。
3. 校验器拆成两层：`validate/generic.py`（结构、类型、缺失码、编号）+ `validate/pack.py`
   （读规则包声明的边界与枚举）。
4. 壳先按"单规则包"跑通，再开放多规则包切换。
