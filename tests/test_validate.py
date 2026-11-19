"""V01–V50：注册表不变量 + 逐条"故意破坏 → 必须命中"。

MUTATIONS 覆盖全部 50 条；新增校验器若忘记写用例，test_every_validator_has_a_case 会失败。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string

from meta_coding.rules.template import generate
from meta_coding.validate.checks_structure import ENUMS
from meta_coding.validate.model import load_codebook
from meta_coding.validate.registry import all_validators, run_all

REPO = Path(__file__).resolve().parents[1]
OTHER_REF = "Roe, A. (2021). Another paper on internalized stigma. Journal, 2(1), 1-9."

GOOD = {
    "full_reference": "Doe, J. (2020). A study of weight bias internalization. Journal, 1(2), 3-4.",
    "subsample_label": "Overall sample",
    "source_type": 1,
    "paper_id": 1,
    "sex": 0,
    "country": "US",
    "sample_type": 1,
    "sampling_method": 2,
    "publication_year": 2020,
    "year_data_collection": 2018,
    "year_imputed_rule": 3,
    "sample_n": 433,
    "mean_age": 35.83,
    "age_group": 4,
    "mean_bmi": 28.98,
    "measure_name": "WBIS",
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


def _cols(rules_dir: Path) -> dict[str, int]:
    contract = yaml.safe_load((rules_dir / "template_contract.json").read_text())
    return {name: column_index_from_string(letter)
            for letter, name in contract["main"]["headers"].items()}


def _first_row(ws) -> dict[str, int]:
    return {c.value: c.column for c in next(ws.iter_rows(min_row=1, max_row=1)) if c.value}


def build(tmp_path: Path, *, overrides: dict | None = None, wssq: dict | None = None,
          tracking: list[dict] | None = None, excluded: list[dict] | None = None) -> Path:
    """一个 WBIS 块（总体行齐全、两个性别行未编码）；可选地在 WSSQ 加第二个块。"""
    rules = tmp_path / "rules"
    rules.mkdir(parents=True, exist_ok=True)
    for name in ("fields.yaml", "CODING_PLAN.md"):
        shutil.copy(REPO / "rules" / name, rules / name)
    generate(rules)
    cols = _cols(rules)

    values = dict(GOOD)
    values.update(overrides or {})

    wb = load_workbook(rules / "clean_codebook_BLANK.xlsx")
    ws = wb["WBIS"]
    for offset, sex in enumerate((0, 1, 2)):
        r = 2 + offset
        for name, column in cols.items():
            if name == "sex":
                continue
            if sex != 0 and name not in ("paper_id",):
                continue
            value = values.get(name)
            if sex != 0 and name == "paper_id":
                value = values.get("paper_id")
            ws.cell(row=r, column=column, value=value)

    if wssq:
        ws2 = wb["WSSQ"]
        row_values = dict(values)
        row_values.update(wssq)
        for offset, sex in enumerate((0, 1, 2)):
            r = 2 + offset
            for name, column in cols.items():
                if name == "sex":
                    continue
                if sex != 0 and name not in ("paper_id",):
                    continue
                value = row_values.get(name)
                if sex != 0 and name == "paper_id":
                    value = row_values.get("paper_id")
                ws2.cell(row=r, column=column, value=value)

    if tracking:
        ws_t = wb["no_mean_or_sd"]
        header = _first_row(ws_t)
        for i, item in enumerate(tracking):
            for name, value in item.items():
                ws_t.cell(row=2 + i, column=header[name], value=value)

    if excluded:
        ws_e = wb["not_eligible"]
        for i, item in enumerate(excluded):
            ws_e.cell(row=2 + i, column=1, value=item.get("full_reference"))
            ws_e.cell(row=2 + i, column=2, value=item.get("reason"))

    path = rules / "codebook.xlsx"
    wb.save(path)
    return path


def run(path: Path, ctx: dict | None = None):
    cb = load_codebook(path, path.parent / "template_contract.json")
    return run_all(cb, ctx or {})


def test_all_fifty_registered() -> None:
    ids = [v.vid for v in all_validators()]
    assert ids == [f"V{i:02d}" for i in range(1, 51)]
    assert len({v.vid for v in all_validators()}) == 50


def test_every_validator_declares_chapter_and_kind() -> None:
    for validator in all_validators():
        assert validator.chapter.startswith("§"), validator.vid
        assert validator.kind in {"auto", "semi"}, validator.vid


def test_enums_match_fields_yaml() -> None:
    doc = yaml.safe_load((REPO / "rules" / "fields.yaml").read_text(encoding="utf-8"))
    for field in doc["fields"]:
        if field["name"] in ENUMS:
            expected = [x for x in field["allowed"] if x != -999]
            assert ENUMS[field["name"]] == expected, field["name"]


def test_clean_fixture_has_no_violations(tmp_path: Path) -> None:
    path = build(tmp_path)
    violations = run(path, {"articles": [GOOD["full_reference"]]})
    assert [x.as_dict() for x in violations] == []


# ---------- 逐条破坏用例 ----------

def _mut(sheet: str, row: int, **fields):
    def apply(rules_dir: Path) -> None:
        path = rules_dir / "codebook.xlsx"
        wb = load_workbook(path)
        cols = _cols(rules_dir)
        for name, value in fields.items():
            wb[sheet].cell(row=row, column=cols[name]).value = value
        wb.save(path)

    return apply


def _set_all(sheet: str, **fields):
    def apply(rules_dir: Path) -> None:
        path = rules_dir / "codebook.xlsx"
        wb = load_workbook(path)
        ws = wb[sheet]
        header = _first_row(ws)
        for row in range(2, 5):
            for name, value in fields.items():
                ws.cell(row=row, column=header[name]).value = value
        wb.save(path)

    return apply


def _sheet(fn):
    def apply(rules_dir: Path) -> None:
        path = rules_dir / "codebook.xlsx"
        wb = load_workbook(path)
        fn(wb)
        wb.save(path)

    return apply


def cases() -> dict[str, dict]:
    return {
        "V01": {"mut": _sheet(lambda wb: wb.remove(wb["not_eligible"]))},
        "V02": {"mut": _mut("WBIS", 1, full_reference="ref")},
        "V03": {"mut": _mut("WBIS", 4, sex=1)},
        "V04": {"mut": _sheet(lambda wb: wb["WBIS"].cell(row=5, column=2, value="x"))},
        "V05": {"mut": _mut("WBIS", 3, country="US")},
        "V06": {"mut": _mut("WBIS", 3, subsample_label="Other")},
        "V07": {"mut": _mut("WBIS", 3, paper_id=9)},
        "V08": {"mut": _sheet(lambda wb: wb["not_eligible"].cell(row=1, column=3, value="x"))},
        "V09": {"mut": _mut("WBIS", 2, source_type=9)},
        "V10": {"mut": _mut("WBIS", 2, sex=1)},
        "V11": {"mut": _mut("WBIS", 2, age_group=1)},
        "V12": {"mut": _mut("WBIS", 2, country="United States")},
        "V13": {"mut": _mut("WBIS", 2, sample_type=3)},
        "V14": {"mut": _mut("WBIS", 2, clinical_type="eating disorder")},
        "V15": {"mut": _mut("WBIS", 2, sample_type=4, mean_calculation=None)},
        "V16": {"mut": _mut("WBIS", 2, sample_type=2, mean_calculation="mixed BMI sample")},
        "V17": {"mut": _mut("WBIS", 2, scale_total=77.0)},
        "V18": {"mut": _mut("WBIS", 2, items_n=-999, mean_calculation="total/11 = 3.54")},
        "V19": {"mut": _mut("WBIS", 2, response_scale_min=9)},
        "V20": {"mut": _mut("WBIS", 2, mean_calculation="")},
        "V21": {"mut": _mut("WBIS", 2, publication_year=2031)},
        "V22": {"mut": _mut("WBIS", 2, year_data_collection=2010)},
        "V23": {"mut": _mut("WBIS", 2, year_imputed_rule=2, year_data_collection=2014,
                            mean_calculation="collected 2014.5 midpoint")},
        "V24": {"mut": _mut("WBIS", 2, publication_year=-999)},
        "V25": {"mut": _mut("WBIS", 2, mean_calculation="follow-up measurement")},
        "V26": {"mut": _mut("WBIS", 2, items_n=None)},
        "V27": {"mut": _mut("WBIS", 2, mean_calculation="subscale only")},
        "V28": {"mut": _mut("WBIS", 2, scale_sd=25.0)},
        "V29": {"mut": _mut("WBIS", 2, mean_calculation="used -999 in pooled formula")},
        "V30": {"mut": _mut("WBIS", 2, scale_mean=-999)},
        "V31": {"mut": _mut("WBIS", 2, scale_sd=-999)},
        "V32": {"mut": _sheet(lambda wb: wb["no_mean_or_sd"].cell(row=2, column=8, value=1))},
        "V33": {"tracking": [
            {"paper_title": "T", "APA_reference": GOOD["full_reference"], "measure_name": "WBIS",
             "subsample_label": "Overall sample", "sex": 0, "missing_statistic": "sd"},
            {"paper_title": "T", "APA_reference": GOOD["full_reference"], "measure_name": "WBIS",
             "subsample_label": "Overall sample", "sex": 0, "missing_statistic": "sd"},
        ]},
        "V34": {"tracking": [
            {"paper_title": "T", "APA_reference": GOOD["full_reference"], "measure_name": "WSSQ",
             "subsample_label": "Nowhere sample", "sex": 0, "missing_statistic": "sd"},
        ]},
        "V35": {"mut": _mut("WBIS", 3, scale_sd=-999)},
        "V36": {"excluded": [{"full_reference": "Some ref", "reason": "n/a"}]},
        "V37": {"tracking": [
            {"paper_title": "T", "APA_reference": GOOD["full_reference"], "measure_name": "WSSQ",
             "subsample_label": "Orphan sample", "sex": 0, "missing_statistic": "sd"},
        ]},
        "V38": {"wssq": {"full_reference": OTHER_REF, "paper_id": 3}},
        "V39": {"wssq": {"paper_id": 2}},
        "V40": {"wssq": {"full_reference": OTHER_REF, "paper_id": 1}},
        "V41": {"wssq": {"full_reference": OTHER_REF, "paper_id": 1}},
        "V42": {"mut": _sheet(
            lambda wb: wb["no_mean_or_sd"].cell(row=1, column=11, value="paper_id"))},
        "V43": {"mut": _set_all("WBIS", paper_id=2)},
        "V44": {"doi_ref": True},
        "V45": {"mut": _mut("WBIS", 2, mean_calculation="pooled across groups")},
        "V46": {"mut": _mut("WBIS", 2, mean_calculation="pooled SD averaged")},
        "V47": {"mut": _mut("WBIS", 2, scale_mean=3.14159)},
        "V48": {"articles": ["Ghost, X. (2019). Missing paper."]},
        "V49": {"duplicate_block": True},
        "V50": {"mut": _mut("WBIS", 2, mean_calculation="conflicting values in table")},
    }


def test_every_validator_has_a_case() -> None:
    assert set(cases()) == {f"V{i:02d}" for i in range(1, 51)}


@pytest.mark.parametrize("vid", [f"V{i:02d}" for i in range(1, 51)])
def test_validator_fires(vid: str, tmp_path: Path) -> None:
    spec = cases()[vid]
    path = build(tmp_path, wssq=spec.get("wssq"), tracking=spec.get("tracking"),
                 excluded=spec.get("excluded"))

    if spec.get("duplicate_block"):
        wb = load_workbook(path)
        ws = wb["WBIS"]
        for offset in range(3):
            for column in range(1, 34):
                ws.cell(row=6 + offset, column=column,
                        value=ws.cell(row=2 + offset, column=column).value)
        wb.save(path)

    if spec.get("doi_ref"):
        wb = load_workbook(path)
        ws = wb["WBIS"]
        header = _first_row(ws)
        ws.cell(row=2, column=header["full_reference"],
                value="Doe, J. (2020). Study. https://doi.org/10.1/xyz")
        wb.save(path)

    if "mut" in spec:
        spec["mut"](tmp_path / "rules")

    ctx = {
        "articles": spec.get("articles", [GOOD["full_reference"]]),
        "registry": [{"doi": "10.1/xyz", "batch_id": "batch00"}] if spec.get("doi_ref") else [],
        "registry_batch": "batch00",
        "batch_id": "batch01",
    }
    hits = {x.vid for x in run(path, ctx)}
    assert vid in hits, f"{vid} 未触发；实际命中 {sorted(hits)}"
