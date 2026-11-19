"""Stage 8 — 分析就绪导出。

输入：Stage 7 的共识工作簿（``data/06_consensus/<batch>_consensus_codebook.xlsx``）
及其 ``provenance.csv``。输出（``data/07_analysis/``）：

* ``analysis_long.csv`` — 一行 = 论文 × 子样本 × 量表 × 性别；
  ``-999`` 一律转为空，纯结构占位行（只有 sex + paper_id）不导出；
* ``data_dictionary.md`` — 字段字典：含义、类型/取值、缺失处理、行单位；
* ``completeness_matrix.csv`` — 论文 × 字段的填写完整度；
* ``unresolved_and_unusable.csv`` — 未解决格与不能直接进入均值元分析的行。

这里只做“导出与标注”，不修改工作簿，也不替编码员补值。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..validate.model import MISSING, Codebook, Row, blank, load_codebook

MAIN_SHEETS = ("WBIS", "WSSQ")
TRACKING_SHEET = "no_mean_or_sd"

SEX_LABELS = {0: "total", 1: "female", 2: "male"}

# 除 full_reference / subsample_label / measure_name / paper_id / sex 之外的 23 个字段
ANALYSIS_FIELDS = [
    "source_type",
    "country",
    "sample_type",
    "clinical_type",
    "sampling_method",
    "publication_year",
    "year_data_collection",
    "year_imputed_rule",
    "sample_n",
    "mean_age",
    "age_calculation",
    "age_group",
    "mean_bmi",
    "translated_flag",
    "scale_comparable_flag",
    "items_n",
    "response_scale_min",
    "response_scale_max",
    "direction",
    "scale_total",
    "scale_mean",
    "scale_sd",
    "mean_calculation",
]

LONG_COLUMNS = [
    "paper_id",
    "full_reference",
    "subsample_label",
    "measure_name",
    "sex",
    "sex_label",
    *ANALYSIS_FIELDS,
    "row_source",
    "pending_cells",
]

FIELD_NOTES: dict[str, tuple[str, str]] = {
    "paper_id": ("论文编号（§10.3）", "整数，批次内从 1 连续编号"),
    "full_reference": ("APA 完整引用", "字符串"),
    "subsample_label": ("独立子样本名称", "字符串；单一合并样本为 Overall sample"),
    "measure_name": ("量表名称", "WBIS / WBIS-M / WBIS-2F / WBIS_short_form / WSSQ"),
    "sex": ("性别行", "0=总体，1=女性，2=男性"),
    "sex_label": ("性别行标签", "total / female / male"),
    "source_type": ("文献类型", "1=期刊论文，2=博士论文，3=硕士论文，4=其他"),
    "country": ("国家", "字符串；US / UK 已规范化"),
    "sample_type": ("样本类型", "1=非肥胖，2=肥胖非临床，3=肥胖临床，4=非肥胖临床"),
    "clinical_type": ("临床类型", "字符串；sample_type=3 时填写"),
    "sampling_method": ("抽样方法", "1=概率抽样，2=非概率抽样"),
    "publication_year": ("首次在线发表年份", "整数；不是卷期年份"),
    "year_data_collection": ("数据收集年份", "整数；区间取中点（.5 向上取整）"),
    "year_imputed_rule": ("年份推算规则", "1=原文报告，2=区间中点，3=发表年-2"),
    "sample_n": ("有效样本量", "整数；与该行目标均值对应"),
    "mean_age": ("平均年龄", "数值，2 位小数"),
    "age_calculation": ("年龄计算说明", "字符串或空"),
    "age_group": ("年龄组", "1=11–15，2=15–18，3=18–25，4=>25"),
    "mean_bmi": ("平均 BMI", "数值，2 位小数"),
    "translated_flag": ("翻译版本标记", "1=翻译/跨文化版本，2=英文原版"),
    "scale_comparable_flag": ("量表可比性", "1=有支持，2=证据不足，3=不确定"),
    "items_n": ("条目数", "整数"),
    "response_scale_min": ("反应量表下限", "整数"),
    "response_scale_max": ("反应量表上限", "整数"),
    "direction": ("计分方向", "1=分数越高内化越强，2=反向"),
    "scale_total": ("总分尺度均值", "数值；仅原文报告时填写"),
    "scale_mean": ("条目均分", "数值，2 位小数；本表分析主变量"),
    "scale_sd": ("条目均分标准差", "数值，2 位小数；与 scale_mean 同尺度"),
    "mean_calculation": ("均值计算说明", "字符串或空"),
    "row_source": ("该行整体来源", "consensus / pending_human"),
    "pending_cells": ("该行待人工确认格数", "整数"),
}

REASON_NOTES = {
    "pending_human": "A/B 冲突，且没有人工或裁决取值；单元格留空",
    "missing_mean": "缺少可用的条目均分，不能进入均值元分析",
    "missing_sd": "缺少可用的标准差，不能计算权重",
    "missing_n": "缺少有效样本量，不能计算权重",
    "non_numeric_mean": "均值不是数值，需回原文核对",
    "non_numeric_sd": "标准差不是数值，需回原文核对",
    "tracked_no_mean_or_sd": "编码员已记录在 no_mean_or_sd 追踪页",
}


@dataclass
class AnalysisResult:
    long_path: Path
    dictionary_path: Path
    completeness_path: Path
    unresolved_path: Path
    papers: int
    rows: int
    skipped_placeholder: int
    missing_cells: int
    pending_cells: int
    unresolved_entries: int
    unusable_rows: int
    missing_code_leaks: int = 0
    inputs: list[Path] = field(default_factory=list)

    def summary(self) -> list[str]:
        return [
            f"长表 {self.rows} 行（论文 {self.papers} 篇），跳过纯占位行 "
            f"{self.skipped_placeholder} 行",
            f"-999/空值转缺失的单元格 {self.missing_cells} 个；"
            f"pending_human {self.pending_cells} 格",
            f"未解决条目 {self.unresolved_entries} 条；不可用行 {self.unusable_rows} 行",
            f"长表：{self.long_path}",
            f"数据字典：{self.dictionary_path}",
            f"完整性矩阵：{self.completeness_path}",
            f"未解决清单：{self.unresolved_path}",
        ]


# --------------------------------------------------------------------------- #
# 读取与清洗
# --------------------------------------------------------------------------- #


def find_consensus_workbook(root: Path, batch: str) -> Path:
    target = root / "data" / "06_consensus" / f"{batch}_consensus_codebook.xlsx"
    if target.is_file():
        return target
    candidates = sorted((root / "data" / "06_consensus").glob("*_consensus_codebook.xlsx"))
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        listing = "、".join(path.name for path in candidates)
        raise FileNotFoundError(f"找到多个共识工作簿（{listing}）；请先确认批次")
    raise FileNotFoundError(
        f"未找到共识工作簿：{target}；请先运行 meta-coding consensus --batch {batch}"
    )


def _clean(value: Any) -> Any:
    """-999 与空串都视为缺失；其余值原样保留。"""
    if blank(value):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if float(value) == float(MISSING):
            return None
    return value


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_missing_code(value: Any) -> bool:
    """导出后的值是否仍是缺失码（应为 0；大于 0 说明清洗漏了）。"""
    return _is_number(value) and float(value) == float(MISSING)


def _norm_reference(value: Any) -> str:
    """引用比对：折叠空白、忽略大小写；不做模糊匹配。"""
    if value is None:
        return ""
    return " ".join(str(value).split()).strip().casefold()


def _load_provenance(path: Path) -> dict[tuple[str, int, str], str]:
    if not path.is_file():
        return {}
    out: dict[tuple[str, int, str], str] = {}
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                excel_row = int(row.get("excel_row") or 0)
            except ValueError:
                continue
            out[(row.get("sheet") or "", excel_row, row.get("field") or "")] = (
                row.get("value_source") or ""
            )
    return out


def _pending_fields(
    sources: dict[tuple[str, int, str], str]
) -> dict[tuple[str, int], list[str]]:
    """一次扫描出每行的 pending_human 字段，避免按行重复遍历整张来源表。"""
    out: dict[tuple[str, int], list[str]] = {}
    for (sheet, excel_row, field_name), source in sources.items():
        if source == "pending_human":
            out.setdefault((sheet, excel_row), []).append(field_name)
    for names in out.values():
        names.sort()
    return out


# --------------------------------------------------------------------------- #
# 长表
# --------------------------------------------------------------------------- #


@dataclass
class _ExportRow:
    paper_id: Any
    reference: str
    subsample: str
    measure: str
    sex: Any
    values: dict[str, Any]
    pending_cells: int
    row_source: str

    def as_csv(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "paper_id": self.paper_id,
            "full_reference": self.reference,
            "subsample_label": self.subsample,
            "measure_name": self.measure,
            "sex": self.sex,
            "sex_label": SEX_LABELS.get(self.sex, ""),
        }
        out.update(self.values)
        out["row_source"] = self.row_source
        out["pending_cells"] = self.pending_cells
        return out


def _is_placeholder(values: dict[str, Any]) -> bool:
    return all(_clean(values.get(name)) is None for name in ANALYSIS_FIELDS)


def _collect_rows(
    cb: Codebook, pending: dict[tuple[str, int], list[str]]
) -> tuple[list[_ExportRow], int, int, int]:
    rows: list[_ExportRow] = []
    skipped = 0
    missing_cells = 0
    leaks = 0
    for sheet in MAIN_SHEETS:
        for block in cb.blocks.get(sheet, []):
            for row in block.rows:
                raw = {name: row.get(name) for name in ANALYSIS_FIELDS}
                if _is_placeholder(raw):
                    skipped += 1
                    continue
                values = {name: _clean(value) for name, value in raw.items()}
                missing_cells += sum(1 for value in values.values() if value is None)
                leaks += sum(1 for value in values.values() if _is_missing_code(value))
                count = len(pending.get((sheet, row.row), []))
                rows.append(
                    _ExportRow(
                        paper_id=_clean(row.get("paper_id")),
                        reference=str(row.get("full_reference") or ""),
                        subsample=str(row.get("subsample_label") or ""),
                        measure=str(row.get("measure_name") or ""),
                        sex=row.get("sex"),
                        values=values,
                        pending_cells=count,
                        row_source="pending_human" if count else "consensus",
                    )
                )
    return rows, skipped, missing_cells, leaks


# --------------------------------------------------------------------------- #
# 完整性矩阵
# --------------------------------------------------------------------------- #


def _completeness_rows(cb: Codebook) -> tuple[list[dict[str, Any]], int]:
    """论文 × 字段完整度；分母只算“有数据的行”，结构占位行不拉低完整度。"""
    groups: dict[Any, dict[str, Any]] = {}
    for sheet in MAIN_SHEETS:
        for block in cb.blocks.get(sheet, []):
            for row in block.rows:
                pid = _clean(row.get("paper_id"))
                entry = groups.setdefault(
                    pid,
                    {
                        "paper_id": pid,
                        "full_reference": "",
                        "sheets": set(),
                        "measures": set(),
                        "rows": 0,
                        "coded_rows": 0,
                        "filled": {name: 0 for name in ANALYSIS_FIELDS},
                    },
                )
                entry["rows"] += 1
                entry["sheets"].add(sheet)
                reference = row.get("full_reference")
                if reference and not entry["full_reference"]:
                    entry["full_reference"] = str(reference)
                measure = row.get("measure_name")
                if measure:
                    entry["measures"].add(str(measure))
                raw = {name: row.get(name) for name in ANALYSIS_FIELDS}
                if _is_placeholder(raw):
                    continue
                entry["coded_rows"] += 1
                for name in ANALYSIS_FIELDS:
                    if _clean(row.get(name)) is not None:
                        entry["filled"][name] += 1

    out: list[dict[str, Any]] = []
    for pid in sorted(groups, key=lambda value: (value is None, value)):
        entry = groups[pid]
        total = entry["coded_rows"]
        record: dict[str, Any] = {
            "paper_id": "" if pid is None else pid,
            "full_reference": entry["full_reference"],
            "sheets": ";".join(sorted(entry["sheets"])),
            "measures": ";".join(sorted(entry["measures"])),
            "n_rows": entry["rows"],
            "n_coded_rows": total,
        }
        filled_total = 0
        for name in ANALYSIS_FIELDS:
            filled = entry["filled"][name]
            filled_total += filled
            record[name] = round(filled / total, 4) if total else ""
        record["overall_pct"] = (
            round(filled_total / (total * len(ANALYSIS_FIELDS)), 4) if total else ""
        )
        out.append(record)
    return out, len(groups)


# --------------------------------------------------------------------------- #
# 未解决与不可用
# --------------------------------------------------------------------------- #


def _unresolved_entries(
    cb: Codebook, pending: dict[tuple[str, int], list[str]]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    ref_to_id: dict[str, Any] = {}
    for sheet in MAIN_SHEETS:
        for block in cb.blocks.get(sheet, []):
            for row in block.rows:
                reference = row.get("full_reference")
                pid = _clean(row.get("paper_id"))
                if reference and pid is not None:
                    ref_to_id.setdefault(_norm_reference(reference), pid)
    for sheet in MAIN_SHEETS:
        for block in cb.blocks.get(sheet, []):
            for row in block.rows:
                raw = {name: row.get(name) for name in ANALYSIS_FIELDS}
                placeholder = _is_placeholder(raw)
                for field_name in pending.get((sheet, row.row), []):
                    out.append(
                        _entry(row, sheet, field_name, "unresolved", "pending_human")
                    )
                if placeholder:
                    continue
                mean = _clean(row.get("scale_mean"))
                sd = _clean(row.get("scale_sd"))
                n = _clean(row.get("sample_n"))
                if mean is None:
                    out.append(_entry(row, sheet, "scale_mean", "unusable", "missing_mean"))
                elif not _is_number(mean):
                    out.append(
                        _entry(row, sheet, "scale_mean", "unusable", "non_numeric_mean")
                    )
                if sd is None:
                    out.append(_entry(row, sheet, "scale_sd", "unusable", "missing_sd"))
                elif not _is_number(sd):
                    out.append(_entry(row, sheet, "scale_sd", "unusable", "non_numeric_sd"))
                if n is None and mean is not None:
                    out.append(_entry(row, sheet, "sample_n", "unusable", "missing_n"))

    for item in cb.tracking:
        reference = item.get("APA_reference")
        reference_text = "" if blank(reference) else str(reference)
        out.append(
            {
                "paper_id": ref_to_id.get(_norm_reference(reference_text), ""),
                "full_reference": reference_text,
                "subsample_label": item.get("subsample_label") or "",
                "measure_name": item.get("measure_name") or "",
                "sex": "" if blank(item.get("sex")) else item.get("sex"),
                "field": "scale_mean/scale_sd",
                "severity": "unresolved",
                "reason_code": "tracked_no_mean_or_sd",
                "detail": f"追踪页缺失统计量：{item.get('missing_statistic') or '未注明'}",
            }
        )
    return out


def _entry(row: Row, sheet: str, field_name: str, severity: str, reason: str) -> dict[str, Any]:
    return {
        "paper_id": "" if blank(row.get("paper_id")) else row.get("paper_id"),
        "full_reference": str(row.get("full_reference") or ""),
        "subsample_label": str(row.get("subsample_label") or ""),
        "measure_name": str(row.get("measure_name") or ""),
        "sex": "" if blank(row.get("sex")) else row.get("sex"),
        "field": field_name,
        "severity": severity,
        "reason_code": reason,
        "detail": f"{sheet}!{row.row}：{REASON_NOTES.get(reason, reason)}",
    }


# --------------------------------------------------------------------------- #
# 数据字典
# --------------------------------------------------------------------------- #


def _dictionary_markdown(
    *, batch: str, workbook: Path, rows: int, papers: int, unresolved: int
) -> str:
    lines = [
        f"# 数据字典 — {batch} 分析长表",
        "",
        f"- 来源工作簿：`{workbook.name}`（Stage 7 共识工作簿）",
        f"- 行单位：**论文 × 独立子样本 × 量表 × 性别行**；本批次 {papers} 篇、{rows} 行",
        "- 缺失约定：源工作簿用 `-999` 表示缺失；导出时 `-999` 一律转为空，",
        "  下游软件按缺失值处理。**不得对含 `-999` 的原始列直接计算**（CODING_PLAN §10.4）。",
        "- 纯结构占位行（只有 `sex` + `paper_id`，所有分析字段为空）不导出；",
        "  它们不是观测记录，只是三行块的位置占位。",
        "- `sex` 行不是相互独立的样本：总体行与女性/男性行同源于一个样本，",
        "  同一分析中不得把三行当作三项独立研究同时合并。",
        "",
        "## 列定义",
        "",
        "| 列名 | 含义 | 类型/取值 |",
        "|---|---|---|",
    ]
    for name in LONG_COLUMNS:
        note, kind = FIELD_NOTES.get(name, (name, ""))
        lines.append(f"| `{name}` | {note} | {kind} |")

    lines += [
        "",
        "## 来源标记",
        "",
        "`row_source` 与 `pending_cells` 来自 Stage 7 的 `provenance.csv`：",
        "",
        "| 标记 | 含义 |",
        "|---|---|",
        "| `consensus` | 该行没有待人工确认格 |",
        "| `pending_human` | 该行至少有一格是 A/B 冲突且无人工/裁决取值，",
        "  已在 `unresolved_and_unusable.csv` 逐格列出 |",
        "",
        "Stage 7 的逐格来源标签（`manual` > `adjudication` > `ab_agree` >",
        "`coder_a_only` / `coder_b_only` > `blank` > `pending_human`）保留在",
        "`data/06_consensus/provenance.csv`，本表只做行级汇总。",
        "",
        "## 不可用行的判定",
        "",
        "`unresolved_and_unusable.csv` 中 `severity=unusable` 的行缺少均值元分析",
        "必需的三要素之一（`sample_n`、`scale_mean`、`scale_sd`），不能直接进入",
        "需要这三项的正式分析；它们仍保留在工作簿与长表中，不删除原始记录。",
        "",
        "| reason_code | 含义 |",
        "|---|---|",
    ]
    for code, note in REASON_NOTES.items():
        lines.append(f"| `{code}` | {note} |")
    lines += [
        "",
        f"本批次未解决/不可用条目：{unresolved} 条。",
        "",
        "## 配套文件",
        "",
        "### `completeness_matrix.csv`",
        "",
        "一行 = 一篇论文。`n_rows` 是三行块总行数；`n_coded_rows` 是真正有数据的行数",
        "（纯占位行不计）。各字段列是该字段在 `n_coded_rows` 中的填充比例（0–1）。",
        "",
        "### `unresolved_and_unusable.csv`",
        "",
        "一行 = 一个未解决单元格或一条不可用记录。`severity=unresolved` 表示",
        "需要人工确认才能定值；`severity=unusable` 表示该行缺均值元分析的必需项。",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


def export_analysis(
    *,
    root: Path,
    batch: str,
    consensus_path: Path | None = None,
    out_dir: Path | None = None,
) -> AnalysisResult:
    root = Path(root)
    contract = root / "rules" / "template_contract.json"
    workbook = (
        Path(consensus_path)
        if consensus_path
        else find_consensus_workbook(root, batch)
    )
    if not workbook.is_file():
        raise FileNotFoundError(f"共识工作簿不存在：{workbook}")

    cb = load_codebook(workbook, contract if contract.is_file() else None)
    sources = _load_provenance(workbook.parent / "provenance.csv")
    pending = _pending_fields(sources)

    rows, skipped, missing_cells, leaks = _collect_rows(cb, pending)
    pending_cells = sum(len(names) for names in pending.values())

    completeness, papers = _completeness_rows(cb)
    unresolved = _unresolved_entries(cb, pending)
    unusable_rows = sum(1 for item in unresolved if item["severity"] == "unusable")

    target_dir = Path(out_dir) if out_dir else root / "data" / "07_analysis"
    target_dir.mkdir(parents=True, exist_ok=True)

    long_path = target_dir / "analysis_long.csv"
    with long_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=LONG_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_csv())

    completeness_path = target_dir / "completeness_matrix.csv"
    with completeness_path.open("w", encoding="utf-8", newline="") as fh:
        fieldnames = [
            "paper_id",
            "full_reference",
            "sheets",
            "measures",
            "n_rows",
            "n_coded_rows",
            *ANALYSIS_FIELDS,
            "overall_pct",
        ]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for record in completeness:
            writer.writerow(record)

    unresolved_path = target_dir / "unresolved_and_unusable.csv"
    with unresolved_path.open("w", encoding="utf-8", newline="") as fh:
        fieldnames = [
            "paper_id",
            "full_reference",
            "subsample_label",
            "measure_name",
            "sex",
            "field",
            "severity",
            "reason_code",
            "detail",
        ]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for item in unresolved:
            writer.writerow(item)

    dictionary_path = target_dir / "data_dictionary.md"
    dictionary_path.write_text(
        _dictionary_markdown(
            batch=batch,
            workbook=workbook,
            rows=len(rows),
            papers=papers,
            unresolved=len(unresolved),
        ),
        encoding="utf-8",
    )

    return AnalysisResult(
        long_path=long_path,
        dictionary_path=dictionary_path,
        completeness_path=completeness_path,
        unresolved_path=unresolved_path,
        papers=papers,
        rows=len(rows),
        skipped_placeholder=skipped,
        missing_cells=missing_cells,
        pending_cells=pending_cells,
        unresolved_entries=len(unresolved),
        unusable_rows=unusable_rows,
        missing_code_leaks=leaks,
        inputs=[workbook, workbook.parent / "provenance.csv"],
    )


__all__ = [
    "ANALYSIS_FIELDS",
    "LONG_COLUMNS",
    "AnalysisResult",
    "export_analysis",
    "find_consensus_workbook",
]
