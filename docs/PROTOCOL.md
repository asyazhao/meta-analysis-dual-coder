# PROTOCOL — 预注册方案（Stage 4 起不得静默修改）

> 本文件在 Stage 4（首次真实编码）之前定稿。此后任何改动必须同时写入
> [`DEVIATIONS.md`](DEVIATIONS.md)，注明日期、原条款、新条款、触发原因。
> 规则来源：`CODING_PLAN.md`，SHA-256 `fa7c75005b97212efd38302faeb43bdfd5fdfde60c5175cef2e35c8bfc5921e4`。

## 1. 研究问题与编码对象

内化体重污名（weight bias internalization）的元分析数据提取。编码对象为每篇纳入文献中
「独立样本 × 合格量表（WBIS 系列 / WSSQ）× 目标时间点」构成的编码块，每块固定三行
（sex = 0 总体 / 1 女性 / 2 男性）。变量、取值与判定规则完全由 `CODING_PLAN.md` 定义，
本项目不新增、不删除、不重解释任何字段。

## 2. 编码员配置

| 槽位 | 用途 | 约束 |
|---|---|---|
| `coder_a` | 编码员 A | 温度 0；最大输出 16000 tokens |
| `coder_b` | 编码员 B | 温度 0；最大输出 16000 tokens；**与 A 不同模型家族** |
| `adjudicator` | 第三方裁决 | 温度 0；**与 A、B 均不同家族，且模型名不得与任一相同** |

家族约束由 `src/meta_coding/config.py::ModelsConfig.validate` 在加载配置时强制，
违反即 `ConfigError`，不会进入运行阶段。模型标识在 G2 绑定后写入 `configs/models.yaml`
并连同 `config_hash` 固化进该批次 manifest。

**未绑定即不可运行**：Stage 4–6 以 `require_bound_models=True` 加载配置，任一槽位缺
provider/model/family 直接失败。

## 3. 独立性与盲法（可验证，不是"我们注意了"）

1. **进程隔离**：A/B 各自独立进程、独立 HTTP 会话、独立重试历史，不共享缓存与内存。
2. **提示词对等**：除模型配置外，渲染后的编码提示词逐字节一致；`prompt_hash_a == prompt_hash_b`，
   不相等则停止该批次。
3. **信息隔离**：编码提示词不含"另一个模型/对比/裁决/一致性"等任何暗示存在对照方的措辞。
4. **顺序去耦**：A/B 以不同随机种子（`random_seed_a` / `random_seed_b`，配置层强制不相等）
   打乱文章处理顺序；顺序落盘，Stage 6 检查顺序效应。
5. **禁止互相修复**：Stage 5 回灌只包含该编码员自己的违规清单。
6. **裁决盲法**：裁决提示词仅出现 `Coder 1` / `Coder 2`，每条争议随机映射；映射表
   `data/05_adjudication/mapping.json` 单独存放，不进入任何裁决请求。
7. **人工盲法**：人工队列不显示模型名称或厂商。

## 4. 比对口径（Stage 6.1）

比对前先做块对齐（sheet → `subsample_label` 规范化模糊匹配 → `measure_name` / `sample_n` /
性别构成交叉校验）。**禁止按数组顺序 zip**。无法匹配的块标记 `block_structure_mismatch`，
属严重分歧，不进入字段级比对。

字段规范化后再判等：

| match_type | 判定 |
|---|---|
| `exact` | 规范化后字符串或数值完全相等 |
| `equivalent` | 仅精度/书写差异（`3.4` vs `3.40`、`US` vs `U.S.`、`Overall sample` 大小写） |
| `numeric_within_tolerance` | 连续字段相差 ≤ 0.01，视为一致 |
| `mismatch` | 规范化后仍不等且差 > 0.01 |
| `missing_vs_value` | 一方 `-999`/空，另一方有值 |

只有后两类构成**实质分歧**，进入预分类与裁决；`formatting_only` 不送裁决。

## 5. 裁决规则（Stage 6.3）

- 裁决员必须先输出 `independent_value` + `independent_evidence`，再输出 `verdict`；
  Schema 用字段顺序与校验器双重保证，缺失独立判断的裁决作废重试。
- `both_wrong` 必须同时给出 `correct_value` 与原文证据，否则降级为 `source_ambiguous`
  或 `needs_human`。
- 裁决员不得写入原文无依据的值；`confidence` < 0.6 的项强制进入人工队列。
- 一致样本审计（6.4）：按字段组分层的 10%、且不少于 100 个**一致**单元格，走同一独立
  判断流程，用于估计共享错误率（两个模型一起错）。仅裁分歧会系统性低估错误率。

## 6. 一致性指标（Stage 6.6 / 9）

| 字段类型 | 指标 |
|---|---|
| 分类 | Cohen's κ + 95% CI；κ 悖论时同时报 PABAK |
| 有序 | 加权 κ（二次权重） |
| 连续 | ICC(2,1)（双向随机、绝对一致、单评分）+ CCC，并报 ±0.01 内一致率 |
| 全字段 | Krippendorff's α（名义 / 区间） |
| 块级 | 完全一致率、部分一致率 |
| 决策级 | 分表去向一致率、资格判断一致率、缺失追踪一致率 |

**置信区间一律按文章做 cluster bootstrap，B = 2000**。同一篇文章的多个单元格不是独立
观测，禁止按单元格做朴素重采样。

## 7. 抽样与人工队列

- 一致样本审计：`agreement_audit_rate = 0.10`，下限 `agreement_audit_min_cells = 100`。
- 人工队列：全部 `needs_human`、全部 `critical`、以及随机 5% 的已裁决项。
- 队列异步处理，不阻塞后续阶段；未处理项在共识数据集中标 `pending_human`，
  分析导出时排除并计数。

## 8. 成本与中断

- 每批次带 `budget_usd` 硬上限，累计估算成本触及上限即停，不"再跑一点看看"。
- 先跑 `pilot_n = 3` 篇，报告结构完整性、证据质量与实测单价，经 G2 确认后再放量。
- 逐篇落盘、断点续跑；已完成篇目不重复计费。

## 9. 修订政策

Stage 4 之后，以下任一项变化都构成偏差，必须记入 `DEVIATIONS.md` 并在报告中披露：
模型版本、温度、提示词版本、字段定义、比对容差、裁决规则、抽样比例、统计方法。
`configs/rules.lock.json` 的哈希变化会自动使旧批次 manifest 失效并触发复算。
