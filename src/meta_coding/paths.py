"""Filesystem layout of the pipeline. Single source of truth for paths."""

from __future__ import annotations

from pathlib import Path

CONFIG_DIR = "configs"
DOCS_DIR = "docs"
RULES_DIR = "rules"
DATA_DIR = "data"
ARTIFACTS_DIR = "artifacts"
REPORTS_DIR = "reports"

PIPELINE_CONFIG = f"{CONFIG_DIR}/pipeline.yaml"
MODELS_CONFIG = f"{CONFIG_DIR}/models.yaml"
RULES_LOCK = f"{CONFIG_DIR}/rules.lock.json"

CORPUS_MANIFEST = f"{DATA_DIR}/01_manifest/corpus_manifest.csv"
REGISTRY = f"{DATA_DIR}/01_manifest/registry.csv"

_DIRS = [
    "configs",
    "docs",
    "rules/schema",
    "rules/validators",
    "src/meta_coding",
    "tests/fixtures",
    "scripts",
    "data/00_inbox",
    "data/01_manifest",
    "data/02_ingest",
    "data/03_coded/coder_a",
    "data/03_coded/coder_b",
    "data/04_validation",
    "data/05_adjudication",
    "data/06_consensus",
    "data/07_analysis",
    "artifacts/public",
    "artifacts/private",
    "reports",
]


def repo_root(start: Path | None = None) -> Path:
    """Walk up from ``start`` until the repo marker is found."""
    if start is None:
        start = Path.cwd()
    p = Path(start).resolve()
    for cand in (p, *p.parents):
        if (cand / "pyproject.toml").is_file() and (cand / "src" / "meta_coding").is_dir():
            return cand
    raise RuntimeError(f"不是 meta-coding 仓库（向上未找到 pyproject.toml）：{p}")


def ensure_layout(root: Path) -> tuple[list[str], list[str]]:
    """Create the directory skeleton. Returns (created, existed)."""
    created: list[str] = []
    existed: list[str] = []
    for rel in _DIRS:
        d = root / rel
        if d.exists():
            existed.append(rel)
        else:
            d.mkdir(parents=True, exist_ok=True)
            created.append(rel)
    return created, existed


def data_path(root: Path, *parts: str) -> Path:
    return root.joinpath(DATA_DIR, *parts)


def artifacts_path(root: Path, *parts: str) -> Path:
    return root.joinpath(ARTIFACTS_DIR, *parts)
