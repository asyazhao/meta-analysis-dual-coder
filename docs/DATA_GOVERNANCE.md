# DATA GOVERNANCE — 数据分级、流向与保留

## 1. 数据分级

| 级别 | 内容 | 存放 | 可否公开 |
|---|---|---|---|
| L0 公开 | manifest、哈希、公开报告、派生统计 | `artifacts/public/`、`reports/` | 是 |
| L1 受限 | 代码、规则、Schema、模板契约 | 仓库全文 | 是（MIT） |
| L2 敏感 | PDF 全文、摄取文本、逐篇编码 JSON、裁决记录 | `data/00_inbox/`、`data/02_ingest/`–`07_analysis/` | **否** |
| L3 私密 | API 密钥、含作者邮箱的追踪表、人工复核原始记录 | `.env`、`artifacts/private/` | **否** |

`data/02_ingest/` 之下是受版权保护的全文，`data/06_consensus/`、`data/07_analysis/`
含 `no_mean_or_sd` 的联系作者邮箱（属个人信息）。因此 `.gitignore` 将 `data/` 下除
`01_manifest/` 以外的产物整体排除。仓库只保留**可校验的哈希**，不保留内容本身。

## 2. 数据流出（唯一的外部传输）

- 唯一外发对象：用户指定的三个模型 API（两个编码员 + 一个裁决员）。
- 外发内容：Stage 3 生成的文章 packet（带页码锚点的正文、表格、补充材料）。
- **触发条件**：G2 明确确认后方可发送；确认记录写入
  `artifacts/stage-04_coding/<run_id>/manifest.json` 的 `notes`。
- 每次调用落盘：模型 ID/版本、参数、`prompt_hash`、`rules_hash`、`input_packet_hash`、
  `response_hash`、token、耗时、成本。审计时可完全离线复算，不需要再次外发。

### 服务商条款核查清单（G2 逐项确认并记录）

- [ ] 是否用于模型训练（training / fine-tuning opt-out 状态）
- [ ] 数据保留期限（retention window）
- [ ] 是否有人工审阅通道（human review）
- [ ] 数据中心所在地与跨境传输
- [ ] 企业版/零保留（zero-retention）条款是否适用

## 3. 引用长度与版权

- 每条 `provenance.quote` **不超过 25 个词**，且必须是定位用的短片段，不得整段复制。
- 不复制整表、整段方法或讨论文字进入任何可公开产物。
- 公开报告中的典型案例一律脱敏：不出现可识别的二级信息组合。

## 4. 个人信息

- `no_mean_or_sd` 含通讯作者姓名与邮箱，属个人信息：
  仅存于 L2/L3，不进入 git，不进入公开 release。
- 公开报告中若需说明"已联系作者"，只报告计数，不列出姓名或邮箱。
- 提交信息与公开仓库使用的 git 身份不得混入学生/个人邮箱；发布前将 author email
  设为 GitHub noreply 地址。

## 5. 删除与保留

- 原始 PDF 由用户持有，仓库不存副本；`data/00_inbox/` 仅作本地指针。
- 批次结束后保留：manifest、哈希、公开报告；L2 内容按用户要求保留在本地。
- 任何"清空重跑"必须先归档旧 manifest，历史 manifest 不覆盖、不删除。

## 6. 合规红线

1. 未取得 G2 确认，不得把 PDF 内容发往任何外部服务。
2. 不得把 `.env`、密钥、作者邮箱、PDF 提交到 git——由 `tests/test_repo_hygiene.py` 在 CI 拦截。
3. 不得将受限数据用于本课题之外的目的。
