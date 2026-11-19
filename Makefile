.PHONY: help install sync test lint fmt verify init clean tree

PY := .venv/bin/python
PIP := .venv/bin/python -m pip

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## 建 venv、装依赖，并把源码同步进 venv
	python3 -m venv .venv
	$(PIP) install -q -e ".[dev]"
	$(MAKE) sync

sync: ## 把当前 src 重新装进 venv（改完代码后跑一次）
	$(PIP) install -q --no-deps --no-build-isolation --force-reinstall .

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
