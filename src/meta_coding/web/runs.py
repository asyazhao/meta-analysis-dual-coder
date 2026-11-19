"""运行工作区：``runs/<run_id>/`` 的读写、差异计算与修改意见。

目录契约见 ``docs/RULE_PACK_SPEC.md`` §5：

    runs/<run_id>/
    ├── results/coder_a.jsonl     # 编码员 A 的逐篇结果
    ├── results/coder_b.jsonl
    ├── diff/cells.csv            # 逐字段差异（含 match_type）
    ├── diff/summary.md
    ├── advice/adjudication.jsonl # 第三方裁决（外部产出，读）
    ├── advice/revisions.md       # 给人看的修改清单
    ├── advice/decisions.json     # 人在壳里的采纳/驳回（写）
    ├── advice/provenance.jsonl   # 决策留痕（写）
    └── run.json                  # manifest：规则包哈希、成本、阶段状态

差异与采纳都是**确定性**的：读既有 JSONL/CSV，不改写编码结果本身。
"""

from __future__ import annotations

import csv
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import store

RUNS_ROOT = Path("runs")
CODED_DIR = Path("data/03_coded")
RUN_LOG = CODED_DIR / "run_log.jsonl"

STEP_SPECS = [
    {"id": "ingest", "label": "摄取", "detail": "PDF → 带页码锚点的 packet.json"},
    {"id": "code_a", "label": "A 编码", "detail": "编码员 A 逐篇取值并记出处"},
    {"id": "code_b", "label": "B 编码", "detail": "编码员 B 独立编码（盲法）"},
    {"id": "diff", "label": "比对", "detail": "逐字段比对，标出差异与严重度"},
    {"id": "adjudicate", "label": "裁决", "detail": "第三方给建议值 + 原文依据"},
]
STEP_IDS = [item["id"] for item in STEP_SPECS]
CODERS = ("a", "b")
# 影响合并效应量的字段：差异默认按 critical 标红（界面层提醒，不是裁决结论）
CRITICAL_FIELDS = {
    "sample_n", "sample_size", "scale_mean", "mean_value",
    "scale_sd", "sd_value", "scale_total",
}
CELL_HEADERS = [
    "paper", "sheet", "block", "sex", "field", "coder_a", "coder_b",
    "match_type", "severity", "page", "quote", "suggestion", "rationale", "confidence",
]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


@dataclass
class RunInfo:
    run_id: str
    created_at: str
    status: str = "draft"
    pack_id: str = ""
    pack_hash: str = ""
    papers_dir: str = ""
    batch_id: str = ""
    config_hash: str = ""
    cost_usd: float = 0.0
    steps: list[dict[str, Any]] = field(default_factory=list)

    @property
    def path_rel(self) -> str:
        return str(Path("runs") / self.run_id)

    def step(self, step_id: str) -> dict[str, Any] | None:
        return next((item for item in self.steps if item.get("id") == step_id), None)

    def step_status(self, step_id: str) -> str:
        item = self.step(step_id)
        return str(item.get("status")) if item else "idle"

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "created_at": self.created_at,
            "status": self.status,
            "rulepack": {"id": self.pack_id, "pack_hash": self.pack_hash},
            "papers_dir": self.papers_dir,
            "batch_id": self.batch_id,
            "config_hash": self.config_hash,
            "cost_usd": self.cost_usd,
            "steps": self.steps,
        }

    @property
    def badge(self) -> str:
        return {
            "draft": "待运行", "running": "运行中", "done": "已完成", "failed": "有失败",
        }.get(self.status, self.status)


def _run_dir(root: Path, run_id: str) -> Path:
    if not re.match(r"^run-[A-Za-z0-9._-]{4,60}$", run_id or ""):
        raise ValueError("run_id 不合法")
    return root / RUNS_ROOT / run_id


