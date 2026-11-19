"""Artifact manifest creation and verification.

Every stage writes ``artifacts/stage-XX_<name>/<run_id>/manifest.json`` holding
SHA-256 hashes for its inputs and outputs. Paths inside a manifest are always
relative to the repository root, so a manifest stays checkable from a fresh
clone. ``meta-coding verify`` re-computes those hashes; any mismatch means the
artifact set is no longer trustworthy.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import sha256_file


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_run_id(seed: str, when: datetime | None = None) -> str:
    when = when or datetime.now(UTC)
    stamp = when.strftime("%Y%m%dT%H%M%SZ")
    short = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]
    return f"{stamp}-{short}"


def _entry(root: Path, path: Path, *, rows: int | None = None) -> dict[str, Any]:
    abs_path = path if path.is_absolute() else (root / path)
    if not abs_path.is_file():
        raise FileNotFoundError(f"manifest 记录的文件不存在：{abs_path}")
    rel = abs_path.resolve().relative_to(root.resolve()).as_posix()
    return {
        "path": rel,
        "sha256": sha256_file(abs_path),
        "bytes": abs_path.stat().st_size,
        "rows": rows,
    }


def build_manifest(
    *,
    root: Path,
    stage: str,
    run_id: str,
    git_commit: str | None,
    rules_hash: str | None,
    config_hash: str | None,
    inputs: Iterable[Path] = (),
    outputs: Iterable[Path] = (),
    counts: dict[str, Any] | None = None,
    checks: list[dict[str, Any]] | None = None,
    status: str = "complete",
    notes: str = "",
) -> dict[str, Any]:
    return {
        "stage": stage,
        "run_id": run_id,
        "created_at": utc_now(),
        "git_commit": git_commit,
        "rules_hash": rules_hash,
        "config_hash": config_hash,
        "inputs": [_entry(root, p) for p in inputs],
        "outputs": [_entry(root, p) for p in outputs],
        "counts": counts or {},
        "checks": checks or [],
        "status": status,
        "notes": notes,
    }


def normalize_stage(stage: str) -> str:
    """Accept both ``06_adjudication`` and ``stage-06_adjudication``."""
    return stage if stage.startswith("stage-") else f"stage-{stage}"


def stage_dir(root: Path, stage: str, run_id: str) -> Path:
    return root / "artifacts" / normalize_stage(stage) / run_id


def write_manifest(root: Path, stage: str, manifest: dict[str, Any]) -> Path:
    target = stage_dir(root, stage, manifest["run_id"])
    target.mkdir(parents=True, exist_ok=True)
    path = target / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


@dataclass
class CheckResult:
    manifest: Path
    ok: bool
    problems: list[str]

    def summary(self) -> str:
        if self.ok:
            return f"PASS  {self.manifest}"
        lines = [f"FAIL  {self.manifest}"]
        lines += [f"      - {p}" for p in self.problems]
        return "\n".join(lines)


def verify_manifest(manifest_path: Path, root: Path) -> CheckResult:
    problems: list[str] = []
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return CheckResult(manifest_path, False, [f"无法读取 manifest：{exc}"])

    for kind in ("inputs", "outputs"):
        for item in data.get(kind, []):
            target = root / item["path"]
            if not target.is_file():
                problems.append(f"缺少 {kind} 文件：{item['path']}")
                continue
            if sha256_file(target) != item["sha256"]:
                problems.append(f"{kind} 哈希不符：{item['path']}")
    if data.get("status") != "complete":
        problems.append(f"status 为 {data.get('status')!r}，不是 complete")
    return CheckResult(manifest_path, not problems, problems)


def discover_manifests(artifacts_dir: Path) -> list[Path]:
    if not artifacts_dir.is_dir():
        return []
    return sorted(artifacts_dir.glob("stage-*/**/manifest.json"))


def verify_all(root: Path) -> list[CheckResult]:
    return [
        verify_manifest(p, root) for p in discover_manifests(root / "artifacts")
    ]
