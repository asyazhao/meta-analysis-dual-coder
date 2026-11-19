"""Stage 6 确定性验收：块对齐、字段判等、预分类、CLI 与 manifest。

覆盖任务要求的五类场景：
① 同值不同精度 → equivalent，不算分歧；
② ±0.01 边界（0.01 一致、0.02 不一致）；
③ -999 vs 有值 → missing_vs_value；
④ 块数量不同 → block_structure_mismatch，且该块不产生字段级比对；
⑤ 分类结果与 match_type 一致（formatting_only 不进入 disagreements）。

另外验证：块对齐不看数组顺序、no_mean_or_sd 去向差异归 routing_mismatch、
CLI 落三张 CSV 并写 checks 全通过的 manifest。
"""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import pytest
from conftest import BATCH, REF_P2, REF_P4, REPO, Scenario, _blocks, build_workbook

from meta_coding.adjudicate.align import (
    BLOCK_STRUCTURE_MISMATCH,
    align_codebooks,
    subsample_similarity,
)
from meta_coding.adjudicate.classify import (
    CATEGORICAL_CONFLICT,
    EVIDENCE_CONFLICT,
    FORMATTING_ONLY,
    NUMERIC_CONFLICT,
    PRESENCE_CONFLICT,
    ROUTING_MISMATCH,
    classify_cell,
    is_substantive,
)
from meta_coding.adjudicate.diff import (
    DIFF_CELL_COLUMNS,
    EQUIVALENT,
    EXACT,
    MISMATCH,
    MISSING_VS_VALUE,
    NUMERIC_WITHIN_TOLERANCE,
    SKIP_FIELDS,
    compare_values,
    diff_codebooks,
    load_field_specs,
)
from meta_coding.cli import main
from meta_coding.validate.model import Codebook, load_codebook

SPECS = load_field_specs(REPO / "rules" / "fields.yaml")
CONTRACT = REPO / "rules" / "template_contract.json"
FORMATTING_TYPES = {EXACT, EQUIVALENT, NUMERIC_WITHIN_TOLERANCE}


def _load(path: Path) -> Codebook:
    return load_codebook(path, CONTRACT)


def _report(scenario: Scenario):
    return diff_codebooks(
        _load(scenario.coder_a), _load(scenario.coder_b), SPECS, batch=scenario.batch
    )


# --------------------------------------------------------------------------- #
# ① 精度 / 书写差异
# --------------------------------------------------------------------------- #


def test_precision_only_difference_is_equivalent_not_disagreement() -> None:
    assert compare_values(SPECS["scale_mean"], 3.4, "3.40") == EQUIVALENT
    assert compare_values(SPECS["scale_mean"], "3.40", 3.4) == EQUIVALENT
    assert compare_values(SPECS["scale_mean"], 3.4, 3.4) == EXACT
    # 规范化后完全相等即 exact；US / U.S. 属于书写差异 → equivalent
    assert compare_values(SPECS["country"], "US", "U.S.") == EQUIVALENT
    assert compare_values(SPECS["measure_name"], "WBIS-2F", "WBIS 2F") == EQUIVALENT
    assert compare_values(SPECS["subsample_label"], "Overall sample", "overall  sample") == EXACT

    assert classify_cell("scale_mean", numeric=True, match_type=EQUIVALENT) == FORMATTING_ONLY
    assert not is_substantive(EQUIVALENT)


def test_precision_and_missing_codes_through_workbooks(tmp_path: Path) -> None:
    """落表后仍能区分精度差异与缺失码（Excel 读取路径同样生效）。"""
    blocks_a = _blocks(overrides={"p1_wbis": {"scale_mean": "3.40", "scale_sd": -999}})
    blocks_b = _blocks(overrides={"p1_wbis": {"scale_mean": 3.4, "scale_sd": 1.69}})
    path_a = build_workbook(tmp_path / "a.xlsx", blocks=blocks_a)
    path_b = build_workbook(tmp_path / "b.xlsx", blocks=blocks_b)

    report = diff_codebooks(_load(path_a), _load(path_b), SPECS, batch=BATCH)
    precision = [
        cell
        for cell in report.cells
        if cell.field == "scale_mean"
        and {str(cell.value_a), str(cell.value_b)} == {"3.40", "3.4"}
    ]
    assert len(precision) == 1
    assert precision[0].match_type == EQUIVALENT
    assert precision[0].category == FORMATTING_ONLY

    missing = [
        cell
        for cell in report.cells
        if cell.field == "scale_sd" and cell.value_a == -999
    ]
    assert len(missing) == 1
    assert missing[0].match_type == MISSING_VS_VALUE
    assert missing[0].category == PRESENCE_CONFLICT

    assert missing[0] in report.disagreements
    assert precision[0] not in report.disagreements


# --------------------------------------------------------------------------- #
# ② ±0.01 边界
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("value_a", "value_b", "expected"),
    [
        (3.40, 3.41, NUMERIC_WITHIN_TOLERANCE),
        (3.40, 3.42, MISMATCH),
        (0.01, 0.02, NUMERIC_WITHIN_TOLERANCE),
        (0.01, 0.03, MISMATCH),
        (3.40, 3.40, EXACT),
    ],
)
def test_numeric_tolerance_boundary(value_a, value_b, expected) -> None:
    assert compare_values(SPECS["mean_age"], value_a, value_b) == expected


def test_tolerance_does_not_apply_to_categorical_codes() -> None:
    # 枚举即使存成数字也不能被 ±0.01 容差吞掉
    assert compare_values(SPECS["source_type"], 1, 2) == MISMATCH
    assert (
        classify_cell("source_type", numeric=False, match_type=MISMATCH)
        == CATEGORICAL_CONFLICT
    )


# --------------------------------------------------------------------------- #
# ③ 缺失 vs 有值
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("value_a", "value_b"),
    [(-999, 1.69), (1.69, -999), (None, 0), (0, None), ("", 2.5)],
)
def test_missing_vs_value(value_a, value_b) -> None:
    assert compare_values(SPECS["scale_sd"], value_a, value_b) == MISSING_VS_VALUE
    assert (
        classify_cell("scale_sd", numeric=True, match_type=MISSING_VS_VALUE)
        == PRESENCE_CONFLICT
    )


def test_both_missing_is_agreement() -> None:
    assert compare_values(SPECS["scale_sd"], -999, None) == EXACT
    assert compare_values(SPECS["scale_sd"], None, "") == EXACT


# --------------------------------------------------------------------------- #
# ④ 块数量不同：结构错位，且不做字段级比对
# --------------------------------------------------------------------------- #


def test_block_count_mismatch_skips_field_comparison(scenario: Scenario) -> None:
    codebook_a = _load(scenario.coder_a)
    codebook_b = _load(scenario.coder_b)
    alignment = align_codebooks(codebook_a, codebook_b)

    assert len(alignment.blocks_a) == 5
    assert len(alignment.blocks_b) == 6  # B 多一个 P4 WSSQ 块
    assert len(alignment.matched) == 5
    assert len(alignment.unmatched) == 1

    unmatched = alignment.unmatched[0]
    assert unmatched.a is None and unmatched.b is not None
    assert unmatched.b.reference == REF_P4
    assert unmatched.status == BLOCK_STRUCTURE_MISMATCH

    report = diff_codebooks(codebook_a, codebook_b, SPECS, batch=BATCH)
    findings = report.block_findings
    assert len(findings) == 1
    assert findings[0].category == BLOCK_STRUCTURE_MISMATCH
    assert findings[0].record_id == unmatched.record_id

    prefix = f"{unmatched.record_id}-"
    assert not any(cell.record_id.startswith(prefix) for cell in report.cells)
    assert report.checks()[1]["ok"]  # mismatched_blocks_not_compared


def test_alignment_ignores_array_order(tmp_path: Path) -> None:
    """把同一论文的两个子样本块对调；按数组 zip 会 College↔Community 错配。"""
    blocks_a = _blocks()
    blocks_b = _blocks()
    blocks_b[1], blocks_b[2] = blocks_b[2], blocks_b[1]

    path_a = build_workbook(tmp_path / "a.xlsx", blocks=blocks_a)
    path_b = build_workbook(tmp_path / "b.xlsx", blocks=blocks_b)
    alignment = align_codebooks(_load(path_a), _load(path_b))

    assert alignment.unmatched == []
    p2_blocks = [
        pair for pair in alignment.pairs if pair.sheet == "WBIS" and pair.paper.endswith("abc2")
    ]
    assert len(p2_blocks) == 2
    for pair in p2_blocks:
        assert pair.matched
        assert pair.a.subsample == pair.b.subsample
        assert pair.similarity is not None and pair.similarity > 0.75


def test_subsample_similarity_floor() -> None:
    assert subsample_similarity("Overall sample", "Overall") >= 0.9
    assert subsample_similarity("College sample", "College samples") >= 0.95
    assert subsample_similarity("College sample", "Community sample") < 0.75


# --------------------------------------------------------------------------- #
# ⑤ 分类与 match_type 一致
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("field_name", "numeric", "match_type", "category"),
    [
        ("scale_mean", True, EXACT, FORMATTING_ONLY),
        ("scale_mean", True, EQUIVALENT, FORMATTING_ONLY),
        ("scale_mean", True, NUMERIC_WITHIN_TOLERANCE, FORMATTING_ONLY),
        ("scale_mean", True, MISMATCH, NUMERIC_CONFLICT),
        ("sample_n", True, MISMATCH, NUMERIC_CONFLICT),
        ("age_group", False, MISMATCH, CATEGORICAL_CONFLICT),
        ("country", False, MISMATCH, CATEGORICAL_CONFLICT),
        ("mean_calculation", False, MISMATCH, EVIDENCE_CONFLICT),
        ("scale_sd", True, MISSING_VS_VALUE, PRESENCE_CONFLICT),
    ],
)
def test_category_matches_match_type(field_name, numeric, match_type, category) -> None:
    assert classify_cell(field_name, numeric=numeric, match_type=match_type) == category


def test_report_classification_is_consistent(scenario: Scenario) -> None:
    report = _report(scenario)
    for cell in report.cells:
        if cell.match_type in FORMATTING_TYPES:
            assert cell.category == FORMATTING_ONLY
            assert not is_substantive(cell.match_type)
        else:
            assert cell.category != FORMATTING_ONLY
            assert is_substantive(cell.match_type)
    assert all(
        cell.category != FORMATTING_ONLY for cell in report.disagreements
    ), "formatting_only 不得进入 disagreements"


# --------------------------------------------------------------------------- #
# 去向差异与场景级数字
# --------------------------------------------------------------------------- #


def test_tracking_counterpart_is_routing_mismatch(tmp_path: Path) -> None:
    blocks_a = _blocks()
    blocks_b = [block for block in _blocks() if block["name"] != "p2_community"]
    path_a = build_workbook(tmp_path / "a.xlsx", blocks=blocks_a)
    path_b = build_workbook(
        tmp_path / "b.xlsx",
        blocks=blocks_b,
        tracking=[
            {
                "APA_reference": REF_P2,
                "measure_name": "WBIS",
                "subsample_label": "Community sample",
                "sex": 0,
                "missing_statistic": "sd",
            }
        ],
    )

    report = diff_codebooks(_load(path_a), _load(path_b), SPECS, batch=BATCH)
    assert len(report.block_findings) == 1
    finding = report.block_findings[0]
    assert finding.category == ROUTING_MISMATCH
    assert finding.value_a.startswith("Community sample")


def test_scenario_known_disagreements(scenario: Scenario) -> None:
    report = _report(scenario)
    mismatches = [cell for cell in report.cells if cell.match_type == MISMATCH]
    observed = {
        (cell.field, float(cell.value_a), float(cell.value_b)) for cell in mismatches
    }
    assert observed == {
        ("sample_n", 433.0, 430.0),
        ("mean_age", 35.83, 36.0),
        ("scale_mean", 3.5, 3.7),
    }
    assert all(cell.category == NUMERIC_CONFLICT for cell in mismatches)
    assert len(report.disagreements) == 4  # 3 个数值冲突 + 1 个错位块


def test_field_specs_cover_codebook_except_identity_fields() -> None:
    assert len(SPECS) == 28
    compared = [name for name in SPECS if name not in SKIP_FIELDS]
    assert len(compared) == 25
    assert SPECS["mean_age"].numeric
    assert not SPECS["age_group"].numeric
    assert not SPECS["country"].numeric


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_cli_diff_writes_csvs_and_manifest(
    scenario: Scenario, capsys: pytest.CaptureFixture[str]
) -> None:
    shutil.copytree(REPO / "configs", scenario.root / "configs")
    code = main(["diff", "--repo", str(scenario.root), "--batch", scenario.batch])
    out = capsys.readouterr().out

    assert code == 0
    assert "块对齐" in out
    assert "实质分歧" in out
    assert "formatting_only" in out

    out_dir = scenario.root / "data" / "05_adjudication"
    diff_cells = out_dir / "diff_cells.csv"
    disagreements = out_dir / "disagreements.csv"
    blocks = out_dir / "blocks.csv"
    for path in (diff_cells, disagreements, blocks):
        assert path.is_file(), f"缺少产物 {path}"

    with diff_cells.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == DIFF_CELL_COLUMNS
        cell_rows = list(reader)
    assert cell_rows
    assert {row["match_type"] for row in cell_rows} <= {
        EXACT,
        EQUIVALENT,
        NUMERIC_WITHIN_TOLERANCE,
        MISMATCH,
        MISSING_VS_VALUE,
    }

    with disagreements.open(encoding="utf-8", newline="") as fh:
        disagreement_rows = list(csv.DictReader(fh))
    assert len(disagreement_rows) == 4
    assert all(row["category"] != FORMATTING_ONLY for row in disagreement_rows)
    assert sum(row["level"] == "block" for row in disagreement_rows) == 1

    with blocks.open(encoding="utf-8", newline="") as fh:
        block_rows = list(csv.DictReader(fh))
    assert len(block_rows) == 6
    assert sum(row["status"] == BLOCK_STRUCTURE_MISMATCH for row in block_rows) == 1

    manifests = sorted(
        (scenario.root / "artifacts").glob("stage-06_diff/*/manifest.json")
    )
    assert len(manifests) == 1
    data = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert data["status"] == "complete"
    assert data["counts"]["matched_blocks"] == 5
    assert data["counts"]["compared_cells"] == len(cell_rows)
    assert data["counts"]["both_absent_cells"] > 0
    assert data["counts"]["disagreements"] == 4
    assert data["counts"]["by_category"]["numeric_conflict"] == 3
    assert data["counts"]["by_category"]["block_structure_mismatch"] == 1
    assert all(check["ok"] for check in data["checks"])
