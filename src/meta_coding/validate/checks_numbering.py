"""V38–V44：paper_id 编号规则（§10.3）。"""

from __future__ import annotations

from .checks_structure import Violation, check, v
from .model import MISSING, SHEET_EXCLUDED, SHEET_TRACKING, Codebook, blank


def _articles_in_order(cb: Codebook) -> list[str]:
    """按 WBIS → WSSQ 自上而下首次出现的顺序返回论文标识。"""
    order: list[str] = []
    for sheet in ("WBIS", "WSSQ"):
        for block in cb.blocks.get(sheet, []):
            ref = block.total.get("full_reference") if block.total else None
            if isinstance(ref, str) and ref not in order:
                order.append(ref)
    return order


def _ids_by_article(cb: Codebook) -> dict[str, set[int]]:
    mapping: dict[str, set[int]] = {}
    for block in cb.all_blocks():
        for row in block.rows:
            pid = row.get("paper_id")
            ref = row.get("full_reference")
            if blank(pid) or pid in (MISSING, None) or not isinstance(ref, str):
                continue
            mapping.setdefault(ref, set()).add(int(pid))
    return mapping


@check("V38", "paper_id 从 1 连续不跳号", "auto", "§10.3")
def v38(cb: Codebook, ctx: dict) -> list[Violation]:
    ids = sorted({pid for s in _ids_by_article(cb).values() for pid in s})
    if not ids:
        return []
    expected = list(range(1, len(ids) + 1))
    if ids != expected:
        return [v("V38", "-", None, "paper_id", f"编号为 {ids}，应为 {expected}")]
    return []


@check("V39", "同一论文所有块、所有行共用同一 ID", "auto", "§10.3")
def v39(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for ref, ids in _ids_by_article(cb).items():
        if len(ids) > 1:
            out.append(v("V39", "-", None, "paper_id", f"{ref[:40]!r} 对应多个 ID：{sorted(ids)}"))
    return out


@check("V40", "WBIS 先编号，WSSQ 接续，不从 1 重启", "auto", "§10.3")
def v40(cb: Codebook, ctx: dict) -> list[Violation]:
    wbis_ids: set[int] = set()
    for block in cb.blocks.get("WBIS", []):
        for row in block.rows:
            if isinstance(row.get("paper_id"), (int, float)):
                wbis_ids.add(int(row.get("paper_id")))
    if not wbis_ids:
        return []
    max_wbis = max(wbis_ids)
    out: list[Violation] = []
    first_seen: set[str] = set()
    for sheet in ("WBIS", "WSSQ"):
        for block in cb.blocks.get(sheet, []):
            ref = block.total.get("full_reference") if block.total else None
            if not isinstance(ref, str):
                continue
            if ref in first_seen:
                continue
            first_seen.add(ref)
            pid = block.total.get("paper_id") if block.total else None
            if sheet == "WSSQ" and not wbis_ids:
                continue
            if isinstance(pid, (int, float)) and sheet == "WSSQ" and int(pid) <= max_wbis:
                if ref not in {
                    b.total.get("full_reference") for b in cb.blocks.get("WBIS", []) if b.total
                }:
                    out.append(v("V40", sheet, block.start_row, "paper_id",
                                 f"WSSQ 新论文 ID={int(pid)} 未接续 WBIS 最大值 {max_wbis}"))
    return out


@check("V41", "主表最大 paper_id 等于两表不同论文数", "auto", "§10.3")
def v41(cb: Codebook, ctx: dict) -> list[Violation]:
    articles = _articles_in_order(cb)
    ids = [pid for s in _ids_by_article(cb).values() for pid in s]
    if not articles or not ids:
        return []
    if max(ids) != len(articles):
        return [v("V41", "-", None, "paper_id",
                  f"最大 ID={max(ids)}，不同论文数={len(articles)}")]
    return []


@check("V42", "no_mean_or_sd 与 not_eligible 无 paper_id 列", "auto", "§9, 附录A")
def v42(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for sheet in (SHEET_TRACKING, SHEET_EXCLUDED):
        headers = [h for h in cb.headers.get(sheet, []) if not blank(h)]
        if "paper_id" in headers:
            out.append(v("V42", sheet, 1, "paper_id", "该表不应有 paper_id 列"))
    return out


@check("V43", "每个批次从 1 开始", "auto", "§10.3")
def v43(cb: Codebook, ctx: dict) -> list[Violation]:
    ids = sorted({pid for s in _ids_by_article(cb).values() for pid in s})
    if ids and ids[0] != 1:
        return [v("V43", "-", None, "paper_id", f"最小 ID 为 {ids[0]}，应从 1 开始")]
    return []


@check("V44", "跨批次重复论文有处理记录", "semi", "§10.3, §2.2")
def v44(cb: Codebook, ctx: dict) -> list[Violation]:
    registry = ctx.get("registry") or []
    if not registry:
        return []  # 未提供 registry 时不臆断
    known = {r.get("doi") for r in registry if r.get("doi")}
    out: list[Violation] = []
    for block in cb.all_blocks():
        ref = block.total.get("full_reference") if block.total else None
        if not isinstance(ref, str):
            continue
        for doi in known:
            if doi and doi.lower() in ref.lower() and ctx.get("registry_batch") != ctx.get(
                "batch_id"
            ):
                out.append(v("V44", block.sheet, block.start_row, "full_reference",
                             "该论文可能已在其他批次编码，需记录处理"))
    return out
