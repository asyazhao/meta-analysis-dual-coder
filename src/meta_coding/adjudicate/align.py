"""Stage 6.1 — 块对齐（纯确定性，不调用任何模型）。

对齐口径（PROTOCOL.md §4）：

1. 先按工作表分组（WBIS / WSSQ）；
2. 组内按**论文身份**分桶——有 DOI 用 DOI，否则用规范化完整引用
   （与 Stage 7 共用 :func:`meta_coding.consensus.build.paper_key`）。
   不同论文的 ``Overall sample`` 绝不能互相配对；
3. 桶内按 ``subsample_label`` 规范化后的 difflib 相似度匹配，
   ``measure_name`` / ``sample_n`` / 性别构成只做交叉校验与打分；
4. 每个编码员的每个块最多配对一次；无法配对的块标
   ``block_structure_mismatch``，属严重分歧，不进入字段级比对。

**禁止按数组顺序 zip**：配对只由内容分数决定，块在表中的先后顺序不影响结果
（同分时用块序号做确定性 tie-break，但仍需内容兼容才可能成为候选）。
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from ..consensus.build import paper_key
from ..rules.template import SHEETS_MAIN
from ..validate.model import Block, Codebook, Row, blank

MATCHED = "matched"
BLOCK_STRUCTURE_MISMATCH = "block_structure_mismatch"

# subsample_label 的模糊匹配下限；低于此值视为不同子样本，不做跨块配对。
MIN_SUBSAMPLE_SIMILARITY = 0.75
# 一方 subsample_label 缺失时的中性分：不因缺标签而直接否决（交给 measure 判断）。
NEUTRAL_SUBSAMPLE_SIMILARITY = 0.75


def normalize_text(value: Any) -> str:
    """比较用的规范化：折叠空白、忽略大小写。"""
    if value is None:
        return ""
    return " ".join(str(value).split()).strip().casefold()


def subsample_similarity(left: Any, right: Any) -> float:
    """两个子样本标签的相似度（0–1）。

    ``overall`` 与 ``overall sample`` 这类“短标签是长标签的前缀”按词边界
    提升到 0.9；``College sample`` 与 ``Community sample`` 只有 0.6，
    低于下限，视为不同块。
    """
    a = normalize_text(left)
    b = normalize_text(right)
    if not a or not b:
        return NEUTRAL_SUBSAMPLE_SIMILARITY
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if longer.startswith(shorter) and (
        len(longer) == len(shorter) or longer[len(shorter)] in " -_/("
    ):
        ratio = max(ratio, 0.9)
    return ratio


def _ordered_rows(block: Block) -> list[Row]:
    """总体行优先，其余按表内顺序；与 Stage 7 的读取口径一致。"""
    total = block.total
    rows: list[Row] = []
    if total is not None:
        rows.append(total)
    rows.extend(row for row in block.rows if row is not total)
    return rows


def _first_text(rows: list[Row], name: str) -> str:
    for row in rows:
        value = row.get(name)
        if not blank(value):
            return str(value).strip()
    return ""


def _sample_n(block: Block, rows: list[Row]) -> Any:
    total = block.total
    if total is not None and not blank(total.get("sample_n")):
        return total.get("sample_n")
    for row in rows:
        value = row.get("sample_n")
        if not blank(value):
            return value
    return None


@dataclass(frozen=True)
class BlockView:
    """一个编码员视角下的块；只保留对齐需要的元数据。"""

    sheet: str
    index: int
    block: Block
    reference: str
    paper: str
    subsample: str
    measure: str
    sample_n: Any
    sexes: tuple[Any, ...]
    subsample_norm: str
    measure_norm: str

    @property
    def start_row(self) -> int:
        return self.block.start_row


def _block_view(block: Block, index: int) -> BlockView:
    rows = _ordered_rows(block)
    reference = _first_text(rows, "full_reference")
    subsample = _first_text(rows, "subsample_label")
    measure = _first_text(rows, "measure_name")
    return BlockView(
        sheet=block.sheet,
        index=index,
        block=block,
        reference=reference,
        paper=paper_key(reference),
        subsample=subsample,
        measure=measure,
        sample_n=_sample_n(block, rows),
        sexes=tuple(row.get("sex") for row in block.rows),
        subsample_norm=normalize_text(subsample),
        measure_norm=normalize_text(measure),
    )


@dataclass
class BlockPair:
    """一对对齐结果；``a``/``b`` 任一为 None 表示该侧独有。"""

    record_id: str
    sheet: str
    a: BlockView | None
    b: BlockView | None
    similarity: float | None = None
    score: float | None = None
    status: str = MATCHED
    reason: str = ""

    @property
    def matched(self) -> bool:
        return self.a is not None and self.b is not None

    @property
    def paper(self) -> str:
        view = self.a or self.b
        return view.paper if view is not None else ""

    @property
    def subsample(self) -> str:
        view = self.a or self.b
        return view.subsample if view is not None else ""

    @property
    def measure(self) -> str:
        view = self.a or self.b
        return view.measure if view is not None else ""


@dataclass
class Alignment:
    pairs: list[BlockPair]
    blocks_a: list[BlockView]
    blocks_b: list[BlockView]

    @property
    def matched(self) -> list[BlockPair]:
        return [pair for pair in self.pairs if pair.matched]

    @property
    def unmatched(self) -> list[BlockPair]:
        return [pair for pair in self.pairs if not pair.matched]

    def of_sheet(self, sheet: str) -> list[BlockPair]:
        return [pair for pair in self.pairs if pair.sheet == sheet]


def _same_number(left: Any, right: Any) -> bool:
    if blank(left) or blank(right):
        return False
    try:
        return abs(float(left) - float(right)) <= 1e-9
    except (TypeError, ValueError):
        return str(left).strip() == str(right).strip()


def _candidate_score(left: BlockView, right: BlockView) -> tuple[float, float] | None:
    """内容兼容则返回 ``(匹配总分, subsample 相似度)``；硬性不兼容返回 None。

    硬否决：跨表（调用方已分表）、论文身份不同、双方 measure_name 不同、
    双方 subsample 相似度低于下限。``sample_n`` 不同**不是**否决项——
    它是字段级分歧，不该把同一个块拆开。
    """
    if left.sheet != right.sheet:
        return None
    if left.paper != right.paper:
        return None
    if left.measure_norm and right.measure_norm and left.measure_norm != right.measure_norm:
        return None
    similarity = subsample_similarity(left.subsample_norm, right.subsample_norm)
    if (
        left.subsample_norm
        and right.subsample_norm
        and similarity < MIN_SUBSAMPLE_SIMILARITY
    ):
        return None

    score = similarity
    if left.measure_norm and left.measure_norm == right.measure_norm:
        score += 0.10
    if _same_number(left.sample_n, right.sample_n):
        score += 0.05
    if left.sexes and left.sexes == right.sexes:
        score += 0.03
    if left.reference and left.reference == right.reference:
        score += 0.02
    return score, similarity


def _match_bucket(
    views_a: list[BlockView], views_b: list[BlockView]
) -> tuple[dict[int, tuple[BlockView, float, float]], set[int]]:
    """桶内一对一匹配：按分数从高到低贪心，保证确定性。

    先算全部候选再排序，因此块的数组顺序从不直接决定配对；顺序只在同分时
    作为 tie-break。返回 (B 视图, 匹配总分, subsample 相似度)。
    """
    candidates: list[tuple[float, float, int, int, BlockView]] = []
    for left in views_a:
        for right in views_b:
            scored = _candidate_score(left, right)
            if scored is None:
                continue
            score, similarity = scored
            candidates.append((score, similarity, left.index, right.index, right))
    candidates.sort(key=lambda item: (-item[0], item[2], item[3]))

    used_a: set[int] = set()
    used_b: set[int] = set()
    partner_of_a: dict[int, tuple[BlockView, float, float]] = {}
    for score, similarity, a_index, b_index, right in candidates:
        if a_index in used_a or b_index in used_b:
            continue
        used_a.add(a_index)
        used_b.add(b_index)
        partner_of_a[a_index] = (right, score, similarity)
    return partner_of_a, used_b


def _align_sheet(
    sheet: str, views_a: list[BlockView], views_b: list[BlockView]
) -> list[BlockPair]:
    buckets: dict[str, tuple[list[BlockView], list[BlockView]]] = {}
    for view in views_a:
        buckets.setdefault(view.paper, ([], []))[0].append(view)
    for view in views_b:
        buckets.setdefault(view.paper, ([], []))[1].append(view)

    partner_of_a: dict[int, tuple[BlockView, float, float]] = {}
    used_b: set[int] = set()
    for a_bucket, b_bucket in buckets.values():
        partners, matched_b = _match_bucket(a_bucket, b_bucket)
        partner_of_a.update(partners)
        used_b.update(matched_b)

    pairs: list[BlockPair] = []
    for view in views_a:
        hit = partner_of_a.get(view.index)
        if hit is None:
            pairs.append(
                BlockPair(
                    record_id="",
                    sheet=sheet,
                    a=view,
                    b=None,
                    status=BLOCK_STRUCTURE_MISMATCH,
                    reason="B 中找不到对应块（论文/子样本/量表不兼容）",
                )
            )
            continue
        right, score, similarity = hit
        pairs.append(
            BlockPair(
                record_id="",
                sheet=sheet,
                a=view,
                b=right,
                similarity=round(similarity, 6),
                score=round(score, 6),
            )
        )
    for view in views_b:
        if view.index in used_b:
            continue
        pairs.append(
            BlockPair(
                record_id="",
                sheet=sheet,
                a=None,
                b=view,
                status=BLOCK_STRUCTURE_MISMATCH,
                reason="A 中找不到对应块（论文/子样本/量表不兼容）",
            )
        )
    for ordinal, pair in enumerate(pairs, start=1):
        pair.record_id = f"{sheet}-{ordinal:03d}"
    return pairs


def align_codebooks(codebook_a: Codebook, codebook_b: Codebook) -> Alignment:
    """把两个编码员工作簿的块按内容对齐；不比较任何字段值。"""
    pairs: list[BlockPair] = []
    blocks_a: list[BlockView] = []
    blocks_b: list[BlockView] = []
    for sheet in SHEETS_MAIN:
        views_a = [
            _block_view(block, index)
            for index, block in enumerate(codebook_a.blocks.get(sheet, []))
        ]
        views_b = [
            _block_view(block, index)
            for index, block in enumerate(codebook_b.blocks.get(sheet, []))
        ]
        blocks_a.extend(views_a)
        blocks_b.extend(views_b)
        pairs.extend(_align_sheet(sheet, views_a, views_b))
    return Alignment(pairs=pairs, blocks_a=blocks_a, blocks_b=blocks_b)


__all__ = [
    "Alignment",
    "BLOCK_STRUCTURE_MISMATCH",
    "BlockPair",
    "BlockView",
    "MATCHED",
    "MIN_SUBSAMPLE_SIMILARITY",
    "align_codebooks",
    "normalize_text",
    "subsample_similarity",
]
