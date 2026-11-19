"""Stage 9 验收：一致性指标、按文章聚类 bootstrap、三份报告与 manifest。

覆盖四类断言：

1. 指标本身的已知值（κ 完全一致 = 1、完全不一致 ≤ 0、PABAK 对称、ICC 在完全一致时 = 1）；
2. 置信区间的口径（包含点估计、重采样单位是文章、同种子可复现）；
3. 报告内容（run_id/生成时间、逐字段表、错误分析未执行占位、方法学段落占位符）；
4. 失败路径（缺 diff_cells.csv 时给人话而不是堆栈；CLI 退出码）。
"""

from __future__ import annotations

import csv
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import BATCH, REPO, Scenario

from meta_coding.adjudicate.diff import run_diff
from meta_coding.cli import main
from meta_coding.config import load_config
from meta_coding.report.metrics import (
    DEFAULT_BOOTSTRAP_B,
    DEFAULT_SEED,
    cluster_bootstrap_ci,
    cohen_kappa,
    icc_2_1,
    label_of,
    pabak,
    percent_agreement,
    weighted_kappa,
)
from meta_coding.report.render import (
    ReportDataError,
    generate_reports,
    load_field_profiles,
)

FIXED_NOW = datetime(2026, 11, 20, 1, 2, 3, tzinfo=UTC)

# 4 篇论文 × 5 个字段：混合一致/分歧，覆盖分类、有序、连续、文本四种画像。
PAPERS = [
    ("WBIS-001", "10.1000/abc1", "WBIS", [
        ("sample_type", "2", "2", "exact"),
        ("age_group", "4", "3", "mismatch"),
        ("scale_mean", "3.54", "3.54", "exact"),
        ("scale_sd", "1.69", "1.69", "exact"),
        ("mean_calculation", "pub-2", "pub-2", "exact"),
    ]),
    ("WBIS-002", "10.1000/abc2", "WBIS", [
        ("sample_type", "1", "1", "exact"),
        ("age_group", "3", "3", "exact"),
        ("scale_mean", "3.5", "3.7", "mismatch"),
        ("scale_sd", "1.2", "1.2", "exact"),
        ("mean_calculation", "reported", "reported", "exact"),
    ]),
    ("WSSQ-001", "10.1000/abc3", "WSSQ", [
        ("sample_type", "1", "1", "exact"),
        ("age_group", "4", "4", "exact"),
        ("scale_mean", "2.9", "2.9", "exact"),
        ("scale_sd", "", "0.8", "missing_vs_value"),
        ("mean_calculation", "a", "b", "mismatch"),
    ]),
    ("WBIS-003", "10.1000/abc4", "WBIS", [
        ("sample_type", "2", "2", "exact"),
        ("age_group", "1", "1", "exact"),
        ("scale_mean", "4.1", "4.1", "exact"),
        ("scale_sd", "0.9", "0.9", "exact"),
        ("mean_calculation", "x", "x", "exact"),
    ]),
]

DIFF_COLUMNS = ["record_id", "field", "coder_a_value", "coder_b_value", "match_type"]
BLOCK_COLUMNS = [
    "record_id", "sheet", "status", "category", "paper_key", "subsample_label",
    "measure_name", "sex_rows", "a_start_row", "b_start_row", "similarity",
    "match_score", "reason",
]
DISAGREEMENT_COLUMNS = [
    "level", "record_id", "sheet", "field", "coder_a_value", "coder_b_value",
    "match_type", "category", "note",
]


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _cells() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for block_id, _doi, _sheet, fields in PAPERS:
        for field_name, value_a, value_b, match_type in fields:
            rows.append(
                {
                    "record_id": f"{block_id}-s0",
                    "field": field_name,
                    "coder_a_value": value_a,
                    "coder_b_value": value_b,
                    "match_type": match_type,
                }
            )
    return rows


