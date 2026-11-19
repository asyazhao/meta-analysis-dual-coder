"""Stage 7 — 共识工作簿构建。

输入：两个编码员的工作簿（可选：Stage 6 的裁决记录、人工修改表）。
输出：``data/06_consensus/<batch>_consensus_codebook.xlsx``、``provenance.csv``、
``paper_id_map.csv``。

取值优先级（任务契约）：人工 > 裁决 > A/B 一致 > pending_human。
- 人工：人工修改表里的显式取值；
- 裁决：Stage 6 裁决记录（``decided_by`` 不是 human 的记录）；
- A/B 一致：两个编码员写出的值相同；
- 其余冲突一律 ``pending_human``：单元格留空，只在 provenance 里留痕，绝不臆造数值。

编号规则（``rules/decisions.yaml`` 的 numbering，CODING_PLAN §10.3）：
先自上而下遍历 WBIS 首次出现的论文分配 1、2、3……，再遍历 WSSQ：
WBIS 已出现的沿用原 ID，新论文从当前最大 ID + 1 接续；同一论文的全部块、
全部行（含未编码的性别占位行）共用同一 paper_id；每批次从 1 开始、连续不跳号。

写盘后按同一份 ``load_codebook`` 重新读取，逐格比对；差异数不为 0 即视为
写回失败（``ConsensusResult.writeback_diffs``）。
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from ..validate.model import MISSING, Codebook, Row, blank, load_codebook

MAIN_SHEETS = ("WBIS", "WSSQ")
TRACKING_SHEET = "no_mean_or_sd"
EXCLUDED_SHEET = "not_eligible"
ALL_SHEETS = (*MAIN_SHEETS, TRACKING_SHEET, EXCLUDED_SHEET)
SHEET_RANK = {sheet: idx for idx, sheet in enumerate(MAIN_SHEETS)}
SEX_ORDER = (0, 1, 2)

# provenance 的 value_source 取值
SRC_MANUAL = "manual"
SRC_ADJ = "adjudication"
SRC_AGREE = "ab_agree"
SRC_A_ONLY = "coder_a_only"
SRC_B_ONLY = "coder_b_only"
SRC_BLANK = "blank"
SRC_PENDING = "pending_human"
SRC_IDENTITY = "paper_identity"
SRC_STRUCTURE = "structure"

PROVENANCE_FIELDS = [
    "sheet",
    "paper_id",
    "excel_row",
    "sex",
    "subsample_label",
    "measure_name",
    "field",
    "value",
    "value_source",
    "coder_a_value",
    "coder_b_value",
    "adjudication_value",
    "manual_value",
    "note",
]

DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+", re.IGNORECASE)
_INT_RE = re.compile(r"^-?\d+$")
_NUM_RE = re.compile(r"^-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")


# --------------------------------------------------------------------------- #
# 取值与身份
# --------------------------------------------------------------------------- #


def _norm(value: Any) -> str:
    """比较用的规范化：折叠空白、忽略大小写。"""
    if value is None:
        return ""
    return " ".join(str(value).split()).strip().casefold()


def paper_key(reference: Any) -> str:
    """论文身份键：有 DOI 用 DOI，否则用规范化后的完整引用。

    仅靠字符串无法判断“引用格式略有差异但实为同一篇”，因此有 DOI 时优先用
    DOI（§10.3 第 1 条），没有 DOI 时退回规范化引用；不做模糊匹配，不臆断。
    """
    text = "" if reference is None else str(reference).strip()
    if not text:
        return ""
    match = DOI_RE.search(text)
    if match:
        return "doi:" + match.group(0).rstrip(".,;)]}").casefold()
    return "ref:" + _norm(text)


def coerce_value(value: Any) -> Any:
    """把 CSV/JSON 读到的字符串还原成单元格该有的类型。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    if text == "":
        return None
    if _INT_RE.match(text):
        return int(text)
    if _NUM_RE.match(text):
        return float(text)
    return text


def _same(left: Any, right: Any) -> bool:
    """单元格相等判定：数值按值比较，字符串去首尾空白后比较。"""
    if blank(left) and blank(right):
        return True
    if blank(left) or blank(right):
        return False
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) == bool(right)
    num_left = isinstance(left, (int, float))
    num_right = isinstance(right, (int, float))
    if num_left and num_right:
        return abs(float(left) - float(right)) <= 1e-9
    if num_left != num_right:
        return False
    return str(left).strip() == str(right).strip()


