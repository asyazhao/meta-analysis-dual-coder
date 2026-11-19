"""Stage 5 物化：把编码员输出（coder_output）投影成清洁版工作簿。

只做投影，不做修复：编码员写下的值原样落到工作表，结构与数值问题交给 V01–V50。
三条硬规则：

* 主表按**表头名**定位列，不按列号硬编码——模板列位变化不会静默写错列；
* 每条 record 落在固定三行块中的一行（sex = 0/1/2），未编码性别行只写 sex；
* ``paper_id`` 在编码阶段一律留空（CODING_PLAN §5.1、§10.3）。

去向映射（§9）：

===============  ==============================  ====================
record_status    工作表                          说明
===============  ==============================  ====================
coded            WBIS / WSSQ                     按 sheet + block_id 合块
missing_statistics  no_mean_or_sd                只登记 sex=0 的总体缺失
not_eligible     not_eligible                    两列：full_reference/reason
其余未解决状态    不写入工作簿                     计入 pending，由批次处理记录承载
===============  ==============================  ====================

编码输出里没有独立的排除理由、题名、联系人字段，因此：

* ``not_eligible.reason`` 取 ``mean_calculation`` → ``age_calculation`` → ``flags``
  的首段非空文本；三处都没有时留空，由 V36 标记"理由过于空泛"；
* ``no_mean_or_sd`` 的 paper_title / Mail / Author / CC_Mail 留空（不虚构），
  notes 由计算说明与 flags 拼成，``missing_statistic`` 按 mean/SD 实际缺失推导。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.workbook import Workbook

from ..rules.template import SHEET_EXCLUDED, SHEET_TRACKING, SHEETS_MAIN
from ..validate.model import blank, is_missing

CODED_STATUS = "coded"
TRACKING_STATUS = "missing_statistics"
EXCLUDED_STATUS = "not_eligible"
PENDING_STATUSES = (
    "eligibility_unresolved",
    "source_unreadable",
    "source_incomplete",
    "overlap_unresolved",
)
ALL_SHEETS = (*SHEETS_MAIN, SHEET_TRACKING, SHEET_EXCLUDED)


class MaterializeError(RuntimeError):
    """模板结构不符合规则时中止，避免物化出结构错误的工作簿。"""


@dataclass(frozen=True)
class MaterializeWarning:
    """物化阶段的提示：不阻断，但必须随报告一起露出来。"""

    article_key: str
    record_id: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {
            "article_key": self.article_key,
            "record_id": self.record_id,
            "message": self.message,
        }


@dataclass
class MaterializeResult:
    records: int = 0
    main_records: int = 0
    blocks: dict[str, int] = field(default_factory=dict)
    tracking_rows: int = 0
    excluded_rows: int = 0
    pending: list[dict[str, str]] = field(default_factory=list)
    warnings: list[MaterializeWarning] = field(default_factory=list)
    output: Path | None = None

    @property
    def pending_records(self) -> int:
        return len(self.pending)

    def as_dict(self) -> dict[str, Any]:
        return {
            "records": self.records,
            "main_records": self.main_records,
            "blocks": dict(self.blocks),
            "tracking_rows": self.tracking_rows,
            "excluded_rows": self.excluded_rows,
            "pending_records": self.pending_records,
            "pending": list(self.pending),
            "warnings": [w.as_dict() for w in self.warnings],
        }


@dataclass
class _Group:
    """一个待写入的三行块：sex -> 该行要写的字段值。"""

    article_key: str
    sheet: str
    block_id: str
    rows: dict[int, dict[str, Any]] = field(default_factory=dict)


def header_map(ws) -> dict[str, int]:
    """表头名 -> 列号；分隔列（空表头）自然被排除。"""
    return {
        str(cell.value).strip(): cell.column
        for cell in next(ws.iter_rows(min_row=1, max_row=1))
        if not blank(cell.value)
    }


def field_values(fields: dict[str, Any], headers: dict[str, int]) -> dict[str, Any]:
    """把 28 字段字典转成"表头名 -> 单元格值"，不写出任何空值。"""
    out: dict[str, Any] = {}
    for name in headers:
        if name == "paper_id":
            continue  # 编码阶段留空，Stage 7 统一编号
        if name not in fields:
            continue
        value = fields[name]
        if isinstance(value, str):
            value = value.strip()
            if not value:
                continue  # 空串按"不适用/未报告"处理，留空而不是写空字符串
        if value is None:
            continue
        out[name] = value
    return out


def sex_row_complete(fields: dict[str, Any]) -> bool:
    """§4.1：性别行只有在目标 mean 与 SD 都可用时才编码。"""
    return not (
        blank(fields.get("scale_mean"))
        or is_missing(fields.get("scale_mean"))
        or blank(fields.get("scale_sd"))
        or is_missing(fields.get("scale_sd"))
    )


def missing_statistic(fields: dict[str, Any]) -> str:
    """§9：按 mean/SD 实际缺失情况给出 mean / sd / mean_and_sd。"""
    mean_missing = blank(fields.get("scale_mean")) or is_missing(fields.get("scale_mean"))
    sd_missing = blank(fields.get("scale_sd")) or is_missing(fields.get("scale_sd"))
    if mean_missing and sd_missing:
        return "mean_and_sd"
    if mean_missing:
        return "mean"
    if sd_missing:
        return "sd"
    return ""


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None


def _notes(fields: dict[str, Any], record: dict[str, Any]) -> str:
    parts: list[str] = []
    for name in ("mean_calculation", "age_calculation"):
        text = _text(fields.get(name))
        if text:
            parts.append(text)
    flags = [
        flag for flag in (record.get("flags") or []) if isinstance(flag, str) and flag.strip()
    ]
    if flags:
        parts.append("flags: " + ", ".join(flags))
    return "；".join(parts)


def _tracking_row(
    record: dict[str, Any],
    fields: dict[str, Any],
    article_key: str,
    record_id: str,
    result: MaterializeResult,
) -> dict[str, Any]:
    stat = missing_statistic(fields)
    if not stat:
        result.warnings.append(
            MaterializeWarning(
                article_key,
                record_id,
                "record_status=missing_statistics，但 mean 与 SD 都不是缺失值；"
                "missing_statistic 留空待复核",
            )
        )
    return {
        "paper_title": None,  # 编码输出没有题名字段，不虚构
        "APA_reference": _text(fields.get("full_reference")),
        "Mail": None,
        "Author": None,
        "CC_Mail": None,
        "measure_name": _text(fields.get("measure_name")),
        "subsample_label": _text(fields.get("subsample_label")),
        "sex": 0,
        "missing_statistic": stat,
        "notes": _notes(fields, record),
    }


def _excluded_row(
    record: dict[str, Any],
    fields: dict[str, Any],
    article_key: str,
    record_id: str,
    result: MaterializeResult,
) -> dict[str, Any]:
    reason = _notes(fields, record)
    if not reason:
        result.warnings.append(
            MaterializeWarning(
                article_key,
                record_id,
                "record_status=not_eligible 但没有给出任何理由文本；V36 会把空理由标记为过于空泛",
            )
        )
    return {"full_reference": _text(fields.get("full_reference")), "reason": reason or None}


def _plan(
    envelopes: list[dict[str, Any]],
    headers: dict[str, dict[str, int]],
    result: MaterializeResult,
) -> tuple[list[_Group], list[dict[str, Any]], list[dict[str, Any]]]:
    """把 record 分流成：主表块、追踪行、排除行；未解决状态进 pending。"""
    groups: dict[tuple[str, str, str], _Group] = {}
    tracking: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen_unknown: set[str] = set()

    for envelope in envelopes:
        if not isinstance(envelope, dict):
            result.warnings.append(MaterializeWarning("?", "?", "信封不是 JSON 对象，已跳过"))
            continue
        article_key = str(envelope.get("article_key") or "?")
        records = envelope.get("records")
        if not isinstance(records, list):
            result.warnings.append(
                MaterializeWarning(article_key, "?", "信封缺少 records 数组，已跳过")
            )
            continue

        for record in records:
            if not isinstance(record, dict):
                result.warnings.append(
                    MaterializeWarning(article_key, "?", "record 不是 JSON 对象，已跳过")
                )
                continue

            result.records += 1
            record_id = str(record.get("record_id") or "?")
            status = record.get("record_status")
            fields = record.get("fields")
            if not isinstance(fields, dict):
                fields = {}

            for name in sorted(set(fields) - set(headers["WBIS"]) - {"paper_id"}):
                if name in seen_unknown:
                    continue
                seen_unknown.add(name)
                result.warnings.append(
                    MaterializeWarning(
                        article_key, record_id, f"字段 {name} 不在主表表头，未写入工作簿"
                    )
                )

            if status == CODED_STATUS:
                sheet = record.get("sheet")
                sex = record.get("sex")
                if sheet not in SHEETS_MAIN:
                    result.warnings.append(
                        MaterializeWarning(article_key, record_id, f"未知工作表 {sheet!r}，未写入")
                    )
                    continue
                if sex not in (0, 1, 2):
                    result.warnings.append(
                        MaterializeWarning(
                            article_key, record_id, f"sex 非法（{sex!r}），未写入主表"
                        )
                    )
                    continue

                block_id = record.get("block_id")
                if not isinstance(block_id, str) or not block_id:
                    block_id = f"__auto{len(groups):03d}"
                    result.warnings.append(
                        MaterializeWarning(
                            article_key, record_id, "record 缺少 block_id，按出现顺序补位"
                        )
                    )

                key = (article_key, sheet, block_id)
                group = groups.get(key)
                if group is None:
                    group = _Group(article_key=article_key, sheet=sheet, block_id=block_id)
                    groups[key] = group
                if sex in group.rows:
                    result.warnings.append(
                        MaterializeWarning(
                            article_key,
                            record_id,
                            f"块 {block_id} 内 sex={sex} 重复；后一条被忽略，避免覆盖",
                        )
                    )
                    continue

                values = field_values(fields, headers[sheet])
                if sex in (1, 2) and not sex_row_complete(fields):
                    if any(name != "sex" for name in values):
                        result.warnings.append(
                            MaterializeWarning(
                                article_key,
                                record_id,
                                "性别行 mean/SD 不完整，按 §4.1 只保留 sex，其余字段不落表",
                            )
                        )
                    values = {}
                group.rows[sex] = values
                result.main_records += 1

            elif status == TRACKING_STATUS:
                if record.get("sex") != 0:
                    result.warnings.append(
                        MaterializeWarning(
                            article_key,
                            record_id,
                            "§9 只登记总体（sex=0）缺失；性别行缺失不进入 no_mean_or_sd",
                        )
                    )
                    continue
                tracking.append(_tracking_row(record, fields, article_key, record_id, result))

            elif status == EXCLUDED_STATUS:
                excluded.append(_excluded_row(record, fields, article_key, record_id, result))

            elif status in PENDING_STATUSES:
                result.pending.append(
                    {
                        "article_key": article_key,
                        "record_id": record_id,
                        "status": str(status),
                    }
                )

            else:
                result.warnings.append(
                    MaterializeWarning(
                        article_key,
                        record_id,
                        f"未知 record_status={status!r}，未写入任何工作表",
                    )
                )

    return list(groups.values()), tracking, excluded


def _clear(ws) -> None:
    """清空表头以下内容，保证重复物化不会残留上一次的行。"""
    if ws.max_row > 1:
        ws.delete_rows(2, ws.max_row - 1)


def _write_block(ws, start_row: int, group: _Group, headers: dict[str, int]) -> None:
    for sex in (0, 1, 2):
        ws.cell(row=start_row + sex, column=headers["sex"], value=sex)
    for sex, values in sorted(group.rows.items()):
        for name, value in values.items():
            if name not in headers:
                continue
            # fields.sex 会覆盖结构行号：record.sex 与 fields.sex 不一致时由 V03/V10 抓住，
            # 不在这里静默修正。
            ws.cell(row=start_row + sex, column=headers[name], value=value)


def _write_named_row(ws, row: int, headers: dict[str, int], values: dict[str, Any]) -> None:
    for name, value in values.items():
        if name in headers:
            ws.cell(row=row, column=headers[name], value=value)


def _checked_headers(wb: Workbook) -> dict[str, dict[str, int]]:
    """模板必须是四张表、顺序固定，主表必须有 sex 列。"""
    if tuple(wb.sheetnames) != ALL_SHEETS:
        raise MaterializeError(f"模板工作表为 {wb.sheetnames}，应为 {list(ALL_SHEETS)}")
    headers = {name: header_map(wb[name]) for name in ALL_SHEETS}
    for sheet in SHEETS_MAIN:
        if "sex" not in headers[sheet]:
            raise MaterializeError(f"{sheet} 缺少 sex 列，模板不符合契约")
    return headers


def build_workbook(
    envelopes: list[dict[str, Any]], template: str | Path
) -> tuple[Workbook, MaterializeResult]:
    """在内存里构造工作簿；调用方负责保存与关闭。"""
    template = Path(template)
    if not template.is_file():
        raise MaterializeError(f"模板不存在：{template}")
    wb = load_workbook(template)
    try:
        headers = _checked_headers(wb)
        result = MaterializeResult(blocks={sheet: 0 for sheet in SHEETS_MAIN})
        groups, tracking, excluded = _plan(envelopes, headers, result)

        for sheet in SHEETS_MAIN:
            ws = wb[sheet]
            _clear(ws)
            row = 2
            for group in groups:
                if group.sheet != sheet or not any(group.rows.values()):
                    continue
                _write_block(ws, row, group, headers[sheet])
                result.blocks[sheet] += 1
                row += 4  # 三行块 + 一个空行（§4.1）

        ws = wb[SHEET_TRACKING]
        _clear(ws)
        for offset, values in enumerate(tracking):
            _write_named_row(ws, 2 + offset, headers[SHEET_TRACKING], values)
        result.tracking_rows = len(tracking)

        ws = wb[SHEET_EXCLUDED]
        _clear(ws)
        for offset, values in enumerate(excluded):
            _write_named_row(ws, 2 + offset, headers[SHEET_EXCLUDED], values)
        result.excluded_rows = len(excluded)
        return wb, result
    except Exception:
        wb.close()
        raise


def write_workbook(
    envelopes: list[dict[str, Any]], template: str | Path, output: str | Path
) -> MaterializeResult:
    """物化并落盘；返回计数与提示。"""
    wb, result = build_workbook(envelopes, template)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        wb.save(output)
    finally:
        wb.close()
    result.output = output
    return result


def read_envelope(path: str | Path) -> dict[str, Any]:
    """读一份 parsed.json；读不到或结构不对时抛 MaterializeError，不静默跳过。"""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MaterializeError(f"无法读取 parsed.json：{path}：{exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MaterializeError(f"parsed.json 不是合法 JSON：{path}：{exc}") from exc
    if not isinstance(data, dict):
        raise MaterializeError(f"parsed.json 顶层不是对象：{path}")
    return data


def materialize_json_files(
    paths: list[str | Path], template: str | Path, output: str | Path
) -> MaterializeResult:
    """把一批 parsed.json 直接物化成一份工作簿。"""
    return write_workbook([read_envelope(path) for path in paths], template, output)


__all__ = [
    "CODED_STATUS",
    "EXCLUDED_STATUS",
    "MaterializeError",
    "MaterializeResult",
    "MaterializeWarning",
    "PENDING_STATUSES",
    "TRACKING_STATUS",
    "build_workbook",
    "field_values",
    "header_map",
    "materialize_json_files",
    "missing_statistic",
    "read_envelope",
    "sex_row_complete",
    "write_workbook",
]
