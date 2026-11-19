"""Stage 6.3 预分类 — 把确定性比对结果映射为固定的七类分歧。

类别与处理口径（PROTOCOL.md §4、docs/ERROR_TAXONOMY.md）：

* ``routing_mismatch`` —— 同一论文×子样本×量表被分到不同去向
  （主表 / no_mean_or_sd / not_eligible，或 WBIS/WSSQ）；进裁决；
* ``block_structure_mismatch`` —— 块数量/子样本/量表版本对不上，无法建立配对；
  进裁决（先人工复核）；
* ``numeric_conflict`` —— 连续/整数/计数字段规范化后仍不等且差 > 0.01；进裁决；
* ``categorical_conflict`` —— 枚举、编码、文本字段规范化后仍不等；进裁决；
* ``presence_conflict`` —— 一方缺失（-999/空），另一方有值；进裁决；
* ``evidence_conflict`` —— ``mean_calculation`` / ``age_calculation`` 依据文本
  不一致（值相同也要对齐依据）；进裁决；
* ``formatting_only`` —— 仅精度/书写差异（含 ±0.01 内视为一致）；**不进裁决**。

``formatting_only`` 覆盖 ``exact`` / ``equivalent`` / ``numeric_within_tolerance``，
按协议不送裁决，也不写入 ``disagreements.csv``。
"""

from __future__ import annotations

from typing import Any

from ..consensus.build import paper_key
from ..validate.model import Codebook
from .align import (
    BLOCK_STRUCTURE_MISMATCH,
    MIN_SUBSAMPLE_SIMILARITY,
    BlockPair,
    BlockView,
    normalize_text,
    subsample_similarity,
)

ROUTING_MISMATCH = "routing_mismatch"
NUMERIC_CONFLICT = "numeric_conflict"
CATEGORICAL_CONFLICT = "categorical_conflict"
PRESENCE_CONFLICT = "presence_conflict"
EVIDENCE_CONFLICT = "evidence_conflict"
FORMATTING_ONLY = "formatting_only"

CATEGORIES = (
    ROUTING_MISMATCH,
    BLOCK_STRUCTURE_MISMATCH,
    NUMERIC_CONFLICT,
    CATEGORICAL_CONFLICT,
    PRESENCE_CONFLICT,
    EVIDENCE_CONFLICT,
    FORMATTING_ONLY,
)

# 只有这两类 match_type 构成实质分歧（PROTOCOL §4 表格后两行）。
SUBSTANTIVE_MATCH_TYPES = frozenset({"mismatch", "missing_vs_value"})
# 计算说明字段：值相同但依据不同也应对齐，单列为 evidence_conflict。
EVIDENCE_FIELDS = frozenset({"mean_calculation", "age_calculation"})


def is_substantive(match_type: str) -> bool:
    """实质分歧判定；block 级发现由调用方单独加入，不走这里。"""
    return match_type in SUBSTANTIVE_MATCH_TYPES


def classify_cell(field_name: str, *, numeric: bool, match_type: str) -> str:
    """单个字段比对结果 → 预分类。

    ``numeric`` 来自 ``rules/fields.yaml`` 的 type（enum 即使存成数字也按
    分类字段处理，避免 1 vs 2 被 ±0.01 容差吞掉）。
    """
    if not is_substantive(match_type):
        return FORMATTING_ONLY
    if match_type == "missing_vs_value":
        return PRESENCE_CONFLICT
    if numeric:
        return NUMERIC_CONFLICT
    if field_name in EVIDENCE_FIELDS:
        return EVIDENCE_CONFLICT
    return CATEGORICAL_CONFLICT


def _identity_match(left: BlockView, right: BlockView) -> bool:
    if not left.paper or left.paper != right.paper:
        return False
    if left.measure_norm and right.measure_norm and left.measure_norm != right.measure_norm:
        return False
    if left.subsample_norm and right.subsample_norm:
        if (
            subsample_similarity(left.subsample_norm, right.subsample_norm)
            < MIN_SUBSAMPLE_SIMILARITY
        ):
            return False
    return True


def _tracking_match(view: BlockView, rows: list[dict[str, Any]]) -> bool:
    """另一编码员把同一块登记在 no_mean_or_sd（有值→缺 SD）即去向分歧。"""
    for item in rows:
        if not view.paper or paper_key(item.get("APA_reference")) != view.paper:
            continue
        measure = normalize_text(item.get("measure_name"))
        if view.measure_norm and measure and view.measure_norm != measure:
            continue
        subsample = normalize_text(item.get("subsample_label"))
        if view.subsample_norm and subsample:
            if (
                subsample_similarity(view.subsample_norm, subsample)
                < MIN_SUBSAMPLE_SIMILARITY
            ):
                continue
        return True
    return False


def _excluded_match(view: BlockView, rows: list[dict[str, Any]]) -> bool:
    """另一编码员把整篇判为 not_eligible：资格/去向判断不一致。"""
    return any(
        view.paper and paper_key(item.get("full_reference")) == view.paper
        for item in rows
    )


def _has_routing_counterpart(
    view: BlockView, other_blocks: list[BlockView], other: Codebook
) -> bool:
    # 1) 同一论文×子样本×量表被分到另一张主表（WBIS ↔ WSSQ）。
    for candidate in other_blocks:
        if candidate.sheet != view.sheet and _identity_match(view, candidate):
            return True
    # 2) 一方建块，另一方只登记 no_mean_or_sd。
    if _tracking_match(view, other.tracking):
        return True
    # 3) 一方建块，另一方把整篇排除。
    return _excluded_match(view, other.excluded)


def classify_block(
    pair: BlockPair,
    *,
    blocks_a: list[BlockView],
    blocks_b: list[BlockView],
    cb_a: Codebook,
    cb_b: Codebook,
) -> str:
    """未配对块的严重度归类：能证明是去向差异 → routing，否则结构错位。"""
    if pair.matched:  # pragma: no cover - 调用方只对未配对块调用
        raise ValueError("classify_block 只接受未配对的块")
    if pair.a is not None:
        view, other_blocks, other = pair.a, blocks_b, cb_b
    else:
        assert pair.b is not None
        view, other_blocks, other = pair.b, blocks_a, cb_a
    if _has_routing_counterpart(view, other_blocks, other):
        return ROUTING_MISMATCH
    return BLOCK_STRUCTURE_MISMATCH


__all__ = [
    "CATEGORIES",
    "CATEGORICAL_CONFLICT",
    "EVIDENCE_CONFLICT",
    "EVIDENCE_FIELDS",
    "FORMATTING_ONLY",
    "NUMERIC_CONFLICT",
    "PRESENCE_CONFLICT",
    "ROUTING_MISMATCH",
    "SUBSTANTIVE_MATCH_TYPES",
    "classify_block",
    "classify_cell",
    "is_substantive",
]