def load_run(root: Path, run_id: str) -> RunInfo:
    raw = store.read_json(_run_dir(root, run_id) / "run.json")
    if not isinstance(raw, dict):
        raise ValueError(f"运行不存在：{run_id}")
    pack = raw.get("rulepack") if isinstance(raw.get("rulepack"), dict) else {}
    return RunInfo(
        run_id=str(raw.get("run_id") or run_id),
        created_at=str(raw.get("created_at") or ""),
        status=str(raw.get("status") or "draft"),
        pack_id=str(pack.get("id") or ""),
        pack_hash=str(pack.get("pack_hash") or ""),
        papers_dir=str(raw.get("papers_dir") or ""),
        batch_id=str(raw.get("batch_id") or ""),
        config_hash=str(raw.get("config_hash") or ""),
        cost_usd=float(raw.get("cost_usd") or 0.0),
        steps=[item for item in (raw.get("steps") or []) if isinstance(item, dict)],
    )


def list_runs(root: Path) -> list[RunInfo]:
    base = root / RUNS_ROOT
    if not base.is_dir():
        return []
    out: list[RunInfo] = []
    for child in base.iterdir():
        if child.is_dir() and (child / "run.json").is_file():
            try:
                out.append(load_run(root, child.name))
            except ValueError:
                continue
    return sorted(out, key=lambda item: item.created_at, reverse=True)


def latest_run(root: Path) -> RunInfo | None:
    runs = list_runs(root)
    return runs[0] if runs else None


def create_run(
    root: Path,
    *,
    pack_id: str,
    pack_hash: str,
    papers_dir: str,
    batch_id: str,
    config_hash: str,
) -> RunInfo:
    run_id = f"run-{_stamp()}-{uuid.uuid4().hex[:4]}"
    target = root / RUNS_ROOT / run_id
    for sub in ("results", "diff", "advice", "exports"):
        (target / sub).mkdir(parents=True, exist_ok=True)
    steps = [
        {
            "id": item["id"],
            "label": item["label"],
            "status": "idle",
            "started_at": None,
            "ended_at": None,
            "returncode": None,
            "note": "",
        }
        for item in STEP_SPECS
    ]
    run = RunInfo(
        run_id=run_id,
        created_at=_now(),
        status="draft",
        pack_id=pack_id,
        pack_hash=pack_hash,
        papers_dir=papers_dir,
        batch_id=batch_id,
        config_hash=config_hash,
        steps=steps,
    )
    save_run(root, run)
    return run


def save_run(root: Path, run: RunInfo) -> None:
    target = _run_dir(root, run.run_id) / "run.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(run.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _recompute_status(run: RunInfo) -> None:
    statuses = [str(item.get("status")) for item in run.steps]
    if any(status == "running" for status in statuses):
        run.status = "running"
    elif any(status == "failed" for status in statuses):
        run.status = "failed"
    elif statuses and all(status == "done" for status in statuses):
        run.status = "done"
    elif any(status in {"done", "failed"} for status in statuses):
        run.status = "running"
    else:
        run.status = "draft"


def update_step(root: Path, run_id: str, step_id: str, **changes: Any) -> RunInfo:
    run = load_run(root, run_id)
    for item in run.steps:
        if item.get("id") == step_id:
            item.update({key: value for key, value in changes.items() if value is not None})
            break
    _recompute_status(run)
    save_run(root, run)
    return run


# --------------------------------------------------------------------------- #
# 结果物化与差异
# --------------------------------------------------------------------------- #


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                out.append(item)
    except OSError:
        return []
    return out


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _collect_coder_rows(root: Path, coder: str) -> list[dict[str, Any]]:
    """从 data/03_coded 汇总某位编码员的逐篇记录（真实数据，不补值）。"""
    corpus = store.read_corpus(root)
    by_id = {store.sha16(str(row.get("article_key") or "")): row for row in corpus}
    rows: list[dict[str, Any]] = []
    base = root / CODED_DIR / f"coder_{coder}"
    if not base.is_dir():
        return rows
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        if by_id and child.name not in by_id:
            continue
        doc = store.read_json(child / "parsed.json")
        if not isinstance(doc, dict):
            continue
        article_key = str(doc.get("article_key") or "")
        source_file = str(by_id.get(child.name, {}).get("source_file") or "")
        for record in doc.get("records") or []:
            if not isinstance(record, dict):
                continue
            rows.append(
                {
                    "article_key": article_key,
                    "source_file": source_file or record.get("source_file", ""),
                    "sheet": record.get("sheet"),
                    "block_id": record.get("block_id"),
                    "sex": record.get("sex"),
                    "fields": record.get("fields") or {},
                    "provenance": record.get("provenance") or {},
                    "record_status": record.get("record_status"),
                }
            )
    return rows


