# ERROR TAXONOMY — 编码错误分类法

固定 12 类（E01–E12），编码员错误归因与裁决 `error_types` 字段共用此表。
任何新增类别须记入 `DEVIATIONS.md`，否则不得出现在报告里。

| 代码 | 名称 | 定义 | 典型例子 | 默认严重度 |
|---|---|---|---|---|
| E01 | value_extraction | 数值读错 | 3.42 读成 3.24；SD 取到相邻列 | critical |
| E02 | scale_unit | 尺度/单位错 | 总分 SD 当条目 SD；SE 当 SD；除以错误的 k | critical |
| E03 | sample_mismatch | 样本错配 | 用总样本 n 填性别行；用随访 n 代替基线 n | critical |
| E04 | timepoint_baseline | 时间点错 | 用了干预后、随访或变化值 | critical |
| E05 | measure_version | 量表版本错 | 引言说 11 题，方法实际 19 题 | major |
| E06 | rule_application | 规则执行错 | 性别行填 -999；无均值却建块；分表去向错 | major |
| E07 | eligibility_decision | 资格判断错 | 纳入单题自编量表；漏排非英语文献 | critical |
| E08 | evidence_mismatch | 证据位置错 | 引用 Table 1，值实际在 Table 2 | major |
| E09 | omission | 遗漏 | 同一论文的 WSSQ 块未建；漏登记追踪页 | major |
| E10 | fabrication | 编造 | 无原文依据的 SD、邮箱、DOI、页码 | critical |
| E11 | formatting_normalization | 仅格式差异 | 3.4 vs 3.40；US vs U.S. | cosmetic |
| E12 | source_ambiguity | 原文歧义 | 同一对象两处不同值且无主次 | 非模型错误 |

## 严重度口径

- **critical**：改变纳入/排除、样本量、均值、SD 或重复样本处理，进而改变合并效应量。
- **major**：影响亚组或调节变量分类（`sample_type`、`clinical_type`、`country`、`age_group`、量表版本）。
- **minor**：背景字段（`sampling_method`、`translated_flag`、`scale_comparable_flag`、年份类字段）。
- **cosmetic**：格式、精度、大小写。

## 归因规则

1. E11 与 E12 **不计入编码员错误率**，单独报告为"书写差异"与"原文歧义"。
2. 一个争议项可对应多个代码（如 E02 + E06），按"直接致错原因"排序。
3. 严重度升级条件：若同一字段在多篇文章重复出现同一代码，且该字段属 critical 组，
   在报告中单列系统性错误，不摊入平均错误率。
4. 错误率分母为"该编码员在该字段上有裁决或审计覆盖的单元格数"，不是总单元格数；
   未覆盖单元格不假设正确。

## 与验证器的对应

部分错误代码由 V01–V50 在校验阶段即可捕获并自动归因（如 E06 对应 V13/V14/V31/V35，
E02 对应 V17/V28）。裁决阶段再次出现的同类错误才认定为**未通过校验的残留错误**，
两者在报告中分列，避免把"已修复"算进"模型能力"。
