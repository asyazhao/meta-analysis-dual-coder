"""规则包（Rule Pack）：把「规则文档」变成可审阅、可锁定的字段契约。

契约见 ``docs/RULE_PACK_SPEC.md``。一个规则包 = 五件产物：

    rulepacks/<pack_id>/
    ├── pack.json                 # 来源、生成方式、状态、pack_hash
    ├── fields.yaml               # 字段清单（名称/类型/取值/缺失规则/出处）
    ├── decisions.yaml            # 分支决策
    ├── schema/record.schema.json # 由 fields.yaml 生成
    └── template_contract.json    # 列位契约

生成有两条路径，走同一个入口 ``create_pack``：
* ``mode="mock"``：离线示例生成器，读文档正文抽 4 个字段，段落原文即 quote；
* ``mode="model"``：调用已绑定的模型槽位，让模型按同一 JSON 契约产出。

两条路径产出同一种规则包，UI 与测试不需要区分。
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ..config import ModelSlot
from . import store

PACK_ROOT = Path("rulepacks")
TYPE_CHOICES = ("string", "integer", "number", "enum", "boolean")
MISSING_CHOICES = (
    "missing_code",
    "never_missing",
    "never_missing_when_coded",
    "optional",
)
PROMPT_VERSION = "rulepack_v1"
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,60}$")
PACK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,39}$")
MOCK_FIELDS = (
    ("study_id", "string", "never_missing_when_coded"),
    ("sample_size", "integer", "missing_code"),
    ("mean_value", "number", "missing_code"),
    ("sd_value", "number", "missing_code"),
)


class RulePackError(ValueError):
    """规则包无法生成/保存时抛出，消息直接给人看。"""


@dataclass
class PackInfo:
    pack_id: str
    status: str
    field_count: int
    source_path: str
    source_sha256: str
    generated_model: str
    created_at: str
    pack_hash: str
    mock: bool
    human_edits: int = 0
    confirmed_at: str = ""
    gaps: list[str] = field(default_factory=list)

    @property
    def confirmed(self) -> bool:
        return self.status == "confirmed"

    @property
    def badge(self) -> str:
        return "已确认" if self.confirmed else "草稿"

    def as_dict(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "status": self.status,
            "badge": self.badge,
            "field_count": self.field_count,
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "generated_model": self.generated_model,
            "created_at": self.created_at,
            "pack_hash": self.pack_hash,
            "mock": self.mock,
            "human_edits": self.human_edits,
            "confirmed_at": self.confirmed_at,
            "confirmed": self.confirmed,
        }


# --------------------------------------------------------------------------- #
# 读写
# --------------------------------------------------------------------------- #


def pack_dir(root: Path, pack_id: str) -> Path:
    if not PACK_ID_RE.match(pack_id or ""):
        raise RulePackError("规则包 id 不合法")
    return root / PACK_ROOT / pack_id


def load_pack(root: Path, pack_id: str) -> PackInfo:
    directory = pack_dir(root, pack_id)
    raw = store.read_json(directory / "pack.json")
    if not isinstance(raw, dict):
        raise RulePackError(f"规则包不存在：{pack_id}")
    source = raw.get("source_doc") if isinstance(raw.get("source_doc"), dict) else {}
    generated = raw.get("generated_by") if isinstance(raw.get("generated_by"), dict) else {}
    return PackInfo(
        pack_id=str(raw.get("pack_id") or pack_id),
        status=str(raw.get("status") or "draft"),
        field_count=int(raw.get("field_count") or 0),
        source_path=str(source.get("path") or ""),
        source_sha256=str(source.get("sha256") or ""),
        generated_model=str(generated.get("model") or ""),
        created_at=str(raw.get("created_at") or generated.get("at") or ""),
        pack_hash=str(raw.get("pack_hash") or ""),
        mock=bool(raw.get("mock")),
        human_edits=int(raw.get("human_edits") or 0),
        confirmed_at=str(raw.get("confirmed_at") or ""),
        gaps=[str(item) for item in (raw.get("gaps") or [])],
    )


def list_packs(root: Path) -> list[PackInfo]:
    base = root / PACK_ROOT
    if not base.is_dir():
        return []
    packs: list[PackInfo] = []
    for child in base.iterdir():
        if not child.is_dir() or child.name.startswith("_"):
            continue
        if not (child / "pack.json").is_file():
            continue
        try:
            packs.append(load_pack(root, child.name))
        except RulePackError:
            continue
    return sorted(packs, key=lambda item: item.created_at, reverse=True)


def read_fields(root: Path, pack_id: str) -> list[dict[str, Any]]:
    path = pack_dir(root, pack_id) / "fields.yaml"
    if not path.is_file():
        raise RulePackError(f"规则包缺少 fields.yaml：{pack_id}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    fields = raw.get("fields") if isinstance(raw, dict) else None
    if not isinstance(fields, list):
        return []
    return [item for item in fields if isinstance(item, dict)]


def read_decisions(root: Path, pack_id: str) -> list[dict[str, Any]]:
    path = pack_dir(root, pack_id) / "decisions.yaml"
    if not path.is_file():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    decisions = raw.get("decisions") if isinstance(raw, dict) else None
    if isinstance(decisions, list):
        return [item for item in decisions if isinstance(item, dict)]
    return []


def _read_contract(root: Path, pack_id: str) -> dict[str, Any]:
    raw = store.read_json(pack_dir(root, pack_id) / "template_contract.json")
    return raw if isinstance(raw, dict) else {}


def _read_schema(root: Path, pack_id: str) -> dict[str, Any]:
    raw = store.read_json(pack_dir(root, pack_id) / "schema" / "record.schema.json")
    return raw if isinstance(raw, dict) else {}


# --------------------------------------------------------------------------- #
# 字段规范化与派生产物
# --------------------------------------------------------------------------- #


def column_letter(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def normalise_field(raw: dict[str, Any], index: int) -> dict[str, Any]:
    name = str(raw.get("name") or "").strip()
    if not NAME_RE.match(name):
        raise RulePackError(
            f"字段名「{name or '(空)'}」不合法：小写字母开头，只含小写字母/数字/下划线"
        )
    kind = str(raw.get("type") or "string").strip()
    if kind not in TYPE_CHOICES:
        raise RulePackError(f"字段 {name} 的类型 {kind} 不在 {TYPE_CHOICES}")

    allowed_raw = raw.get("allowed")
    if isinstance(allowed_raw, str):
        allowed = [part.strip() for part in allowed_raw.split(",") if part.strip()]
    elif isinstance(allowed_raw, list):
        allowed = [str(part).strip() for part in allowed_raw if str(part).strip()]
    else:
        allowed = []
    if kind == "enum" and not allowed:
        raise RulePackError(f"字段 {name} 是 enum，但取值域为空")

    missing = str(raw.get("missing_policy") or "missing_code").strip()
    if missing not in MISSING_CHOICES:
        raise RulePackError(f"字段 {name} 的缺失规则 {missing} 不在 {MISSING_CHOICES}")

    labels = raw.get("labels")
    labels = labels if isinstance(labels, dict) else {}
    out: dict[str, Any] = {
        "name": name,
        "column": column_letter(index),
        "type": kind,
        "missing_policy": missing,
        "required_when": str(raw.get("required_when") or "").strip(),
        "chapter": str(raw.get("chapter") or "").strip(),
        "quote": str(raw.get("quote") or "").strip(),
        "note": str(raw.get("note") or "").strip(),
    }
    if allowed:
        out["allowed"] = allowed
    if labels:
        out["labels"] = {str(k): str(v) for k, v in labels.items()}
    if raw.get("range"):
        out["range"] = list(raw["range"])
    return out


def normalise_fields(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not fields:
        raise RulePackError("规则包至少要有 1 个字段")
    seen: set[str] = set()
    out = []
    for index, item in enumerate(fields):
        field_def = normalise_field(item, index)
        if field_def["name"] in seen:
            raise RulePackError(f"字段名重复：{field_def['name']}")
        seen.add(field_def["name"])
        out.append(field_def)
    return out


def _field_schema(field_def: dict[str, Any]) -> dict[str, Any]:
    kind = field_def["type"]
    if kind == "enum":
        return {"enum": field_def.get("allowed", [])}
    if kind == "integer":
        schema: dict[str, Any] = {"type": "integer"}
        if field_def.get("range"):
            schema["minimum"], schema["maximum"] = field_def["range"]
        return schema
    if kind == "number":
        return {"type": "number"}
    if kind == "boolean":
        return {"type": "boolean"}
    return {"type": "string"}


def build_schema(fields: list[dict[str, Any]], pack_id: str) -> dict[str, Any]:
    required = [
        item["name"] for item in fields
        if item.get("missing_policy") == "never_missing"
    ]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": f"{pack_id} record",
        "type": "object",
        "required": ["fields"],
        "properties": {
            "fields": {
                "type": "object",
                "additionalProperties": False,
                "required": required,
                "properties": {item["name"]: _field_schema(item) for item in fields},
            }
        },
    }


def build_contract(fields: list[dict[str, Any]], pack_id: str, source_name: str) -> dict[str, Any]:
    headers = {item["column"]: item["name"] for item in fields}
    return {
        "pack_id": pack_id,
        "derived_from": source_name,
        "generator": "src/meta_coding/web/rulepacks.py",
        "sheets": {"main": ["main"], "tracking": "tracking", "excluded": "excluded", "count": 3},
        "main": {"header_row": 1, "field_count": len(fields), "headers": headers},
    }


def pack_hash(
    fields: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    schema: dict[str, Any],
    contract: dict[str, Any],
) -> str:
    payload = {"fields": fields, "decisions": decisions, "schema": schema, "contract": contract}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return store.sha256_text(canonical)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_fields_yaml(path: Path, pack_id: str, fields: list[dict[str, Any]],
                       generated_by: str) -> None:
    payload = {
        "meta": {
            "pack_id": pack_id,
            "field_count": len(fields),
            "generated_by": generated_by,
            "spec": "docs/RULE_PACK_SPEC.md",
        },
        "fields": fields,
    }
    _write_text(path, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))


def _write_decisions_yaml(path: Path, decisions: list[dict[str, Any]]) -> None:
    payload = {"decisions": decisions}
    _write_text(path, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))


def _regenerate(pack_root: Path, pack: dict[str, Any], fields: list[dict[str, Any]],
                decisions: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], str]:
    pack_id = str(pack.get("pack_id"))
    source = pack.get("source_doc") if isinstance(pack.get("source_doc"), dict) else {}
    schema = build_schema(fields, pack_id)
    contract = build_contract(fields, pack_id, str(source.get("path") or ""))
    digest = pack_hash(fields, decisions, schema, contract)

    _write_fields_yaml(pack_root / "fields.yaml", pack_id, fields,
                       str((pack.get("generated_by") or {}).get("model") or "unknown"))
    _write_decisions_yaml(pack_root / "decisions.yaml", decisions)
    _write_text(pack_root / "schema" / "record.schema.json",
                json.dumps(schema, ensure_ascii=False, indent=2) + "\n")
    _write_text(pack_root / "template_contract.json",
                json.dumps(contract, ensure_ascii=False, indent=2) + "\n")
    pack["field_count"] = len(fields)
    pack["pack_hash"] = digest
    return schema, contract, digest


def _write_pack_json(pack_root: Path, pack: dict[str, Any]) -> None:
    _write_text(pack_root / "pack.json", json.dumps(pack, ensure_ascii=False, indent=2) + "\n")


# --------------------------------------------------------------------------- #
# 生成器：mock 与真实模型走同一个出口
# --------------------------------------------------------------------------- #


def _doc_lines(text: str) -> list[str]:
    """从规则文档里挑可当「原文片段」的行：去掉 markdown 标记，保留完整句子。"""
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("```", "|", "#!")):
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^[-*+]\s+", "", line)
        line = re.sub(r"\s+", " ", line)
        if 10 <= len(line) <= 180:
            out.append(line)
    return out


def generate_mock(
    text: str, doc_name: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """离线示例生成器：固定 4 个字段，quote 取自文档真实段落（不编原文）。"""
    lines = _doc_lines(text)
    if len(lines) < 2:
        raise RulePackError("规则文档正文太少，无法抽取字段出处；请换一份更完整的规则文档")
    quotes = [lines[min(index, len(lines) - 1)] for index in range(len(MOCK_FIELDS))]
    fields = []
    for (name, kind, missing), quote in zip(MOCK_FIELDS, quotes, strict=True):
        fields.append(
            {
                "name": name,
                "type": kind,
                "missing_policy": missing,
                "quote": quote,
                "chapter": "",
                "note": "离线示例字段（mock）：用于在没有密钥时演示规则包流程",
            }
        )
    decisions = [
        {
            "id": "D-001",
            "topic": "缺失值",
            "rule": "缺失统一记 -999，不参与任何均值/SD 计算",
            "quote": quotes[0],
        },
        {
            "id": "D-002",
            "topic": "去向",
            "rule": "缺均值或 SD 的记录进入 tracking 表，不进入主表",
            "quote": quotes[1],
        },
    ]
    return fields, decisions, []


def generate_with_model(
    text: str, slot: ModelSlot
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """真实调用：让模型按 RULE_PACK_SPEC 的 JSON 契约产出字段。"""
    from ..coders.adapters.base import get_adapter

    system = (
        "你是元分析编码规则解析器。读用户给出的规则文档，抽取可机器校验的字段清单。"
        "只输出 JSON，形如 "
        '{"fields":[{"name":"snake_case","type":"string|integer|number|enum|boolean",'
        '"allowed":[],"missing_policy":"missing_code|never_missing|never_missing_when_coded",'
        '"chapter":"§号","quote":"原文片段"}],'
        '"decisions":[{"id":"D-001","topic":"","rule":"","quote":""}],'
        '"gaps":["规则文档没写清的地方"]}。'
        "每个字段必须带 chapter 或 quote；没有原文依据的字段不要输出。"
    )
    user = f"规则文档（截断至 100k 字符）：\n\n{text[:100_000]}"
    try:
        result = get_adapter(slot).complete(system, user)
    except Exception as exc:  # noqa: BLE001 - 网络/凭据错误要原样告诉用户
        raise RulePackError(f"模型调用失败：{type(exc).__name__}: {exc}") from exc

    cleaned = result.text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        cleaned = cleaned.rsplit("```", 1)[0]
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RulePackError(f"模型输出不是合法 JSON：{exc}") from exc

    gaps = [str(item) for item in (data.get("gaps") or [])]
    fields: list[dict[str, Any]] = []
    for raw in data.get("fields") or []:
        if not isinstance(raw, dict):
            continue
        if not (str(raw.get("chapter") or "").strip() or str(raw.get("quote") or "").strip()):
            gaps.append(f"字段 {raw.get('name')} 没有原文出处，已丢弃")
            continue
        fields.append(raw)
    if not fields:
        raise RulePackError("模型没有产出任何带原文出处的字段")
    decisions = [item for item in (data.get("decisions") or []) if isinstance(item, dict)]
    return fields, decisions, gaps


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")
    return (slug[:28] or "rules").strip("-")


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def create_pack(
    root: Path,
    *,
    doc_path: Path,
    text: str,
    pages: int | None,
    mode: str = "mock",
    slot: ModelSlot | None = None,
    pack_id: str | None = None,
) -> PackInfo:
    """解析规则文档并落盘一个草稿规则包。"""
    if mode not in {"mock", "model"}:
        raise RulePackError("mode 只能是 mock 或 model")
    if mode == "model" and slot is None:
        raise RulePackError("用模型解析前，需要先在 configs/models.yaml 绑定一个模型槽位")

    digest = store.sha256_file(doc_path)
    identifier = pack_id or f"{_slug(doc_path.stem)}-{_stamp()}"
    if not PACK_ID_RE.match(identifier):
        raise RulePackError("pack_id 只能用小写字母、数字、点、下划线与短横线（≤40 字符）")
    target = pack_dir(root, identifier)
    if target.exists():
        raise RulePackError(f"规则包 id 已存在：{identifier}")

    if mode == "model":
        assert slot is not None
        fields, decisions, gaps = generate_with_model(text, slot)
        model_name = f"{slot.provider}/{slot.model}"
    else:
        fields, decisions, gaps = generate_mock(text, doc_path.name)
        model_name = "mock-rulepack-v1"

    fields = normalise_fields(fields)
    now = datetime.now().isoformat(timespec="seconds")
    pack = {
        "pack_id": identifier,
        "source_doc": {
            "path": str(doc_path),
            "sha256": digest,
            "pages": pages,
        },
        "field_count": len(fields),
        "generated_by": {
            "model": model_name,
            "prompt_version": PROMPT_VERSION,
            "at": now,
            "mode": mode,
        },
        "status": "draft",
        "mock": mode == "mock",
        "human_edits": 0,
        "gaps": gaps,
        "created_at": now,
        "confirmed_by": None,
        "confirmed_at": None,
        "pack_hash": "",
    }
    target.mkdir(parents=True)
    _regenerate(target, pack, fields, decisions)
    _write_pack_json(target, pack)
    _write_text(
        target / "gaps.md",
        "# 规则缺口\n\n" + ("\n".join(f"- {gap}" for gap in gaps) if gaps else "- 无\n"),
    )
    return load_pack(root, identifier)


# --------------------------------------------------------------------------- #
# 审阅：改 / 删 / 增 / 确认
# --------------------------------------------------------------------------- #


def _load_raw_pack(root: Path, pack_id: str) -> dict[str, Any]:
    raw = store.read_json(pack_dir(root, pack_id) / "pack.json")
    if not isinstance(raw, dict):
        raise RulePackError(f"规则包不存在：{pack_id}")
    if str(raw.get("status")) == "confirmed":
        raise RulePackError("规则包已确认，不能再改；请重新解析生成新版本")
    return raw


def save_fields(root: Path, pack_id: str, fields: list[dict[str, Any]],
                *, human_edit: bool = True) -> PackInfo:
    pack = _load_raw_pack(root, pack_id)
    decisions = read_decisions(root, pack_id)
    normalised = normalise_fields(fields)
    target = pack_dir(root, pack_id)
    _regenerate(target, pack, normalised, decisions)
    if human_edit:
        pack["human_edits"] = int(pack.get("human_edits") or 0) + 1
    _write_pack_json(target, pack)
    return load_pack(root, pack_id)


def add_field(root: Path, pack_id: str, raw: dict[str, Any]) -> PackInfo:
    fields = read_fields(root, pack_id)
    fields.append(raw)
    return save_fields(root, pack_id, fields)


def update_field(root: Path, pack_id: str, name: str, patch: dict[str, Any]) -> PackInfo:
    fields = read_fields(root, pack_id)
    for index, item in enumerate(fields):
        if str(item.get("name")) == name:
            merged = {**item, **{k: v for k, v in patch.items() if v is not None}}
            fields[index] = merged
            return save_fields(root, pack_id, fields)
    raise RulePackError(f"字段不存在：{name}")


def delete_field(root: Path, pack_id: str, name: str) -> PackInfo:
    fields = [item for item in read_fields(root, pack_id) if str(item.get("name")) != name]
    if len(fields) == 0:
        raise RulePackError("删掉最后一个字段会让规则包失效，已拒绝")
    return save_fields(root, pack_id, fields)


def confirm_pack(root: Path, pack_id: str) -> PackInfo:
    pack = store.read_json(pack_dir(root, pack_id) / "pack.json")
    if not isinstance(pack, dict):
        raise RulePackError(f"规则包不存在：{pack_id}")
    fields = normalise_fields(read_fields(root, pack_id))
    target = pack_dir(root, pack_id)
    schema, contract, digest = _regenerate(target, pack, fields, read_decisions(root, pack_id))
    pack["status"] = "confirmed"
    pack["confirmed_by"] = "human"
    pack["confirmed_at"] = datetime.now().isoformat(timespec="seconds")
    pack["pack_hash"] = digest
    _write_pack_json(target, pack)
    return load_pack(root, pack_id)


# --------------------------------------------------------------------------- #
# 导入现有 rules/（meta-wbi 样板）
# --------------------------------------------------------------------------- #


def import_builtin(root: Path, pack_id: str = "meta-wbi-v1") -> PackInfo:
    """把现有 rules/ 冻结成一个规则包草稿，内容逐字节不动。"""
    source_dir = root / "rules"
    fields_src = source_dir / "fields.yaml"
    if not fields_src.is_file():
        raise RulePackError("rules/fields.yaml 不存在，无法导入内置规则包")
    target = pack_dir(root, pack_id)
    if target.exists():
        raise RulePackError(f"规则包 id 已存在：{pack_id}")

    fields = read_fields_from_file(fields_src)
    decisions_src = source_dir / "decisions.yaml"
    contract_src = source_dir / "template_contract.json"
    schema_src = source_dir / "schema" / "record.schema.json"

    now = datetime.now().isoformat(timespec="seconds")
    plan = source_dir / "CODING_PLAN.md"
    pack = {
        "pack_id": pack_id,
        "source_doc": {
            "path": str(plan.relative_to(root)) if plan.is_file() else "rules/fields.yaml",
            "sha256": store.sha256_file(plan) if plan.is_file() else store.sha256_file(fields_src),
            "pages": None,
        },
        "field_count": len(fields),
        "generated_by": {
            "model": "imported:rules/",
            "prompt_version": "import",
            "at": now,
            "mode": "import",
        },
        "status": "draft",
        "mock": False,
        "human_edits": 0,
        "gaps": [],
        "created_at": now,
        "confirmed_by": None,
        "confirmed_at": None,
        "pack_hash": "",
    }
    target.mkdir(parents=True)
    if decisions_src.is_file():
        shutil.copyfile(decisions_src, target / "decisions.yaml")
    else:
        _write_decisions_yaml(target / "decisions.yaml", [])
    if contract_src.is_file():
        shutil.copyfile(contract_src, target / "template_contract.json")
    if schema_src.is_file():
        (target / "schema").mkdir(exist_ok=True)
        shutil.copyfile(schema_src, target / "schema" / "record.schema.json")
    # 逐字节保留原有 fields.yaml（这是「冻结」承诺的一部分），不重新 dump
    shutil.copyfile(fields_src, target / "fields.yaml")
    if not (target / "template_contract.json").is_file() or not (
        target / "schema" / "record.schema.json"
    ).is_file():
        _regenerate(target, pack, fields, read_decisions(root, pack_id))
    else:
        digest = pack_hash(
            fields,
            read_decisions(root, pack_id),
            _read_schema(root, pack_id),
            _read_contract(root, pack_id),
        )
        pack["pack_hash"] = digest
    _write_pack_json(target, pack)
    _write_text(
        target / "gaps.md",
        "# 规则缺口\n\n- 内置规则包来自 rules/，字段与决策逐字节保留原文件。\n",
    )
    return load_pack(root, pack_id)


def read_fields_from_file(path: Path) -> list[dict[str, Any]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    fields = raw.get("fields") if isinstance(raw, dict) else None
    if not isinstance(fields, list):
        return []
    return [item for item in fields if isinstance(item, dict)]


__all__ = [
    "MISSING_CHOICES",
    "PACK_ROOT",
    "PACK_ID_RE",
    "PROMPT_VERSION",
    "PackInfo",
    "RulePackError",
    "TYPE_CHOICES",
    "add_field",
    "build_contract",
    "build_schema",
    "column_letter",
    "confirm_pack",
    "create_pack",
    "delete_field",
    "generate_mock",
    "generate_with_model",
    "import_builtin",
    "list_packs",
    "load_pack",
    "normalise_field",
    "normalise_fields",
    "pack_dir",
    "pack_hash",
    "read_decisions",
    "read_fields",
    "save_fields",
    "update_field",
]
