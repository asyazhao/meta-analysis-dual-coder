# 本机 Web 控制台（通用编码工具）

> 代码：`src/meta_coding/web/`　契约：`docs/RULE_PACK_SPEC.md`
> 特性：只监听 `127.0.0.1`、密钥只写 `.env` 且永不回显、规则包驱动、G2/G3 门禁。

## 1. 启动

```bash
# 首次或改完代码后，把 src 同步进 venv（模板会被打进包）
make sync

# 方式一：直接跑模块（与 README 约定一致）
python -m meta_coding.web.app --port 8765

# 方式二：用 Makefile（内部加 PYTHONPATH=src，改代码后无需 sync）
make web            # 默认 8765，可 make web PORT=9000
```

打开 <http://127.0.0.1:8765/>（与 `/keys` 同一页），Ctrl+C 停止。

## 2. 页面与路由

| 页面 | 作用 | 关键接口 |
|---|---|---|
| `/keys` | 三个密钥 + 预算 + 并发；只写 `.env`，只显示「已设置」 | `POST /api/settings` |
| `/rules` | 选/传规则文档 → 解析 → 规则包；字段可改/删/增；确认锁定 | `POST /api/rules/parse`、`/upload`、`/import`、`PATCH/DELETE /api/rules/{id}/fields/{name}`、`POST /api/rules/{id}/confirm` |
| `/papers` | 选 PDF 目录或上传，展示识别篇数与清单 | `POST /api/papers/dir`、`POST /api/papers/upload` |
| `/run` | 摄取 → 双编码 → 比对 → 裁决；进度条、当前篇、成本累计、运行详情（折叠日志） | `POST /api/runs`、`POST /api/runs/{id}/steps/{step}`、`GET /api/runs/{id}/progress` |
| `/results` | 三栏对照：字段 / A 值 / B 值 / 建议值 / 依据页码 / 严重度；差异高亮、可筛选、点开看理由 | 读 `runs/<id>/diff/cells.csv` |
| `/advice` | 修改意见逐条「采纳/驳回」，写回 `decisions.json` 与 `provenance.jsonl` | `POST /api/advice/decision` |
| `/export` | 工作簿 / analysis_long.csv / revisions.md 等产物清单；G3 后可生成带哈希的导出清单 | `POST /api/runs/{id}/export`、`GET /files/{rel}` |

其它：`GET /api/health`、`GET /api/state`、`GET /api/jobs/{id}`（未知 id 返回 404）。

## 3. 规则包（换文档 = 换课题）

```
rulepacks/<pack_id>/
├── pack.json                 # 来源 sha256、生成模型、status、pack_hash、human_edits
├── fields.yaml               # 字段清单（名称/类型/取值/缺失规则/章节/原文片段）
├── decisions.yaml            # 分支决策
├── schema/record.schema.json # 由 fields.yaml 生成
└── template_contract.json    # 列位契约（A/B/C… 按字段顺序）
```

- **离线示例（mock）**：无需密钥，固定 4 个字段（`study_id` / `sample_size` / `mean_value` /
  `sd_value`），`quote` 取自所选文档的真实段落，`pack.json` 标 `"mock": true`，页面显示「离线示例」。
- **模型解析**：`mode=model`，需要 `configs/models.yaml` 至少绑定一个槽位；走同一个 `create_pack` 入口，
  输出同一种规则包。没有 `chapter`/`quote` 的字段会被丢弃并记入 `gaps.md`。
- **确认**：`POST /api/rules/{id}/confirm` 把 `status` 置为 `confirmed` 并固化 `pack_hash`；
  确认后任何字段修改都会被 400 拒绝（要改就重新解析生成新版本）。
- **导入已有规则**：`POST /api/rules/import` 把 `rules/` 冻结为 `meta-wbi-v1` 草稿（fields/decisions/
  contract/schema 逐字节复制，28 字段）。

## 4. 运行产物

```
runs/<run_id>/
├── results/coder_a.jsonl, coder_b.jsonl   # 从 data/03_coded 的 parsed.json 汇总（真实值）
├── diff/cells.csv, summary.md             # 确定性比对：agree / mismatch / missing_vs_value
├── advice/revisions.md                    # 裁决产出（外部写入），本页负责结构化与决策
├── advice/decisions.json, provenance.jsonl# 人的采纳/驳回与留痕（本页写）
├── exports/                               # 导出清单与工作簿/长表（存在才列出）
└── run.json                               # 规则包哈希、阶段状态、成本
```

进度与成本**只读落盘状态**：`data/03_coded` 的完成篇数 + `run_log.jsonl` 的 `cost_usd` 累计；
当前篇由「最新尚未产出 parsed.json 的文章目录」推断。没有数据就显示 `—`，不估算。

## 5. 门禁与安全

- **G2 真实调用**（左下角开关）：未勾选时，`code_*`、`adjudicate` 的真实运行返回 **403**，只允许 `dry_run=true`。
- **G3 导出**：未勾选时 `POST /api/runs/{id}/export` 返回 403；勾选后生成 `exports/export_manifest.json`（含每个文件 sha256）。
- **规则包闸门**：`status != confirmed` 的规则包不能创建运行（409）——这是 G1 的通用化。
- 只监听 `127.0.0.1`；`--host` 拒绝非回环地址。
- 密钥只写 `.env`；页面/JSON 只暴露 `keys_set` 布尔值；响应中间件与任务日志都会用 `.env` 实际值做脱敏。
- 目录浏览/规则文档限定在仓库或当前论文目录；下载只放开 `runs/`、`rulepacks/`、`reports/`、
  `data/07_analysis/`，隐藏文件一律拒绝。

## 6. 测试

```bash
.venv/bin/python -m pytest tests/test_web.py -q   # 16 项
.venv/bin/ruff check src tests
```

覆盖：首页 302、七页 200 且不含密钥、设置写 `.env` 不回显、G2/G3 403、未知 job 404、
mock 规则包生成与增删改/确认锁定、导入内置 28 字段、结果页差异高亮、采纳/驳回写回 decisions+provenance、
目录穿越/下载边界、上传、预算与并发校验、未确认规则包不能开跑。

## 7. 已知边界

- 工作簿（`exports/<pack_id>_codebook.xlsx`）与 `analysis_long.csv` 由编码/共识阶段产出，
  本控制台只登记与下载，不替代生成。
- 裁决（`adjudicate`）仍走既有 CLI；CLI 未实现时会显式失败，页面显示退出码与 stderr，不假装成功。
- 运行目录按 `runs/` 约定读写，未接入数据库；重启服务后任务列表清空，但产物与决策都在磁盘上。
