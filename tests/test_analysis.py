"""Stage 8 验收：长表行单位、-999 不进分析、占位行不导出、完整性与未解决清单。

合成两个编码员 → Stage 7 共识 → Stage 8 导出，检查：
① -999 不进入 analysis_long.csv；② 纯占位行不导出；
③ 一行 = 论文 × 子样本 × 量表 × 性别；④ 数据字典与未解决清单齐备。
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path

from conftest import REPO, Scenario

from meta_coding.analysis.export import (
    ANALYSIS_FIELDS,
    LONG_COLUMNS,
    export_analysis,
)
from meta_coding.cli import main
from meta_coding.consensus.build import build_consensus
from meta_coding.validate.model import load_codebook


def _export(scenario: Scenario):
    consensus = build_consensus(root=scenario.root, batch=scenario.batch)
    result = export_analysis(
        root=scenario.root, batch=scenario.batch, consensus_path=consensus.workbook
    )
    return consensus, result


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def test_long_table_one_row_per_paper_subsample_scale_sex(scenario: Scenario) -> None:
    _, result = _export(scenario)
    rows = _read_csv(result.long_path)

    assert len(rows) == 7  # 6 个已编码总体行 + 1 个已编码女性行
    keys = {
        (row["paper_id"], row["subsample_label"], row["measure_name"], row["sex"])
        for row in rows
    }
    assert len(keys) == len(rows), "长表存在重复的行单位"
    assert result.rows == 7
    assert result.papers == 4
    assert [row.strip() for row in LONG_COLUMNS if row not in rows[0]] == []
    assert {(row["sex"], row["sex_label"]) for row in rows} == {
        ("0", "total"),
        ("1", "female"),
    }


def test_missing_code_never_enters_analysis_long(scenario: Scenario) -> None:
    consensus, result = _export(scenario)

    # 源工作簿里确实存在 -999（P2 Community 的 scale_sd）
    cb = load_codebook(consensus.workbook, scenario.contract)
    community = [
        block
        for block in cb.blocks["WBIS"]
        if block.total is not None and block.total.get("subsample_label") == "Community sample"
    ]
    assert len(community) == 1
    assert community[0].total.get("scale_sd") == -999

    rows = _read_csv(result.long_path)
    for row in rows:
        for name, value in row.items():
            assert value != "-999", f"-999 泄漏到 analysis_long：{name}"
            assert value != "-999.0", f"-999.0 泄漏到 analysis_long：{name}"

    target = next(row for row in rows if row["subsample_label"] == "Community sample")
    assert target["scale_sd"] == ""
    assert target["scale_mean"] == "3.9"
    assert result.missing_cells >= 1


def test_placeholder_rows_are_not_exported(scenario: Scenario) -> None:
    consensus, result = _export(scenario)
    cb = load_codebook(consensus.workbook, scenario.contract)

    total_main_rows = sum(
        len(block.rows) for sheet in ("WBIS", "WSSQ") for block in cb.blocks[sheet]
    )
    assert total_main_rows == 18  # 6 块 × 3 行
    assert result.skipped_placeholder == 11  # 只有 1 个性别行真正编码，其余 11 行是占位

    rows = _read_csv(result.long_path)
    assert len(rows) + result.skipped_placeholder == total_main_rows
    assert {row["sex"] for row in rows} == {"0", "1"}


def test_unencoded_sex_rows_keep_only_sex_and_paper_id(scenario: Scenario) -> None:
    consensus, result = _export(scenario)
    cb = load_codebook(consensus.workbook, scenario.contract)
    for block in cb.all_blocks():
        for row in block.rows:
            if row.get("sex") not in (1, 2):
                continue
            if row.get("scale_mean") is not None or row.get("scale_sd") is not None:
                continue  # 已编码性别行，另由 V35 与长表检查
            extras = {
                name: value
                for name, value in row.values.items()
                if name not in ("sex", "paper_id") and value not in (None, "")
            }
            assert extras == {}, f"占位行被写入内容：{extras}"
    assert result.rows == 7


def test_data_dictionary_documents_every_column(scenario: Scenario) -> None:
    _, result = _export(scenario)
    text = result.dictionary_path.read_text(encoding="utf-8")

    assert "-999" in text
    assert "论文 × 独立子样本 × 量表 × 性别行" in text
    for name in LONG_COLUMNS:
        assert f"`{name}`" in text, f"数据字典缺少列 {name}"


def test_completeness_matrix_covers_all_papers(scenario: Scenario) -> None:
    _, result = _export(scenario)
    rows = _read_csv(result.completeness_path)

    assert [row["paper_id"] for row in rows] == ["1", "2", "3", "4"]
    paper_one = rows[0]
    assert int(paper_one["n_rows"]) == 6  # WBIS + WSSQ 各一个三行块
    assert int(paper_one["n_coded_rows"]) == 2  # 两个总体行有数据，4 行是占位
    assert paper_one["sheets"] == "WBIS;WSSQ"
    for name in ANALYSIS_FIELDS:
        value = paper_one[name]
        assert value == "" or 0.0 <= float(value) <= 1.0
    assert 0.0 <= float(paper_one["overall_pct"]) <= 1.0


def test_unresolved_and_unusable_lists_both_categories(scenario: Scenario) -> None:
    _, result = _export(scenario)
    rows = _read_csv(result.unresolved_path)
    reasons = {row["reason_code"] for row in rows}
    severities = {row["severity"] for row in rows}

    assert "pending_human" in reasons
    assert "missing_sd" in reasons
    assert "missing_n" in reasons
    assert "tracked_no_mean_or_sd" in reasons
    assert severities == {"unresolved", "unusable"}
    assert result.unusable_rows == 2

    pending = [row for row in rows if row["reason_code"] == "pending_human"]
    assert len(pending) == 1
    assert pending[0]["field"] == "sample_n"
    assert pending[0]["paper_id"] == "1"

    tracked = [row for row in rows if row["reason_code"] == "tracked_no_mean_or_sd"]
    assert len(tracked) == 1
    assert tracked[0]["paper_id"] == "2"  # 追踪页条目也能回连到主表 paper_id


def test_export_does_not_modify_consensus_workbook(scenario: Scenario) -> None:
    consensus = build_consensus(root=scenario.root, batch=scenario.batch)
    before = hashlib.sha256(consensus.workbook.read_bytes()).hexdigest()
    export_analysis(
        root=scenario.root, batch=scenario.batch, consensus_path=consensus.workbook
    )
    after = hashlib.sha256(consensus.workbook.read_bytes()).hexdigest()
    assert before == after


def test_row_source_flags_pending_cells(scenario: Scenario) -> None:
    _, result = _export(scenario)
    rows = _read_csv(result.long_path)

    flagged = [row for row in rows if row["row_source"] == "pending_human"]
    assert len(flagged) == 1
    assert flagged[0]["paper_id"] == "1"
    assert flagged[0]["pending_cells"] == "1"
    assert result.pending_cells == 1


def test_cli_qa_writes_all_four_outputs_and_manifest(scenario: Scenario) -> None:
    shutil.copytree(REPO / "configs", scenario.root / "configs")
    assert main(["consensus", "--repo", str(scenario.root), "--batch", scenario.batch]) == 0
    assert main(["qa", "--repo", str(scenario.root), "--batch", scenario.batch]) == 0

    out_dir = scenario.root / "data" / "07_analysis"
    for name in (
        "analysis_long.csv",
        "data_dictionary.md",
        "completeness_matrix.csv",
        "unresolved_and_unusable.csv",
    ):
        assert (out_dir / name).is_file(), f"缺少 {name}"

    manifests = sorted((scenario.root / "artifacts").glob("stage-08_analysis/*/manifest.json"))
    assert len(manifests) == 1
    data = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert data["status"] == "complete"
    assert data["counts"]["rows"] == 7
    assert data["counts"]["skipped_placeholder"] == 11
    assert all(check["ok"] for check in data["checks"])
