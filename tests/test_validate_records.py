"""Stage 5 单篇校验：Schema、物化、V01–V50、报告与回灌清单。"""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pytest
from openpyxl import load_workbook

from meta_coding.cli import main
from meta_coding.consensus.materialize import write_workbook
from meta_coding.validate.model import is_missing
from meta_coding.validate.records import run_validate, validate_article

REPO = Path(__file__).resolve().parents[1]
ARTICLE_KEY = "sha256:" + "b" * 64
ARTICLE_ID = "b" * 16
OTHER_KEY = "sha256:" + "c" * 64
OTHER_ID = "c" * 16

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


def make_root(tmp_path: Path) -> Path:
    """最小仓库根：只放 Stage 5 需要的规则产物。"""
    root = tmp_path / "repo"
    (root / "rules" / "schema").mkdir(parents=True)
    shutil.copy(REPO / "rules" / "clean_codebook_BLANK.xlsx", root / "rules")
    shutil.copy(REPO / "rules" / "template_contract.json", root / "rules")
    shutil.copy(
        REPO / "rules" / "schema" / "coder_output.schema.json", root / "rules" / "schema"
    )
    return root


def write_manifest(root: Path, rows: list[dict]) -> Path:
    path = root / "data" / "01_manifest" / "corpus_manifest.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["article_key", "source_file", "role", "batch_id"]
        )
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_parsed(root: Path, payload: dict, coder: str = "a") -> Path:
    path = root / "data" / "03_coded" / f"coder_{coder}" / ARTICLE_ID
    path.mkdir(parents=True, exist_ok=True)
    target = path / "parsed.json"
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return target


def article_row(key: str = ARTICLE_KEY, source_file: str = "6047.pdf") -> dict:
    return {
        "article_key": key,
        "source_file": source_file,
        "role": "article",
        "batch_id": "batch01",
    }


def test_valid_article_has_no_violations(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    write_parsed(root, envelope(make_record()))

    report = validate_article(root, "a", "batch01", article_row())

    assert report.status == "ok"
    assert report.schema_ok is True
    assert report.materialize is not None
    assert report.materialize.blocks == {"WBIS": 1, "WSSQ": 0}
    assert [(item.vid, item.field, item.message) for item in report.violations] == []


def test_sex_row_misalignment_is_caught(tmp_path: Path) -> None:
    """record.sex=1 但 fields.sex=2：物化照写，V03 必须抓住三行块序列异常。"""
    root = make_root(tmp_path)
    total = make_record(record_id="r1")
    female = make_record(
        sex=1,
        fields_sex=2,
        record_id="r2",
        overrides={"sample_n": 220, "scale_mean": 3.61, "scale_sd": 1.72},
    )
    write_parsed(root, envelope(total, female))

    report = validate_article(root, "a", "batch01", article_row())
    vids = {item.vid for item in report.violations}

    assert "V03" in vids, f"sex 行错位未被抓住，实际命中 {sorted(vids)}"
    messages = [item.message for item in report.violations if item.vid == "V03"]
    assert any("[0, 2, 2]" in (message or "") for message in messages), messages


def test_missing_code_is_not_used_as_a_number(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    record = make_record(
        overrides={"mean_age": -999, "age_group": -999, "mean_bmi": -999}
    )
    write_parsed(root, envelope(record))

    report = validate_article(root, "a", "batch01", article_row())
    vids = {item.vid for item in report.violations}
    assert "V11" not in vids, "-999 被当作 mean_age 真实值参与年龄分组判断"
    assert "V47" not in vids, "-999 被当作数值精度问题"
    assert "V09" not in vids, "-999 被当作非法枚举而不是缺失"

    out = tmp_path / "roundtrip.xlsx"
    write_workbook([envelope(record)], REPO / "rules" / "clean_codebook_BLANK.xlsx", out)
    wb = load_workbook(out, data_only=True)
    ws = wb["WBIS"]
    head = {c.value: c.column for c in next(ws.iter_rows(min_row=1, max_row=1)) if c.value}
    value = ws.cell(row=2, column=head["mean_age"]).value
    assert value == -999 and is_missing(value)
    wb.close()


def test_schema_error_is_reported_without_materializing(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    write_parsed(root, {"article_key": ARTICLE_KEY})

    report = validate_article(root, "a", "batch01", article_row())

    assert report.status == "schema_error"
    assert report.schema_ok is False
    assert report.materialize is None
    assert [item.vid for item in report.violations] == ["SCHEMA"]
    assert "records" in (report.violations[0].message or "")


def test_schema_error_points_at_the_offending_field(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    write_parsed(root, envelope(make_record(overrides={"sample_n": "433"})))

    report = validate_article(root, "a", "batch01", article_row())
    paths = [item.field or "" for item in report.violations if item.vid == "SCHEMA"]

    assert report.status == "schema_error"
    assert any("sample_n" in path for path in paths), paths


def test_missing_parsed_output_is_a_status_not_a_crash(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    report = validate_article(root, "a", "batch01", article_row())

    assert report.status == "missing_output"
    assert report.schema_ok is False
    assert report.violations == []
    assert report.materialize is None


def test_article_status_is_carried_into_the_report(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    payload = envelope(make_record())
    payload["article_status"] = "coded"
    write_parsed(root, payload)

    report = validate_article(root, "a", "batch01", article_row())

    assert report.article_status == "coded"
    assert report.as_dict()["article_status"] == "coded"


def test_unresolved_record_lands_in_pending_and_has_no_destination(
    tmp_path: Path,
) -> None:
    """未解决状态不进四张表：pending 记一条，V48 提示该文在主表里没有去向。"""
    root = make_root(tmp_path)
    payload = {
        "article_key": ARTICLE_KEY,
        "records": [make_record(status="eligibility_unresolved")],
        "escalations": [],
        "article_status": "eligibility_unresolved",
    }
    write_parsed(root, payload)

    report = validate_article(root, "a", "batch01", article_row())
    vids = {item.vid for item in report.violations}

    assert report.status == "ok"
    assert report.materialize is not None
    assert report.materialize.pending_records == 1
    assert report.materialize.blocks == {"WBIS": 0, "WSSQ": 0}
    assert "V48" in vids


def test_run_validate_writes_reports_and_repair_log(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    write_manifest(root, [article_row(), article_row(OTHER_KEY, "6127.pdf")])
    total = make_record(record_id="r1")
    female = make_record(
        sex=1,
        fields_sex=2,
        record_id="r2",
        overrides={"sample_n": 220, "scale_mean": 3.61, "scale_sd": 1.72},
    )
    write_parsed(root, envelope(total, female))

    result = run_validate(root, "a", "batch01")

    assert result.articles == 2
    assert result.outputs == 1
    assert result.missing_output == 1
    assert result.schema_errors == 0
    assert result.violation_total > 0
    assert result.repair_articles == 1
    assert "V03" in result.by_vid

    out_dir = root / "data" / "04_validation" / "coder_a"
    assert (out_dir / f"{ARTICLE_ID}.json").is_file()
    assert (out_dir / f"{OTHER_ID}.json").is_file()

    report = json.loads((out_dir / f"{ARTICLE_ID}.json").read_text(encoding="utf-8"))
    assert report["coder"] == "a" and report["article_id"] == ARTICLE_ID
    assert report["status"] == "ok"
    assert report["violation_counts"]["V03"] >= 1
    assert report["materialize"]["blocks"] == {"WBIS": 1, "WSSQ": 0}

    lines = [
        json.loads(line)
        for line in (out_dir / "repair_log.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 1
    assert lines[0]["article_id"] == ARTICLE_ID
    assert lines[0]["coder"] == "a"
    assert {item["vid"] for item in lines[0]["violations"]} >= {"V03"}

    with (out_dir / "validation_summary.csv").open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 2
    assert {row["status"] for row in rows} == {"ok", "missing_output"}

    summary = json.loads((out_dir / "run_summary.json").read_text(encoding="utf-8"))
    assert summary["stage"] == "05_validation"
    assert summary["articles"] == 2
    assert summary["repair_articles"] == 1

    workbook = out_dir / "batch01_materialized.xlsx"
    assert workbook.is_file()
    assert result.workbook == workbook
    assert result.workbook_blocks == {"WBIS": 1, "WSSQ": 0}


def test_run_validate_limit_only_reports_requested_articles(tmp_path: Path) -> None:
    root = make_root(tmp_path)
    write_manifest(root, [article_row(), article_row(OTHER_KEY, "6127.pdf")])
    write_parsed(root, envelope(make_record()))

    result = run_validate(root, "a", "batch01", limit=1)

    assert result.articles == 1
    assert result.outputs == 1
    out_dir = root / "data" / "04_validation" / "coder_a"
    assert (out_dir / f"{ARTICLE_ID}.json").is_file()
    assert not (out_dir / f"{OTHER_ID}.json").exists()


def test_cli_validate_prints_counts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_root(tmp_path)
    shutil.copytree(REPO / "configs", root / "configs")
    write_manifest(root, [article_row()])
    write_parsed(root, envelope(make_record()))

    code = main(["validate", "--coder", "a", "--batch", "batch01", "--repo", str(root)])
    out = capsys.readouterr().out

    assert code == 0, out
    assert "违规 0" in out
    assert "回灌清单" in out
    assert "data/04_validation/coder_a" in out


def test_cli_validate_rejects_wrong_batch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_root(tmp_path)
    shutil.copytree(REPO / "configs", root / "configs")

    code = main(["validate", "--coder", "a", "--batch", "batch99", "--repo", str(root)])

    assert code == 2
    assert "批次不一致" in capsys.readouterr().err
