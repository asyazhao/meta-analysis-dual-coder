# meta-coding

**把一份编码规则、一批论文、三个模型 API，变成可核对的多模型编码结果、逐字段差异，和带原文依据的修改建议。**

不是某个课题的专用脚本——规则文档是**输入**：换一份规则，就换一个研究课题。

```
规则文档 ──┐
3 个 API ──┼──▶  双模型独立编码  ──▶  逐字段比对  ──▶  第三方裁决  ──▶  结果 + 差异 + 修改建议
论文 PDF ──┘
```

## 现在能跑什么（诚实状态）

| 能力 | 状态 |
|---|---|
| PDF 摄取、表格抽取、页码锚定、packet 哈希 | ✅ 已实现，21 篇实测 |
| 规则文档 → 字段表 → Schema → 模板契约 | ✅ 已实现（含人工确认环节） |
| 双盲编码（A/B 独立进程、提示词逐字节一致） | ✅ 机制完成，`--dry-run` 可验证 |
| V01–V50 结构/口径校验 | ✅ 已实现，每条有「破坏→必须命中」用例 |
| 记录 → 工作簿 → 共识工作簿 → 分析长表 | ✅ 已实现 |
| 块对齐、字段比对、分歧分类 | ✅ 已实现 |
| 第三方裁决 | ⏳ 代码就绪，**未跑过真实调用**（无 API key） |
| 端到端真实编码 | ⏳ 未发生 |

> 没有 key 之前，仓库里**不存在**任何真实论文的编码结果。用合成数据测过全链路，但别把测试数据当成研究结论。

## 快速开始

```bash
git clone git@github.com:asyazhao/meta-analysis-dual-coder.git
cd meta-analysis-dual-coder
make install          # 建 venv、装依赖
make test             # 167 passed

# 网页控制台（推荐）
cp .env.example .env  # 填三把 key
make web              # 打开 http://127.0.0.1:8765

# 或纯命令行
meta-coding intake --batch batch01
meta-coding rules build
meta-coding ingest --batch batch01
meta-coding code --coder a --batch batch01 --limit 3 --dry-run
```

`--dry-run` 不发起任何网络请求，只用来看提示词哈希是否 A==B、单篇要多少 token。**没有 key 也能跑**。

## 界面流程

```
/keys   → 三把密钥 + 预算          （只写 .env，页面不回显）
/rules  → 上传规则文档 → 解析成字段表 → 逐条改 → 确认锁定
/papers → 选目录或上传 PDF
/run    → 摄取 → 双编码 → 比对 → 裁决（G2 未勾选时只允许 dry-run）
/results→ 字段 | A值 | B值 | 建议值 | 页码 | 严重度
/advice → 修改意见，逐条采纳/驳回
/export → 工作簿 + 分析长表 + revisions.md
```

## 核心概念：规则包

规则文档（人话，几千到几万字）经模型解析成**规则包**：

```
rulepacks/<id>/
├── pack.json           来源文档哈希、生成模型、确认状态
├── fields.yaml         字段：名称/类型/取值域/缺失规则/原文出处
├── decisions.yaml      分表去向、资格、合并、编号
├── schema/             JSON Schema（由 fields 生成）
└── template_contract.json   列位契约
```

三条硬规则：

1. **每个字段必须带原文出处**，无出处的字段直接丢弃——模型编字段是最难发现的错误。
2. **必须人工确认**（`status=confirmed`）才允许进入编码，这是通用化的 G1。
3. 确认后计算 `pack_hash`，写入所有后续 manifest，改规则必须升版本。

详见 [`docs/RULE_PACK_SPEC.md`](docs/RULE_PACK_SPEC.md)。

## 仓库结构

```
src/meta_coding/
├── ingest/         PDF → packet（页码锚点、表格、哈希）
├── rules/          规则文档 → 字段表 / Schema / 模板契约
├── coders/         提示词渲染、三种 provider 适配器、双盲运行器
├── validate/       V01–V50 + 记录物化
├── adjudicate/     块对齐、字段比对、分歧分类
├── consensus/      共识工作簿、paper_id 编号
├── analysis/       分析长表、完整性矩阵
└── web/            本机控制台（FastAPI + 原生 JS）
rules/              CODING_PLAN.md 只读副本 + 当前规则包实例
docs/               协议、数据治理、错误分类、规则缺口、偏差记录、运行手册
artifacts/          每阶段 manifest（可离线复算哈希）
```

## 可复现性

每个阶段产出 `artifacts/stage-XX/<run_id>/manifest.json`：

```json
{
  "stage": "06_diff", "git_commit": "…", "rules_hash": "…", "config_hash": "…",
  "outputs": [{"path": "data/05_adjudication/diff_cells.csv", "sha256": "…"}],
  "checks": [{"id": "V01", "status": "pass"}]
}
```

`meta-coding verify --all` 离线复算全部哈希。受限数据（PDF 全文）不进仓，manifest 里标
`external: true`，本地缺失不算篡改。

## 数据与隐私

- PDF、摄取文本、编码结果、作者邮箱：**不进仓库**，`.gitignore` 全排除。
- 三把密钥只写 `.env`，页面只回显「已设置」。
- 网页只监听 `127.0.0.1`；**这不是公网站点**，因为要接密钥和全文。
- 引用原文片段每条 ≤ 25 词。

## 部署

```bash
gh auth login
git push origin main && git push --tags
```

公开前自检：

```bash
meta-coding verify --all
pytest -q
git ls-files | grep -E '\.pdf$|(^|/)\.env$'   # 必须无输出
```

提交身份已统一为 `asyazhao@users.noreply.github.com`，历史中不含个人邮箱。

## 许可与引用

代码 MIT；文档与报告 CC BY 4.0。引用见 [`CITATION.cff`](CITATION.cff)。

## 局限

1. **共享输入风险**：A/B 读同一份 packet，摄取阶段的表格错位会被两个模型同时继承，
   分歧率会低估真实错误率；一致样本审计只能部分弥补。
2. **模型版本漂移**：厂商静默升级会导致"同配置重跑"出现差异，本项目以快照哈希 + 偏差
   登记处理，不做"再跑一次取平均"。
3. **裁决者自身误差**：裁决模型也是 LLM，`confidence` 未校准。
4. **规则解析不是确定性的**：规则文档 → 字段表依赖模型，必须人工过一遍。
5. **单批次范围**：`paper_id` 每批次从 1 开始，跨批次合并需另行去重。
