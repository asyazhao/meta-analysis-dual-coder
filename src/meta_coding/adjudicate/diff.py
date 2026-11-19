"""Stage 6.2 — 字段级比对（纯确定性，不调用任何模型）。

流程：块对齐（:mod:`meta_coding.adjudicate.align`）→ 逐行逐字段规范化判等
→ 预分类（:mod:`meta_coding.adjudicate.classify`）→ 落盘三张表。

判等口径（PROTOCOL.md §4）：

* ``exact`` —— 规范化后字符串或数值完全相等；
* ``equivalent`` —— 仅精度/书写差异（3.4 vs ``"3.40"``、US vs U.S.）；
* ``numeric_within_tolerance`` —— 连续字段相差 ≤ 0.01（含边界）视为一致；
* ``mismatch`` —— 规范化后仍不等且差 > 0.01；
* ``missing_vs_value`` —— 一方 -999/空，另一方有值。

比对范围：``rules/fields.yaml`` 中除 ``full_reference`` / ``paper_id`` / ``sex``
以外的 25 个内容字段。前三者是块身份或结构字段（引用差异由块对齐与 Stage 7 的
paper_id_map 处理，编码阶段 paper_id 为空，sex 是行键），不参与单元格判等。

``record_id`` 是「工作表-块序号-性别」的合成 ID（如 ``WBIS-001-s0``）：编码阶段的
工作簿不保存 Stage 4 的 record_id，且 paper_id 留空，无法还原原始 ID。
``blocks.csv`` 保留 record_id 到 paper_key / 子样本 / 量表 / Excel 行号的映射。

双方都缺失（空或 -999）的单元格视为一致，不写入 ``diff_cells.csv``，只计数。

产物（默认在 ``data/05_adjudication/``）：

* ``diff_cells.csv`` —— 所有比对过的单元格，含 match_type；
* ``disagreements.csv`` —— 仅实质分歧（cell 级 + block 级）；
* ``blocks.csv`` —— 块对齐审计表：配对依据、相似度、无法配对的原因。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..consensus.build import find_coder_workbook
from ..validate.model import Codebook, Row, blank, is_missing, load_codebook
from .align import Alignment, BlockPair, BlockView, align_codebooks, normalize_text
from .classify import (
    CATEGORIES,
    FORMATTING_ONLY,
    SUBSTANTIVE_MATCH_TYPES,
    classify_block,
    classify_cell,
    is_substantive,
)

EXACT = "exact"
EQUIVALENT = "equivalent"
NUMERIC_WITHIN_TOLERANCE = "numeric_within_tolerance"
MISMATCH = "mismatch"
MISSING_VS_VALUE = "missing_vs_value"
MATCH_TYPES = (EXACT, EQUIVALENT, NUMERIC_WITHIN_TOLERANCE, MISMATCH, MISSING_VS_VALUE)

# PROTOCOL §4：连续字段相差 ≤ 0.01 视为一致；浮点误差单独给 1e-9 余量。
TOLERANCE = 0.01
_EPS = 1e-9

# 不参与单元格判等的字段：块身份 / 结构键 / 编码阶段为空。
SKIP_FIELDS = frozenset({"full_reference", "paper_id", "sex"})
NUMERIC_KINDS = frozenset({"integer", "number", "number_or_missing", "number_or_null"})

DIFF_CELL_COLUMNS = ["record_id", "field", "coder_a_value", "coder_b_value", "match_type"]
DISAGREEMENT_COLUMNS = [
    "level",
    "record_id",
    "sheet",
    "field",
    "coder_a_value",
    "coder_b_value",
    "match_type",
    "category",
    "note",
]
BLOCK_COLUMNS = [
    "record_id",
    "sheet",
    "status",
    "category",
    "paper_key",
    "subsample_label",
    "measure_name",
    "sex_rows",
    "a_start_row",
    "b_start_row",
    "similarity",
    "match_score",
    "reason",
]


# --------------------------------------------------------------------------- #
# 字段定义与取值比较
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FieldSpec:
    """``rules/fields.yaml`` 里与比对有关的最小投影。"""

    name: str
    kind: str
    aliases: dict[str, str] = field(default_factory=dict)

    @property
    def numeric(self) -> bool:
        return self.kind in NUMERIC_KINDS


def load_field_specs(path: Path) -> dict[str, FieldSpec]:
    """读 rules/fields.yaml；``normalization`` 段转成别名 → 规范值。"""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"缺少字段定义：{path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    specs: dict[str, FieldSpec] = {}
    for item in payload.get("fields") or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        aliases: dict[str, str] = {}
        for canonical, variants in (item.get("normalization") or {}).items():
            canon = normalize_text(canonical)
            aliases[canon] = canon
            for variant in variants or []:
                aliases[normalize_text(str(variant))] = canon
        specs[name] = FieldSpec(
            name=name, kind=str(item.get("type") or "string"), aliases=aliases
        )
    return specs


def absent(value: Any) -> bool:
    """空值或缺失码 -999；真实 0 不是缺失。"""
    return blank(value) or is_missing(value)


def as_number(value: Any) -> float | None:
    """能解析成数字就返回 float，否则 None；逗号千分位按数字处理。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _number_text(value: Any) -> str:
    """数值的书写形式；字符串保留原写法（"3.40" 与 3.4 才能区分精度）。"""
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    return normalize_text(value)


