"""物化往返：coder_output JSON → 三行块工作簿 → load_codebook 结构核对。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from openpyxl import load_workbook

from meta_coding.consensus.materialize import (
    MaterializeError,
    materialize_json_files,
    missing_statistic,
    write_workbook,
)
from meta_coding.validate.model import is_missing, load_codebook

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "rules" / "clean_codebook_BLANK.xlsx"
CONTRACT = REPO / "rules" / "template_contract.json"
ARTICLE_KEY = "sha256:" + "a" * 64

GOOD_FIELDS = {
    "full_reference": "Doe, J. (2020). A study of weight bias internalization. Journal, 1(2), 3-4.",
    "subsample_label": "Overall sample",
    "source_type": 1,
    "paper_id": 1,
    "sex": 0,
    "country": "US",
    "sample_type": 1,
    "clinical_type": None,
    "sampling_method": 2,
    "publication_year": 2020,
    "year_data_collection": 2018,
    "year_imputed_rule": 3,
    "sample_n": 433,
    "mean_age": 35.83,
    "age_calculation": None,
    "age_group": 4,
    "mean_bmi": 28.98,
    "measure_name": "WBIS",
    "translated_flag": 2,
    "scale_comparable_flag": 1,
    "items_n": 11,
    "response_scale_min": 1,
    "response_scale_max": 7,
    "direction": 1,
    "scale_total": None,
    "scale_mean": 3.54,
    "scale_sd": 1.69,
    "mean_calculation": "Year: publication_year - 2 = 2018.",
}


def make_record(
    *,
    sex: int = 0,
    fields_sex: int | None = None,
    status: str = "coded",
    sheet: str = "WBIS",
    block_id: str = "b01",
    record_id: str = "r001",
    overrides: dict | None = None,
) -> dict:
    fields = dict(GOOD_FIELDS)
    fields["sex"] = sex if fields_sex is None else fields_sex
    fields.update(overrides or {})
    return {
        "record_id": record_id,
        "article_key": ARTICLE_KEY,
        "source_file": "6047.pdf",
        "sheet": sheet,
        "block_id": block_id,
        "sex": sex,
        "fields": fields,
        "provenance": {"scale_mean": {"page": 3, "quote": "M = 3.54, SD = 1.69"}},
        "flags": [],
        "record_status": status,
    }


def envelope(*records: dict) -> dict:
    return {
        "article_key": ARTICLE_KEY,
        "records": list(records),
        "escalations": [],
        "article_status": "coded",
    }


def columns(ws) -> dict[str, int]:
    return {c.value: c.column for c in next(ws.iter_rows(min_row=1, max_row=1)) if c.value}


def cell(ws, header: dict[str, int], row: int, name: str):
    return ws.cell(row=row, column=header[name]).value


def test_valid_record_becomes_three_row_block(tmp_path: Path) -> None:
    out = tmp_path / "wb.xlsx"
    result = write_workbook([envelope(make_record())], TEMPLATE, out)

    assert result.records == 1
    assert result.main_records == 1
    assert result.blocks == {"WBIS": 1, "WSSQ": 0}
    assert result.tracking_rows == 0 and result.excluded_rows == 0

    wb = load_workbook(out, data_only=True)
    ws = wb["WBIS"]
    head = columns(ws)
    assert [cell(ws, head, row, "sex") for row in (2, 3, 4)] == [0, 1, 2]
    assert cell(ws, head, 2, "full_reference") == GOOD_FIELDS["full_reference"]
    assert cell(ws, head, 2, "scale_mean") == 3.54
    assert cell(ws, head, 2, "paper_id") is None, "编码阶段 paper_id 必须留空"
    for row in (3, 4):
        filled = [
            name
            for name in head
            if name != "sex" and cell(ws, head, row, name) is not None
        ]
        assert filled == [], f"未编码性别行只应写 sex，实际写了 {filled}"
    wb.close()


def test_coded_sex_row_fills_only_its_own_line(tmp_path: Path) -> None:
    out = tmp_path / "wb.xlsx"
    total = make_record(record_id="r1")
    female = make_record(
        sex=1,
        record_id="r2",
        overrides={"sample_n": 220, "scale_mean": 3.61, "scale_sd": 1.72},
    )
    result = write_workbook([envelope(total, female)], TEMPLATE, out)
    assert result.blocks == {"WBIS": 1, "WSSQ": 0}
    assert result.main_records == 2

    codebook = load_codebook(out, CONTRACT, keep_sex_placeholders=True)
    block = codebook.blocks["WBIS"][0]
    assert block.sex_sequence == [0, 1, 2]
    assert block.total.get("scale_mean") == 3.54
    assert block.female is not None and block.female.get("sample_n") == 220
    assert block.male is not None and block.male.get("scale_mean") is None


def test_incomplete_sex_row_keeps_only_sex(tmp_path: Path) -> None:
    out = tmp_path / "wb.xlsx"
    total = make_record(record_id="r1")
    female = make_record(
        sex=1,
        record_id="r2",
        overrides={"scale_mean": -999, "scale_sd": -999, "sample_n": 220},
    )
    result = write_workbook([envelope(total, female)], TEMPLATE, out)

    wb = load_workbook(out, data_only=True)
    ws = wb["WBIS"]
    head = columns(ws)
    assert cell(ws, head, 3, "sex") == 1
    assert [name for name in head if name != "sex" and cell(ws, head, 3, name) is not None] == []
    wb.close()
    assert any("§4.1" in warning.message for warning in result.warnings)


def test_missing_statistics_writes_tracking_row(tmp_path: Path) -> None:
    out = tmp_path / "wb.xlsx"
    total = make_record(record_id="r1", overrides={"scale_sd": -999})
    tracking = make_record(
        record_id="r2",
        status="missing_statistics",
        overrides={"scale_sd": -999, "mean_calculation": "Table 2 reports M only."},
    )
    result = write_workbook([envelope(total, tracking)], TEMPLATE, out)
    assert result.tracking_rows == 1
    assert result.blocks == {"WBIS": 1, "WSSQ": 0}

    wb = load_workbook(out, data_only=True)
    ws = wb["no_mean_or_sd"]
    head = columns(ws)
    assert cell(ws, head, 2, "APA_reference") == GOOD_FIELDS["full_reference"]
    assert cell(ws, head, 2, "measure_name") == "WBIS"
    assert cell(ws, head, 2, "subsample_label") == "Overall sample"
    assert cell(ws, head, 2, "sex") == 0
    assert cell(ws, head, 2, "missing_statistic") == "sd"
    assert cell(ws, head, 2, "paper_title") is None
    wb.close()

    assert missing_statistic({"scale_mean": -999, "scale_sd": 1.2}) == "mean"
    assert missing_statistic({"scale_mean": 3.5, "scale_sd": -999}) == "sd"
    assert missing_statistic({"scale_mean": -999, "scale_sd": -999}) == "mean_and_sd"
    assert missing_statistic({"scale_mean": 3.5, "scale_sd": 1.2}) == ""


def test_not_eligible_writes_excluded_row(tmp_path: Path) -> None:
    reason = "Did not administer WBIS or WSSQ; only a single unvalidated item was used."
    record = make_record(status="not_eligible", overrides={"mean_calculation": reason})
    result = write_workbook([envelope(record)], TEMPLATE, tmp_path / "wb.xlsx")

    assert result.excluded_rows == 1
    assert result.blocks == {"WBIS": 0, "WSSQ": 0}
    wb = load_workbook(result.output, data_only=True)
    ws = wb["not_eligible"]
    head = columns(ws)
    assert cell(ws, head, 2, "full_reference") == GOOD_FIELDS["full_reference"]
    assert cell(ws, head, 2, "reason") == reason
    wb.close()


def test_unresolved_status_goes_to_pending_not_to_sheets(tmp_path: Path) -> None:
    record = make_record(status="eligibility_unresolved")
    result = write_workbook([envelope(record)], TEMPLATE, tmp_path / "wb.xlsx")

    assert result.pending == [
        {
            "article_key": ARTICLE_KEY,
            "record_id": "r001",
            "status": "eligibility_unresolved",
        }
    ]
    assert result.pending_records == 1
    assert result.blocks == {"WBIS": 0, "WSSQ": 0}
    assert result.tracking_rows == 0 and result.excluded_rows == 0

    wb = load_workbook(result.output, data_only=True)
    for sheet in ("WBIS", "WSSQ", "no_mean_or_sd", "not_eligible"):
        assert wb[sheet].max_row == 1, f"{sheet} 不应有数据行"
    wb.close()


def test_duplicate_sex_in_one_block_is_not_overwritten(tmp_path: Path) -> None:
    first = make_record(record_id="r1", overrides={"scale_mean": 3.54})
    second = make_record(record_id="r2", overrides={"scale_mean": 4.99})
    result = write_workbook([envelope(first, second)], TEMPLATE, tmp_path / "wb.xlsx")

    assert result.blocks == {"WBIS": 1, "WSSQ": 0}
    assert any("重复" in warning.message for warning in result.warnings)
    wb = load_workbook(result.output, data_only=True)
    ws = wb["WBIS"]
    head = columns(ws)
    assert cell(ws, head, 2, "scale_mean") == 3.54
    wb.close()


def test_records_roundtrip_to_codebook_rows(tmp_path: Path) -> None:
    out = tmp_path / "wb.xlsx"
    records = [
        make_record(record_id="r1", block_id="b01"),
        make_record(
            record_id="r2",
            block_id="b02",
            overrides={"subsample_label": "Taiwan university-student sample"},
        ),
        make_record(
            record_id="r3",
            sheet="WSSQ",
            block_id="b01",
            overrides={"measure_name": "WSSQ"},
        ),
    ]
    result = write_workbook([envelope(*records)], TEMPLATE, out)
    assert result.blocks == {"WBIS": 2, "WSSQ": 1}

    codebook = load_codebook(out, CONTRACT, keep_sex_placeholders=True)
    assert [block.start_row for block in codebook.blocks["WBIS"]] == [2, 6]
    assert [block.start_row for block in codebook.blocks["WSSQ"]] == [2]
    for block in codebook.all_blocks():
        assert block.sex_sequence == [0, 1, 2]
        assert [row.row for row in block.rows] == list(
            range(block.start_row, block.start_row + 3)
        )
    assert len(codebook.coded_rows()) == 3
    assert codebook.blocks["WBIS"][1].total.get("subsample_label") == (
        "Taiwan university-student sample"
    )
    assert codebook.blocks["WSSQ"][0].total.get("measure_name") == "WSSQ"


def test_placeholder_view_is_opt_in(tmp_path: Path) -> None:
    """默认块视图（其他阶段）不含空性别占位行；Stage 5 显式打开才看三行。"""
    out = tmp_path / "wb.xlsx"
    write_workbook([envelope(make_record())], TEMPLATE, out)

    default = load_codebook(out, CONTRACT)
    staged = load_codebook(out, CONTRACT, keep_sex_placeholders=True)

    assert default.blocks["WBIS"][0].sex_sequence == [0]
    assert staged.blocks["WBIS"][0].sex_sequence == [0, 1, 2]


def test_missing_code_stays_numeric_minus_999(tmp_path: Path) -> None:
    record = make_record(
        overrides={"mean_age": -999, "age_group": -999, "mean_bmi": -999}
    )
    result = write_workbook([envelope(record)], TEMPLATE, tmp_path / "wb.xlsx")

    wb = load_workbook(result.output, data_only=True)
    ws = wb["WBIS"]
    head = columns(ws)
    for name in ("mean_age", "mean_bmi", "age_group"):
        value = cell(ws, head, 2, name)
        assert value == -999, f"{name} 实际写入 {value!r}"
        assert isinstance(value, int) and not isinstance(value, bool)
        assert is_missing(value)
    wb.close()


def test_materialize_takes_a_batch_of_parsed_json_files(tmp_path: Path) -> None:
    first = tmp_path / "a.json"
    first.write_text(json.dumps(envelope(make_record()), ensure_ascii=False), encoding="utf-8")
    second = tmp_path / "b.json"
    second.write_text(
        json.dumps(envelope(make_record(status="not_eligible")), ensure_ascii=False),
        encoding="utf-8",
    )

    result = materialize_json_files([first, second], TEMPLATE, tmp_path / "wb.xlsx")

    assert result.records == 2
    assert result.blocks == {"WBIS": 1, "WSSQ": 0}
    assert result.excluded_rows == 1

    with pytest.raises(MaterializeError):
        materialize_json_files([tmp_path / "missing.json"], TEMPLATE, tmp_path / "x.xlsx")