# --------------------------------------------------------------------------- #
# 决策记录（人工 / 裁决）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Decision:
    sheet: str
    paper: str
    subsample: str
    measure: str
    sex: Any
    field_name: str
    value: Any
    source: str
    note: str = ""

    @property
    def key(self) -> tuple[Any, ...]:
        return (
            self.sheet,
            self.paper,
            _norm(self.subsample),
            _norm(self.measure),
            self.sex,
            self.field_name,
        )


def _decision_from_mapping(raw: dict[str, Any], source: str) -> Decision | None:
    sheet = str(raw.get("sheet") or "").strip()
    field_name = str(raw.get("field") or "").strip()
    if not sheet or not field_name:
        return None
    reference = (
        raw.get("full_reference")
        or raw.get("APA_reference")
        or raw.get("paper")
        or raw.get("reference")
    )
    pkey = str(raw.get("paper_key") or "") or paper_key(reference)
    if not pkey:
        return None
    value = raw.get("value")
    if value is None:
        value = raw.get("adjudicated_value")
    sex = raw.get("sex")
    return Decision(
        sheet=sheet,
        paper=pkey,
        subsample=str(raw.get("subsample_label") or ""),
        measure=str(raw.get("measure_name") or ""),
        sex=None if blank(sex) else coerce_value(sex),
        field_name=field_name,
        value=coerce_value(value),
        source=source,
        note=str(raw.get("note") or raw.get("reason") or ""),
    )


def load_decisions(path: Path | None, source: str) -> list[Decision]:
    """读取人工修改表（CSV）或裁决记录（JSONL）；文件缺失返回空列表。"""
    if path is None:
        return []
    path = Path(path)
    if not path.is_file():
        return []
    out: list[Decision] = []
    if path.suffix.lower() in {".jsonl", ".json"}:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                continue
            decided_by = _norm(record.get("decided_by") or record.get("decided_by_role"))
            effective = SRC_MANUAL if decided_by in {"human", "manual", "人工"} else source
            item = _decision_from_mapping(record, effective)
            if item is not None:
                out.append(item)
        return out

    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            item = _decision_from_mapping(
                {k: v for k, v in row.items() if k is not None}, source
            )
            if item is not None:
                out.append(item)
    return out


def _index_decisions(decisions: list[Decision]) -> tuple[dict, dict]:
    manual: dict[tuple, Decision] = {}
    adjudicated: dict[tuple, Decision] = {}
    for item in decisions:
        target = manual if item.source == SRC_MANUAL else adjudicated
        target[item.key] = item
    return manual, adjudicated


# --------------------------------------------------------------------------- #
# 编码员工作簿 → 块
# --------------------------------------------------------------------------- #


@dataclass
class _BlockRef:
    """一个编码员的块视图；``rows`` 以 sex 为键。"""

    sheet: str
    position: int
    paper: str
    reference: str
    subsample: str
    measure: str
    rows: dict[Any, Row] = field(default_factory=dict)


def _first_text(rows: list[Row], name: str) -> str:
    for row in rows:
        value = row.get(name)
        if not blank(value):
            return str(value).strip()
    return ""


def _block_reference(block) -> str:
    ordered: list[Row] = []
    if block.total is not None:
        ordered.append(block.total)
    ordered.extend(row for row in block.rows if row is not block.total)
    for row in ordered:
        value = row.get("full_reference")
        if not blank(value):
            return str(value).strip()
    return ""


def _collect_blocks(cb: Codebook) -> list[_BlockRef]:
    out: list[_BlockRef] = []
    for sheet in MAIN_SHEETS:
        for position, block in enumerate(cb.blocks.get(sheet, [])):
            reference = _block_reference(block)
            out.append(
                _BlockRef(
                    sheet=sheet,
                    position=position,
                    paper=paper_key(reference),
                    reference=reference,
                    subsample=_first_text(block.rows, "subsample_label"),
                    measure=_first_text(block.rows, "measure_name"),
                    rows={row.get("sex"): row for row in block.rows},
                )
            )
    return out