def _build_repo(
    tmp_path: Path,
    *,
    with_blocks: bool = True,
    with_disagreements: bool = True,
    with_configs: bool = False,
) -> Path:
    (tmp_path / "rules").mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "rules" / "fields.yaml", tmp_path / "rules" / "fields.yaml")
    if with_configs:
        shutil.copytree(REPO / "configs", tmp_path / "configs")
    diff_dir = tmp_path / "data" / "05_adjudication"
    diff_dir.mkdir(parents=True, exist_ok=True)
    cells = _cells()
    _write_csv(diff_dir / "diff_cells.csv", DIFF_COLUMNS, cells)

    if with_disagreements:
        rows = [
            {
                "level": "cell",
                "record_id": cell["record_id"],
                "sheet": "WBIS",
                "field": cell["field"],
                "coder_a_value": cell["coder_a_value"],
                "coder_b_value": cell["coder_b_value"],
                "match_type": cell["match_type"],
                "category": "categorical_conflict",
                "note": "",
            }
            for cell in cells
            if cell["match_type"] in ("mismatch", "missing_vs_value")
        ]
        _write_csv(diff_dir / "disagreements.csv", DISAGREEMENT_COLUMNS, rows)

    if with_blocks:
        rows = [
            {
                "record_id": block_id,
                "sheet": sheet,
                "status": "matched",
                "category": "",
                "paper_key": f"doi:{doi}",
                "subsample_label": "Overall sample",
                "measure_name": sheet,
                "sex_rows": "0",
                "a_start_row": "",
                "b_start_row": "",
                "similarity": "",
                "match_score": "",
                "reason": "",
            }
            for block_id, doi, sheet, _fields in PAPERS
        ]
        _write_csv(diff_dir / "blocks.csv", BLOCK_COLUMNS, rows)
    return tmp_path


def _clusters() -> dict[str, list[tuple[str, str]]]:
    return {
        "ref:p1": [("a", "a"), ("b", "b")],
        "ref:p2": [("a", "a"), ("b", "a")],
        "ref:p3": [("a", "b"), ("b", "b")],
        "ref:p4": [("a", "a"), ("a", "b")],
    }


# --------------------------------------------------------------------------- #
# 1. 指标已知值
# --------------------------------------------------------------------------- #


def test_kappa_perfect_agreement_is_one() -> None:
    stats = cohen_kappa([("a", "a"), ("b", "b"), ("a", "a"), ("b", "b")])
    assert stats.kappa == pytest.approx(1.0)
    assert stats.observed == pytest.approx(1.0)
    assert stats.pabak == pytest.approx(1.0)
    assert not stats.paradox


def test_kappa_complete_disagreement_is_not_positive() -> None:
    stats = cohen_kappa([("a", "b"), ("a", "b"), ("b", "a"), ("b", "a")])
    assert stats.observed == pytest.approx(0.0)
    assert stats.kappa == pytest.approx(-1.0)
    assert stats.kappa is not None and stats.kappa <= 0


def test_kappa_undefined_when_only_one_category_present() -> None:
    stats = cohen_kappa([("a", "a"), ("a", "a")])
    assert stats.expected == pytest.approx(1.0)
    assert stats.kappa is None, "Pe=1 时 κ 无定义，不能假装等于 1"
    assert stats.pabak == pytest.approx(1.0)


def test_kappa_paradox_is_flagged_with_pabak() -> None:
    """一致率 95%，但类别高度偏斜 → κ ≈ 0，必须由 paradox 标出并以 PABAK 补充。"""
    pairs = [("a", "a")] * 95 + [("b", "a")] * 5
    stats = cohen_kappa(pairs)
    assert stats.observed == pytest.approx(0.95)
    assert stats.kappa == pytest.approx(0.0)
    assert stats.pabak == pytest.approx(0.9)
    assert stats.paradox is True


def test_pabak_is_symmetric_and_matches_2po_minus_1() -> None:
    pairs = [("a", "a"), ("b", "b"), ("a", "b"), ("b", "b")]
    reversed_pairs = [(right, left) for left, right in pairs]
    assert pabak(pairs) == pytest.approx(2 * percent_agreement(pairs) - 1)
    assert pabak(pairs) == pytest.approx(pabak(reversed_pairs))


def test_weighted_kappa_known_value_and_quadratic_penalty() -> None:
    pairs = [(1, 1), (1, 2), (2, 2), (3, 3), (3, 2)]
    stats = weighted_kappa(pairs, [1, 2, 3])
    # 手算：Po_w = 0.9，Pe_w = 0.7 → κ_w = 0.2 / 0.3 = 2/3
    assert stats.kappa == pytest.approx(2.0 / 3.0)
    assert stats.dropped == 0
    assert weighted_kappa(pairs, [1, 2, 3]).kappa > cohen_kappa(pairs).kappa, (
        "差一级应比完全不一致惩罚更轻，加权 κ 必须高于未加权 κ"
    )
    perfect = [(1, 1), (2, 2), (3, 3)]
    assert weighted_kappa(perfect, [1, 2, 3]).kappa == pytest.approx(1.0)
    with_missing = weighted_kappa([(1, 1), (None, 2)], [1, 2, 3])
    assert with_missing.n == 1 and with_missing.dropped == 1


def test_icc_is_one_when_raters_agree_exactly() -> None:
    stats = icc_2_1([(1.0, 1.0), (2.0, 2.0), (3.0, 3.0), (4.0, 4.0)])
    assert stats.icc == pytest.approx(1.0)
    assert stats.subjects == 4 and stats.raters == 2


def test_icc_matches_known_anova_values_including_negative() -> None:
    # [[0,0],[0,2]]：MSR = 1，MSE = 1，MSC = 1 → ICC(2,1) = 0
    zero = icc_2_1([(0.0, 0.0), (0.0, 2.0)])
    assert zero.icc == pytest.approx(0.0)
    # 手算：MSR=0.25、MSE=0.35、MSC=0.1、n=5、k=2 → (0.25-0.35)/0.5 = -0.2
    negative = icc_2_1([(1.0, 1.0), (1.0, 2.0), (2.0, 1.0), (2.0, 2.0), (1.0, 2.0)])
    assert negative.icc == pytest.approx(-0.2)


def test_percent_agreement_treats_missing_code_and_blank_as_same_label() -> None:
    assert label_of("") == label_of("-999") == label_of(None)
    assert label_of("3.40") == label_of(3.4) == "3.4"
    assert percent_agreement([("", "-999"), ("x", "x")]) == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# 2. Cluster bootstrap
# --------------------------------------------------------------------------- #


def test_default_bootstrap_settings_match_protocol() -> None:
    assert DEFAULT_BOOTSTRAP_B == 2000
    assert DEFAULT_SEED == 20260101


def test_bootstrap_ci_contains_point_estimate() -> None:
    clusters = _clusters()
    point = cohen_kappa([pair for group in clusters.values() for pair in group]).kappa
    assert point is not None
    interval = cluster_bootstrap_ci(
        clusters, lambda sample: cohen_kappa(sample).kappa, b=400, seed=7
    )
    assert interval is not None
    assert interval.low <= point <= interval.high
    assert interval.replicates > 0


def test_bootstrap_resamples_whole_papers_not_cells() -> None:
    """只有一篇论文时，任何整篇重抽都等于原样本 → 区间宽度必须为 0。

    若实现按单元格重采样，同一篇论文内部会变化，区间宽度不可能恒为 0。
    """
    pairs = [("a", "a"), ("b", "b"), ("a", "b"), ("b", "b")]
    interval = cluster_bootstrap_ci(
        {"ref:only": pairs}, lambda sample: cohen_kappa(sample).kappa, b=200, seed=11
    )
    assert interval is not None
    assert interval.width == pytest.approx(0.0)
    assert interval.replicates == 0


def test_bootstrap_is_reproducible_with_same_seed() -> None:
    clusters = _clusters()
    first = cluster_bootstrap_ci(
        clusters, lambda sample: cohen_kappa(sample).kappa, b=300, seed=20260101
    )
    second = cluster_bootstrap_ci(
        clusters, lambda sample: cohen_kappa(sample).kappa, b=300, seed=20260101
    )
    assert first is not None and second is not None
    assert (first.low, first.high, first.replicates) == (
        second.low,
        second.high,
        second.replicates,
    )


# --------------------------------------------------------------------------- #
# 3. 报告内容
# --------------------------------------------------------------------------- #


def test_field_profiles_map_metric_families() -> None:
    profiles = load_field_profiles(REPO / "rules" / "fields.yaml")
    assert profiles["sample_type"].kind == "categorical"
    assert profiles["age_group"].kind == "ordinal"
    assert profiles["age_group"].order == (1, 2, 3, 4)
    assert profiles["scale_mean"].kind == "continuous"
    assert profiles["mean_calculation"].kind == "text"
    assert profiles["sample_type"].label_for(2) == "obese_nonclinical"


def test_report_end_to_end_writes_three_files_and_manifest(tmp_path: Path) -> None:
    root = _build_repo(tmp_path)
    result = generate_reports(root, batch=BATCH, bootstrap_b=150, now=FIXED_NOW)

    assert result.agreement_path.is_file()
    assert result.error_path.is_file()
    assert result.methods_path.is_file()
    assert result.papers == 4 and result.cells == 20 and result.fields == 5
    assert all(check["ok"] for check in result.checks), result.checks

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["stage"] == "09_reports"
    assert manifest["run_id"] == result.run_id
    assert manifest["status"] == "complete"
    assert manifest["created_at"] == result.created_at
    assert manifest["counts"]["cells"] == 20
    assert manifest["counts"]["bootstrap_seed"] == 20260101
    assert result.manifest_path.parent.parent.name == "stage-09_reports"

    for path in (result.agreement_path, result.error_path, result.methods_path):
        head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:8])
        assert result.run_id in head
        assert result.created_at in head


def test_agreement_report_has_per_field_metrics_and_routing(tmp_path: Path) -> None:
    root = _build_repo(tmp_path)
    result = generate_reports(root, batch=BATCH, bootstrap_b=120, now=FIXED_NOW)
    text = result.agreement_path.read_text(encoding="utf-8")

    assert "| 字段 | 类型 | 单元格 | 论文 | 重数 | 一致率 |" in text
    for field_name in ("sample_type", "age_group", "scale_mean", "mean_calculation"):
        assert f"`{field_name}`" in text
    assert "加权 κ" in text          # 有序字段
    assert "ICC(2,1)" in text         # 连续字段
    assert "PABAK" in text            # 分类字段
    assert "分表去向一致率" in text
    assert "100.0%" in text           # 4 个块全部 matched
    assert "B = 120" in text, "报告必须写明实际 bootstrap 次数"


def test_error_analysis_marks_shared_error_rate_not_executed(tmp_path: Path) -> None:
    root = _build_repo(tmp_path)
    result = generate_reports(root, batch=BATCH, bootstrap_b=80, now=FIXED_NOW)
    text = result.error_path.read_text(encoding="utf-8")

    for code in ("E01", "E06", "E10", "E12"):
        assert f"| {code} |" in text
    assert "共享错误率" in text
    assert "未执行" in text
    assert "一致率不等于正确率" in text
    # 没有审计数据时不允许出现编造的百分比作为共享错误率
    shared_section = text.split("## 5. 共享错误率")[1]
    assert "%" not in shared_section.split("## 6.")[0], "无审计数据时不得给出共享错误率数值"


def test_error_analysis_counts_codes_when_adjudication_present(tmp_path: Path) -> None:
    root = _build_repo(tmp_path)
    diff_dir = root / "data" / "05_adjudication"
    adjudication = [
        {
            "field": "scale_mean",
            "full_reference": "Doe 2020 doi:10.1000/abc1",
            "error_types": {"coder_1": ["E01"], "coder_2": []},
            "verdict": "coder1_correct",
        },
        {
            "field": "scale_mean",
            "full_reference": "Roe 2021 doi:10.1000/abc2",
            "error_types": {"coder_1": [], "coder_2": ["E01", "E06"]},
            "verdict": "coder2_correct",
        },
    ]
    (diff_dir / f"{BATCH}_adjudication.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in adjudication) + "\n",
        encoding="utf-8",
    )
    audit = [
        {"field": "scale_sd", "verdict": "both_wrong"},
        {"field": "scale_sd", "verdict": "both_equivalent"},
        {"field": "scale_mean", "verdict": "coder1_correct"},
        {"field": "scale_mean", "verdict": "coder2_correct"},
    ]
    (diff_dir / f"{BATCH}_audit.jsonl").write_text(
        "\n".join(json.dumps(item) for item in audit) + "\n", encoding="utf-8"
    )

    result = generate_reports(root, batch=BATCH, bootstrap_b=80, now=FIXED_NOW)
    text = result.error_path.read_text(encoding="utf-8")

    assert "| E01 | value_extraction | critical | 1 | 1 | 2 |" in text
    assert "| E06 | rule_application | major | 0 | 1 | 1 |" in text
    assert "| `scale_mean` | 3 |" in text  # E01×2 + E06×1，按字段分层
    assert "共享错误率 | 25.0%" in text
    assert result.audit_executed is True


def test_methods_paragraph_uses_placeholders_for_unbound_models(tmp_path: Path) -> None:
    root = _build_repo(tmp_path, with_configs=True)
    config = load_config(root)
    result = generate_reports(
        root, batch=BATCH, config=config, bootstrap_b=80, now=FIXED_NOW
    )
    text = result.methods_path.read_text(encoding="utf-8")

    assert "【待填：编码员 A 的模型与版本】" in text
    assert "【待填：编码员 B 的模型与版本】" in text
    assert "【待填：裁决模型与版本】" in text
    assert "占位符清单" in text
    assert "双模型独立编码" in text and "盲法" in text
    assert "人工复核" in text
    # 占位符总数与清单行数一致
    assert text.count("【待填：") >= result.placeholders


