# RULE GAPS — 规则缺口登记

> 依据：`CODING_PLAN.md` §4 硬约束 1。只登记「规则缺口或规则冲突」，不在此讨论实现细节。
> 处理原则：按现有最具体条款执行并标记 `unresolved`；**不发明规则**。
> 状态取值：`unresolved`（待裁定）｜`resolved-by-existing`（已由更具体条款解决）｜
> `derived`（由现有材料可推导，待 G1 确认）。

规则源 SHA-256：`fa7c75005b97212efd38302faeb43bdfd5fdfde60c5175cef2e35c8bfc5921e4`

---

## GAP-001 缺失码：`0` 还是 `-999`

- **冲突**：`编码口径更新_2026-10-06.md` §2.4 规定「均值有、SD 缺失 → `scale_sd` 填 0」
  「年龄、BMI 的 mean/SD 缺失 → 填 0」；`CODING_PLAN.md` §8 规定「缺失码统一为数值 `-999`，
  不再使用 `0` 表示缺失」，附录 C 禁止「用 `-999` 填充未编码性别行」以外的一切 0 占位。
- **影响范围**：`scale_sd`、`mean_age`、`mean_bmi`、`age_group` 及全部 V29/V31/V47 校验。
- **处理**：按 `CODING_PLAN.md` §8 与用户 2026-11-19 的明确指令，**统一 `-999`**。
- **状态**：`unresolved`（组内口径未正式对账；本批次按 md 执行，见 DEV-001）

## GAP-002 `scale_total` 是否保留

- **冲突**：备忘录 §1 将 `scale_total` 列入「不再编码、直接删除的 10 列」；`CODING_PLAN.md`
  §5.6 与附录 A 明确保留 `scale_total`（「文献报告的目标量表总分尺度的平均得分」）。
- **影响范围**：V02 表头逐字比对、模板契约列数、Stage 7 写入。
- **处理**：按 md 保留，位于 `direction` 之后、`scale_mean` 之前。
- **状态**：`unresolved`（同上）

## GAP-003 `scale_comparable_flag` 判定口径

- **冲突**：`CODING_PLAN.md` §5.5 写作「1 = 论文报告翻译/回译和/或心理测量学性质；2 = 支持不足；
  3 = 不确定」；组内通知口径为「引用量表开发/验证文献即可记 1」。两者对"仅引用验证文献、
  未自行报告心理测量学结果"的论文给出不同取值。备忘录 §4 亦自认此项「仍待裁定」。
- **影响范围**：`scale_comparable_flag` 全量；V09 只能校验枚举合法，无法校验口径。
- **处理**：按 md 字面执行（须论文自身报告翻译/回译或心理测量学性质），并在
  `mean_calculation` 记录判断依据；差异项在裁决阶段单独统计。
- **状态**：`unresolved`

## GAP-004 非性别同质子样本合并时 SD 是否合并

- **缺口**：`CODING_PLAN.md` §4.2 明确治疗组/控制组属同一总体时须 pooled mean + pooled SD；
  §7.3/7.4 给出两组与多组公式。但「同一总体按严重程度分层」「同质性非 RCT 亚组」是否适用
  同一合并公式，md 未表态；备忘录 §4 亦列为待裁定。
- **影响范围**：`scale_mean`、`scale_sd`、`sample_n`、V45/V46。
- **处理**：仅在 md 明确覆盖的情形（性别合计、治疗/控制组、多组且明确同一总体）执行合并；
  其余情形不合并、按独立子样本分块，并在 `mean_calculation` 记录未合并理由。
- **状态**：`unresolved`

## GAP-005 空白分隔列的位置

- **缺口**：`CODING_PLAN.md` §1 只说「空白分隔列不算变量」，未规定分隔列出现在哪些列位。
- **影响范围**：V02（表头逐字与位置比对）、`rules/template_contract.json`、Stage 7 写入。
- **推导依据**：现有两份已编码工作簿（论文1、论文二）与 `updated_codebook_TEMPLATE.xlsx`
  三者的分隔列位置完全一致——`sex` 后、`sampling_method` 后、`year_imputed_rule` 后、
  `age_group` 后、`mean_bmi` 后。按 md 的 28 字段顺序落到列位即 **F / K / O / T / V**，
  合计 33 列。
- **状态**：`derived`（G1 由用户确认后转 `resolved-by-existing`）

## GAP-006 `paper_id` 列位与既有工作簿不一致

- **冲突**：`CODING_PLAN.md` §5.1 写「`paper_id` D 列」（即第 4 个字段）；现有
  `updated_codebook_TEMPLATE.xlsx` 与论文1/论文二 工作簿把 `study_id` 放在 **A 列**，
  D 列是 `source_type`。
- **影响范围**：模板派生、V02、V38–V44 编号校验、Stage 7。
- **处理**：按 md 生成派生模板，`paper_id` 置于 D 列；旧工作簿的 `study_id` 视同 `paper_id`
  的别名，仅用于跨批次去重比对，不改变本批次列位。
- **状态**：`resolved-by-existing`（md 明确指定 D 列）

---

## 维护约定

- 新增条目必须包含：冲突/缺口描述、涉及章节号、原文引用（≤25 词）、影响字段或校验器、
  当前执行动作、状态。
- 不允许在此文件中定义新规则；只能记录"按哪条现有规则执行"或"为何无法执行"。
- G1 审阅时逐条过一遍；状态从 `unresolved` 转为已解决须写明依据与日期。