def _compatible(left: _BlockRef, right: _BlockRef) -> bool:
    if left.sheet != right.sheet or not left.paper or left.paper != right.paper:
        return False
    if left.measure and right.measure and _norm(left.measure) != _norm(right.measure):
        return False
    return not (
        left.subsample
        and right.subsample
        and _norm(left.subsample) != _norm(right.subsample)
    )


def _match_blocks(
    a_blocks: list[_BlockRef], b_blocks: list[_BlockRef]
) -> tuple[dict[int, int], set[int]]:
    """按块身份对齐；同身份多条时按出现顺序一一配对。"""
    used: set[int] = set()
    partner_of_a: dict[int, int] = {}
    for b_idx, b_block in enumerate(b_blocks):
        best: int | None = None
        best_score = 99
        for a_idx, a_block in enumerate(a_blocks):
            if a_idx in used or not _compatible(a_block, b_block):
                continue
            exact = (
                _norm(a_block.subsample) == _norm(b_block.subsample)
                and _norm(a_block.measure) == _norm(b_block.measure)
            )
            score = 0 if exact else 1
            if score < best_score:
                best, best_score = a_idx, score
                if score == 0:
                    break
        if best is not None:
            used.add(best)
            partner_of_a[best] = b_idx
    return partner_of_a, set(partner_of_a.values())


@dataclass
class _MergedBlock:
    sheet: str
    paper: str
    reference: str
    subsample: str
    measure: str
    sort_key: tuple[int, float]
    rows: dict[Any, dict[str, Row]] = field(default_factory=dict)


def _merge_block(
    a_block: _BlockRef | None, b_block: _BlockRef | None, order: float
) -> _MergedBlock:
    first = a_block or b_block
    assert first is not None
    reference = ""
    for candidate in (a_block, b_block):
        if candidate is not None and candidate.reference:
            reference = candidate.reference
            break
    rows: dict[Any, dict[str, Row]] = {}
    for label, block in (("a", a_block), ("b", b_block)):
        if block is None:
            continue
        for sex, row in block.rows.items():
            rows.setdefault(sex, {})[label] = row
    return _MergedBlock(
        sheet=first.sheet,
        paper=first.paper,
        reference=reference,
        subsample=first.subsample,
        measure=first.measure,
        sort_key=(SHEET_RANK.get(first.sheet, len(SHEET_RANK)), order),
        rows=rows,
    )


def merge_blocks(
    a_blocks: list[_BlockRef], b_blocks: list[_BlockRef]
) -> list[_MergedBlock]:
    """合并两个编码员的块，保持“自上而下”的相对顺序。"""
    partner_of_a, matched_b = _match_blocks(a_blocks, b_blocks)
    merged: list[_MergedBlock] = []
    for a_idx, a_block in enumerate(a_blocks):
        b_idx = partner_of_a.get(a_idx)
        merged.append(
            _merge_block(a_block, b_blocks[b_idx] if b_idx is not None else None,
                         float(a_block.position))
        )
    for b_idx, b_block in enumerate(b_blocks):
        if b_idx in matched_b:
            continue
        merged.append(_merge_block(None, b_block, 1e6 + b_block.position))
    merged.sort(key=lambda block: block.sort_key)
    return merged


# --------------------------------------------------------------------------- #
# 单元格取值
# --------------------------------------------------------------------------- #


def _row_pair(block: _MergedBlock, sex: Any) -> tuple[Row | None, Row | None]:
    pair = block.rows.get(sex, {})
    return pair.get("a"), pair.get("b")


def _decide(
    value_a: Any,
    value_b: Any,
    manual: Decision | None,
    adjudicated: Decision | None,
) -> tuple[Any, str]:
    if manual is not None:
        return manual.value, SRC_MANUAL
    if adjudicated is not None:
        return adjudicated.value, SRC_ADJ
    if blank(value_a) and blank(value_b):
        return None, SRC_BLANK
    if blank(value_a):
        return value_b, SRC_B_ONLY
    if blank(value_b):
        return value_a, SRC_A_ONLY
    if _same(value_a, value_b):
        return value_a, SRC_AGREE
    return None, SRC_PENDING


@dataclass
class _Cell:
    sheet: str
    paper_id: int | None
    excel_row: int
    sex: Any
    subsample: str
    measure: str
    field_name: str
    value: Any
    source: str
    value_a: Any = None
    value_b: Any = None
    adjudication_value: Any = None
    manual_value: Any = None
    note: str = ""

    def as_row(self) -> dict[str, Any]:
        return {
            "sheet": self.sheet,
            "paper_id": "" if self.paper_id is None else self.paper_id,
            "excel_row": self.excel_row,
            "sex": "" if self.sex is None else self.sex,
            "subsample_label": self.subsample,
            "measure_name": self.measure,
            "field": self.field_name,
            "value": "" if self.value is None else self.value,
            "value_source": self.source,
            "coder_a_value": "" if self.value_a is None else self.value_a,
            "coder_b_value": "" if self.value_b is None else self.value_b,
            "adjudication_value": (
                "" if self.adjudication_value is None else self.adjudication_value
            ),
            "manual_value": "" if self.manual_value is None else self.manual_value,
            "note": self.note,
        }


@dataclass
class _OutRow:
    """一个待写入主表的三行块中某一行。"""

    sheet: str
    excel_row: int
    sex: Any
    paper: str
    paper_id: int | None
    subsample: str
    measure: str
    values: dict[str, Any] = field(default_factory=dict)
    cells: list[_Cell] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# 编号与写盘
# --------------------------------------------------------------------------- #


def assign_paper_ids(blocks: list[_MergedBlock]) -> dict[str, int]:
    """§10.3：WBIS 自上而下先编号，WSSQ 接续；同一论文共用，连续不跳号。"""
    ids: dict[str, int] = {}
    for sheet in MAIN_SHEETS:
        for block in blocks:
            if block.sheet != sheet or not block.paper:
                continue
            if block.paper not in ids:
                ids[block.paper] = len(ids) + 1
    return ids


def _sheet_headers(ws) -> dict[str, int]:
    return {
        str(cell.value): cell.column
        for cell in next(ws.iter_rows(min_row=1, max_row=1))
        if cell.value
    }


def _layout_main(
    blocks: list[_MergedBlock],
    ids: dict[str, int],
    fields: list[str],
    manual: dict[tuple, Decision],
    adjudicated: dict[tuple, Decision],
) -> list[_OutRow]:
    """计算主表每一行的取值与 provenance，不触碰工作簿。"""
    out: list[_OutRow] = []
    for sheet in MAIN_SHEETS:
        row_index = 2
        for block in [b for b in blocks if b.sheet == sheet]:
            present = [sex for sex in block.rows if sex in SEX_ORDER]
            if present:
                sexes = sorted(set(present) | set(SEX_ORDER), key=SEX_ORDER.index)
            else:
                # 孤儿行（sex 缺失）：按原样保留，不臆造 sex 值
                sexes = [None]
            for offset, sex in enumerate(sexes):
                excel_row = row_index + offset
                row_a, row_b = _row_pair(block, sex)
                out_row = _OutRow(
                    sheet=sheet,
                    excel_row=excel_row,
                    sex=sex,
                    paper=block.paper,
                    paper_id=ids.get(block.paper),
                    subsample=block.subsample,
                    measure=block.measure,
                )
                for name in fields:
                    value_a = row_a.get(name) if row_a is not None else None
                    value_b = row_b.get(name) if row_b is not None else None
                    key = (
                        sheet,
                        block.paper,
                        _norm(block.subsample),
                        _norm(block.measure),
                        sex,
                        name,
                    )
                    manual_item = manual.get(key)
                    adj_item = adjudicated.get(key)
                    value, source = _decide(value_a, value_b, manual_item, adj_item)
                    note = ""
                    if name == "sex":
                        value, source = sex, SRC_STRUCTURE
                    elif name == "paper_id":
                        value, source = ids.get(block.paper), SRC_STRUCTURE
                    elif source == SRC_PENDING:
                        note = "A/B 冲突，且无人工或裁决取值"
                    elif name == "full_reference" and source in {SRC_A_ONLY, SRC_B_ONLY}:
                        note = "引用仅一方填写，按论文身份补全"
                    if name == "full_reference" and value is None and block.reference and sex == 0:
                        value, source = block.reference, SRC_IDENTITY
                        note = "引用冲突时保留论文身份文本，供 paper_id_map 人工核对"
                    out_row.values[name] = value
                    out_row.cells.append(
                        _Cell(
                            sheet=sheet,
                            paper_id=ids.get(block.paper),
                            excel_row=excel_row,
                            sex=sex,
                            subsample=block.subsample,
                            measure=block.measure,
                            field_name=name,
                            value=value,
                            source=source,
                            value_a=value_a,
                            value_b=value_b,
                            adjudication_value=adj_item.value if adj_item else None,
                            manual_value=manual_item.value if manual_item else None,
                            note=note,
                        )
                    )
                out.append(out_row)
            row_index += len(sexes) + 1  # 块后空一行
    return out


@dataclass
class _ExtraRow:
    sheet: str
    excel_row: int
    paper: str
    paper_id: int | None
    sex: Any
    subsample: str
    measure: str
    values: dict[str, Any] = field(default_factory=dict)
    cells: list[_Cell] = field(default_factory=list)


def _extra_source_rows(cb: Codebook, sheet: str) -> list[dict[str, Any]]:
    rows = cb.tracking if sheet == TRACKING_SHEET else cb.excluded
    return [dict(item) for item in rows]


def _extra_reference(sheet: str, item: dict[str, Any]) -> str:
    key = "APA_reference" if sheet == TRACKING_SHEET else "full_reference"
    value = item.get(key)
    return "" if blank(value) else str(value).strip()


def _extra_identity(sheet: str, item: dict[str, Any]) -> tuple[Any, ...]:
    pkey = paper_key(_extra_reference(sheet, item))
    if sheet == TRACKING_SHEET:
        return (
            pkey,
            _norm(item.get("subsample_label")),
            _norm(item.get("measure_name")),
            item.get("sex"),
        )
    return (pkey,)


def _merge_extra_rows(
    sheet: str, rows_a: list[dict[str, Any]], rows_b: list[dict[str, Any]]
) -> list[tuple[str, dict[str, dict[str, Any] | None]]]:
    """合并追踪页 / 排除页的行；返回 [(reference, {"a": ..., "b": ...})]。"""
    buckets: dict[tuple, list[dict[str, Any]]] = {}
    for label, rows in (("a", rows_a), ("b", rows_b)):
        for item in rows:
            buckets.setdefault(_extra_identity(sheet, item), []).append(
                {"label": label, "item": item}
            )

    merged: list[tuple[str, dict[str, dict[str, Any] | None]]] = []
    seen: set[tuple] = set()
    for rows in (rows_a, rows_b):
        for item in rows:
            ident = _extra_identity(sheet, item)
            if ident in seen:
                continue
            seen.add(ident)
            pair: dict[str, dict[str, Any] | None] = {"a": None, "b": None}
            for entry in buckets[ident]:
                if pair[entry["label"]] is None:
                    pair[entry["label"]] = entry["item"]
            merged.append((_extra_reference(sheet, item), pair))
    return merged


def _layout_extra_sheet(
    sheet: str,
    headers: list[str],
    rows_a: list[dict[str, Any]],
    rows_b: list[dict[str, Any]],
    ids: dict[str, int],
    manual: dict[tuple, Decision],
    adjudicated: dict[tuple, Decision],
) -> list[_ExtraRow]:
    out: list[_ExtraRow] = []
    excel_row = 2
    for reference, pair in _merge_extra_rows(sheet, rows_a, rows_b):
        item_a = pair.get("a")
        item_b = pair.get("b")
        base = item_a or item_b or {}
        pkey = paper_key(reference)
        sex = base.get("sex")
        subsample = str(base.get("subsample_label") or "")
        measure = str(base.get("measure_name") or "")
        out_row = _ExtraRow(
            sheet=sheet,
            excel_row=excel_row,
            paper=pkey,
            paper_id=ids.get(pkey),
            sex=sex,
            subsample=subsample,
            measure=measure,
        )
        for name in headers:
            value_a = item_a.get(name) if item_a else None
            value_b = item_b.get(name) if item_b else None
            key = (sheet, pkey, _norm(subsample), _norm(measure), sex, name)
            manual_item = manual.get(key)
            adj_item = adjudicated.get(key)
            value, source = _decide(value_a, value_b, manual_item, adj_item)
            if name == "sex":
                value, source = sex, SRC_STRUCTURE
            note = "A/B 冲突，且无人工或裁决取值" if source == SRC_PENDING else ""
            out_row.values[name] = value
            out_row.cells.append(
                _Cell(
                    sheet=sheet,
                    paper_id=ids.get(pkey),
                    excel_row=excel_row,
                    sex=sex,
                    subsample=subsample,
                    measure=measure,
                    field_name=name,
                    value=value,
                    source=source,
                    value_a=value_a,
                    value_b=value_b,
                    adjudication_value=adj_item.value if adj_item else None,
                    manual_value=manual_item.value if manual_item else None,
                    note=note,
                )
            )
        out.append(out_row)
        excel_row += 1
    return out


# --------------------------------------------------------------------------- #
# 写回与读回比对
# --------------------------------------------------------------------------- #


def _write_rows(wb, rows: list[_OutRow] | list[_ExtraRow], headers: dict[str, int]):
    written: dict[tuple[str, int, str], Any] = {}
    for entry in rows:
        ws = wb[entry.sheet]
        for name, column in headers.items():
            value = entry.values.get(name)
            ws.cell(row=entry.excel_row, column=column, value=value)
            written[(entry.sheet, entry.excel_row, name)] = value
    return written


def _iter_readback_rows(cb: Codebook):
    for sheet in MAIN_SHEETS:
        for block in cb.blocks.get(sheet, []):
            for row in block.rows:
                yield sheet, row.row, row.values
    for sheet, rows in ((TRACKING_SHEET, cb.tracking), (EXCLUDED_SHEET, cb.excluded)):
        for item in rows:
            row_index = item.get("_row")
            if row_index is None:
                continue
            values = {k: v for k, v in item.items() if k != "_row"}
            yield sheet, int(row_index), values


def _compare_readback(
    cb: Codebook, written: dict[tuple[str, int, str], Any]
) -> tuple[int, list[str]]:
    diffs: list[str] = []
    seen: set[tuple[str, int, str]] = set()
    for sheet, row_index, values in _iter_readback_rows(cb):
        for name, actual in values.items():
            key = (sheet, row_index, name)
            if key not in written:
                continue
            seen.add(key)
            expected = written[key]
            if not _same(expected, actual):
                diffs.append(
                    f"{sheet}!{row_index}:{name} 期望 {expected!r} 实际 {actual!r}"
                )
    for key in written:
        if key not in seen:
            diffs.append(f"{key[0]}!{key[1]}:{key[2]} 写回后未读到")
    return len(diffs), diffs[:20]


# --------------------------------------------------------------------------- #
# 输入发现
# --------------------------------------------------------------------------- #


def find_coder_workbook(root: Path, coder: str, batch: str) -> Path:
    """按约定位置查找编码员工作簿；找不到时抛 FileNotFoundError 并列出候选。

    先找 ``data/03_coded/coder_<x>/`` 下的编码工作簿，再退回 Stage 5 物化出的
    ``data/04_validation/coder_<x>/<batch>_materialized.xlsx``（同一模板、同一契约）。
    """
    coded = root / "data" / "03_coded"
    validation = root / "data" / "04_validation"
    candidates = [
        coded / f"coder_{coder}" / f"{batch}_codebook.xlsx",
        coded / f"coder_{coder}" / "codebook.xlsx",
        coded / f"{batch}_coder_{coder}.xlsx",
        coded / f"coder_{coder}.xlsx",
        validation / f"coder_{coder}" / f"{batch}_materialized.xlsx",
        validation / f"coder_{coder}" / "materialized.xlsx",
    ]
    for path in candidates:
        if path.is_file():
            return path
    for search_dir in (coded / f"coder_{coder}", validation / f"coder_{coder}"):
        if not search_dir.is_dir():
            continue
        found = sorted(search_dir.glob("*.xlsx"))
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            listing = "、".join(path.name for path in found)
            raise FileNotFoundError(
                f"coder_{coder} 下有多个工作簿（{listing}）；请用 --coder-{coder} 指定"
            )
    raise FileNotFoundError(
        f"未找到 coder_{coder} 的工作簿，已查找："
        + "、".join(str(path) for path in candidates)
    )


