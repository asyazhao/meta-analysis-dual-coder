"""V01–V50：编码工作簿校验器。

每个校验器标注依据的 CODING_PLAN.md 章节，以及类型：
* ``auto`` —— 只凭工作簿内容即可判定，可自动修复或直接阻断；
* ``semi`` —— 需要证据文本或模型判断，只提示可疑，不自动改值。

``run_all`` 返回 Violation 列表；Stage 5 用它生成回灌清单，Stage 7 用它做交付闸门。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from openpyxl.utils import column_index_from_string

from .model import (
    MISSING,
    SHEET_EXCLUDED,
    SHEET_TRACKING,
    Block,
    Codebook,
    Row,
    blank,
    is_missing,
)

# 与 rules/fields.yaml 保持一致；tests/test_validate.py 会做漂移检查
ENUMS: dict[str, list[int]] = {
    "source_type": [1, 2, 3, 4],
    "sex": [0, 1, 2],
    "sample_type": [1, 2, 3, 4],
    "sampling_method": [1, 2],
    "year_imputed_rule": [1, 2, 3],
    "age_group": [1, 2, 3, 4],
    "translated_flag": [1, 2],
    "scale_comparable_flag": [1, 2, 3],
    "direction": [1, 2],
}

US_ALIASES = {"usa", "u.s.", "u.s.a.", "united states", "united states of america"}
UK_ALIASES = {"u.k.", "united kingdom", "great britain", "england"}
CJK = re.compile(r"[\u4e00-\u9fff]")
GENERIC_REASONS = {"not eligible", "不合格", "未使用量表", "不符合", "no", "n/a", "na", "x"}


@dataclass
class Violation:
    vid: str
    sheet: str
    row: int | None
    field: str | None
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "vid": self.vid,
            "sheet": self.sheet,
            "row": self.row,
            "field": self.field,
            "message": self.message,
        }


@dataclass
class Validator:
    vid: str
    name: str
    kind: str            # auto | semi
    chapter: str
    fn: Callable[[Codebook, dict[str, Any]], list[Violation]]


VALIDATORS: list[Validator] = []


def check(vid: str, name: str, kind: str, chapter: str):
    def register(fn):
        VALIDATORS.append(Validator(vid, name, kind, chapter, fn))
        return fn

    return register


# ---------- helpers ----------

def v(vid: str, sheet: str, row: int | None, field_name: str | None, msg: str) -> Violation:
    return Violation(vid, sheet, row, field_name, msg)


def num(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def coded_rows(cb: Codebook) -> list[Row]:
    return [r for b in cb.all_blocks() for r in b.rows]


def main_rows(cb: Codebook) -> list[Row]:
    """所有数据行（含未编码性别行），用于结构检查。"""
    return [r for b in cb.all_blocks() for r in b.rows]


def _block_last_row(block: Block) -> int:
    return max(r.row for r in block.rows)


# ---------- 结构 V01–V08 ----------

@check("V01", "恰好四个工作表且名称精确", "auto", "§1, §9")
def v01(cb: Codebook, ctx: dict) -> list[Violation]:
    expected = ["WBIS", "WSSQ", SHEET_TRACKING, SHEET_EXCLUDED]
    if cb.sheetnames != expected:
        return [v("V01", "-", None, None, f"工作表为 {cb.sheetnames}，应为 {expected}")]
    return []


@check("V02", "主表 28 个表头与模板逐字一致、分隔列未改动", "auto", "§1, 附录A")
def v02(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    contract = cb.contract or ctx.get("contract") or {}
    expected = contract.get("main", {}).get("headers", {})
    for sheet in ("WBIS", "WSSQ"):
        headers = cb.headers.get(sheet)
        if headers is None:
            out.append(v("V02", sheet, None, None, "缺少该工作表"))
            continue
        for letter, name in expected.items():
            idx = column_index_from_string(letter) - 1
            actual = headers[idx] if idx < len(headers) else None
            if actual != name:
                out.append(v("V02", sheet, 1, None,
                             f"{letter}1 期望 {name!r} 实际 {actual!r}"))
        for letter in contract.get("main", {}).get("separator_columns", []):
            idx = column_index_from_string(letter) - 1
            if idx < len(headers) and not blank(headers[idx]):
                out.append(v("V02", sheet, 1, None, f"分隔列 {letter} 非空：{headers[idx]!r}"))
    return out


@check("V03", "每个编码块为连续三行 sex=0,1,2", "auto", "§4.1")
def v03(cb: Codebook, ctx: dict) -> list[Violation]:
    return [
        v("V03", b.sheet, b.start_row, "sex", f"块内 sex 序列为 {b.sex_sequence}")
        for b in cb.all_blocks()
        if b.sex_sequence != [0, 1, 2] or [r.row for r in b.rows] != list(
            range(b.start_row, b.start_row + len(b.rows))
        )
    ]


@check("V04", "块与块之间恰好一个空行", "auto", "§4.1")
def v04(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for sheet in ("WBIS", "WSSQ"):
        blocks = cb.blocks.get(sheet, [])
        for prev, nxt in zip(blocks, blocks[1:], strict=False):
            if nxt.start_row != _block_last_row(prev) + 2:
                out.append(v("V04", sheet, nxt.start_row, None, "块间空行数量不为 1"))
    return out


@check("V05", "未编码性别行仅 sex 与 paper_id 非空", "auto", "§4.1, 附录B")
def v05(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for block in cb.all_blocks():
        for row in block.rows:
            if row.get("sex") not in (1, 2):
                continue
            if not (blank(row.get("scale_mean")) and blank(row.get("scale_sd"))):
                continue  # 已编码性别行由 V35 管
            for name, value in row.values.items():
                if name in ("sex", "paper_id") or blank(value):
                    continue
                out.append(v("V05", row.sheet, row.row, name,
                             "未编码性别行填了内容，应全部留空"))
    return out


@check("V06", "同一块内 full_reference/subsample_label/measure_name 一致", "auto", "§4.1")
def v06(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for block in cb.all_blocks():
        for name in ("full_reference", "subsample_label", "measure_name"):
            if not block.consistent(name):
                out.append(v("V06", block.sheet, block.start_row, name, "块内取值不一致"))
    return out


@check("V07", "同一块内 paper_id 一致", "auto", "§5.1, §10.3")
def v07(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for block in cb.all_blocks():
        ids = {r.get("paper_id") for r in block.rows if not blank(r.get("paper_id"))}
        if len(ids) > 1:
            out.append(v("V07", block.sheet, block.start_row, "paper_id", f"块内 ID 不一致：{ids}"))
    return out


@check("V08", "not_eligible 两列；no_mean_or_sd 十列", "auto", "§9, 附录A")
def v08(cb: Codebook, ctx: dict) -> list[Violation]:
    contract = cb.contract or ctx.get("contract") or {}
    out: list[Violation] = []
    for sheet, key in ((SHEET_TRACKING, "tracking"), (SHEET_EXCLUDED, "excluded")):
        headers = [h for h in cb.headers.get(sheet, []) if not blank(h)]
        expected = contract.get(key, {}).get("field_count")
        if expected is not None and len(headers) != expected:
            out.append(v("V08", sheet, 1, None, f"列数 {len(headers)}，应为 {expected}"))
    return out


# ---------- 编码域 V09–V16 ----------

@check("V09", "枚举字段取值合法", "auto", "§5, §6")
def v09(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for row in coded_rows(cb):
        for name, allowed in ENUMS.items():
            value = row.get(name)
            if blank(value) or is_missing(value):
                continue
            if value not in allowed:
                out.append(v("V09", row.sheet, row.row, name, f"{value!r} 不在 {allowed}"))
    return out


@check("V10", "sex ∈ {0,1,2}", "auto", "§4.1")
def v10(cb: Codebook, ctx: dict) -> list[Violation]:
    return [
        v("V10", b.sheet, b.start_row, "sex", f"块首行 sex 不是 0：{b.sex_sequence}")
        for b in cb.all_blocks()
        if b.total is None
    ]


@check("V11", "age_group 与 mean_age 边界一致", "auto", "§5.4")
def v11(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for row in coded_rows(cb):
        age, group = num(row.get("mean_age")), row.get("age_group")
        if age is None:
            if age == MISSING or is_missing(row.get("mean_age")):
                if not is_missing(group):
                    out.append(v("V11", row.sheet, row.row, "age_group",
                                 "mean_age 缺失时 age_group 应为 -999"))
            continue
        expected = None
        if age < 11:
            expected = MISSING
        elif 11 <= age <= 15:
            expected = 1
        elif 15 < age < 18:
            expected = 2
        elif 18 <= age <= 25:
            expected = 3
        elif age > 25:
            expected = 4
        if expected is not None and group != expected:
            out.append(v("V11", row.sheet, row.row, "age_group",
                         f"mean_age={age} 应对应 {expected}，实际 {group!r}"))
    return out


@check("V12", "country 规范化（US/UK，不出现全名或中文）", "auto", "§5.2")
def v12(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for row in coded_rows(cb):
        value = row.get("country")
        if blank(value) or is_missing(value):
            continue
        if not isinstance(value, str):
            out.append(v("V12", row.sheet, row.row, "country", f"非字符串：{value!r}"))
            continue
        low = value.strip().lower()
        if CJK.search(value):
            out.append(v("V12", row.sheet, row.row, "country", "出现中文国名"))
        elif low in US_ALIASES:
            out.append(v("V12", row.sheet, row.row, "country", f"{value!r} 应写 US"))
        elif low in UK_ALIASES:
            out.append(v("V12", row.sheet, row.row, "country", f"{value!r} 应写 UK"))
    return out


@check("V13", "sample_type=3 时 clinical_type 有值", "auto", "§5.2, §6")
def v13(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for row in coded_rows(cb):
        if row.get("sample_type") == 3 and blank(row.get("clinical_type")):
            out.append(v("V13", row.sheet, row.row, "clinical_type", "临床样本缺 clinical_type"))
    return out


@check("V14", "sample_type∈{1,2} 时 clinical_type 为空", "auto", "§5.2")
def v14(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for row in coded_rows(cb):
        if row.get("sample_type") in (1, 2) and not blank(row.get("clinical_type")):
            out.append(v("V14", row.sheet, row.row, "clinical_type",
                         "非临床样本不应填 clinical_type"))
    return out


@check("V15", "sample_type=4 时临床细节写入 mean_calculation", "semi", "§5.2, §6")
def v15(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for row in coded_rows(cb):
        if row.get("sample_type") == 4 and blank(row.get("mean_calculation")):
            out.append(v("V15", row.sheet, row.row, "mean_calculation",
                         "非肥胖临床样本需在 mean_calculation 说明临床身份"))
    return out


@check("V16", "肥胖与非肥胖混合样本只能为 1 或 4", "semi", "§6")
def v16(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for row in coded_rows(cb):
        note = text(row.get("mean_calculation")).lower()
        if "mixed" in note and row.get("sample_type") not in (1, 4, None, MISSING):
            out.append(v("V16", row.sheet, row.row, "sample_type",
                         "记录提到 mixed，但 sample_type 不是 1 或 4"))
    return out


# ---------- 统计口径 V17–V28 ----------

@check("V17", "scale_mean 与 scale_sd 同为条目均分尺度", "auto", "§5.6, §7.2")
def v17(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for row in coded_rows(cb):
        total, mean = num(row.get("scale_total")), num(row.get("scale_mean"))
        items = num(row.get("items_n"))
        if None in (total, mean, items) or items == 0:
            continue
        if abs(total / items - mean) > 0.02:
            out.append(v("V17", row.sheet, row.row, "scale_mean",
                         f"scale_total/items_n={total / items:.2f} 与 scale_mean={mean} 不一致"))
    return out


@check("V18", "items_n 不可核实或为 -999 时不得做总分转换", "auto", "§7.2")
def v18(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for row in coded_rows(cb):
        items = row.get("items_n")
        if blank(items) or is_missing(items) or items in (0, None):
            if not is_missing(row.get("scale_mean")) and not blank(row.get("scale_mean")):
                note = text(row.get("mean_calculation"))
                if "/" in note and re.search(r"\d", note):
                    out.append(v("V18", row.sheet, row.row, "items_n",
                                 "条目数缺失却有除法换算记录"))
    return out


@check("V19", "response_scale_min ≤ max 且为整数", "auto", "§5.5")
def v19(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for row in coded_rows(cb):
        lo, hi = row.get("response_scale_min"), row.get("response_scale_max")
        if is_missing(lo) or is_missing(hi) or blank(lo) or blank(hi):
            continue
        if not all(isinstance(x, int) and not isinstance(x, bool) for x in (lo, hi)):
            out.append(v("V19", row.sheet, row.row, "response_scale_min", "反应范围不是整数"))
        elif lo > hi:
            out.append(v("V19", row.sheet, row.row, "response_scale_min", f"min={lo} > max={hi}"))
    return out


@check("V20", "direction ∈ {1,2} 且有计分说明", "semi", "§5.5")
def v20(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for row in coded_rows(cb):
        d = row.get("direction")
        if blank(d) or is_missing(d):
            continue
        if d not in (1, 2):
            out.append(v("V20", row.sheet, row.row, "direction", f"非法方向码：{d!r}"))
        elif blank(row.get("mean_calculation")):
            out.append(v("V20", row.sheet, row.row, "mean_calculation", "缺少计分方向说明"))
    return out


@check("V21", "publication_year 为首次在线发表年份", "semi", "§5.3")
def v21(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for row in coded_rows(cb):
        pub = num(row.get("publication_year"))
        ref = text(row.get("full_reference"))
        if pub is None or pub == MISSING:
            continue
        match = re.search(r"\((?:19|20)\d{2}", ref)
        if match:
            ref_year = int(re.search(r"\d{4}", match.group(0)).group(0))
            if pub > ref_year:
                out.append(v("V21", row.sheet, row.row, "publication_year",
                             f"publication_year={int(pub)} 晚于引用年份 {ref_year}"))
    return out


@check("V22", "year_imputed_rule 与 year_data_collection 来源一致", "auto", "§5.3")
def v22(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for row in coded_rows(cb):
        rule, ydc = row.get("year_imputed_rule"), row.get("year_data_collection")
        if blank(rule) or is_missing(rule) or blank(ydc) or is_missing(ydc):
            continue
        note = text(row.get("mean_calculation")).lower()
        if rule == 2 and not any(k in note for k in ("midpoint", "中点", "区间")):
            out.append(v("V22", row.sheet, row.row, "year_data_collection",
                         "rule=2 但未记录区间中点来源"))
        if rule == 3:
            pub = num(row.get("publication_year"))
            if pub and pub != MISSING and int(ydc) != int(pub) - 2:
                out.append(v("V22", row.sheet, row.row, "year_data_collection",
                             f"rule=3 但 {ydc} != publication_year-2={int(pub) - 2}"))
    return out


@check("V23", "年份区间中点按 .5 向上取整", "auto", "§5.3")
def v23(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for row in coded_rows(cb):
        if row.get("year_imputed_rule") != 2:
            continue
        note = text(row.get("mean_calculation"))
        match = re.search(r"(\d{4})\.5", note)
        if match and row.get("year_data_collection") is not None:
            expected = int(match.group(1)) + 1
            if int(row.get("year_data_collection")) != expected:
                out.append(v("V23", row.sheet, row.row, "year_data_collection",
                             f"中点为 {match.group(0)}，应取 {expected}"))
    return out


@check("V24", "rule=3 时 ydc=publication_year-2 且 publication_year 已知", "auto", "§5.3")
def v24(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for row in coded_rows(cb):
        if row.get("year_imputed_rule") != 3:
            continue
        pub, ydc = row.get("publication_year"), row.get("year_data_collection")
        if blank(pub) or is_missing(pub):
            out.append(v("V24", row.sheet, row.row, "publication_year",
                         "rule=3 但 publication_year 缺失，不得对 -999 做减法"))
        elif not blank(ydc) and int(ydc) != int(pub) - 2:
            out.append(v("V24", row.sheet, row.row, "year_data_collection",
                         f"{ydc} != {int(pub) - 2}"))
    return out


@check("V25", "baseline 为干预前且有证据", "semi", "§4.2")
def v25(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    banned = ("follow-up", "post-intervention", "post-treatment", "随访", "干预后")
    for row in coded_rows(cb):
        note = text(row.get("mean_calculation")).lower()
        if any(word in note for word in banned):
            out.append(v("V25", row.sheet, row.row, "mean_calculation",
                         "记录中出现非基线时间点，需确认取的是干预前"))
    return out


@check("V26", "量表版本以 Methods 实际施测为准", "semi", "§5.5")
def v26(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for block in cb.all_blocks():
        total = block.total
        if total is None:
            continue
        if blank(total.get("items_n")) or is_missing(total.get("items_n")):
            out.append(v("V26", total.sheet, total.row, "items_n",
                         "未核实实际条目数；不得套用常见版本"))
    return out


@check("V27", "未把子量表统计量当作全量表", "semi", "§2.2, §7.1")
def v27(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for row in coded_rows(cb):
        note = text(row.get("mean_calculation")).lower()
        if "subscale" in note and "subscale" not in text(row.get("measure_name")).lower():
            out.append(v("V27", row.sheet, row.row, "scale_mean",
                         "记录提到 subscale，疑似把子量表统计量当全量表"))
    return out


@check("V28", "总分转条目时 mean 与 SD 同除一个 k", "auto", "§7.2")
def v28(cb: Codebook, ctx: dict) -> list[Violation]:
    out: list[Violation] = []
    for row in coded_rows(cb):
        items = num(row.get("items_n"))
        sd, mean = num(row.get("scale_sd")), num(row.get("scale_mean"))
        if None in (items, sd, mean) or items == 0:
            continue
        lo = num(row.get("response_scale_min"))
        hi = num(row.get("response_scale_max"))
        if lo is None or hi is None:
            continue
        if not (lo <= mean <= hi):
            out.append(v("V28", row.sheet, row.row, "scale_mean",
                         f"条目均分 {mean} 超出反应范围 [{lo},{hi}]，疑似未除以 k"))
        if sd > (hi - lo):
            out.append(v("V28", row.sheet, row.row, "scale_sd",
                         f"SD {sd} 大于量表全距 {hi - lo}，疑似仍为总分尺度"))
    return out
