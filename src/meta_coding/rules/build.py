"""`meta-coding rules build` — materialise every machine-readable rule artifact.

Deterministic by construction: fields.yaml + template.py are the only inputs, so
re-running on an unchanged rules file produces byte-identical schema/contract/
workbook (the xlsx embeds timestamps, so only the JSON side is compared).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..config import sha256_file
from .contract import check_contract
from .template import generate

SCHEMA_DIR = "schema"
RECORD_SCHEMA = "record.schema.json"
ADJUDICATION_SCHEMA = "adjudication.schema.json"

TYPE_MAP: dict[str, dict[str, Any]] = {
    "string": {"type": "string"},
    "string_or_null": {"type": ["string", "null"]},
    "integer": {"type": "integer"},
    "number_or_missing": {"anyOf": [{"type": "number"}, {"const": -999}]},
    "number_or_null": {"type": ["number", "null"]},
}


@dataclass
class BuildResult:
    fields: int
    schema_path: Path
    adjudication_path: Path
    contract_path: Path
    workbook_path: Path
    problems: list[str]


def _field_schema(field: dict[str, Any]) -> dict[str, Any]:
    kind = field["type"]
    allowed = field.get("allowed")

    if kind == "enum":
        return {"enum": allowed}
    if kind == "enum_or_missing":
        return {"enum": allowed}
    if kind == "integer":
        schema: dict[str, Any] = {"type": "integer"}
        if "range" in field:
            schema["minimum"], schema["maximum"] = field["range"]
        if "minimum" in field:
            schema["minimum"] = field["minimum"]
        return schema
    if kind in TYPE_MAP:
        return dict(TYPE_MAP[kind])
    raise ValueError(f"未知字段类型：{kind}（字段 {field['name']}）")


def build_record_schema(fields_doc: dict[str, Any]) -> dict[str, Any]:
    fields = fields_doc["fields"]
    properties = {f["name"]: _field_schema(f) for f in fields}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://github.com/asyazhao/meta-analysis-dual-coder/record.schema.json",
        "title": "Meta-analysis coding record",
        "description": "一个编码块的单行记录。fields 必须恰好含 28 个字段，一个不多一个不少。",
        "type": "object",
        "required": [
            "record_id",
            "article_key",
            "source_file",
            "sheet",
            "block_id",
            "sex",
            "fields",
            "provenance",
            "flags",
            "record_status",
        ],
        "additionalProperties": False,
        "properties": {
            "record_id": {"type": "string", "minLength": 1},
            "article_key": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
            "source_file": {"type": "string", "minLength": 1},
            "sheet": {"enum": ["WBIS", "WSSQ"]},
            "block_id": {"type": "string", "pattern": "^b[0-9]{2,}$"},
            "sex": {"enum": [0, 1, 2]},
            "fields": {
                "type": "object",
                "additionalProperties": False,
                "required": [f["name"] for f in fields],
                "properties": properties,
            },
            "provenance": {
                "type": "object",
                "additionalProperties": {
                    "type": "object",
                    "required": ["page", "quote"],
                    "properties": {
                        "page": {"type": ["integer", "null"]},
                        "table": {"type": ["string", "null"]},
                        "quote": {"type": "string", "maxLength": 250},
                    },
                },
            },
            "flags": {"type": "array", "items": {"type": "string"}},
            "record_status": {
                "enum": [
                    "coded",
                    "missing_statistics",
                    "not_eligible",
                    "eligibility_unresolved",
                    "source_unreadable",
                    "source_incomplete",
                    "overlap_unresolved",
                ]
            },
        },
    }


ERROR_CODE_SCHEMA: dict[str, Any] = {"enum": [f"E{i:02d}" for i in range(1, 13)]}


def build_adjudication_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://github.com/asyazhao/meta-analysis-dual-coder/adjudication.schema.json",
        "title": "Third-party adjudication of one field-level disagreement",
        "type": "object",
        "required": [
            "independent_value",
            "independent_evidence",
            "verdict",
            "correct_value",
            "error_types",
            "severity",
            "rationale",
            "confidence",
        ],
        "additionalProperties": False,
        "properties": {
            "independent_value": {"type": ["string", "number", "null"]},
            "independent_evidence": {
                "type": "object",
                "required": ["page", "quote"],
                "additionalProperties": False,
                "properties": {
                    "page": {"type": ["integer", "null"]},
                    "table": {"type": ["string", "null"]},
                    "quote": {"type": "string", "maxLength": 250},
                },
            },
            "verdict": {
                "enum": [
                    "coder1_correct",
                    "coder2_correct",
                    "both_equivalent",
                    "both_wrong",
                    "source_ambiguous",
                    "rule_gap",
                    "needs_human",
                ]
            },
            "correct_value": {"type": ["string", "number", "null"]},
            "error_types": {
                "type": "object",
                "properties": {
                    "coder_1": {"type": "array", "items": ERROR_CODE_SCHEMA},
                    "coder_2": {"type": "array", "items": ERROR_CODE_SCHEMA},
                },
                "additionalProperties": False,
            },
            "severity": {"enum": ["critical", "major", "minor", "cosmetic"]},
            "rationale": {"type": "string", "minLength": 1},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "allOf": [
            {
                "if": {"properties": {"verdict": {"const": "both_wrong"}}},
                "then": {"required": ["correct_value"]},
            }
        ],
    }


def verify_rules_lock(rules_dir: Path) -> list[str]:
    lock_path = rules_dir.parent / "configs" / "rules.lock.json"
    plan = rules_dir / "CODING_PLAN.md"
    if not lock_path.is_file():
        return ["缺少 configs/rules.lock.json"]
    if not plan.is_file():
        return ["缺少 rules/CODING_PLAN.md"]
    locked = json.loads(lock_path.read_text(encoding="utf-8"))["sha256"]
    actual = sha256_file(plan)
    if locked != actual:
        return [f"规则副本哈希不符：锁 {locked[:12]}… 实际 {actual[:12]}…"]
    return []


def build(rules_dir: Path) -> BuildResult:
    fields_path = rules_dir / "fields.yaml"
    fields_doc = yaml.safe_load(fields_path.read_text(encoding="utf-8"))
    schemas = rules_dir / SCHEMA_DIR
    schemas.mkdir(parents=True, exist_ok=True)

    record_path = schemas / RECORD_SCHEMA
    record_path.write_text(
        json.dumps(build_record_schema(fields_doc), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    adjudication_path = schemas / ADJUDICATION_SCHEMA
    adjudication_path.write_text(
        json.dumps(build_adjudication_schema(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    generate(rules_dir)
    workbook_path = rules_dir / "clean_codebook_BLANK.xlsx"
    contract_path = rules_dir / "template_contract.json"

    problems = verify_rules_lock(rules_dir)
    problems += check_contract(workbook_path, contract_path)

    return BuildResult(
        fields=len(fields_doc["fields"]),
        schema_path=record_path,
        adjudication_path=adjudication_path,
        contract_path=contract_path,
        workbook_path=workbook_path,
        problems=problems,
    )
