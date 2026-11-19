# G1 规则锁审阅清单

> Stage 2 的交付物全部在 `rules/` 下。G1 通过前不得进入 Stage 3（摄取）与 Stage 4（编码）。
> 规则源：`rules/CODING_PLAN.md`，SHA-256 `fa7c7500…921e4`（53,010 B，与桌面原件逐字节一致）。

## 1. 需要你点头的三件事

### ① 28 字段的列位（决定 V02 与 Stage 7 写入）

派生模板 `rules/clean_codebook_BLANK.xlsx` 共 **33 列 = 28 有效字段 + 5 个空白分隔列**：

| 列 | 字段 | 列 | 字段 |
|---|---|---|---|
| A | full_reference | R | age_calculation |
| B | subsample_label | S | age_group |
| C | source_type | T | —（分隔） |
| D | paper_id | U | mean_bmi |
| E | sex | V | —（分隔） |
| F | —（分隔） | W | measure_name |
| G | country | X | translated_flag |
| H | sample_type | Y | scale_comparable_flag |
| I | clinical_type | Z | items_n |
| J | sampling_method | AA | response_scale_min |
| K | —（分隔） | AB | response_scale_max |
| L | publication_year | AC | direction |
| M | year_data_collection | AD | scale_total |
| N | year_imputed_rule | AE | scale_mean |
| O | —（分隔） | AF | scale_sd |
| P | sample_n | AG | mean_calculation |
| Q | mean_age | | |

与你的历史工作簿相比只有两处差异：`study_id`(A) 换成 `paper_id`(D)，`direction` 后插入 `scale_total`。
四个工作表：`WBIS` / `WSSQ` / `no_mean_or_sd`（10 列）/ `not_eligible`（2 列），无 RULE 页。

### ② 六个规则缺口（详见 `docs/RULE_GAPS.md`）

| 编号 | 争点 | 本批次怎么执行 |
|---|---|---|
| GAP-001 | 缺失码 0 还是 -999 | 统一 **-999**（按 md §8；与论文1/论文2 的历史 0 占位不兼容） |
| GAP-002 | `scale_total` 存废 | **保留**（按 md §5.6/附录 A） |
| GAP-003 | `scale_comparable_flag` 口径 | 按 md 字面：须论文自身报告翻译/回译或心理测量学性质 |
| GAP-004 | 非性别同质子样本是否合并 SD | 只在 md 明确覆盖的情形合并，其余分块并记录理由 |
| GAP-005 | 分隔列位置 | 由三份现有工作簿推得 F/K/O/T/V，**待你确认** |
| GAP-006 | `paper_id` 在 D 列 vs 旧表 A 列 | 按 md 用 D 列 |

后三条（003、004）会直接改变编码结果，建议在 G1 一并定口径。

### ③ 验证器语义

50 个校验器全部实现，`auto` 37 个、`semi` 13 个（需证据或模型判断，只提示不自动改值）。
每个都有「故意破坏 → 必须命中」的用例，共 55 项测试。

- 结构 V01–V08：四表、表头逐字、三行块、空行、未编码性别行、块内一致、ID 一致、列数
- 编码域 V09–V16：枚举、sex、age_group 边界、国名规范、临床类型与 sample_type 联动
- 统计口径 V17–V28：总分/条目尺度、反应范围、方向、在线年份、年份中点进位、子量表误用
- 缺失追踪 V29–V37：-999 不进计算、缺均值/缺 SD 的三种去向、追踪页唯一性、性别行
- 编号 V38–V44：连续、跨块一致、WBIS 先 WSSQ 接续、最大 ID 等于论文数、追踪页无 ID
- 计算 V45–V50：合并均值/SD 可复算、两位小数、-999 不写成 -999.00、每篇有去向

## 2. 可以自己复核的命令

```bash
cd ~/Desktop/西浦大二/何老师lab/meta-analysis-dual-coder
.venv/bin/meta-coding rules verify        # 规则锁 + 模板契约
.venv/bin/python -m pytest -q             # 87 passed
open rules/clean_codebook_BLANK.xlsx      # 看四个工作表
```

## 3. 已知偏差（`docs/DEVIATIONS.md`）

- DEV-001 不采用 `编码口径更新_2026-10-06.md` 的 0 占位与删列条款
- DEV-002 空白模板为程序生成（原 `updated_codebook_TEMPLATE.xlsx` 为 37 列 6 表旧版，只读未动）
- DEV-005 阶段 manifest 只覆盖该阶段产物（源码由 git tag 覆盖）

## 4. G1 通过后我会做什么

Stage 3 摄取：按页提取带页码锚点的全文、表格转 Markdown/CSV、生成 `packet.json`，
对 21 篇计算 `packet_hash`。这一步仍然不需要 API key。