def canonical_text(spec: FieldSpec, value: Any) -> str:
    """规范化文本：空白/大小写 → 配置别名 → 连字符/斜杠/下划线视为空格 → 去首尾标点。"""
    text = normalize_text(value)
    if text in spec.aliases:
        return spec.aliases[text]
    text = text.replace("–", "-").replace("—", "-")
    for separator in ("-", "/", "_"):
        text = text.replace(separator, " ")
    return " ".join(text.split()).strip(" .,;:，。；、")


def compare_values(spec: FieldSpec, value_a: Any, value_b: Any) -> str:
    """两个单元格值的 match_type；调用方负责先排除“双方都缺失”。"""
    if absent(value_a) and absent(value_b):
        return EXACT
    if absent(value_a) or absent(value_b):
        return MISSING_VS_VALUE

    if spec.numeric:
        number_a = as_number(value_a)
        number_b = as_number(value_b)
        if number_a is not None and number_b is not None:
            delta = abs(number_a - number_b)
            if delta <= _EPS:
                same_text = _number_text(value_a) == _number_text(value_b)
                return EXACT if same_text else EQUIVALENT
            if delta <= TOLERANCE + _EPS:
                return NUMERIC_WITHIN_TOLERANCE
            return MISMATCH

    if normalize_text(value_a) == normalize_text(value_b):
        return EXACT
    if canonical_text(spec, value_a) == canonical_text(spec, value_b):
        return EQUIVALENT
    return MISMATCH


# --------------------------------------------------------------------------- #
# 比对结果
# --------------------------------------------------------------------------- #


@dataclass
class DiffCell:
    """一行比对结果；block 级发现的 ``field``/``match_type`` 为空。"""

    record_id: str
    sheet: str
    field: str
    value_a: Any
    value_b: Any
    match_type: str
    category: str
    level: str = "cell"
    note: str = ""


def _describe(view: BlockView | None) -> Any:
    if view is None:
        return ""
    parts = [part for part in (view.subsample, view.measure) if part]
    return " | ".join(parts) if parts else f"row {view.start_row}"


def _row_map(block_rows: list[Row]) -> dict[Any, Row]:
    mapping: dict[Any, Row] = {}
    for row in block_rows:
        mapping.setdefault(row.get("sex"), row)
    return mapping


