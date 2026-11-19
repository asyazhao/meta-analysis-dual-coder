#!/usr/bin/env bash
# 一键跑一个阶段，并在结束时复算 manifest 哈希。
# 用法： scripts/run_stage.sh 03 ingest --batch batch01
set -euo pipefail

if [ $# -lt 2 ]; then
  echo "用法： $0 <stage编号> <命令> [参数...]" >&2
  echo "例：   $0 03 ingest --batch batch01" >&2
  exit 64
fi

STAGE="$1"; shift
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

echo "==> Stage ${STAGE}: meta-coding $*"
"$PY" -m meta_coding.cli "$@"

echo "==> 校验 manifest"
"$PY" scripts/verify_artifacts.py

echo "==> git 状态"
git status --short || true
