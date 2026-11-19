"""测试夹具：从空白模板合成两个编码员的工作簿、裁决记录与人工修改表。

只在测试里构造“像真实编码结果”的工作簿；不触碰 rules/ 下的任何规则文件。
"""

from __future__ import annotations

import csv
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml
from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string

REPO = Path(__file__).resolve().parents[1]
BATCH = "batch01"

REF_P1 = (
    "Doe, J. (2020). Weight bias internalization in adults. "
    "Journal of Testing, 1(2), 3-4. https://doi.org/10.1000/abc1"
)
REF_P1_ALT = (
    "Doe, J. (2020). Weight bias internalization in adults. "
    "J Test, 1(2), 3-4. https://doi.org/10.1000/abc1"
)
REF_P2 = (
    "Roe, A. (2021). Internalized weight stigma in students. "
    "Journal of Testing, 2(1), 1-9. https://doi.org/10.1000/abc2"
)
REF_P3 = (
    "Moe, B. (2022). Weight self-stigma in community samples. "
    "Journal of Testing, 3(3), 10-20. https://doi.org/10.1000/abc3"
)
REF_P4 = (
    "Poe, C. (2023). Weight bias internalization in adolescents. "
    "Journal of Testing, 4(1), 5-15. https://doi.org/10.1000/abc4"
)


def _columns() -> dict[str, int]:
    contract = yaml.safe_load((REPO / "rules" / "template_contract.json").read_text())
    return {
        name: column_index_from_string(letter)
        for letter, name in contract["main"]["headers"].items()
    }


COLUMNS = _columns()


def _write_block(ws, start: int, block: dict) -> None:
    """写一个三行块；未编码的性别行只写 sex，不写其它字段。"""
    for offset, sex in enumerate((0, 1, 2)):
        row_index = start + offset
        ws.cell(row=row_index, column=COLUMNS["sex"], value=sex)
        values = block.get("rows", {}).get(sex, {})
        if sex == 0:
            ws.cell(row=row_index, column=COLUMNS["full_reference"], value=block["reference"])
            ws.cell(row=row_index, column=COLUMNS["subsample_label"], value=block["subsample"])
            ws.cell(row=row_index, column=COLUMNS["measure_name"], value=block["measure"])
        for name, value in values.items():
            ws.cell(row=row_index, column=COLUMNS[name], value=value)


