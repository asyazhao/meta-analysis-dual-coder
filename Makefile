.PHONY: help install test lint fmt verify init clean tree

PY := .venv/bin/python
PIP := .venv/bin/python -m pip

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## 建 venv 并安装可编辑包
	python3 -m venv .venv
	$(PIP) install -q -e ".[dev]"
	# Python 3.13 会跳过带 macOS UF_HIDDEN 标志的 .pth（setuptools 的
	# __editable__.meta_coding-*.pth 正好会被跳过），导致 import 失败。
	# 用 .py 形式的 sitecustomize 兜底——site 按模块名导入，不看文件标志位。
	printf '%s\n' \
	  'import sys' \
	  '_SRC = "$(CURDIR)/src"' \
	  'if _SRC not in sys.path: sys.path.insert(0, _SRC)' \
	  > .venv/lib/python*/site-packages/sitecustomize.py

test: ## 跑全部测试
	$(PY) -m pytest -q

lint: ## ruff 检查
	.venv/bin/ruff check src tests

fmt: ## ruff 自动修复
	.venv/bin/ruff check --fix src tests

init: ## 初始化目录骨架并校验配置
	$(PY) -m meta_coding.cli init

verify: ## 复算全部 manifest 哈希
	$(PY) -m meta_coding.cli verify --all

tree: ## 打印仓库结构
	@find . -type f -not -path './.git/*' -not -path './.venv/*' \
	  -not -path './data/*' -not -path './__pycache__/*' | sort

clean: ## 清缓存（不动 data/）
	find . -name '__pycache__' -type d -not -path './.venv/*' -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache
