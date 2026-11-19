"""Stage 7 验收：编号、跨表一致、取值优先级、provenance 覆盖、写回读回。

合成两个编码员的工作簿（见 conftest.py），检查：
① paper_id 连续且跨表一致；② 最大 paper_id == 不同论文数；
③ 取值优先级 人工 > 裁决 > A/B 一致 > pending_human；
④ provenance 每格有来源；⑤ 写回读回 0 差异。
"""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pytest
from conftest import REF_P1, REF_P2, REF_P3, REF_P4, REPO, Scenario

from meta_coding.cli import main
from meta_coding.consensus.build import build_consensus
from meta_coding.validate.model import load_codebook
from meta_coding.validate.registry import run_all

MAIN_FIELDS = [
    "full_reference",
    "subsample_label",
    "source_type",
    "paper_id",
    "sex",
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
    "measure_name",
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


def _build(scenario: Scenario):
    return build_consensus(root=scenario.root, batch=scenario.batch)


def _provenance(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _cell(
    rows: list[dict[str, str]],
    *,
    sheet: str,
    paper_id: int,
    subsample: str,
    measure: str,
    field_name: str,
    sex: int = 0,
) -> dict[str, str]:
    matches = [
        row
        for row in rows
        if row["sheet"] == sheet
        and row["paper_id"] == str(paper_id)
        and row["subsample_label"] == subsample
        and row["measure_name"] == measure
        and row["field"] == field_name
        and row["sex"] == str(sex)
    ]
    assert len(matches) == 1, (
        f"{sheet}/{paper_id}/{subsample}/{measure}/{field_name} 命中 {len(matches)} 行"
    )
    return matches[0]


def _text(value) -> str:
    return "" if value is None else str(value)


def test_paper_ids_contiguous_and_cross_sheet(scenario: Scenario) -> None:
    result = _build(scenario)
    cb = load_codebook(result.workbook, scenario.contract)

    by_reference: dict[str, set[int]] = {}
    for block in cb.all_blocks():
        total = block.total
        assert total is not None
        pid = total.get("paper_id")
        assert isinstance(pid, int) and pid >= 1
        # 同一块三行必须共用同一 ID
        assert {row.get("paper_id") for row in block.rows} == {pid}
        reference = total.get("full_reference")
        by_reference.setdefault(str(reference), set()).add(pid)

    assert sorted(by_reference) == sorted([REF_P1, REF_P2, REF_P3, REF_P4])
    assert by_reference[REF_P1] == {1}
    assert by_reference[REF_P2] == {2}
    assert by_reference[REF_P3] == {3}
    assert by_reference[REF_P4] == {4}

    ids = sorted({pid for values in by_reference.values() for pid in values})
    assert ids == [1, 2, 3, 4]  # 连续不跳号，每批次从 1 开始


def test_max_paper_id_equals_distinct_paper_count(scenario: Scenario) -> None:
    result = _build(scenario)
    cb = load_codebook(result.workbook, scenario.contract)

    ids = {
        row.get("paper_id")
        for block in cb.all_blocks()
        for row in block.rows
        if row.get("paper_id") is not None
    }
    references = {
        str(block.total.get("full_reference"))
        for block in cb.all_blocks()
        if block.total is not None
    }
    assert max(ids) == len(references) == 4
    assert result.papers == 4


def test_value_priority_manual_adjudication_agreement_pending(
    scenario: Scenario,
) -> None:
    result = _build(scenario)
    rows = _provenance(result.provenance)

    # 人工最高：P2 College scale_mean，A=3.5、B=3.7，人工表定为 3.5
    manual = _cell(
        rows,
        sheet="WBIS",
        paper_id=2,
        subsample="College sample",
        measure="WBIS",
        field_name="scale_mean",
    )
    assert (manual["value"], manual["value_source"]) == ("3.5", "manual")
    assert manual["coder_b_value"] == "3.7"
    assert manual["manual_value"] == "3.5"

    # 裁决次之：P1 mean_age，A=35.83、B=36.0，裁决记录取 35.83
    adjudicated = _cell(
        rows,
        sheet="WBIS",
        paper_id=1,
        subsample="Overall sample",
        measure="WBIS",
        field_name="mean_age",
    )
    assert (adjudicated["value"], adjudicated["value_source"]) == (
        "35.83",
        "adjudication",
    )
    assert float(adjudicated["coder_b_value"]) == 36.0

    # A/B 一致：P1 WSSQ scale_mean
    agreed = _cell(
        rows,
        sheet="WSSQ",
        paper_id=1,
        subsample="Overall sample",
        measure="WSSQ",
        field_name="scale_mean",
    )
    assert agreed["value_source"] == "ab_agree"
    assert float(agreed["value"]) == 3.0

    # 冲突且无人工/裁决：P1 sample_n 留空，标 pending_human，绝不选边
    pending = _cell(
        rows,
        sheet="WBIS",
        paper_id=1,
        subsample="Overall sample",
        measure="WBIS",
        field_name="sample_n",
    )
    assert pending["value"] == ""
    assert pending["value_source"] == "pending_human"
    assert pending["coder_a_value"] == "433"
    assert pending["coder_b_value"] == "430"
    assert "冲突" in pending["note"]


def test_pending_cell_is_blank_in_workbook(scenario: Scenario) -> None:
    result = _build(scenario)
    cb = load_codebook(result.workbook, scenario.contract)

    block = cb.blocks["WBIS"][0]
    assert block.total is not None
    assert block.total.get("sample_n") is None
    assert block.total.get("mean_age") == 35.83
    assert result.pending >= 1


def test_provenance_covers_every_cell(scenario: Scenario) -> None:
    result = _build(scenario)
    cb = load_codebook(result.workbook, scenario.contract)
    rows = _provenance(result.provenance)

    indexed = {
        (row["sheet"], int(row["excel_row"]), row["field"]): row for row in rows
    }
    assert len(indexed) == len(rows), "provenance 存在重复单元格记录"

    expected: list[tuple[str, int, str, object]] = []
    for sheet in ("WBIS", "WSSQ"):
        for block in cb.blocks.get(sheet, []):
            for row in block.rows:
                for name in MAIN_FIELDS:
                    expected.append((sheet, row.row, name, row.get(name)))
    for item in cb.tracking:
        for name in ("paper_title", "APA_reference", "measure_name", "subsample_label",
                     "sex", "missing_statistic", "notes"):
            expected.append(("no_mean_or_sd", int(item["_row"]), name, item.get(name)))

    for sheet, excel_row, name, value in expected:
        key = (sheet, excel_row, name)
        assert key in indexed, f"缺少 provenance：{key}"
        record = indexed[key]
        assert record["value_source"], f"{key} 没有来源标签"
        assert record["value"] == _text(value), f"{key} 的 provenance 值不一致"


def test_writeback_readback_has_zero_differences(scenario: Scenario) -> None:
    result = _build(scenario)
    assert result.writeback_diffs == 0
    assert result.diff_examples == []


def test_paper_id_map_lists_each_paper_once(scenario: Scenario) -> None:
    result = _build(scenario)
    with result.paper_id_map.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))

    assert [int(row["paper_id"]) for row in rows] == [1, 2, 3, 4]
    assert [row["full_reference"] for row in rows] == [REF_P1, REF_P2, REF_P3, REF_P4]
    assert rows[0]["sheets"] == "WBIS;WSSQ"
    assert rows[1]["blocks"] == "2"  # P2 两个独立子样本块共用 ID 2


def test_missing_decisions_degrade_to_pending(scenario: Scenario) -> None:
    """没有裁决与人工记录时，不能把冲突值悄悄写成其中一方的值。"""
    result = build_consensus(
        root=scenario.root,
        batch=scenario.batch,
        adjudication=scenario.root / "data" / "05_adjudication" / "absent.jsonl",
        manual=scenario.root / "data" / "06_consensus" / "absent.csv",
    )
    rows = _provenance(result.provenance)

    mean_age = _cell(
        rows,
        sheet="WBIS",
        paper_id=1,
        subsample="Overall sample",
        measure="WBIS",
        field_name="mean_age",
    )
    assert mean_age["value"] == ""
    assert mean_age["value_source"] == "pending_human"

    scale_mean = _cell(
        rows,
        sheet="WBIS",
        paper_id=2,
        subsample="College sample",
        measure="WBIS",
        field_name="scale_mean",
    )
    assert scale_mean["value"] == ""
    assert scale_mean["value_source"] == "pending_human"


def test_coder_b_only_block_continues_numbering(scenario: Scenario) -> None:
    result = _build(scenario)
    cb = load_codebook(result.workbook, scenario.contract)

    wssq_references = [
        str(block.total.get("full_reference"))
        for block in cb.blocks["WSSQ"]
        if block.total is not None
    ]
    assert wssq_references == [REF_P3, REF_P1, REF_P4]
    assert cb.blocks["WSSQ"][-1].total.get("paper_id") == 4


def test_writeback_reports_differences_on_tampering(
    scenario: Scenario, tmp_path: Path
) -> None:
    """写回校验必须是真比对：手工改坏一格后，重新读取能发现差异。"""
    from meta_coding.consensus.build import _compare_readback

    result = _build(scenario)
    cb = load_codebook(result.workbook, scenario.contract)
    block = cb.blocks["WBIS"][0]
    row = block.total
    assert row is not None
    written = {
        ("WBIS", row.row, name): row.get(name) for name in MAIN_FIELDS
    }
    written[("WBIS", row.row, "sample_n")] = 999

    diffs, examples = _compare_readback(cb, written)
    assert diffs == 1
    assert "sample_n" in examples[0]


@pytest.mark.parametrize("sheet", ["no_mean_or_sd"])
def test_tracking_sheet_has_no_paper_id(scenario: Scenario, sheet: str) -> None:
    result = _build(scenario)
    cb = load_codebook(result.workbook, scenario.contract)
    assert "paper_id" not in [h for h in cb.headers[sheet] if h]
    assert len(cb.tracking) == 1
    assert cb.tracking[0]["missing_statistic"] == "sd"


def test_consensus_workbook_passes_stage5_validators(scenario: Scenario) -> None:
    """Stage 7 是交付闸门：共识工作簿必须能通过 V01–V50。"""
    result = _build(scenario)
    cb = load_codebook(result.workbook, scenario.contract)
    violations = run_all(cb, {"articles": []})
    assert [item.as_dict() for item in violations] == []


def test_cli_consensus_writes_outputs_and_manifest(scenario: Scenario) -> None:
    shutil.copytree(REPO / "configs", scenario.root / "configs")
    code = main(["consensus", "--repo", str(scenario.root), "--batch", scenario.batch])

    assert code == 0
    out_dir = scenario.root / "data" / "06_consensus"
    assert (out_dir / f"{scenario.batch}_consensus_codebook.xlsx").is_file()
    assert (out_dir / "provenance.csv").is_file()
    assert (out_dir / "paper_id_map.csv").is_file()

    manifests = sorted((scenario.root / "artifacts").glob("stage-07_consensus/*/manifest.json"))
    assert len(manifests) == 1
    data = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert data["status"] == "complete"
    assert data["counts"]["writeback_diffs"] == 0
    assert data["counts"]["papers"] == 4
    assert any(check["id"] == "paper_id_contiguous" and check["ok"] for check in data["checks"])
