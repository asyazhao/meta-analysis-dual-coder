# 部署到 GitHub

本仓库已就绪（干净的 main、stage-00…03 四个 tag、CI 配置、许可证、CITATION）。只差认证。

## 一条命令解决认证

```bash
gh auth login          # 选 GitHub.com → HTTPS → 浏览器授权
```

认证后执行：

```bash
cd ~/Desktop/西浦大二/何老师lab/meta-analysis-dual-coder

# 1) 建私有仓库并推送（首次）
gh repo create asyazhao/meta-analysis-dual-coder --private --source=. --remote=origin --push

# 2) 推送 tag
git push origin --tags

# 3) 校验 CI
gh run list --limit 5
```

公开发布（G3）前再执行：

```bash
# 把提交身份换成 GitHub noreply，避免学生邮箱进入永久历史
git config user.email "asyazhao@users.noreply.github.com"
git config user.name  "asyazhao"
```

## 发布前自检

```bash
meta-coding verify --all        # 必须全 PASS
.venv/bin/python -m pytest -q   # 必须全绿
git ls-files | grep -E '\.pdf$|(^|/)\.env$'   # 必须无输出
```

## 不应进入仓库的东西（已由 .gitignore + CI 拦截）

- `data/00_inbox/`、`data/02_ingest/`–`07_analysis/`：PDF 全文与派生数据
- `artifacts/private/`：历史快照与受限产物
- `.env`：三把 API key

受限数据只以 `manifest.json` 里的 SHA-256 形式出现，标 `external: true`。