def _sex_order(rows_a: dict[Any, Row], rows_b: dict[Any, Row]) -> list[Any]:
    order: list[Any] = [sex for sex in (0, 1, 2) if sex in rows_a or sex in rows_b]
    for sex in (*rows_a, *rows_b):
        if sex not in order:
            order.append(sex)
    return order


def _record_suffix(sex: Any) -> str:
    return f"s{sex}" if sex is not None else "s?"


def _compare_pair(
    pair: BlockPair,
    specs: dict[str, FieldSpec],
    fields: list[str],
    cells: list[DiffCell],
) -> int:
    """比较一个已配对块；返回双方都缺失的单元格数。"""
    assert pair.a is not None and pair.b is not None
    rows_a = _row_map(pair.a.block.rows)
    rows_b = _row_map(pair.b.block.rows)
    both_absent = 0
    for sex in _sex_order(rows_a, rows_b):
        row_a = rows_a.get(sex)
        row_b = rows_b.get(sex)
        record_id = f"{pair.record_id}-{_record_suffix(sex)}"
        for name in fields:
            value_a = row_a.get(name) if row_a is not None else None
            value_b = row_b.get(name) if row_b is not None else None
            if absent(value_a) and absent(value_b):
                both_absent += 1
                continue
            spec = specs[name]
            match_type = compare_values(spec, value_a, value_b)
            category = classify_cell(name, numeric=spec.numeric, match_type=match_type)
            cells.append(
                DiffCell(
                    record_id=record_id,
                    sheet=pair.sheet,
                    field=name,
                    value_a=value_a,
                    value_b=value_b,
                    match_type=match_type,
                    category=category,
                )
            )
    return both_absent