def materialize_results(root: Path, run_id: str) -> dict[str, int]:
    """把 ``data/03_coded`` 的逐篇 parsed.json 汇总进 run 工作区。

    若 ``data/03_coded`` 还没有该编码员的任何输出，就保留 run 工作区里已有的
    results 文件——那可能是外部流水线按 RULE_PACK_SPEC §5 直接写入的，不能被清空。
    """
    counts: dict[str, int] = {}
    for coder in CODERS:
        target = _run_dir(root, run_id) / "results" / f"coder_{coder}.jsonl"
        rows = _collect_coder_rows(root, coder)
        if rows or not target.is_file():
            _write_jsonl(target, rows)
            counts[f"coder_{coder}"] = len(rows)
        else:
            counts[f"coder_{coder}"] = len(_read_jsonl(target))
    counts["papers"] = len(store.read_corpus(root))
    return counts


def _index_records(
    rows: list[dict[str, Any]]
) -> dict[tuple[str, str, str, str, str], dict[str, Any]]:
    out: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        fields = row.get("fields") if isinstance(row.get("fields"), dict) else {}
        provenance = row.get("provenance") if isinstance(row.get("provenance"), dict) else {}
        paper = str(row.get("source_file") or "")
        sheet = str(row.get("sheet") or "")
        block = str(row.get("block_id") or "")
        sex = str(row.get("sex") if row.get("sex") is not None else "")
        for name, value in fields.items():
            key = (paper, sheet, block, sex, str(name))
            prov = provenance.get(name) if isinstance(provenance.get(name), dict) else {}
            out[key] = {
                "value": "" if value is None else str(value),
                "page": prov.get("page"),
                "quote": str(prov.get("quote") or ""),
            }
    return out


def _advice_index(root: Path, run_id: str) -> dict[tuple[str, str, str, str, str], dict[str, Any]]:
    path = _run_dir(root, run_id) / "advice" / "adjudication.jsonl"
    out: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for record in _read_jsonl(path):
        evidence = record.get("independent_evidence")
        evidence = evidence if isinstance(evidence, dict) else {}
        key = (
            str(record.get("source_file") or record.get("paper") or ""),
            str(record.get("sheet") or ""),
            str(record.get("block_id") or record.get("block") or ""),
            str(record.get("sex") if record.get("sex") is not None else ""),
            str(record.get("field") or record.get("field_name") or ""),
        )
        out[key] = {
            "suggestion": record.get("correct_value", record.get("suggestion")),
            "rationale": str(record.get("rationale") or ""),
            "confidence": record.get("confidence"),
            "severity": str(record.get("severity") or ""),
            "page": record.get("page", evidence.get("page")),
            "quote": str(record.get("quote") or evidence.get("quote") or ""),
        }
    return out


def _severity(field_name: str, match_type: str) -> str:
    if match_type == "agree":
        return ""
    return "critical" if field_name in CRITICAL_FIELDS else "major"