def build_workbook(path: Path, *, blocks: list[dict], tracking=None, excluded=None) -> Path:
    wb = load_workbook(REPO / "rules" / "clean_codebook_BLANK.xlsx")
    for sheet in ("WBIS", "WSSQ"):
        for row in wb[sheet].iter_rows(min_row=2):
            for cell in row:
                cell.value = None
    starts = {"WBIS": 2, "WSSQ": 2}
    for block in blocks:
        sheet = block["sheet"]
        _write_block(wb[sheet], starts[sheet], block)
        starts[sheet] += 4  # 三行块 + 一个空行

    if tracking:
        ws = wb["no_mean_or_sd"]
        header = {c.value: c.column for c in next(ws.iter_rows(min_row=1, max_row=1)) if c.value}
        for offset, item in enumerate(tracking):
            for name, value in item.items():
                ws.cell(row=2 + offset, column=header[name], value=value)

    if excluded:
        ws = wb["not_eligible"]
        for offset, item in enumerate(excluded):
            ws.cell(row=2 + offset, column=1, value=item.get("full_reference"))
            ws.cell(row=2 + offset, column=2, value=item.get("reason"))

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def _blocks(overrides: dict[str, dict] | None = None) -> list[dict]:
    """标准编码方案；overrides 按块名替换字段，用于制造 A/B 差异。"""
    overrides = overrides or {}
    blocks: list[dict] = []

    def add(name: str, sheet: str, reference: str, subsample: str, measure: str, rows: dict):
        rows = {int(sex): dict(values) for sex, values in rows.items()}
        rows.setdefault(0, {}).update(overrides.get(name, {}))
        blocks.append(
            {
                "name": name,
                "sheet": sheet,
                "reference": reference,
                "subsample": subsample,
                "measure": measure,
                "rows": rows,
            }
        )

    add(
        "p1_wbis",
        "WBIS",
        REF_P1,
        "Overall sample",
        "WBIS",
        {
            0: {
                "source_type": 1,
                "country": "US",
                "sample_type": 2,
                "sampling_method": 2,
                "publication_year": 2020,
                "year_data_collection": 2018,
                "year_imputed_rule": 3,
                "sample_n": 433,
                "mean_age": 35.83,
                "age_group": 4,
                "mean_bmi": 28.98,
                "translated_flag": 2,
                "scale_comparable_flag": 1,
                "items_n": 11,
                "response_scale_min": 1,
                "response_scale_max": 7,
                "direction": 1,
                "scale_mean": 3.54,
                "scale_sd": 1.69,
                "mean_calculation": "Year: publication_year - 2 = 2018.",
            }
        },
    )
    add(
        "p2_college",
        "WBIS",
        REF_P2,
        "College sample",
        "WBIS",
        {
            0: {
                "source_type": 2,
                "country": "UK",
                "sample_type": 1,
                "sampling_method": 2,
                "publication_year": 2021,
                "year_data_collection": 2019,
                "year_imputed_rule": 3,
                "sample_n": 210,
                "mean_age": 20.1,
                "age_group": 3,
                "mean_bmi": 22.4,
                "translated_flag": 2,
                "scale_comparable_flag": 2,
                "items_n": 11,
                "response_scale_min": 1,
                "response_scale_max": 7,
                "direction": 1,
                "scale_mean": 3.5,
                "scale_sd": 1.2,
                "mean_calculation": "Year: publication_year - 2 = 2019.",
            }
        },
    )
    add(
        "p2_community",
        "WBIS",
        REF_P2,
        "Community sample",
        "WBIS",
        {
            0: {
                "source_type": 2,
                "country": "UK",
                "sample_type": 2,
                "sampling_method": 2,
                "publication_year": 2021,
                "year_data_collection": 2019,
                "year_imputed_rule": 3,
                "sample_n": 150,
                "mean_age": 41.0,
                "age_group": 4,
                "mean_bmi": 27.0,
                "translated_flag": 2,
                "scale_comparable_flag": 2,
                "items_n": 11,
                "response_scale_min": 1,
                "response_scale_max": 7,
                "direction": 1,
                "scale_mean": 3.9,
                "scale_sd": -999,
                "mean_calculation": "Year: publication_year - 2 = 2019.",
            }
        },
    )
    add(
        "p3_wssq",
        "WSSQ",
        REF_P3,
        "Overall sample",
        "WSSQ",
        {
            0: {
                "source_type": 1,
                "country": "US",
                "sample_type": 1,
                "sampling_method": 1,
                "publication_year": 2022,
                "year_data_collection": 2020,
                "year_imputed_rule": 3,
                "sample_n": 300,
                "mean_age": 25.0,
                "age_group": 3,
                "mean_bmi": 24.0,
                "translated_flag": 2,
                "scale_comparable_flag": 1,
                "items_n": 11,
                "response_scale_min": 1,
                "response_scale_max": 5,
                "direction": 2,
                "scale_mean": 2.9,
                "scale_sd": 0.8,
                "mean_calculation": "Year: publication_year - 2 = 2020.",
            },
            # 已编码的性别行：mean 与 SD 都有，导出时应单独成为一行
            1: {
                "source_type": 1,
                "country": "US",
                "sample_type": 1,
                "sampling_method": 1,
                "publication_year": 2022,
                "year_data_collection": 2020,
                "year_imputed_rule": 3,
                "sample_n": 160,
                "mean_age": 24.5,
                "age_group": 3,
                "mean_bmi": 23.0,
                "translated_flag": 2,
                "scale_comparable_flag": 1,
                "items_n": 11,
                "response_scale_min": 1,
                "response_scale_max": 5,
                "direction": 2,
                "scale_mean": 3.05,
                "scale_sd": 0.85,
                "mean_calculation": "Year: publication_year - 2 = 2020.",
            },
        },
    )
    add(
        "p1_wssq",
        "WSSQ",
        REF_P1,
        "Overall sample",
        "WSSQ",
        {
            0: {
                "source_type": 1,
                "country": "US",
                "sample_type": 2,
                "sampling_method": 2,
                "publication_year": 2020,
                "year_data_collection": 2018,
                "year_imputed_rule": 3,
                "sample_n": 433,
                "mean_age": 35.83,
                "age_group": 4,
                "mean_bmi": 28.98,
                "translated_flag": 2,
                "scale_comparable_flag": 1,
                "items_n": 12,
                "response_scale_min": 1,
                "response_scale_max": 5,
                "direction": 2,
                "scale_mean": 3.0,
                "scale_sd": 0.95,
                "mean_calculation": "Year: publication_year - 2 = 2018.",
            }
        },
    )
    return blocks


