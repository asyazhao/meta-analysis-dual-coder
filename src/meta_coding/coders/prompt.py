"""编码员与裁决员的提示词渲染。

硬约束（CODING_PLAN.md §9 / 项目 §9）：
* 渲染后的提示词**除模型配置外逐字节一致**，因此 ``prompt_hash_a == prompt_hash_b``；
* 编码提示词中不得出现"另一个模型 / 对比 / 裁决"等暗示对照组存在的字样；
* 规则以**原文**注入，不做摘要。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROMPT_DIR = Path(__file__).parent / "prompts"
CODER_TEMPLATE = PROMPT_DIR / "coder_v1.txt"
REPAIR_TEMPLATE = PROMPT_DIR / "repair_v1.txt"
ADJUDICATOR_TEMPLATE = PROMPT_DIR / "adjudicator_v1.txt"

FORBIDDEN_IN_CODER_PROMPT = ("另一个模型", "对比", "裁决", "一致性", "another model",
                             "adjudicat", "coder b", "编码员 b")


@dataclass(frozen=True)
class RenderedPrompt:
    system: str
    user: str
    sha256: str
    version: str
    chars: int

    def canonical(self) -> dict[str, str]:
        return {"system": self.system, "user": self.user}


def _hash(system: str, user: str) -> str:
    blob = json.dumps({"system": system, "user": user},
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load_template(path: Path) -> tuple[str, str, str]:
    """Return (system, user_template, version)."""
    text = path.read_text(encoding="utf-8")
    version_line = text.splitlines()[0]
    version = version_line.strip("# ").strip() if version_line.startswith("#") else path.stem
    body = text if not version_line.startswith("#") else text.split("\n", 1)[1]
    system_part, _, user_part = body.partition("[USER]")
    system = system_part.replace("[SYSTEM]", "", 1).strip()
    return system, user_part.strip(), version


def _fill(template: str, values: dict[str, Any]) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{{" + key + "}}", str(value))
    return out


def _render_pages(packet: dict[str, Any]) -> str:
    return "\n\n".join(
        f"--- page {p['page']} ---\n{p['text'].strip()}" for p in packet["pages"]
    )


def _render_tables(packet: dict[str, Any]) -> str:
    if not packet["tables"]:
        return "(本文未检出表格；表格可能以图片形式存在，需要时请请求页面图)"
    chunks = []
    for table in packet["tables"]:
        label = table["label"] or "Table"
        chunks.append(
            f"[{label} | page {table['page']} | {table['n_rows']}x{table['n_cols']}]\n"
            f"{table['caption']}\n{table['markdown']}"
        )
    return "\n\n".join(chunks)


def schema_text(schema_path: Path) -> str:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    props = schema["properties"]["fields"]["required"]
    pieces = []
    for name in props:
        spec = schema["properties"]["fields"]["properties"][name]
        if "enum" in spec:
            values = ", ".join(str(x) for x in spec["enum"])
            pieces.append(f'  "{name}": {values}')
        elif spec.get("anyOf"):
            pieces.append(f'  "{name}": <number 或 -999>')
        else:
            kind = spec.get("type", "string")
            pieces.append(f'  "{name}": <{kind}>')
    return "{\n" + ",\n".join(pieces) + "\n}"


def render_coder(
    packet: dict[str, Any],
    rules_text: str,
    rules_hash: str,
    schema_path: Path,
) -> RenderedPrompt:
    system, user_tpl, version = load_template(CODER_TEMPLATE)
    biblio = packet.get("biblio", {})
    user = _fill(
        user_tpl,
        {
            "RULES_HASH": rules_hash,
            "RULES_TEXT": rules_text.strip(),
            "ARTICLE_KEY": packet["article_key"],
            "SOURCE_FILE": packet["source_file"],
            "BIBLIO": json.dumps(biblio, ensure_ascii=False),
            "PAGES": packet["page_count"],
            "MATERIAL_STATUS": json.dumps(packet["material_status"], ensure_ascii=False),
            "PAGE_ANCHORED_TEXT": _render_pages(packet),
            "TABLES": _render_tables(packet),
            "SUPPLEMENTS": "(本批次无补充材料文件)",
            "SCHEMA": schema_text(schema_path),
        },
    )

    # 只检查我们自己写的模板，不检查注入的规则原文与论文原文——
    # 规则文件本身就会出现"裁决""一致性"这类词，那是编码对象，不是提示词。
    scaffold = (system + "\n" + user_tpl).lower()
    leaked = [word for word in FORBIDDEN_IN_CODER_PROMPT if word.lower() in scaffold]
    if leaked:
        raise ValueError(f"编码提示词模板出现禁止字样：{leaked}")
    return RenderedPrompt(system=system, user=user, sha256=_hash(system, user),
                          version=version, chars=len(system) + len(user))


def assert_parity(a: RenderedPrompt, b: RenderedPrompt) -> None:
    if a.sha256 != b.sha256:
        raise AssertionError(
            f"双编码员提示词不一致：{a.sha256[:16]}… vs {b.sha256[:16]}…（隔离被破坏）"
        )