def build_cells(root: Path, run_id: str) -> list[dict[str, Any]]:
    """A/B 逐字段对照；建议值来自裁决记录（若有）。只读，不改任何输入。"""
    base = _run_dir(root, run_id)
    rows_a = _read_jsonl(base / "results" / "coder_a.jsonl") or _collect_coder_rows(root, "a")
    rows_b = _read_jsonl(base / "results" / "coder_b.jsonl") or _collect_coder_rows(root, "b")
    a_index = _index_records(rows_a)
    b_index = _index_records(rows_b)
    advice = _advice_index(root, run_id)

    keys = sorted(set(a_index) | set(b_index))
    cells: list[dict[str, Any]] = []
    for key in keys:
        paper, sheet, block, sex, name = key
        left = a_index.get(key, {})
        right = b_index.get(key, {})
        a_value = str(left.get("value") if left else "")
        b_value = str(right.get("value") if right else "")
        if a_value and b_value:
            match_type = "agree" if a_value.strip() == b_value.strip() else "mismatch"
        elif a_value or b_value:
            match_type = "missing_vs_value"
        else:
            match_type = "agree"
        hint = advice.get(key, {})
        page = left.get("page") if left.get("page") is not None else right.get("page")
        quote = str(left.get("quote") or right.get("quote") or "")
        if hint:
            page = hint.get("page", page)
            quote = str(hint.get("quote") or quote)
        cells.append(
            {
                "paper": paper,
                "sheet": sheet,
                "block": block,
                "sex": sex,
                "field": name,
                "coder_a": a_value,
                "coder_b": b_value,
                "match_type": match_type,
                "severity": str(hint.get("severity") or "") or _severity(name, match_type),
                "page": page,
                "quote": quote,
                "suggestion": _display(hint.get("suggestion")),
                "rationale": str(hint.get("rationale") or ""),
                "confidence": hint.get("confidence"),
            }
        )
    return cells