@dataclass
class DiffReport:
    batch: str
    alignment: Alignment
    cells: list[DiffCell] = field(default_factory=list)
    block_findings: list[DiffCell] = field(default_factory=list)
    both_absent: int = 0
    inputs: list[Path] = field(default_factory=list)
    diff_cells_path: Path | None = None
    disagreements_path: Path | None = None
    blocks_path: Path | None = None

    @property
    def disagreements(self) -> list[DiffCell]:
        cell_level = [cell for cell in self.cells if is_substantive(cell.match_type)]
        return [*self.block_findings, *cell_level]

    @property
    def match_type_counts(self) -> dict[str, int]:
        counts = {name: 0 for name in MATCH_TYPES}
        for cell in self.cells:
            counts[cell.match_type] = counts.get(cell.match_type, 0) + 1
        return counts

    @property
    def category_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for cell in self.disagreements:
            counts[cell.category] = counts.get(cell.category, 0) + 1
        return counts

    def summary(self) -> list[str]:
        alignment = self.alignment
        matched = len(alignment.matched)
        unmatched_a = sum(1 for pair in alignment.unmatched if pair.a is not None)
        unmatched_b = sum(1 for pair in alignment.unmatched if pair.b is not None)
        counts = self.match_type_counts
        categories = self.category_counts
        category_text = "，".join(
            f"{name} {categories[name]}"
            for name in CATEGORIES
            if name != FORMATTING_ONLY and categories.get(name)
        ) or "无"
        formatting = sum(1 for cell in self.cells if not is_substantive(cell.match_type))
        return [
            f"块对齐：A {len(alignment.blocks_a)} 块 / B {len(alignment.blocks_b)} 块；"
            f"匹配 {matched}，A 独有 {unmatched_a}，B 独有 {unmatched_b}",
            f"字段比对：{len(self.cells)} 格（双方均缺失 {self.both_absent} 格不计）；"
            + "，".join(f"{name} {counts.get(name, 0)}" for name in MATCH_TYPES),
            f"实质分歧：{len(self.disagreements)}（{category_text}）",
            f"formatting_only：{formatting} 格，不进入裁决",
        ]

    def checks(self) -> list[dict[str, Any]]:
        """阶段自检：块账目平、错位块不参与字段比对、formatting_only 不混入。"""
        alignment = self.alignment
        matched = len(alignment.matched)
        unmatched_a = sum(1 for pair in alignment.unmatched if pair.a is not None)
        unmatched_b = sum(1 for pair in alignment.unmatched if pair.b is not None)
        accounted = (
            matched + unmatched_a == len(alignment.blocks_a)
            and matched + unmatched_b == len(alignment.blocks_b)
        )
        mismatched_ids = {pair.record_id for pair in alignment.unmatched}
        compared_ids = {cell.record_id.rsplit("-s", 1)[0] for cell in self.cells}
        leaked = sorted(mismatched_ids & compared_ids)
        formatting_leak = [
            cell.record_id for cell in self.disagreements
            if cell.category == FORMATTING_ONLY
        ]
        unknown_categories = sorted(
            {cell.category for cell in self.disagreements} - set(CATEGORIES)
        )
        substantive_ok = all(
            is_substantive(cell.match_type) or cell.level == "block"
            for cell in self.disagreements
        )
        return [
            {
                "id": "blocks_accounted",
                "ok": accounted,
                "detail": f"匹配 {matched} + A 独有 {unmatched_a} = {len(alignment.blocks_a)}；"
                f"匹配 {matched} + B 独有 {unmatched_b} = {len(alignment.blocks_b)}",
            },
            {
                "id": "mismatched_blocks_not_compared",
                "ok": not leaked,
                "detail": f"错位块 {len(mismatched_ids)} 个；"
                f"其中进入字段比对 {len(leaked)} 个",
            },
            {
                "id": "formatting_only_excluded",
                "ok": not formatting_leak,
                "detail": f"formatting_only 混入 disagreements {len(formatting_leak)} 条",
            },
            {
                "id": "disagreements_substantive",
                "ok": substantive_ok and not unknown_categories,
                "detail": f"未知分类 {unknown_categories or '无'}；"
                f"非实质 match_type {0 if substantive_ok else '有'}",
            },
        ]

    # -- CSV 行 ---------------------------------------------------------- #

    def _diff_rows(self) -> list[dict[str, Any]]:
        return [
            {
                "record_id": cell.record_id,
                "field": cell.field,
                "coder_a_value": "" if cell.value_a is None else cell.value_a,
                "coder_b_value": "" if cell.value_b is None else cell.value_b,
                "match_type": cell.match_type,
            }
            for cell in self.cells
        ]

    def _disagreement_rows(self) -> list[dict[str, Any]]:
        return [
            {
                "level": cell.level,
                "record_id": cell.record_id,
                "sheet": cell.sheet,
                "field": cell.field,
                "coder_a_value": "" if cell.value_a is None else cell.value_a,
                "coder_b_value": "" if cell.value_b is None else cell.value_b,
                "match_type": cell.match_type,
                "category": cell.category,
                "note": cell.note,
            }
            for cell in self.disagreements
        ]

    def _block_rows(self) -> list[dict[str, Any]]:
        findings = {cell.record_id: cell.category for cell in self.block_findings}
        rows: list[dict[str, Any]] = []
        for pair in self.alignment.pairs:
            view_a, view_b = pair.a, pair.b
            if view_a is not None:
                sexes = view_a.sexes
            elif view_b is not None:
                sexes = view_b.sexes
            else:  # pragma: no cover - 对齐产物的不变量
                sexes = ()
            rows.append(
                {
                    "record_id": pair.record_id,
                    "sheet": pair.sheet,
                    "status": pair.status,
                    "category": findings.get(pair.record_id, ""),
                    "paper_key": pair.paper,
                    "subsample_label": pair.subsample,
                    "measure_name": pair.measure,
                    "sex_rows": ",".join(str(sex) for sex in sexes),
                    "a_start_row": view_a.start_row if view_a is not None else "",
                    "b_start_row": view_b.start_row if view_b is not None else "",
                    "similarity": pair.similarity if pair.similarity is not None else "",
                    "match_score": pair.score if pair.score is not None else "",
                    "reason": pair.reason,
                }
            )
        return rows

    def write(self, out_dir: Path) -> None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        self.diff_cells_path = out_dir / "diff_cells.csv"
        self.disagreements_path = out_dir / "disagreements.csv"
        self.blocks_path = out_dir / "blocks.csv"
        _write_csv(self.diff_cells_path, DIFF_CELL_COLUMNS, self._diff_rows())
        _write_csv(self.disagreements_path, DISAGREEMENT_COLUMNS, self._disagreement_rows())
        _write_csv(self.blocks_path, BLOCK_COLUMNS, self._block_rows())


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def count_rows(path: Path) -> int:
    """CSV 数据行数（不含表头），用于写后读回复核。"""
    with Path(path).open(encoding="utf-8", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


def diff_codebooks(
    codebook_a: Codebook,
    codebook_b: Codebook,
    specs: dict[str, FieldSpec],
    *,
    batch: str = "",
) -> DiffReport:
    """纯内存比对：对齐 → 逐格判等 → 预分类。"""
    fields = [name for name in specs if name not in SKIP_FIELDS]
    alignment = align_codebooks(codebook_a, codebook_b)
    report = DiffReport(batch=batch, alignment=alignment)

    for pair in alignment.pairs:
        if pair.matched:
            report.both_absent += _compare_pair(pair, specs, fields, report.cells)
            continue
        category = classify_block(
            pair,
            blocks_a=alignment.blocks_a,
            blocks_b=alignment.blocks_b,
            cb_a=codebook_a,
            cb_b=codebook_b,
        )
        report.block_findings.append(
            DiffCell(
                record_id=pair.record_id,
                sheet=pair.sheet,
                field="",
                value_a=_describe(pair.a),
                value_b=_describe(pair.b),
                match_type="",
                category=category,
                level="block",
                note=pair.reason,
            )
        )
    return report


def run_diff(
    *,
    root: Path,
    batch: str,
    coder_a: Path | None = None,
    coder_b: Path | None = None,
    fields_path: Path | None = None,
    contract: Path | None = None,
    out_dir: Path | None = None,
) -> DiffReport:
    """Stage 6 确定性入口：找工作簿 → 读字段定义 → 比对 → 写三张 CSV。"""
    root = Path(root)
    fields_path = Path(fields_path) if fields_path else root / "rules" / "fields.yaml"
    contract_path = (
        Path(contract) if contract else root / "rules" / "template_contract.json"
    )
    path_a = Path(coder_a) if coder_a else find_coder_workbook(root, "a", batch)
    path_b = Path(coder_b) if coder_b else find_coder_workbook(root, "b", batch)

    specs = load_field_specs(fields_path)
    codebook_a = load_codebook(path_a, contract_path if contract_path.is_file() else None)
    codebook_b = load_codebook(path_b, contract_path if contract_path.is_file() else None)

    report = diff_codebooks(codebook_a, codebook_b, specs, batch=batch)
    report.inputs = [path_a, path_b, fields_path]
    if contract_path.is_file():
        report.inputs.append(contract_path)
    report.write(Path(out_dir) if out_dir else root / "data" / "05_adjudication")
    return report


__all__ = [
    "BLOCK_COLUMNS",
    "DIFF_CELL_COLUMNS",
    "DISAGREEMENT_COLUMNS",
    "DiffCell",
    "DiffReport",
    "EQUIVALENT",
    "EXACT",
    "FieldSpec",
    "MATCH_TYPES",
    "MISMATCH",
    "MISSING_VS_VALUE",
    "NUMERIC_WITHIN_TOLERANCE",
    "SKIP_FIELDS",
    "SUBSTANTIVE_MATCH_TYPES",
    "TOLERANCE",
    "absent",
    "as_number",
    "canonical_text",
    "compare_values",
    "count_rows",
    "diff_codebooks",
    "load_field_specs",
    "run_diff",
]
