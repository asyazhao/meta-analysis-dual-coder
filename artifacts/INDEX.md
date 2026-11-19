# ARTIFACTS INDEX

每个阶段的可追溯索引。**任何阶段结束都必须在此追加一行**，否则该阶段视为未完成。

| 阶段 | run_id | 产物 | 关键计数 | git tag | 验收 |
|---|---|---|---|---|---|
| stage-00 baseline | `20261119T153637Z-3ba4333` | 仓库骨架、配置、治理文档、CI、manifest | 45 文件 / 81,462 B | `stage-00` | 见下 |

## stage-00 验收记录

- `pytest`：**23 passed**（配置校验 9、CLI 6、manifest 4、仓库卫生 4）
- `ruff check src tests scripts`：All checks passed
- `meta-coding --help`：可用，11 个命令全部注册；未实现阶段以退出码 2 显式失败
- `meta-coding verify --all`：1 份 manifest，失败 0
- `git status` 敏感文件：无 PDF、无 `.env`、无密钥（`tests/test_repo_hygiene.py` 持续拦截）
- 配置自检：`batch01` / 21 篇 / 50 USD / 并发 4；`config_hash=85dcfebd…b4112`

| 检查 | 结果 |
|---|---|
| `pytest -q` | 全绿 |
| `meta-coding --help` | 可用 |
| `git status` 敏感文件 | 无 |
| README 可说明项目 | 是 |

## 约定

1. `manifest.json` 的路径相对于仓库根目录，克隆后可离线复算。
2. `artifacts/private/` 与 `data/` 下的内容不进 git，只登记哈希。
3. 重跑生成新 `run_id`，旧目录保留，不覆盖。