@dataclass
class Scenario:
    root: Path
    batch: str
    coder_a: Path
    coder_b: Path
    adjudication: Path
    manual: Path
    contract: Path


@pytest.fixture
def scenario(tmp_path: Path) -> Scenario:
    """两个编码员 + 裁决 + 人工修改的最小可复现场景。"""
    rules = tmp_path / "rules"
    rules.mkdir(parents=True, exist_ok=True)
    for name in ("clean_codebook_BLANK.xlsx", "template_contract.json", "fields.yaml"):
        shutil.copy(REPO / "rules" / name, rules / name)

    # 编码员 A：标准方案
    coder_a = build_workbook(
        tmp_path / "data" / "03_coded" / "coder_a" / f"{BATCH}_codebook.xlsx",
        blocks=_blocks(),
        tracking=[
            {
                "paper_title": "Internalized weight stigma in students",
                "APA_reference": REF_P2,
                "measure_name": "WBIS",
                "subsample_label": "Community sample",
                "sex": 0,
                "missing_statistic": "sd",
                "notes": "仅报告均值",
            }
        ],
    )

    # 编码员 B：三处差异
    #   1) P1 sample_n 430（无裁决 → pending_human）
    #   2) P1 mean_age 36.0（裁决 → 35.83）
    #   3) P2 College scale_mean 3.7（人工 → 3.5）
    # 另加一个只在 B 出现的 WSSQ 块（P4），用于验证编号接续
    b_blocks = _blocks(
        overrides={
            "p1_wbis": {"sample_n": 430, "mean_age": 36.0},
            "p2_college": {"scale_mean": 3.7},
        }
    )
    b_blocks[4]["reference"] = REF_P1_ALT  # P1 的 WSSQ 块引用写法不同，但 DOI 相同
    b_blocks.append(
        {
            "name": "p4_wssq",
            "sheet": "WSSQ",
            "reference": REF_P4,
            "subsample": "Overall sample",
            "measure": "WSSQ",
            "rows": {
                0: {
                    "source_type": 1,
                    "country": "US",
                    "sample_type": 1,
                    "sampling_method": 2,
                    "publication_year": 2023,
                    "year_data_collection": 2021,
                    "year_imputed_rule": 3,
                    "sample_n": 180,
                    "mean_age": 16.2,
                    "age_group": 2,
                    "mean_bmi": 23.0,
                    "translated_flag": 2,
                    "scale_comparable_flag": 1,
                    "items_n": 12,
                    "response_scale_min": 1,
                    "response_scale_max": 5,
                    "direction": 2,
                    "scale_mean": 3.1,
                    "scale_sd": 0.9,
                    "mean_calculation": "Year: publication_year - 2 = 2021.",
                }
            },
        }
    )
    coder_b = build_workbook(
        tmp_path / "data" / "03_coded" / "coder_b" / f"{BATCH}_codebook.xlsx",
        blocks=b_blocks,
    )

    adjudication = tmp_path / "data" / "05_adjudication" / f"{BATCH}_adjudication.jsonl"
    adjudication.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "sheet": "WBIS",
            "full_reference": REF_P1,
            "subsample_label": "Overall sample",
            "measure_name": "WBIS",
            "sex": 0,
            "field": "mean_age",
            "value": 35.83,
            "decided_by": "adjudicator",
            "reason": "Table 2 报告 35.83；B 的 36.0 为摘要四舍五入",
        }
    ]
    adjudication.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n",
        encoding="utf-8",
    )

    manual = tmp_path / "data" / "06_consensus" / f"{BATCH}_manual_overrides.csv"
    manual.parent.mkdir(parents=True, exist_ok=True)
    with manual.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "sheet",
                "full_reference",
                "subsample_label",
                "measure_name",
                "sex",
                "field",
                "value",
                "note",
            ]
        )
        writer.writerow(
            [
                "WBIS",
                REF_P2,
                "College sample",
                "WBIS",
                0,
                "scale_mean",
                3.5,
                "人工复核表 3：3.5（B 误读为 3.7）",
            ]
        )
        writer.writerow(
            [
                "WSSQ",
                REF_P3,
                "Overall sample",
                "WSSQ",
                0,
                "items_n",
                12,
                "人工复核：WSSQ 共 12 条",
            ]
        )

    return Scenario(
        root=tmp_path,
        batch=BATCH,
        coder_a=coder_a,
        coder_b=coder_b,
        adjudication=adjudication,
        manual=manual,
        contract=rules / "template_contract.json",
    )
