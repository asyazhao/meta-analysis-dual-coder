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

## DEV-009 裁决模型与 Coder A 同家族（DeepSeek）

- **日期**：2026-11-19
- **指令**：用户指定 Coder A = DeepSeek、Coder B = MiMo、裁决 = DeepSeek。
- **冲突**：`PROTOCOL.md` §2 与 `ModelsConfig.validate` 要求裁决者与 A、B 均不同家族。
  同家族会让裁决者与 A 的错误相关：A 判对时更易被认同、判错时更易被复制，
  **裁决准确率会被系统性高估**，且偏倚方向不可知。
- **处置**：新增显式开关 `pipeline.allow_same_family_adjudicator`（默认 false → 直接报错）。
  本批次置为 true，并把裁决模型从 `deepseek-chat` 换成同家族的 `deepseek-reasoner`，
  以降低"完全同源"的程度（换模型 ≠ 换家族，只能减轻不能消除相关性）。
- **披露义务**：论文 Methods 与局限性必须写明：裁决模型与 Coder A 属同一厂商/家族，
  裁决准确性可能被高估；建议以人工队列结果作为对照。
- **可逆性**：把开关改回 false 并把裁决换成第三方家族（如 Gemini）即可恢复协议原状，
  无需改代码。
- **状态**：生效中（待用户在 G2 复核是否接受该风险）