def _display(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def write_diff(root: Path, run_id: str, cells: list[dict[str, Any]]) -> dict[str, Any]:
    base = _run_dir(root, run_id)
    path = base / "diff" / "cells.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CELL_HEADERS)
        writer.writeheader()
        for cell in cells:
            writer.writerow({key: cell.get(key, "") for key in CELL_HEADERS})

    diff_count = sum(1 for cell in cells if cell["match_type"] != "agree")
    critical = sum(1 for cell in cells if cell["severity"] == "critical")
    by_field: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for cell in cells:
        if cell["match_type"] == "agree":
            continue
        by_field[cell["field"]] = by_field.get(cell["field"], 0) + 1
        by_type[cell["match_type"]] = by_type.get(cell["match_type"], 0) + 1
    lines = [
        f"# 差异摘要 · {run_id}",
        "",
        f"- 字段格：{len(cells)}",
        f"- 差异格：{diff_count}（critical {critical}）",
        "",
        "## 按字段",
        "",
    ]
    lines += [
        f"- {name}: {count}"
        for name, count in sorted(by_field.items(), key=lambda kv: -kv[1])
    ]
    lines += ["", "## 按类型", ""]
    lines += [f"- {name}: {count}" for name, count in sorted(by_type.items())]
    (base / "diff" / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"cells": len(cells), "diff": diff_count, "critical": critical,
            "by_field": by_field, "by_type": by_type}


def sync_and_diff(root: Path, run_id: str) -> dict[str, Any]:
    counts = materialize_results(root, run_id)
    cells = build_cells(root, run_id)
    summary = write_diff(root, run_id, cells)
    update_step(
        root, run_id, "diff",
        status="done", started_at=_now(), ended_at=_now(), returncode=0,
        note=f"差异 {summary['diff']} 格 / 共 {summary['cells']} 格",
    )
    run = load_run(root, run_id)
    run.cost_usd = float(run_progress(root, run_id)["cost_usd"])
    save_run(root, run)
    return {**counts, **summary}


def load_cells(root: Path, run_id: str) -> list[dict[str, Any]]:
    path = _run_dir(root, run_id) / "diff" / "cells.csv"
    if not path.is_file():
        return build_cells(root, run_id)
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = [dict(row) for row in csv.DictReader(handle)]
    except OSError:
        return []
    for row in rows:
        page = row.get("page")
        row["page"] = int(page) if str(page).isdigit() else page
        confidence = row.get("confidence")
        try:
            row["confidence"] = float(confidence) if str(confidence).strip() else ""
        except (TypeError, ValueError):
            row["confidence"] = ""
        row["severity"] = str(row.get("severity") or "")
        row["match_type"] = str(row.get("match_type") or "agree")
    return rows


# --------------------------------------------------------------------------- #
# 修改意见（revisions.md 的结构化版本）
# --------------------------------------------------------------------------- #


def parse_revisions(text: str) -> list[dict[str, Any]]:
    """解析 ``advice/revisions.md``（格式见 RULE_PACK_SPEC §5）。"""
    items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            header = line[3:].strip()
            parts = [part.strip() for part in header.split("·") if part.strip()]
            current = {
                "paper": parts[0] if parts else "",
                "sheet": parts[1] if len(parts) > 1 else "",
                "subsample": parts[2] if len(parts) > 3 else "",
                "field": parts[-1] if len(parts) > 1 else "",
                "coder_a": "",
                "coder_b": "",
                "diff_type": "",
                "suggestion": "",
                "page": None,
                "table": "",
                "quote": "",
                "rationale": "",
                "confidence": "",
                "severity": "",
            }
            items.append(current)
            continue
        if current is None or not line.startswith(("-", "*")):
            continue
        body = line.lstrip("-* ").strip()
        if re.match(r"^A\s*[:：]", body):
            pattern = (
                r"A\s*[:：]\s*(.*?)\s+B\s*[:：]\s*(.*?)"
                r"(?:\s+差异类型\s*[:：]\s*(\S+))?$"
            )
            match = re.search(pattern, body)
            if match:
                current["coder_a"] = match.group(1).strip()
                current["coder_b"] = match.group(2).strip()
                current["diff_type"] = (match.group(3) or "").strip()
        elif body.startswith("建议"):
            payload = re.split(r"[:：]", body, maxsplit=1)[-1].strip()
            page = re.search(r"p\.?\s*(\d+)", payload)
            quote = re.search(r"「(.+?)」", payload)
            table = re.search(r"[（(]\s*(Table\s*[A-Za-z0-9.\-]+)", payload)
            value = re.split(r"[（(]", payload, maxsplit=1)[0].strip()
            current["suggestion"] = value
            current["page"] = int(page.group(1)) if page else None
            current["table"] = table.group(1).strip() if table else ""
            current["quote"] = quote.group(1) if quote else ""
        elif body.startswith("理由"):
            current["rationale"] = re.split(r"[:：]", body, maxsplit=1)[-1].strip()
        elif body.startswith("置信度"):
            confidence = re.search(r"([0-9]+(?:\.[0-9]+)?)", body)
            severity = re.search(r"严重度\s*[:：]\s*([A-Za-z_]+)", body)
            current["confidence"] = confidence.group(1) if confidence else ""
            current["severity"] = severity.group(1) if severity else ""

    for item in items:
        item["id"] = "|".join(
            [item["paper"], item["sheet"], item["subsample"], item["field"]]
        ).strip("|")
        if not item["id"]:
            item["id"] = f"item-{len(items)}"
    return items


def _cell_match(
    cells: list[dict[str, Any]], paper: str, sheet: str, subsample: str, field_name: str
) -> dict[str, Any] | None:
    candidates = [
        cell for cell in cells
        if cell.get("paper") == paper and cell.get("field") == field_name
        and (not sheet or cell.get("sheet") == sheet)
    ]
    if not candidates:
        return None
    if subsample:
        for cell in candidates:
            if cell.get("block") == subsample:
                return cell
    return candidates[0]


def load_advice(root: Path, run_id: str) -> list[dict[str, Any]]:
    base = _run_dir(root, run_id)
    cells = load_cells(root, run_id)
    revisions = base / "advice" / "revisions.md"
    items: list[dict[str, Any]] = []
    if revisions.is_file():
        items = parse_revisions(revisions.read_text(encoding="utf-8"))

    if not items:
        for record in _read_jsonl(base / "advice" / "adjudication.jsonl"):
            evidence = record.get("independent_evidence")
            evidence = evidence if isinstance(evidence, dict) else {}
            field_name = str(record.get("field") or record.get("field_name") or "")
            paper = str(record.get("source_file") or record.get("paper") or "")
            item = {
                "paper": paper,
                "sheet": str(record.get("sheet") or ""),
                "subsample": str(record.get("subsample_label") or ""),
                "field": field_name,
                "coder_a": "",
                "coder_b": "",
                "diff_type": "adjudicated",
                "suggestion": _display(record.get("correct_value")),
                "page": record.get("page", evidence.get("page")),
                "table": str(record.get("table") or evidence.get("table") or ""),
                "quote": str(record.get("quote") or evidence.get("quote") or ""),
                "rationale": str(record.get("rationale") or ""),
                "confidence": _display(record.get("confidence")),
                "severity": str(record.get("severity") or ""),
            }
            item["id"] = "|".join([paper, item["sheet"], item["subsample"], field_name]).strip("|")
            items.append(item)

    if not items:
        for cell in cells:
            if not cell.get("suggestion"):
                continue
            items.append(
                {
                    "id": "|".join([cell["paper"], cell["sheet"], cell["block"], cell["field"]]),
                    "paper": cell["paper"],
                    "sheet": cell["sheet"],
                    "subsample": cell["block"],
                    "field": cell["field"],
                    "coder_a": cell["coder_a"],
                    "coder_b": cell["coder_b"],
                    "diff_type": cell["match_type"],
                    "suggestion": cell["suggestion"],
                    "page": cell["page"],
                    "table": "",
                    "quote": cell["quote"],
                    "rationale": cell["rationale"],
                    "confidence": cell["confidence"],
                    "severity": cell["severity"],
                }
            )

    decisions = _load_decisions(root, run_id)
    for item in items:
        cell = _cell_match(cells, item["paper"], item["sheet"], item["subsample"], item["field"])
        if cell:
            item["coder_a"] = item.get("coder_a") or cell["coder_a"]
            item["coder_b"] = item.get("coder_b") or cell["coder_b"]
            item["severity"] = item.get("severity") or cell["severity"]
            item["page"] = item.get("page") if item.get("page") is not None else cell["page"]
            item["quote"] = item.get("quote") or cell["quote"]
        decision = decisions.get(item["id"])
        item["decision"] = str(decision.get("action") or "") if decision else ""
        item["decision_value"] = str(decision.get("value") or "") if decision else ""
        item["decision_at"] = str(decision.get("at") or "") if decision else ""
    return items


def advice_stats(items: list[dict[str, Any]]) -> dict[str, int]:
    out = {"total": len(items), "accepted": 0, "rejected": 0, "pending": 0, "critical": 0}
    for item in items:
        action = item.get("decision")
        if action == "accepted":
            out["accepted"] += 1
        elif action == "rejected":
            out["rejected"] += 1
        else:
            out["pending"] += 1
        if item.get("severity") == "critical":
            out["critical"] += 1
    return out


def _decisions_path(root: Path, run_id: str) -> Path:
    return _run_dir(root, run_id) / "advice" / "decisions.json"


def _load_decisions(root: Path, run_id: str) -> dict[str, dict[str, Any]]:
    raw = store.read_json(_decisions_path(root, run_id))
    if not isinstance(raw, dict):
        return {}
    return {str(key): value for key, value in raw.items() if isinstance(value, dict)}


def decide(
    root: Path, run_id: str, item_id: str, action: str, value: str = "", note: str = ""
) -> dict[str, Any]:
    """采纳/驳回一条修改意见，写 decisions.json 并追加 provenance.jsonl。"""
    if action not in {"accept", "reject"}:
        raise ValueError("action 只能是 accept 或 reject")
    items = load_advice(root, run_id)
    item = next((entry for entry in items if entry["id"] == item_id), None)
    if item is None:
        raise ValueError("找不到这条修改意见")

    if action == "accept":
        final_value = (value or str(item.get("suggestion") or "")).strip()
        if not final_value:
            raise ValueError("该条没有建议值，采纳前请填写取值")
    else:
        final_value = ""

    decisions = _load_decisions(root, run_id)
    decisions[item_id] = {
        "action": "accepted" if action == "accept" else "rejected",
        "value": final_value,
        "note": note,
        "at": _now(),
        "actor": "human",
        "paper": item.get("paper", ""),
        "sheet": item.get("sheet", ""),
        "field": item.get("field", ""),
        "severity": item.get("severity", ""),
    }
    path = _decisions_path(root, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(decisions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    provenance = _run_dir(root, run_id) / "advice" / "provenance.jsonl"
    with provenance.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "at": decisions[item_id]["at"],
                    "actor": "human",
                    "action": decisions[item_id]["action"],
                    "item_id": item_id,
                    "run_id": run_id,
                    "paper": item.get("paper", ""),
                    "sheet": item.get("sheet", ""),
                    "field": item.get("field", ""),
                    "original_a": item.get("coder_a", ""),
                    "original_b": item.get("coder_b", ""),
                    "suggestion": item.get("suggestion", ""),
                    "value": final_value,
                    "note": note,
                    "source": "web_console",
                },
                ensure_ascii=False,
            )
            + "\n"
        )
    return {
        "item": {**item, "decision": decisions[item_id]["action"],
                 "decision_value": final_value, "decision_at": decisions[item_id]["at"]},
        "stats": advice_stats(load_advice(root, run_id)),
        "decisions_path": str(path.relative_to(root)),
        "provenance_path": str(provenance.relative_to(root)),
    }


# --------------------------------------------------------------------------- #
# 进度与成本
# --------------------------------------------------------------------------- #


def _run_log_rows(root: Path) -> list[dict[str, Any]]:
    return _read_jsonl(root / RUN_LOG)


def run_progress(root: Path, run_id: str) -> dict[str, Any]:
    run = load_run(root, run_id)
    corpus = store.read_corpus(root)
    keys = {str(row.get("article_key") or "") for row in corpus}
    source_of = {store.sha16(str(row.get("article_key") or "")): str(row.get("source_file") or "")
                 for row in corpus}

    coded: dict[str, int] = {}
    newest_missing: dict[str, tuple[float, str]] = {}
    for coder in CODERS:
        done = 0
        base = root / CODED_DIR / f"coder_{coder}"
        if base.is_dir():
            for child in base.iterdir():
                if not child.is_dir():
                    continue
                if keys and child.name not in source_of:
                    continue
                if (child / "parsed.json").is_file():
                    done += 1
                    continue
                try:
                    mtime = child.stat().st_mtime
                except OSError:
                    continue
                previous = newest_missing.get(coder)
                if previous is None or mtime > previous[0]:
                    newest_missing[coder] = (mtime, source_of.get(child.name, child.name))
        coded[coder] = done

    total = len(corpus)
    if total == 0 and run.papers_dir:
        try:
            total = len(store.list_pdfs(Path(run.papers_dir))[0])
        except (OSError, ValueError):
            total = 0

    cost = 0.0
    cost_by_coder: dict[str, float] = {}
    for row in _run_log_rows(root):
        if keys and str(row.get("article_key") or "") not in keys:
            continue
        if str(row.get("status") or "") not in {"ok", ""}:
            continue
        try:
            amount = float(row.get("cost_usd") or 0.0)
        except (TypeError, ValueError):
            amount = 0.0
        cost += amount
        coder = str(row.get("coder") or "")
        cost_by_coder[coder] = cost_by_coder.get(coder, 0.0) + amount

    active = next(
        (coder for coder in CODERS if run.step_status(f"code_{coder}") == "running"), ""
    )
    current_paper = newest_missing.get(active, (0.0, ""))[1] if active else ""
    if not current_paper and active:
        current_paper = newest_missing.get("a", (0.0, ""))[1]

    return {
        "run": run.as_dict(),
        "status": run.status,
        "badge": run.badge,
        "total": total,
        "coded": {"a": coded.get("a", 0), "b": coded.get("b", 0)},
        "percent": {
            "a": round(100 * coded.get("a", 0) / total) if total else 0,
            "b": round(100 * coded.get("b", 0) / total) if total else 0,
        },
        "current": {"coder": active, "paper": current_paper},
        "cost_usd": round(cost, 4),
        "cost_by_coder": {key: round(value, 4) for key, value in cost_by_coder.items()},
        "cost_source": "data/03_coded/run_log.jsonl（本批次累计）",
        "steps": run.steps,
    }


