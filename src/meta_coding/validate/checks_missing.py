"""V29–V37：缺失值、追踪页与性别行的一致性。"""

from __future__ import annotations

from typing import Any

from .checks_structure import Violation, check, coded_rows, num, text, v
from .model import SHEET_TRACKING, Codebook, Row, blank, is_missing


def tracking_for(cb: Codebook, measure: str, subsample: str) -> list[dict[str, Any]]:
    return [
        t
        for t in cb.tracking
        if text(t.get("measure_name")) == text(measure)
        and text(t.get("subsample_label")) == text(subsample)
    ]


@check("V29", "-999 未参与任何计算", "auto", "§8, 附录C")
def v29(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for row in coded_rows(cb):
        for name in ("mean_calculation", "age_calculation"):
            note = text(row.get(name))
            if "-999" in note:
                out.append(v("V29", row.sheet, row.row, name, "计算说明中出现 -999"))
    return out


@check("V30", "总体 mean=-999 时存在追踪记录或完整性别行", "auto", "§3, §4.1")
def v30(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for block in cb.all_blocks():
        total = block.total
        if total is None or not is_missing(total.get("scale_mean")):
            continue
        has_tracking = bool(
            tracking_for(cb, total.get("measure_name"), total.get("subsample_label"))
        )
        sex_complete = all(
            isinstance(r, Row) and num(r.get("scale_mean")) is not None
            and num(r.get("scale_sd")) is not None
            for r in (block.female, block.male)
        )
        if not (has_tracking or sex_complete):
            out.append(v("V30", block.sheet, block.start_row, "scale_mean",
                         "总体均值缺失，但既无追踪记录也无完整性别组"))
    return out


@check("V31", "总体有 mean 缺 SD 时 scale_sd=-999 且登记缺失 SD", "auto", "§3, §9")
def v31(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for block in cb.all_blocks():
        total = block.total
        if total is None:
            continue
        mean, sd = total.get("scale_mean"), total.get("scale_sd")
        if blank(mean) or is_missing(mean):
            continue
        if is_missing(sd):
            hits = tracking_for(cb, total.get("measure_name"), total.get("subsample_label"))
            if not any(text(t.get("missing_statistic")) == "sd" for t in hits):
                out.append(v("V31", block.sheet, block.start_row, "scale_sd",
                             "总体缺 SD 但追踪页未登记 missing_statistic=sd"))
        elif blank(sd):
            out.append(v("V31", block.sheet, block.start_row, "scale_sd",
                         "总体有均值缺 SD 时应填 -999 而非留空"))
    return out


@check("V32", "no_mean_or_sd.sex 恒为 0", "auto", "§9")
def v32(cb: Codebook, ctx: dict) -> list[Violation]:
    return [
        v("V32", SHEET_TRACKING, int(t.get("_row", 0)), "sex",
          f"追踪页 sex 应为 0，实际 {t.get('sex')!r}")
        for t in cb.tracking
        if t.get("sex") != 0
    ]


@check("V33", "每个 (article, measure, subsample) 至多一条追踪记录", "auto", "§9")
def v33(cb: Codebook, ctx: dict) -> list[Violation]:
    seen: dict[tuple[str, str, str], int] = {}
    out: list[Violation] = []
    for t in cb.tracking:
        key = (
            text(t.get("APA_reference")) or text(t.get("paper_title")),
            text(t.get("measure_name")),
            text(t.get("subsample_label")),
        )
        if key in seen:
            out.append(v("V33", SHEET_TRACKING, int(t.get("_row", 0)), None,
                         f"重复追踪记录：{key}"))
        seen[key] = seen.get(key, 0) + 1
    return out


@check("V34", "missing_statistic 与主表状态一致", "auto", "§3, §9")
def v34(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for t in cb.tracking:
        stat = text(t.get("missing_statistic"))
        hits = tracking_for(cb, t.get("measure_name"), t.get("subsample_label"))
        in_main = [
            b for b in cb.all_blocks()
            if text(b.total.get("measure_name") if b.total else "")
            == text(t.get("measure_name"))
            and text(b.total.get("subsample_label") if b.total else "")
            == text(t.get("subsample_label"))
        ]
        if stat == "sd" and not in_main:
            out.append(v("V34", SHEET_TRACKING, int(t.get("_row", 0)), "missing_statistic",
                         "登记缺失 SD，但主表没有对应总体行"))
        if stat in ("mean", "mean_and_sd") and in_main:
            out.append(v("V34", SHEET_TRACKING, int(t.get("_row", 0)), "missing_statistic",
                         "登记缺均值，但主表仍有该块（应只在缺 SD 时保留主表）"))
        if not hits:
            out.append(v("V34", SHEET_TRACKING, int(t.get("_row", 0)), None, "孤立追踪记录"))
    return out


@check("V35", "性别行缺 mean 或 SD 时未编码", "auto", "§4.1, 附录B")
def v35(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for block in cb.all_blocks():
        for row in (block.female, block.male):
            if row is None:
                continue
            mean, sd = row.get("scale_mean"), row.get("scale_sd")
            coded = not blank(mean) and not blank(sd)
            if is_missing(mean) or is_missing(sd):
                out.append(v("V35", row.sheet, row.row, "scale_sd",
                             "性别行不得用 -999 补齐"))
            elif not coded and any(
                not blank(val) for name, val in row.values.items()
                if name not in ("sex", "paper_id")
            ):
                out.append(v("V35", row.sheet, row.row, "scale_mean",
                             "性别行缺少完整 mean+SD，却填了其他内容"))
    return out


@check("V36", "not_eligible 的 reason 具体、非空泛", "semi", "§9")
def v36(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for item in cb.excluded:
        reason = text(item.get("reason")).strip()
        if len(reason) < 15 or reason.lower() in ("not eligible", "n/a", "na"):
            out.append(v("V36", "not_eligible", int(item.get("_row", 0)), "reason",
                         f"排除理由过于空泛：{reason!r}"))
    return out


@check("V37", "有均值缺 SD 的记录同时出现在主表与追踪页", "auto", "§3, §9")
def v37(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for t in cb.tracking:
        if text(t.get("missing_statistic")) != "sd":
            continue
        in_main = any(
            text(b.total.get("measure_name") if b.total else "") == text(t.get("measure_name"))
            and text(b.total.get("subsample_label") if b.total else "")
            == text(t.get("subsample_label"))
            for b in cb.all_blocks()
        )
        if not in_main:
            out.append(v("V37", SHEET_TRACKING, int(t.get("_row", 0)), None,
                         "缺 SD 的总体未出现在主表"))
    return out

