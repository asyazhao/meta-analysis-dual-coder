"""In-memory view of a codebook workbook.

Every validator works on this model rather than on raw cells, so the same V01–V50
run against a pilot workbook, a repaired workbook, and the final consensus file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from ..rules.template import EXCLUDED_FIELDS, SHEET_EXCLUDED, SHEET_TRACKING, SHEETS_MAIN

MISSING = -999


def is_missing(value: Any) -> bool:
    return value == MISSING


def blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


@dataclass
class Row:
    sheet: str
    row: int
    values: dict[str, Any]

    def get(self, name: str) -> Any:
        return self.values.get(name)


@dataclass
class Block:
    sheet: str
    start_row: int
    rows: list[Row] = field(default_factory=list)

    @property
    def total(self) -> Row | None:
        return next((r for r in self.rows if r.get("sex") == 0), None)

    @property
    def female(self) -> Row | None:
        return next((r for r in self.rows if r.get("sex") == 1), None)

    @property
    def male(self) -> Row | None:
        return next((r for r in self.rows if r.get("sex") == 2), None)

    @property
    def sex_sequence(self) -> list[Any]:
        return [r.get("sex") for r in self.rows]

    def consistent(self, name: str) -> bool:
        vals = {r.get(name) for r in self.rows if not blank(r.get(name))}
        return len(vals) <= 1


@dataclass
class Codebook:
    path: Path | None
    sheetnames: list[str]
    headers: dict[str, list[Any]]
    blocks: dict[str, list[Block]]
    tracking: list[dict[str, Any]]
    excluded: list[dict[str, Any]]
    max_row: dict[str, int]
    contract: dict[str, Any] = field(default_factory=dict)

    def all_blocks(self) -> list[Block]:
        return [b for sheet in SHEETS_MAIN for b in self.blocks.get(sheet, [])]

    def coded_rows(self) -> list[Row]:
        """Rows that carry data (un-coded sex rows are structural only)."""
        out: list[Row] = []
        for block in self.all_blocks():
            for row in block.rows:
                if row.get("sex") == 0:
                    out.append(row)
                elif not blank(row.get("scale_mean")) and not blank(row.get("scale_sd")):
                    out.append(row)
        return out


def _row_values(ws, header_map: dict[int, str], r: int) -> dict[str, Any]:
    return {name: ws.cell(row=r, column=col).value for col, name in header_map.items()}


def _header_map(headers: list[Any]) -> dict[int, str]:
    return {i + 1: name for i, name in enumerate(headers) if name}


def load_codebook(path: Path, contract_path: Path | None = None) -> Codebook:
    contract: dict[str, Any] = {}
    if contract_path and contract_path.is_file():
        contract = json.loads(contract_path.read_text(encoding="utf-8"))

    wb = load_workbook(path, data_only=True)
    try:
        sheetnames = list(wb.sheetnames)
        headers: dict[str, list[Any]] = {}
        blocks: dict[str, list[Block]] = {}
        max_row: dict[str, int] = {}

        for sheet in SHEETS_MAIN:
            if sheet not in wb.sheetnames:
                continue
            ws = wb[sheet]
            head = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
            headers[sheet] = head
            hmap = _header_map(head)
            max_row[sheet] = ws.max_row

            sheet_blocks: list[Block] = []
            current: Block | None = None
            for r in range(2, ws.max_row + 1):
                values = _row_values(ws, hmap, r)
                if all(blank(v) for v in values.values()):
                    current = None
                    continue
                row = Row(sheet=sheet, row=r, values=values)
                has_data = any(
                    not blank(val) for key, val in values.items()
                    if key != "sex"
                )
                if not has_data:
                    current = None
                    continue
                if values.get("sex") == 0:
                    current = Block(sheet=sheet, start_row=r, rows=[row])
                    sheet_blocks.append(current)
                elif current is not None and values.get("sex") in (1, 2):
                    current.rows.append(row)
                else:
                    # 孤儿行：sex 为空或是 1/2 但没有对应 0 行
                    current = Block(sheet=sheet, start_row=r, rows=[row])
                    sheet_blocks.append(current)
                    current = None
            blocks[sheet] = sheet_blocks

        for extra in (SHEET_TRACKING, SHEET_EXCLUDED):
            if extra in wb.sheetnames:
                headers[extra] = [
                    c.value for c in next(wb[extra].iter_rows(min_row=1, max_row=1))
                ]

        def _sheet_rows(name: str) -> list[dict[str, Any]]:
            if name not in wb.sheetnames:
                return []
            ws = wb[name]
            head = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
            hmap = _header_map(head)
            out = []
            for r in range(2, ws.max_row + 1):
                values = {name_: ws.cell(row=r, column=col).value
                          for col, name_ in hmap.items()}
                if all(blank(v) for v in values.values()):
                    continue
                values["_row"] = r
                out.append(values)
            return out

        return Codebook(
            path=path,
            sheetnames=sheetnames,
            headers=headers,
            blocks=blocks,
            tracking=_sheet_rows(SHEET_TRACKING),
            excluded=_sheet_rows(SHEET_EXCLUDED),
            max_row=max_row,
            contract=contract,
        )
    finally:
        wb.close()


def main_field_names(contract: dict[str, Any]) -> list[str]:
    headers = contract.get("main", {}).get("headers", {})
    return [headers[k] for k in sorted(headers, key=_col_key)]


def _col_key(letter: str) -> int:
    from openpyxl.utils import column_index_from_string

    return column_index_from_string(letter)


__all__ = [
    "Block",
    "Codebook",
    "Row",
    "EXCLUDED_FIELDS",
    "MISSING",
    "SHEETS_MAIN",
    "SHEET_EXCLUDED",
    "SHEET_TRACKING",
    "blank",
    "is_missing",
    "load_codebook",
    "main_field_names",
]
