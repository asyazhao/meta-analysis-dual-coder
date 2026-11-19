# RUNBOOK — 操作手册

## 0. 环境

```bash
cd ~/Desktop/西浦大二/何老师lab/meta-analysis-dual-coder
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
cp .env.example .env          # 填入三把密钥；.env 已被 gitignore
```

## 1. 阶段命令

| 阶段 | 命令 | 产物 | Gate |
|---|---|---|---|
| 0 | `meta-coding init` | 目录骨架、配置校验 | — |
| 1 | `meta-coding intake --batch batch01` | `corpus_manifest.csv`、`registry.csv`、`intake_report.md` | — |
| 2 | `meta-coding rules build` / `rules verify` | `fields.yaml`、`decisions.yaml`、Schema、`template_contract.json`、`rules.lock.json` | **G1** |
| 3 | `meta-coding ingest --batch batch01` | `packet.json`、`pages.jsonl`、`tables/`、`ingest_qa.csv` | — |
| 4 | `meta-coding code --coder a --batch batch01 --limit 3` | 原始响应、解析 JSON、`run_log.jsonl`、`pilot_review.md` | **G2** |
| 5 | `meta-coding validate --coder a --batch batch01` | `validation_summary.csv`、`repair_log.jsonl` | — |
| 6 | `meta-coding diff --batch batch01`<br>`meta-coding adjudicate --batch batch01` | `diff_cells.csv`、`adjudication.jsonl`、`agreement_metrics.json` | — |
| 7 | `meta-coding consensus --batch batch01` | `batch01_consensus_codebook.xlsx`、`provenance.csv`、`paper_id_map.csv` | — |
| 8 | `meta-coding qa --batch batch01` | `analysis_long.csv`、完整性矩阵、`unresolved_and_unusable.csv` | — |
| 9 | `meta-coding report --batch batch01` | 三份报告、README、release | **G3** |
| 任意 | `meta-coding verify --all` | 逐份 manifest 哈希复算 | — |

## 2. 成本护栏（每个计费阶段都必须带）

```bash
meta-coding code --coder a --batch batch01 --dry-run     # 只渲染提示词，不调用
meta-coding code --coder a --batch batch01 --limit 3     # 试跑
meta-coding code --coder a --batch batch01 --max-cost 8  # 硬上限，触顶即停
```

`--dry-run` 必须能在**没有 API key** 的情况下跑完并产出提示词哈希——这是 CI 的检查项。

## 3. 断点与检查点

- 逐篇落盘：`data/03_coded/<coder>/<article_key>/` 存在即视为已完成，重跑跳过。
- 每 50 篇强制保存工作簿 + 批次处理记录 + 检查点（`data/03_coded/CHECKPOINT.json`），
  随后执行该段 double check，修正后再保存一次，再进入下一段。
- 中断后从检查点续跑：`meta-coding code --coder a --batch batch01 --resume`（Stage 4 实现）。

## 4. 常见故障处置

| 现象 | 判断 | 动作 |
|---|---|---|
| 单篇打不开/乱码 | 篇级问题 | 记 `source_unreadable` / `source_incomplete`，继续下一篇 |
| Schema 全崩 / API 持续 5xx | 流水线级 | 停机，保留 manifest，不写半成品 |
| `prompt_hash_a != prompt_hash_b` | 隔离被破坏 | 停止该批次，检查渲染路径 diff |
| 磁盘写满 | 流水线级 | 停机；`data/02_ingest/` 可安全清理后重生成 |
| 成本超 `budget_usd` | 护栏触发 | 停机并报告已完成/剩余篇目 |

## 5. 每阶段收尾（缺一不可）

1. 跑该阶段的验收脚本或测试；
2. 生成 `artifacts/stage-XX_<name>/<run_id>/manifest.json`（含 inputs/outputs 哈希、checks、counts）；
3. 更新 `artifacts/INDEX.md`；
4. `git add` → commit → `git tag stage-XX`；
5. 按汇报模板回报：命令、产物路径、验收通过数、关键数字、异常、下一步、是否触发 Gate。

## 6. 回滚

- 阶段产物不可变：重跑生成新 `run_id`，旧目录保留。
- 代码回滚：`git checkout stage-XX-1`。
- 数据回滚：不得覆盖，只能以新文件名重生成（`batch01_consensus_codebook_v2.xlsx`）。

## 7. 发布前（G3）

```bash
meta-coding verify --all          # 必须全 PASS
pytest -q                         # 必须全绿
git ls-files | grep -E '\.pdf$|\.env$'   # 必须为空
```
