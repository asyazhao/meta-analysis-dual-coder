#!/usr/bin/env python3
"""Freeze a stage: hash the repository tree into a manifest.

Run this *after* the stage's code is committed and *before* tagging. It writes

    artifacts/<stage>/<run_id>/manifest.json
    artifacts/<stage>/<run_id>/README.md

and then you commit those two files. Because the manifest hashes content (not
the commit), committing the manifest afterwards keeps every hash valid.

Usage:
    python scripts/freeze.py --stage 00_baseline --run-id 20261119T000000Z-a1b2c3d4 \
        --label "Stage 0 基线" --notes "CODING_PLAN.md sha256=fa7c7500..."
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from meta_coding.artifacts import build_manifest, utc_now, write_manifest  # noqa: E402
from meta_coding.config import sha256_file  # noqa: E402
from meta_coding.paths import repo_root  # noqa: E402

SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache",
    ".mypy_cache", ".idea", ".vscode", "data", "node_modules", "build", "dist",
}
SKIP_SUFFIXES = {".pyc", ".pdf", ".mp4", ".zip", ".tar.gz"}


def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def collect(root: Path, stage_dir: Path, only: list[str] | None = None) -> list[Path]:
    files: list[Path] = []
    candidates = [root / p for p in only] if only else [root]
    for base in candidates:
        walker = [base] if base.is_file() else sorted(base.rglob("*"))
        for path in walker:
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            if any(part in SKIP_DIRS for part in rel.parts):
                continue
            if path.suffix in SKIP_SUFFIXES:
                continue
            if stage_dir in path.parents:
                continue
            files.append(path)
    return sorted(set(files))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True, help="如 00_baseline")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--label", default="")
    ap.add_argument("--notes", default="")
    ap.add_argument("--git-commit", default=None)
    ap.add_argument(
        "--path",
        action="append",
        default=None,
        help="只冻结指定路径（可重复）。缺省=整个仓库树；阶段 manifest 应只覆盖该阶段产物。",
    )
    args = ap.parse_args()

    root = repo_root(Path(__file__).resolve().parent)
    stage = args.stage if args.stage.startswith("stage-") else f"stage-{args.stage}"
    stage_dir = root / "artifacts" / stage / args.run_id

    outputs = collect(root, stage_dir, args.path)
    if not outputs:
        print("没有可冻结的文件", file=sys.stderr)
        return 1

    commit = args.git_commit if args.git_commit is not None else _git(root, "rev-parse", "HEAD")
    manifest = build_manifest(
        root=root,
        stage=stage,
        run_id=args.run_id,
        git_commit=commit or None,
        rules_hash=None,
        config_hash=None,
        outputs=outputs,
        counts={
            "files": len(outputs),
            "bytes": sum(p.stat().st_size for p in outputs),
        },
        checks=[],
        notes=args.notes,
    )
    path = write_manifest(root, stage, manifest)

    readme = stage_dir / "README.md"
    readme.write_text(
        f"# {args.label or stage}\n\n"
        f"- run_id: `{args.run_id}`\n"
        f"- frozen_at: {utc_now()}\n"
        f"- git_commit: `{commit or 'n/a'}`\n"
        f"- files: {len(outputs)}\n"
        f"- manifest: [`manifest.json`](manifest.json)\n\n"
        f"校验：`meta-coding verify --all`（离线复算全部 SHA-256）。\n",
        encoding="utf-8",
    )

    total = sum(p.stat().st_size for p in outputs)
    print(f"manifest: {path.relative_to(root)}")
    print(f"冻结 {len(outputs)} 个文件，{total} 字节")
    print(f"抽样哈希：{outputs[0].relative_to(root)} -> {sha256_file(outputs[0])[:16]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
