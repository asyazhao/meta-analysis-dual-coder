# ARTIFACTS INDEX

每个阶段的可追溯索引。**任何阶段结束都必须在此追加一行**，否则该阶段视为未完成。

| 阶段 | run_id | 产物 | 关键计数 | git tag | 验收 |
|---|---|---|---|---|---|
| stage-00 baseline | — | 仓库骨架、配置、治理文档、CI | — | `stage-00` | 见下 |

## stage-00 验收记录

| 检查 | 结果 |
|---|---|
| `pytest -q` | 全绿（见 CI） |
| `meta-coding --help` | 可用，11 个命令全部注册 |
| `git status` 敏感文件 | 无 PDF / .env / 密钥 |
| README 可说明项目 | 是 |

## 约定

1. `manifest.json` 的路径相对于仓库根目录，克隆后可离线复算。
2. `artifacts/private/` 与 `data/` 下的内容不进 git，只登记哈希。
3. 重跑生成新 `run_id`，旧目录保留，不覆盖。
