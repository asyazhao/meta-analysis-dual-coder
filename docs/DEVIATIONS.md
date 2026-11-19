# DEVIATIONS — 方案偏差记录

> 与 `PROTOCOL.md` 或 `CODING_PLAN.md` 的任何偏离都必须在此登记：日期、原条款、
> 实际做法、依据、影响范围、是否需在论文局限性中披露。**未登记的偏离视为未发生**。

---

## DEV-001 采用 `CODING_PLAN.md`，不采用 2026-10-06 组内口径备忘录

- **日期**：2026-11-19
- **背景**：`编码口径更新_2026-10-06.md` 与 `CODING_PLAN.md` 在缺失码（0 vs -999）、
  `scale_total` 存废上冲突（详见 `RULE_GAPS.md` GAP-001、GAP-002）。
- **决定**：**以 `CODING_PLAN.md` 为唯一规则源**（用户 2026-11-19 明确指令：
  「一切以 md 文件为准，xlsx 仅参考格式」）。缺失统一 `-999`；保留 `scale_total`。
- **影响**：本批次全部缺失值写法；与论文1/论文二 历史工作簿（0 占位）**不可直接拼接**，
  跨批次合并时须先做 0→-999 语义对齐，并在分析数据字典中说明。
- **是否披露**：是。在 Methods 与局限性中说明"缺失码统一为 -999"。
- **状态**：生效中

## DEV-002 空白模板为派生产物

- **日期**：2026-11-19
- **背景**：`CODING_PLAN.md` §1 指向的 `clean_codebook_BLANK.xlsx` 在本机不存在；
  仓库内的 `updated_codebook_TEMPLATE.xlsx` 为旧版（37 列 / 6 表 / 含 RULE 页 / 非空白，
  带示例数据）。
- **决定**：按 md 的 28 字段、4 工作表、33 列（含 F/K/O/T/V 五处空白分隔列）**生成**
  `clean_codebook_BLANK.xlsx` 作为派生产物，并写入 `rules/template_contract.json` 与
  规则锁。原 `updated_codebook_TEMPLATE.xlsx` 只读，不写入、不修改。
- **影响**：Stage 7 写入目标为本派生模板；V01/V02/V08 以派生模板为基准。
- **是否披露**：是（数据可用性声明中说明模板为程序生成，附契约哈希）。
- **状态**：待 G1 确认

## DEV-003 模型槽位延后绑定

- **日期**：2026-11-19
- **背景**：API 凭据由用户在需要时提供（用户 2026-11-19 指令）。
- **决定**：Stage 0–3 不绑定模型，`configs/models.yaml` 三个槽位留空；代码以
  `require_bound_models` 开关区分——Stage 4 起强制绑定，缺任一槽位即失败。
- **影响**：Stage 0–3 可完全离线开发与验收；G2 时只需改 YAML 字符串，不改代码。
- **是否披露**：否（工程细节，不影响方法学）。
- **状态**：生效中

## DEV-005 阶段 manifest 只覆盖该阶段产物

- **日期**：2026-11-19
- **背景**：stage-00 首次冻结时把整个仓库树（45 个文件）写进 manifest。Stage 1 修改
  `cli.py`/`artifacts.py` 后，`meta-coding verify` 报 stage-00 哈希不符——这不是篡改，
  而是 manifest 语义错了：它把源码快照当成了阶段产物。
- **决定**：
  1. 阶段 manifest 只登记**该阶段的产物**（数据、报告、生成的配置），源码由 git tag 覆盖；
  2. `scripts/freeze.py` 增加 `--path`，默认行为不变但阶段冻结必须显式传路径；
  3. stage-00 的 manifest 只冻结**按设计不得静默变更**的两件：`docs/PROTOCOL.md`
     （预注册方案）与 `LICENSE`；README/Makefile/pyproject/CITATION 属活文件，由 git tag 覆盖。
     早先的全仓库快照移入 `artifacts/private/superseded/`。
- **影响**：`verify` 从此只在"产物被改写"时报警，不再因后续阶段改代码而误报。
- **状态**：生效中

## DEV-006 registry 的 first_seen 必须确定性