# --------------------------------------------------------------------------- #
# 导出
# --------------------------------------------------------------------------- #

EXPORT_CANDIDATES = [
    ("工作簿", "exports/{pack_id}_codebook.xlsx"),
    ("分析长表", "exports/analysis_long.csv"),
    ("修改意见", "advice/revisions.md"),
    ("差异逐格表", "diff/cells.csv"),
    ("差异摘要", "diff/summary.md"),
    ("A 逐篇结果", "results/coder_a.jsonl"),
    ("B 逐篇结果", "results/coder_b.jsonl"),
    ("人工决策", "advice/decisions.json"),
    ("决策留痕", "advice/provenance.jsonl"),
    ("运行 manifest", "run.json"),
]


def artifact_groups(root: Path, run_id: str) -> list[tuple[str, list[dict[str, Any]]]]:
    run = load_run(root, run_id)
    base = _run_dir(root, run_id)
    groups: dict[str, list[dict[str, Any]]] = {"交付物": [], "证据": [], "清单": []}
    for label, template in EXPORT_CANDIDATES:
        rel = template.format(pack_id=run.pack_id or "pack")
        path = base / rel
        group = "交付物" if label in {"工作簿", "分析长表", "修改意见"} else (
            "清单" if label in {"运行 manifest", "人工决策", "决策留痕"} else "证据"
        )
        entry = {"label": label, "rel": str(Path("runs") / run_id / rel), "exists": path.is_file(),
                 "size": 0, "mtime": ""}
        if path.is_file():
            stat = path.stat()
            entry["size"] = stat.st_size
            entry["size_text"] = store.human_size(stat.st_size)
            entry["mtime"] = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        else:
            entry["size_text"] = "—"
        groups[group].append(entry)
    return [(name, items) for name, items in groups.items() if items]


