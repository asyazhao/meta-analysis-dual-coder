# ARTIFACTS INDEX

每个阶段的可追溯索引。**任何阶段结束都必须在此追加一行**，否则该阶段视为未完成。

| 阶段 | run_id | 产物 | 关键计数 | git tag | 验收 |
|---|---|---|---|---|---|
| stage-00 baseline | `20261119T154456Z-6b5e095` | 不变量快照：`docs/PROTOCOL.md`、`LICENSE` | 2 文件 / 7,019 B | `stage-00` | 通过 |
| stage-01 intake | `20261119T153935Z-bb38d28a` | `corpus_manifest.csv`、`registry.csv`、`intake_report.md` | 文件 21 / **文章 21** / 补充 0 / 重复 0；扫描 0；无 DOI 3 | `stage-01` | 通过 |
| stage-03 ingest | `20261119T155227Z-2312dddf` | `data/02_ingest/<id>/packet.json`×21、`pages.jsonl`、`tables/`、`ingest_qa.csv` | 21 篇 / 159 表 / 1,595,484 字符 / 3.6 MB | `stage-03` | 通过 |
| stage-02 rules | `20261119T164027Z-d358520` | `fields.yaml`、`decisions.yaml`、两份 Schema、`template_contract.json`、`clean_codebook_BLANK.xlsx`、`rules.lock.json` | 28 字段 / 33 列 / 4 表；V01–V50；10 文件 / 92,648 B | `stage-02` | 通过（待 G1 签字） |

## stage-03 验收记录

- 21 篇全部生成 `packet.json`，`packet_hash` 唯一 21/21；A/B 拿同一份字节
- 表格 159 个（含页码、表号线索、Markdown + CSV）；总字符 1,595,484
- 3 篇存在低文本页（学位论文封面/图页），已标 `ocr=true` 并计入 QA，未静默跳过
- `data/02_ingest/` 为受限数据（全文），**不进 git**；manifest 中以 `external: true` 标记，
  本地缺失不算篡改，存在则仍校验哈希
- `pytest`：93 passed

## stage-02 验收记录

- 28 个字段全部映射到列位；分隔列 F/K/O/T/V；四张工作表名称与列数符合 md
- `record.schema.json` 由 `fields.yaml` 生成，`additionalProperties=false` + 28 个 required
- V01–V50 全实现：auto 37、semi 13；每条一个「破坏→命中」用例
- `pytest`：**87 passed**；`meta-coding rules verify` 通过
- 规则锁：`rules/CODING_PLAN.md` sha256 与桌面原件一致，副本 chmod 444
- 待办：G1 需确认 GAP-003/004/005 与派生模板列位

> 注：两处 run 曾按「全仓库快照」冻结（`20261119T153637Z-3ba4333`、`…T153858Z`），
> 因 Stage 1 修改源码导致 `verify` 误报。已按 DEV-005 改为只冻结该阶段产物/基线文档，
> 旧快照移入 `artifacts/private/superseded/`（gitignored）。

## stage-01 验收记录

- 每 PDF 恰好一个角色；`21 + 0 + 0 = 21` 与文件总数一致
- 重复项：0（本批次无重复副本）；`duplicate_of` 逻辑由 12 项单元测试覆盖
- 扫描件：0 → OCR 队列为空（本机无 tesseract，若有扫描件将显式排队而非静默跳过）
- DOI 覆盖 18/21；其余 3 篇为学位论文（6127 Sall 2023、6259 Wagner 2022、
  7218 Pudney 2021），改用「规范化标题+第一作者+年份」生成 `article_key`，
  状态 `ok_citation_key`，不依赖文件名或路径
- `article_key` 唯一性：21/21
- 本批次 **N = 21**（按文章计，非文件计）

## stage-00 验收记录

- `pytest`：**23 passed**（配置校验 9、CLI 6、manifest 4、仓库卫生 4）
- `ruff check src tests scripts`：All checks passed
- `meta-coding --help`：可用，11 个命令全部注册；未实现阶段以退出码 2 显式失败
- `meta-coding verify --all`：1 份 manifest，失败 0
- `git status` 敏感文件：无 PDF、无 `.env`、无密钥（`tests/test_repo_hygiene.py` 持续拦截）
- 配置自检：`batch01` / 21 篇 / 50 USD / 并发 4；`config_hash=85dcfebd…b4112`

| 检查 | 结果 |
|---|---|
| `pytest -q` | 全绿 |
| `meta-coding --help` | 可用 |
| `git status` 敏感文件 | 无 |
| README 可说明项目 | 是 |

## 约定

1. `manifest.json` 的路径相对于仓库根目录，克隆后可离线复算。
2. `artifacts/private/` 与 `data/` 下的内容不进 git，只登记哈希。
3. 重跑生成新 `run_id`，旧目录保留，不覆盖。
