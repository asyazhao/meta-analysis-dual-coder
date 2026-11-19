"""V45–V50：计算复算、精度与去向完整性。"""

from __future__ import annotations

from .checks_structure import Violation, check, coded_rows, text, v
from .model import MISSING, Codebook, is_missing

NUMERIC_FIELDS = (
    "sample_n",
    "mean_age",
    "mean_bmi",
    "items_n",
    "response_scale_min",
    "response_scale_max",
    "scale_total",
    "scale_mean",
    "scale_sd",
    "publication_year",
    "year_data_collection",
)


@check("V45", "合并均值可按公式复算", "semi", "§7.3, §7.4")
def v45(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for row in coded_rows(cb):
        note = text(row.get("mean_calculation")).lower()
        if "pooled" in note or "合并" in note:
            if not any(ch.isdigit() for ch in note):
                out.append(v("V45", row.sheet, row.row, "mean_calculation",
                             "声明为合并值，但未记录代入数值"))
    return out


@check("V46", "合并 SD 含组内与组间两项，未对 SD 取平均", "semi", "§7.3, 附录B")
def v46(cb: Codebook, ctx: dict) -> list[Violation]:  # noqa: N802
    out: list[Violation] = []
    for row in coded_rows(cb):
        note = text(row.get("mean_calculation")).lower()
        if ("pooled" in note or "合并" in note) and ("sd" in note or "标准差" in note):
            if not any(token in note for token in ("sqrt", "√", "**2", "^2", "²")):
                out.append(v("V46", row.sheet, row.row, "mean_calculation",
                             "合并 SD 未体现组内+组间两项（疑似对 SD 取平均）"))
    return out


@check("V47", "两位小数；-999 不写成 -999.00；微小值保留有效数字", "auto", "§8")
def v47(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for row in coded_rows(cb):
        for name in NUMERIC_FIELDS:
            value = row.get(name)
            if isinstance(value, str):
                if value.strip() == "-999.00":
                    out.append(v("V47", row.sheet, row.row, name, "-999 不得写成 -999.00"))
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            if value == MISSING:
                continue
            if float(value).is_integer():
                continue
            if round(float(value), 2) != float(value):
                out.append(v("V47", row.sheet, row.row, name, f"{value} 超过两位小数"))
            if 0 < abs(float(value)) <= 0.01:
                out.append(v("V47", row.sheet, row.row, name,
                             f"{value} 为微小值，需保留首个非零有效数字，不得舍入为 0.00"))
            if abs(float(value)) < 1e-12:
                out.append(v("V47", row.sheet, row.row, name, "非缺失值被写成 0.00"))
    return out


@check("V48", "每篇文章都有去向", "auto", "§10.3, §10.4")
def v48(cb: Codebook, ctx: dict) -> list[Violation]:
    articles = ctx.get("articles") or []
    if not articles:
        return []
    accounted: set[str] = set()
    for block in cb.all_blocks():
        ref = block.total.get("full_reference") if block.total else None
        if isinstance(ref, str):
            accounted.add(ref)
    for item in cb.tracking:
        for key in ("APA_reference", "paper_title"):
            value = item.get(key)
            if isinstance(value, str):
                accounted.add(value)
    for item in cb.excluded:
        value = item.get("full_reference")
        if isinstance(value, str):
            accounted.add(value)

    out: list[Violation] = []
    for ref in articles:
        if isinstance(ref, str) and ref not in accounted:
            out.append(v("V48", "-", None, "full_reference",
                         f"文章无任何去向：{ref[:50]!r}"))
    return out


@check("V49", "三行块未被当作三个独立研究计数", "semi", "§4.1, 附录B")
def v49(cb: Codebook, ctx: dict) -> list[Violation]:
    seen: dict[tuple[str, str, str], int] = {}
    out: list[Violation] = []
    for block in cb.all_blocks():
        total = block.total
        ref = total.get("full_reference") if total else None
        if not isinstance(ref, str):
            continue
        key = (
            ref,
            text(total.get("subsample_label")),
            text(total.get("measure_name")),
        )
        if key in seen:
            out.append(v("V49", block.sheet, block.start_row, "subsample_label",
                         "同一论文/量表/子样本重复建块（疑似把三行当三个研究）"))
        seen[key] = seen.get(key, 0) + 1
    return out


@check("V50", "冲突值按证据顺序处理，未解决时 -999 并保留双值", "semi", "§7.1")
def v50(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for row in coded_rows(cb):
        note = text(row.get("mean_calculation")).lower()
        conflicting = any(k in note for k in ("conflicting", "冲突", "两处不同"))
        if conflicting:
            if not is_missing(row.get("scale_mean")) and not is_missing(row.get("scale_sd")):
                out.append(v("V50", row.sheet, row.row, "scale_mean",
                             "标注为冲突，但均值/SD 仍取了单一值"))
    return out