def _find_optional(root: Path, batch: str, names: list[str]) -> Path | None:
    for rel in names:
        path = root / rel.format(batch=batch)
        if path.is_file():
            return path
    return None


def default_adjudication_path(root: Path, batch: str) -> Path | None:
    return _find_optional(
        root,
        batch,
        [
            "data/05_adjudication/{batch}_adjudication.jsonl",
            "data/05_adjudication/adjudication.jsonl",
        ],
    )


def default_manual_path(root: Path, batch: str) -> Path | None:
    return _find_optional(
        root,
        batch,
        [
            "data/06_consensus/{batch}_manual_overrides.csv",
            "data/06_consensus/manual_overrides.csv",
            "data/05_adjudication/{batch}_manual_overrides.csv",
        ],
    )


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


@dataclass
class ConsensusResult:
    workbook: Path
    provenance: Path
    paper_id_map: Path
    papers: int
    blocks: int
    rows: int
    cells: int
    by_source: dict[str, int]
    pending: int
    writeback_diffs: int
    diff_examples: list[str]
    numbering_ok: bool = True
    unidentified_blocks: int = 0
    inputs: list[Path] = field(default_factory=list)

    def summary(self) -> list[str]:
        by_source = "、".join(
            f"{name}={count}" for name, count in sorted(self.by_source.items())
        )
        return [
            f"论文 {self.papers} 篇，块 {self.blocks} 个，主表行 {self.rows} 行，"
            f"单元格 {self.cells} 个",
            f"来源：{by_source}",
            f"待人工确认（pending_human）{self.pending} 格",
            f"写回读回差异 {self.writeback_diffs} 格",
            f"工作簿：{self.workbook}",
            f"provenance：{self.provenance}",
            f"paper_id 对照表：{self.paper_id_map}",
        ] + (
            [f"⚠ 无 full_reference 的块 {self.unidentified_blocks} 个，未能分配 paper_id"]
            if self.unidentified_blocks
            else []
        )


def build_consensus(
    *,
    root: Path,
    batch: str,
    coder_a: Path | None = None,
    coder_b: Path | None = None,
    adjudication: Path | None = None,
    manual: Path | None = None,
    out_dir: Path | None = None,
) -> ConsensusResult:
    root = Path(root)
    rules = root / "rules"
    blank_path = rules / "clean_codebook_BLANK.xlsx"
    contract = rules / "template_contract.json"
    if not blank_path.is_file():
        raise FileNotFoundError(f"缺少空白模板：{blank_path}")

    path_a = Path(coder_a) if coder_a else find_coder_workbook(root, "a", batch)
    path_b = Path(coder_b) if coder_b else find_coder_workbook(root, "b", batch)
    path_adj = Path(adjudication) if adjudication else default_adjudication_path(root, batch)
    path_manual = Path(manual) if manual else default_manual_path(root, batch)

    cb_a = load_codebook(path_a, contract if contract.is_file() else None)
    cb_b = load_codebook(path_b, contract if contract.is_file() else None)

    decisions = load_decisions(path_manual, SRC_MANUAL)
    decisions += load_decisions(path_adj, SRC_ADJ)
    manual_idx, adj_idx = _index_decisions(decisions)

    wb = load_workbook(blank_path)
    try:
        fields = list(_sheet_headers(wb[MAIN_SHEETS[0]]))
        headers_by_sheet = {sheet: _sheet_headers(wb[sheet]) for sheet in ALL_SHEETS}
        for sheet in ALL_SHEETS:
            for row in wb[sheet].iter_rows(min_row=2):
                for cell in row:
                    cell.value = None

        blocks = merge_blocks(_collect_blocks(cb_a), _collect_blocks(cb_b))
        ids = assign_paper_ids(blocks)

        main_rows = _layout_main(blocks, ids, fields, manual_idx, adj_idx)
        extra_rows: list[_ExtraRow] = []
        for sheet in (TRACKING_SHEET, EXCLUDED_SHEET):
            extra_rows.extend(
                _layout_extra_sheet(
                    sheet,
                    list(headers_by_sheet[sheet]),
                    _extra_source_rows(cb_a, sheet),
                    _extra_source_rows(cb_b, sheet),
                    ids,
                    manual_idx,
                    adj_idx,
                )
            )

        written = _write_rows(wb, main_rows, headers_by_sheet[MAIN_SHEETS[0]])
        for sheet in (TRACKING_SHEET, EXCLUDED_SHEET):
            written.update(
                _write_rows(
                    wb,
                    [row for row in extra_rows if row.sheet == sheet],
                    headers_by_sheet[sheet],
                )
            )

        target_dir = Path(out_dir) if out_dir else root / "data" / "06_consensus"
        target_dir.mkdir(parents=True, exist_ok=True)
        workbook_path = target_dir / f"{batch}_consensus_codebook.xlsx"
        wb.save(workbook_path)
    finally:
        wb.close()

    readback = load_codebook(workbook_path, contract if contract.is_file() else None)
    diffs, examples = _compare_readback(readback, written)

    cells = [cell for row in main_rows for cell in row.cells]
    cells += [cell for row in extra_rows for cell in row.cells]
    by_source: dict[str, int] = {}
    for cell in cells:
        by_source[cell.source] = by_source.get(cell.source, 0) + 1

    provenance_path = target_dir / "provenance.csv"
    with provenance_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=PROVENANCE_FIELDS)
        writer.writeheader()
        for cell in cells:
            writer.writerow(cell.as_row())

    paper_map_path = target_dir / "paper_id_map.csv"
    _write_paper_map(paper_map_path, main_rows, blocks, ids)

    papers = len(ids)
    numbering_ok = sorted(ids.values()) == list(range(1, papers + 1))
    unidentified = sum(1 for block in blocks if not block.paper)
    return ConsensusResult(
        workbook=workbook_path,
        provenance=provenance_path,
        paper_id_map=paper_map_path,
        papers=papers,
        blocks=len(blocks),
        rows=len(main_rows),
        cells=len(cells),
        by_source=by_source,
        pending=by_source.get(SRC_PENDING, 0),
        writeback_diffs=diffs,
        diff_examples=examples,
        numbering_ok=numbering_ok,
        unidentified_blocks=unidentified,
        inputs=[path for path in (path_a, path_b, path_adj, path_manual) if path],
    )


