"""Generate the blank workbook and the template contract from fields.yaml.

The original ``updated_codebook_TEMPLATE.xlsx`` is reference-only: it is a 37-column,
6-sheet legacy file with example rows. ``CODING_PLAN.md`` describes a 28-field,
4-sheet clean instrument, so the blank template is *generated* here (DEV-002) and
locked by ``rules/template_contract.json``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

SHEETS_MAIN = ("WBIS", "WSSQ")
SHEET_TRACKING = "no_mean_or_sd"
SHEET_EXCLUDED = "not_eligible"

TRACKING_FIELDS = [
    "paper_title",
    "APA_reference",
    "Mail",
    "Author",
    "CC_Mail",
    "measure_name",
    "subsample_label",
    "sex",
    "missing_statistic",
    "notes",
]
EXCLUDED_FIELDS = ["full_reference", "reason"]

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(bold=True, color="FFFFFF")
SEX_FILL = PatternFill("solid", fgColor="E2EFDA")


@dataclass
class Layout:
    fields: list[dict[str, Any]]
    separators: list[str]
    columns: dict[str, str]          # field -> column letter
    total_columns: int

    def header_row(self) -> list[str]:
        row = [""] * self.total_columns
        for field in self.fields:
            row[column_index(field["column"]) - 1] = field["name"]
        return row


def column_index(letter: str) -> int:
    from openpyxl.utils import column_index_from_string

    return column_index_from_string(letter)


def build_layout(fields_doc: dict[str, Any]) -> Layout:
    fields = fields_doc["fields"]
    meta = fields_doc["meta"]
    columns = {f["name"]: f["column"] for f in fields}
    total = max(column_index(c) for c in columns.values())
    return Layout(
        fields=fields,
        separators=list(meta.get("separator_columns", [])),
        columns=columns,
        total_columns=total,
    )


def _write_header(ws, headers: list[str]) -> None:
    for idx, name in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=idx, value=name or None)
        if name:
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
            cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[get_column_letter(idx)].width = max(12, min(28, len(name) + 4))
    ws.freeze_panes = "A2"


def build_workbook(layout: Layout) -> Workbook:
    wb = Workbook()
    wb.remove(wb.active)

    for sheet in SHEETS_MAIN:
        ws = wb.create_sheet(sheet)
        _write_header(ws, layout.header_row())
        sex_col = column_index(layout.columns["sex"])
        for offset, sex in enumerate((0, 1, 2)):
            cell = ws.cell(row=2 + offset, column=sex_col, value=sex)
            cell.fill = SEX_FILL
        # row 5 stays empty: 块间空行

    ws = wb.create_sheet(SHEET_TRACKING)
    _write_header(ws, TRACKING_FIELDS)

    ws = wb.create_sheet(SHEET_EXCLUDED)
    _write_header(ws, EXCLUDED_FIELDS)
    return wb


def build_contract(layout: Layout) -> dict[str, Any]:
    return {
        "derived_from": "CODING_PLAN.md",
        "rules_sha256": "fa7c75005b97212efd38302faeb43bdfd5fdfde60c5175cef2e35c8bfc5921e4",
        "generator": "src/meta_coding/rules/template.py",
        "sheets": {
            "main": list(SHEETS_MAIN),
            "tracking": SHEET_TRACKING,
            "excluded": SHEET_EXCLUDED,
            "count": 4,
        },
        "main": {
            "header_row": 1,
            "field_count": len(layout.fields),
            "headers": {f["column"]: f["name"] for f in layout.fields},
            "separator_columns": layout.separators,
            "total_columns": layout.total_columns,
            "block_rows": {"sex_0": 2, "sex_1": 3, "sex_2": 4, "blank_row_after": 5},
            "block_pattern": (
                "每个编码块连续三行 sex=0,1,2，块后空一行；"
                "未编码性别行仅 sex 与 paper_id"
            ),
        },
        "tracking": {"field_count": len(TRACKING_FIELDS), "headers": TRACKING_FIELDS},
        "excluded": {"field_count": len(EXCLUDED_FIELDS), "headers": EXCLUDED_FIELDS},
        "forbidden": [
            "不得增删主表字段",
            "不得改动分隔列位置",
            "不得在 not_eligible 增加 paper_id",
            "不得在 no_mean_or_sd 增加 paper_id",
        ],
    }


def generate(rules_dir: Path) -> dict[str, Any]:
    fields_doc = yaml.safe_load((rules_dir / "fields.yaml").read_text(encoding="utf-8"))
    layout = build_layout(fields_doc)

    wb = build_workbook(layout)
    out = rules_dir / "clean_codebook_BLANK.xlsx"
    wb.save(out)

    contract = build_contract(layout)
    (rules_dir / "template_contract.json").write_text(
        json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return contract