- **日期**：2026-11-19
- **背景**：最初用运行时时间戳生成 `first_seen`，同一批次重跑会写出不同 registry.csv，
  导致 manifest 每次重算都变，破坏"相同配置重跑可解释"这一硬约束。
- **决定**：`first_seen` 记**首次见到该文章的批次号**（`batch01`），merge 时永不改写。
- **影响**：`registry.csv` 对同一输入稳定；跨批次沿用首次批次号。
- **状态**：生效中

## DEV-004 双随机种子强制不等

- **日期**：2026-11-19
- **背景**：协议要求 A/B 用不同种子打乱顺序；人工约定易失效。
- **决定**：`PipelineConfig.validate` 在配置加载时强制 `random_seed_a != random_seed_b`，
  相等即 `ConfigError`，不进入运行。
- **影响**：`configs/pipeline.yaml` 必须提供两个不同种子。
- **状态**：生效中

## DEV-007 G1 以「继续」放行，三项口径未逐条裁定

- **日期**：2026-11-19
- **背景**：G1 需确认派生模板列位与 GAP-003/004/005。用户回复「继续」，未逐项裁定。
- **决定**：按 `CODING_PLAN.md` 最具体条款执行（GAP-003 依 §5.5 字面；GAP-004 只在 md
  明确覆盖的情形合并；GAP-005 分隔列 F/K/O/T/V）；三项在 `RULE_GAPS.md` 中仍为 `unresolved`。
- **影响**：若之后组内改口径，受影响字段需重新编码并在报告中披露，不得静默改值。
- **状态**：生效中

## DEV-008 venv 采用非可编辑安装

- **日期**：2026-11-19
- **背景**：本机 Python 3.13 + macOS，setuptools 生成的 `__editable__.*.pth` 会带 `UF_HIDDEN`
  标志，`site.py` 直接跳过，导致 `import meta_coding` 时好时坏；`sitecustomize` 也被基础
  conda 环境里的同名模块抢先，不可靠。
- **决定**：`make install` 在可编辑安装后再执行一次 `pip install --no-deps --no-build-isolation .`，
  把 `src/` 实体拷进 venv；改完代码跑 `make sync`。CI（Linux）仍用可编辑安装，不受影响。
- **影响**：本机 CLI 运行的是安装副本，测试仍用 `pythonpath=src` 指向源码；改代码后忘记
  `make sync` 会出现"测试过了但 CLI 还是旧行为"，已在 RUNBOOK 标注。
- **状态**：生效中

## DEV-009 裁决模型家族约束提供显式开关（尚未启用）

- **日期**：2026-11-19
- **背景**：有提议让裁决模型与 Coder A 同家族（DeepSeek）。协议要求三者不同家族，
  同家族会让裁决者与 A 的错误相关，**裁决准确率被系统性高估**且偏倚方向不可知。
- **处置**：新增 `pipeline.allow_same_family_adjudicator`，默认 `false`（同家族直接 ConfigError）。
  当前**保持 false**；等 G2 绑定真实模型时再定。
- **若将来启用**：必须同时把该偏差写进 Methods 与局限性，并以人工队列结果作对照。
- **状态**：开关已实现，未启用

## DEV-010 同一篇允许分多次编码（长文不再被一刀切排除）

- **日期**：2026-11-19
- **背景**：`--dry-run` 实测输入量——期刊文章约 2 万 token，三篇学位论文 46k–66k token，
  后者会顶到多数模型的单次上下文上限。用户指示「可以分多次进行 coding，不一定一次完成」。
- **决定**：
  1. 长文按**页码窗口**切分，切分规则只依赖 packet 内容（确定性），因此 A/B 得到完全相同的
     分块计划；分块计划写入 manifest，可追溯到页码区间。
  2. 每块单独调用，产出仍须符合 `coder_output.schema.json`；跨块合并由 `block_id` 与
     `subsample_label` 对齐，**不做跨块数值合并**（合并属编码判断，不属于解析）。
  3. 同一篇的多块结果共享 `article_key`，`record_id` 用 `block_id` 区分，禁止重复计数。
- **影响**：长文进入正式编码；代价是单篇调用次数上升、裁决时"同一篇不同块的判断不一致"
  成为新的分歧来源，Stage 6 需单独统计该类冲突。
- **状态**：生效中（切分器实现于 Stage 4 后续提交）
