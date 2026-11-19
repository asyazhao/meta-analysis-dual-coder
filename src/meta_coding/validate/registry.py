"""校验器注册表与执行入口。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import checks_calc, checks_missing, checks_numbering, checks_structure  # noqa: F401
from .checks_structure import VALIDATORS, Violation
from .model import Codebook, load_codebook


def all_validators():
    return sorted(VALIDATORS, key=lambda item: item.vid)


def validator_ids() -> list[str]:
    return [v.vid for v in all_validators()]


def run_all(cb: Codebook, ctx: dict[str, Any] | None = None) -> list[Violation]:
    ctx = ctx or {}
    ctx.setdefault("contract", cb.contract)
    out: list[Violation] = []
    for validator in all_validators():
        try:
            out.extend(validator.fn(cb, ctx) or [])
        except Exception as exc:  # noqa: BLE001 - 校验器自身错误不能被吞掉
            out.append(
                Violation(
                    validator.vid,
                    "-",
                    None,
                    None,
                    f"校验器异常：{type(exc).__name__}: {exc}",
                )
            )
    return out


def run_on_workbook(
    workbook: Path,
    contract: Path | None = None,
    ctx: dict[str, Any] | None = None,
) -> list[Violation]:
    cb = load_codebook(workbook, contract)
    return run_all(cb, ctx)


def summary(violations: list[Violation]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in violations:
        counts[item.vid] = counts.get(item.vid, 0) + 1
    return counts
