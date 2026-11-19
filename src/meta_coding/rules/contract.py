"""Validate a workbook against the generated template contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from .template import EXCLUDED_FIELDS, SHEET_EXCLUDED, SHEET_TRACKING, SHEETS_MAIN, TRACKING_FIELDS


def read_headers(ws) -> list[str]:
    return [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]


def check_contract(workbook_path: Path, contract_path: Path) -> list[str]:
    problems: list[str] = []
    contract: dict[str, Any] = json.loads(contract_path.read_text(encoding="utf-8"))

    if not workbook_path.is_file():
        return [f"工作簿不存在：{workbook_path}"]

    wb = load_workbook(workbook_path, read_only=True)
    try:
        expected_sheets = [*SHEETS_MAIN, SHEET_TRACKING, SHEET_EXCLUDED]
        if wb.sheetnames != expected_sheets:
            problems.append(f"工作表顺序/名称不符：{wb.sheetnames} != {expected_sheets}")

        for sheet in SHEETS_MAIN:
            if sheet not in wb.sheetnames:
                continue
            headers = read_headers(wb[sheet])
            mapping = contract["main"]["headers"]
            expected = [
                mapping.get(_letter(i + 1)) for i in range(contract["main"]["total_columns"])
            ]
            trimmed = headers[: contract["main"]["total_columns"]]
            if trimmed != expected:
                problems.append(f"{sheet} 表头与契约不符")
            # 三行块结构：E2..E4 == 0,1,2
            col = _column_index(contract["main"]["headers"], "sex")
            if col:
                sex_values = [wb[sheet].cell(row=r, column=col).value for r in (2, 3, 4)]
                if sex_values != [0, 1, 2]:
                    problems.append(f"{sheet} 首组三行 sex 标识不是 0,1,2：{sex_values}")

        if SHEET_TRACKING in wb.sheetnames:
            if read_headers(wb[SHEET_TRACKING])[: len(TRACKING_FIELDS)] != TRACKING_FIELDS:
                problems.append("no_mean_or_sd 表头与契约不符")

        if SHEET_EXCLUDED in wb.sheetnames:
            if read_headers(wb[SHEET_EXCLUDED])[: len(EXCLUDED_FIELDS)] != EXCLUDED_FIELDS:
                problems.append("not_eligible 表头与契约不符")
    finally:
        wb.close()
    return problems


def _letter(index: int) -> str:
    from openpyxl.utils import get_column_letter

    return get_column_letter(index)


def _column_index(headers: dict[str, str], field: str) -> int | None:
    for letter, name in headers.items():
        if name == field:
            from openpyxl.utils import column_index_from_string

            return column_index_from_string(letter)
    return None