def test_methods_paragraph_uses_bound_model_names(tmp_path: Path) -> None:
    root = _build_repo(tmp_path, with_configs=True)
    models = (root / "configs" / "models.yaml").read_text(encoding="utf-8")
    models = models.replace(
        'coder_a:\n  provider: ""\n  family: ""\n  model: ""',
        'coder_a:\n  provider: "vendor-a"\n  family: "family-a"\n  model: "test-model-a"',
    )
    models = models.replace(
        'coder_b:\n  provider: ""\n  family: ""\n  model: ""',
        'coder_b:\n  provider: "vendor-b"\n  family: "family-b"\n  model: "test-model-b"',
    )
    models = models.replace(
        'adjudicator:\n  provider: ""\n  family: ""\n  model: ""',
        'adjudicator:\n  provider: "vendor-c"\n  family: "family-c"\n  model: "test-model-c"',
    )
    (root / "configs" / "models.yaml").write_text(models, encoding="utf-8")

    config = load_config(root)
    result = generate_reports(
        root, batch=BATCH, config=config, bootstrap_b=80, now=FIXED_NOW
    )
    text = result.methods_path.read_text(encoding="utf-8")

    assert "test-model-a" in text and "test-model-b" in text and "test-model-c" in text
    assert "【待填：编码员 A 的模型与版本】" not in text
    assert result.placeholders < 8


def test_reports_are_byte_identical_with_fixed_clock(tmp_path: Path) -> None:
    root = _build_repo(tmp_path)
    first = generate_reports(root, batch=BATCH, bootstrap_b=100, now=FIXED_NOW)
    texts = {
        path: path.read_text(encoding="utf-8")
        for path in (first.agreement_path, first.error_path, first.methods_path)
    }
    second = generate_reports(root, batch=BATCH, bootstrap_b=100, now=FIXED_NOW)
    assert second.run_id == first.run_id
    for path, text in texts.items():
        assert path.read_text(encoding="utf-8") == text, f"{path.name} 重跑后发生变化"


# --------------------------------------------------------------------------- #
# 4. 缺数据与 CLI
# --------------------------------------------------------------------------- #


def test_missing_diff_cells_raises_actionable_error(tmp_path: Path) -> None:
    (tmp_path / "rules").mkdir(parents=True)
    shutil.copy(REPO / "rules" / "fields.yaml", tmp_path / "rules" / "fields.yaml")
    with pytest.raises(ReportDataError) as excinfo:
        generate_reports(tmp_path, batch=BATCH, bootstrap_b=50, now=FIXED_NOW)
    message = str(excinfo.value)
    assert "diff_cells.csv" in message
    assert f"meta-coding diff --batch {BATCH}" in message


def test_missing_blocks_degrades_and_fails_cluster_check(tmp_path: Path) -> None:
    root = _build_repo(tmp_path, with_blocks=False)
    result = generate_reports(root, batch=BATCH, bootstrap_b=60, now=FIXED_NOW)
    checks = {check["id"]: check for check in result.checks}
    assert checks["cluster_unit_is_paper"]["ok"] is False
    assert checks["disagreements_row_count"]["ok"] is True  # 缺 blocks 时跳过核对
    assert result.blocks == 0
    assert "缺少 `data/05_adjudication/blocks.csv`" in result.agreement_path.read_text(
        encoding="utf-8"
    )
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"


def test_cli_report_without_data_prints_human_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shutil.copytree(REPO / "configs", tmp_path / "configs")
    code = main(["report", "--batch", BATCH, "--repo", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == 2
    assert "diff_cells.csv" in captured.err
    assert f"meta-coding diff --batch {BATCH}" in captured.err
    assert "Traceback" not in captured.err


def test_cli_report_succeeds_and_prints_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _build_repo(tmp_path, with_configs=True)
    code = main(["report", "--batch", BATCH, "--repo", str(root)])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert "manifest：artifacts/stage-09_reports/" in captured.out
    assert "自检：全部通过" in captured.out
    assert list((root / "artifacts" / "stage-09_reports").glob("*/manifest.json"))


def test_report_on_real_stage6_diff_output(
    scenario: Scenario, tmp_path: Path
) -> None:
    """端到端：合成编码工作簿 → Stage 6 diff → Stage 9 报告。"""
    shutil.copytree(REPO / "configs", scenario.root / "configs")
    diff = run_diff(root=scenario.root, batch=scenario.batch)
    assert diff.cells, "Stage 6 应产出比对单元格"

    config = load_config(scenario.root)
    result = generate_reports(
        scenario.root, batch=scenario.batch, config=config, bootstrap_b=100,
        now=FIXED_NOW,
    )

    assert result.cells == len(diff.cells)
    assert result.papers == 4
    assert all(check["ok"] for check in result.checks), result.checks
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert manifest["counts"]["substantive_disagreements"] == len(
        [cell for cell in diff.cells if cell.match_type in ("mismatch", "missing_vs_value")]
    )
    text = result.agreement_path.read_text(encoding="utf-8")
    assert "分表去向一致率" in text
    assert "未执行 / 未报告项" in text
