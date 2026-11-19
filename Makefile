.PHONY: help install test lint fmt verify init clean tree

PY := .venv/bin/python
PIP := .venv/bin/python -m pip

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## 建 venv 并安装可编辑包
	python3 -m venv .venv
	$(PIP) install -q -e ".[dev]"
	# Python 3.13 的 site.py 会跳过名字以 _ 开头的 .pth 文件，而 setuptools
	# 的可编辑安装正好叫 __editable__.*.pth，导致 `import meta_coding` 失败。
	# 这里补一个普通名字的 .pth 指向 src/，仅影响本机 venv。
	printf '%s\n' "$(CURDIR)/src" > .venv/lib/python*/site-packages/meta_coding_src.pth
	# macOS 上若 venv 内的文件带 UF_HIDDEN 标志，Python 3.13 会跳过其 .pth。
	-chflags -R nohidden .venv

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