def _write_paper_map(
    path: Path,
    main_rows: list[_OutRow],
    blocks: list[_MergedBlock],
    ids: dict[str, int],
) -> None:
    reference_of: dict[str, str] = {}
    sheets: dict[str, set[str]] = {}
    blocks_of: dict[str, int] = {}
    rows_of: dict[str, int] = {}
    first_sheet: dict[str, str] = {}

    for block in blocks:
        if not block.paper:
            continue
        if block.reference and not reference_of.get(block.paper):
            reference_of[block.paper] = block.reference
        reference_of.setdefault(block.paper, "")
        sheets.setdefault(block.paper, set()).add(block.sheet)
        blocks_of[block.paper] = blocks_of.get(block.paper, 0) + 1
        first_sheet.setdefault(block.paper, block.sheet)
    for row in main_rows:
        rows_of[row.paper] = rows_of.get(row.paper, 0) + 1

    ordered = sorted(ids.items(), key=lambda item: item[1])
    fieldnames = [
        "paper_id",
        "full_reference",
        "sheets",
        "blocks",
        "rows",
        "first_sheet",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for pkey, pid in ordered:
            writer.writerow(
                {
                    "paper_id": pid,
                    "full_reference": reference_of.get(pkey, ""),
                    "sheets": ";".join(sorted(sheets.get(pkey, set()))),
                    "blocks": blocks_of.get(pkey, 0),
                    "rows": rows_of.get(pkey, 0),
                    "first_sheet": first_sheet.get(pkey, ""),
                }
            )


__all__ = [
    "ALL_SHEETS",
    "ConsensusResult",
    "Decision",
    "MISSING",
    "PROVENANCE_FIELDS",
    "SRC_ADJ",
    "SRC_AGREE",
    "SRC_A_ONLY",
    "SRC_BLANK",
    "SRC_B_ONLY",
    "SRC_IDENTITY",
    "SRC_MANUAL",
    "SRC_PENDING",
    "SRC_STRUCTURE",
    "assign_paper_ids",
    "build_consensus",
    "coerce_value",
    "default_adjudication_path",
    "default_manual_path",
    "find_coder_workbook",
    "load_decisions",
    "merge_blocks",
    "paper_key",
]