def write_export_manifest(root: Path, run_id: str) -> Path:
    """生成导出清单：列出实际存在的产物并记哈希（G3 门禁后的动作）。"""
    run = load_run(root, run_id)
    base = _run_dir(root, run_id)
    entries = []
    for label, template in EXPORT_CANDIDATES:
        rel = template.format(pack_id=run.pack_id or "pack")
        path = base / rel
        if not path.is_file():
            continue
        entries.append(
            {
                "label": label,
                "path": str(Path("runs") / run_id / rel),
                "bytes": path.stat().st_size,
                "sha256": store.sha256_file(path),
            }
        )
    manifest = {
        "run_id": run_id,
        "pack_id": run.pack_id,
        "pack_hash": run.pack_hash,
        "created_at": _now(),
        "entries": entries,
        "note": "本清单只登记真实存在的文件；缺失项不会被补造。",
    }
    target = base / "exports" / "export_manifest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


__all__ = [
    "CRITICAL_FIELDS",
    "EXPORT_CANDIDATES",
    "RUNS_ROOT",
    "STEP_IDS",
    "STEP_SPECS",
    "RunInfo",
    "advice_stats",
    "artifact_groups",
    "build_cells",
    "create_run",
    "decide",
    "latest_run",
    "list_runs",
    "load_advice",
    "load_cells",
    "load_run",
    "materialize_results",
    "parse_revisions",
    "run_progress",
    "save_run",
    "sync_and_diff",
    "update_step",
    "write_diff",
    "write_export_manifest",
]
