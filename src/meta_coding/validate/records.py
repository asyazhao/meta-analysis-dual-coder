"""Stage 5 单模型校验：Schema → 物化 → V01–V50 → 逐篇报告与回灌清单。

每个编码员独立校验、独立出清单：coder_a 的回灌清单里不会出现 coder_b 的违规
（PROTOCOL.md §3.5「禁止互相修复」）。

产物（都在 ``data/04_validation/<coder>/`` 下）：

* ``<article_id>.json`` —— 单篇校验报告：Schema 结果、物化计数、逐条 Violation；
* ``repair_log.jsonl`` —— 回灌清单，一行一篇，只含该编码员自己的违规，
  反映最近一次运行的范围（带 ``--limit`` 时只覆盖该范围）；
* ``validation_summary.csv`` —— 逐篇汇总，便于人工扫一遍；
* ``<batch>_materialized.xlsx`` —— 整批物化工作簿，供 Stage 6 比对与抽查；
* ``run_summary.json`` —— 本次运行的计数与整批复核结果。
"""

from __future__ import annotations

import csv
import json
import tempfile
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

from ..artifacts import utc_now
from ..consensus.materialize import MaterializeResult, write_workbook
from .checks_structure import Violation
from .model import load_codebook
from .registry import all_validators, run_all, summary

SCHEMA_REL = Path("rules/schema/coder_output.schema.json")
TEMPLATE_REL = Path("rules/clean_codebook_BLANK.xlsx")
CONTRACT_REL = Path("rules/template_contract.json")
CORPUS_MANIFEST = Path("data/01_manifest/corpus_manifest.csv")
CODED_DIR = Path("data/03_coded")
VALIDATION_DIR = Path("data/04_validation")

SUMMARY_FIELDS = [
    "article_id",
    "article_key",
    "source_file",
    "status",
    "schema_ok",
    "records",
    "blocks",
    "tracking_rows",
    "excluded_rows",
    "pending_records",
    "violations",
    "auto_violations",
    "semi_violations",
    "schema_violations",
    "violation_counts",
]


def short_id(article_key: str) -> str:
    """与 Stage 4 落盘目录一致：sha256 摘要的前 16 位。"""
    return article_key.split(":")[-1][:16]


def article_dir(root: Path, coder: str, article_key: str) -> Path:
    return root / CODED_DIR / f"coder_{coder}" / short_id(article_key)


def _rel(root: Path, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def load_schema(root: Path) -> dict[str, Any]:
    path = root / SCHEMA_REL
    if not path.is_file():
        raise FileNotFoundError(f"缺少 coder_output Schema：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def read_articles(root: Path, batch: str | None = None) -> list[dict[str, str]]:
    """读语料清单中的文章行；指定批次时只保留该批次。"""
    path = root / CORPUS_MANIFEST
    if not path.is_file():
        raise FileNotFoundError(f"缺少语料清单，请先跑 intake：{path}")
    with path.open(newline="", encoding="utf-8") as fh:
        rows = [row for row in csv.DictReader(fh) if row.get("role") == "article"]
    if batch:
        rows = [row for row in rows if row.get("batch_id") in (None, "", batch)]
    return rows


def schema_violations(envelope: Any, schema: dict[str, Any]) -> list[Violation]:
    """用 jsonschema 校验信封；错误路径写进 field 列，便于回灌提示。"""
    validator = jsonschema.Draft202012Validator(schema)
    out: list[Violation] = []
    for error in sorted(
        validator.iter_errors(envelope), key=lambda err: [str(p) for p in err.absolute_path]
    ):
        path = ".".join(str(p) for p in error.absolute_path) or "(根)"
        out.append(Violation("SCHEMA", "-", None, path, error.message))
    return out


def article_references(envelope: dict[str, Any]) -> list[str]:
    """信封里出现过的 full_reference，用于 V48「每篇文章都有去向」。"""
    refs: list[str] = []
    for record in envelope.get("records") or []:
        if not isinstance(record, dict):
            continue
        fields = record.get("fields")
        if not isinstance(fields, dict):
            continue
        ref = fields.get("full_reference")
        if isinstance(ref, str) and ref.strip() and ref.strip() not in refs:
            refs.append(ref.strip())
    return refs


def _kinds() -> dict[str, str]:
    return {validator.vid: validator.kind for validator in all_validators()}


@contextmanager
def _workdir(workdir: Path | None):
    if workdir is not None:
        Path(workdir).mkdir(parents=True, exist_ok=True)
        yield Path(workdir)
        return
    with tempfile.TemporaryDirectory(prefix="meta-coding-validate-") as tmp:
        yield Path(tmp)


@dataclass
class ArticleValidation:
    root: Path
    coder: str
    batch: str
    article_key: str
    article_id: str
    source_file: str
    status: str
    schema_ok: bool
    article_status: str | None = None
    violations: list[Violation] = field(default_factory=list)
    materialize: MaterializeResult | None = None
    parsed_path: Path | None = None
    report_path: Path | None = None
    envelope: dict[str, Any] | None = field(default=None, repr=False)

    @property
    def counts(self) -> dict[str, int]:
        return summary(self.violations)

    @property
    def auto_total(self) -> int:
        kinds = _kinds()
        return sum(n for vid, n in self.counts.items() if kinds.get(vid, "auto") == "auto")

    @property
    def semi_total(self) -> int:
        kinds = _kinds()
        return sum(n for vid, n in self.counts.items() if kinds.get(vid) == "semi")

    def as_dict(self) -> dict[str, Any]:
        counts = self.counts
        materialized = self.materialize.as_dict() if self.materialize else None
        doc: dict[str, Any] = {
            "coder": self.coder,
            "batch": self.batch,
            "article_key": self.article_key,
            "article_id": self.article_id,
            "source_file": self.source_file,
            "status": self.status,
            "schema_ok": self.schema_ok,
            "article_status": self.article_status,
            "generated_at": utc_now(),
            "parsed_path": _rel(self.root, self.parsed_path),
            "records": materialized["records"] if materialized else 0,
            "violation_total": len(self.violations),
            "violation_counts": counts,
            "auto_violations": self.auto_total,
            "semi_violations": self.semi_total,
            "schema_violations": counts.get("SCHEMA", 0),
            "violations": [item.as_dict() for item in self.violations],
            "materialize": materialized,
        }
        return doc


def validate_article(
    root: Path,
    coder: str,
    batch: str,
    article: dict[str, str],
    *,
    schema: dict[str, Any] | None = None,
    workdir: Path | None = None,
) -> ArticleValidation:
    """校验一篇：读 parsed.json → jsonschema → 物化临时工作簿 → run_all。"""
    root = Path(root)
    key = str(article.get("article_key") or "")
    parsed = article_dir(root, coder, key) / "parsed.json"
    report = ArticleValidation(
        root=root,
        coder=coder,
        batch=batch,
        article_key=key,
        article_id=short_id(key),
        source_file=str(article.get("source_file") or ""),
        status="missing_output",
        schema_ok=False,
        parsed_path=parsed,
    )
    if not parsed.is_file():
        return report

    try:
        envelope = json.loads(parsed.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        report.status = "parse_error"
        report.violations = [
            Violation("SCHEMA", "-", None, "parsed.json", f"JSON 解析失败：{exc}")
        ]
        return report

    if isinstance(envelope, dict):
        status_value = envelope.get("article_status")
        report.article_status = status_value if isinstance(status_value, str) else None

    schema = schema if schema is not None else load_schema(root)
    report.violations = schema_violations(envelope, schema)
    report.schema_ok = not report.violations
    report.status = "ok" if report.schema_ok else "schema_error"

    if not isinstance(envelope, dict) or not isinstance(envelope.get("records"), list):
        # jsonschema 已经就 records 缺失/类型不符报过 SCHEMA，这里只标记不可物化。
        report.schema_ok = False
        report.status = "schema_error"
        return report
    report.envelope = envelope

    with _workdir(workdir) as tmp:
        workbook = tmp / f"{report.article_id}.xlsx"
        materialized = write_workbook([envelope], root / TEMPLATE_REL, workbook)
        codebook = load_codebook(
            workbook, root / CONTRACT_REL, keep_sex_placeholders=True
        )
        report.violations.extend(
            run_all(codebook, {"articles": article_references(envelope)})
        )
        report.materialize = materialized
    return report


def write_report(report: ArticleValidation, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{report.article_id}.json"
    path.write_text(
        json.dumps(report.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    report.report_path = path
    return path


def _counts_text(counts: dict[str, int]) -> str:
    return ";".join(f"{vid}:{n}" for vid, n in counts.items())


def _write_repair_log(path: Path, reports: list[ArticleValidation]) -> int:
    """一行一篇；没有违规的篇目不出现，保证回灌清单只包含需要修的内容。"""
    written = 0
    with path.open("w", encoding="utf-8") as fh:
        for report in reports:
            if not report.violations:
                continue
            entry = {
                "coder": report.coder,
                "batch": report.batch,
                "article_key": report.article_key,
                "article_id": report.article_id,
                "source_file": report.source_file,
                "violation_total": len(report.violations),
                "violation_counts": report.counts,
                "violations": [item.as_dict() for item in report.violations],
            }
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            written += 1
    return written


def _write_summary_csv(path: Path, reports: list[ArticleValidation]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        for report in reports:
            materialized = report.materialize
            blocks = materialized.blocks if materialized else {}
            writer.writerow(
                {
                    "article_id": report.article_id,
                    "article_key": report.article_key,
                    "source_file": report.source_file,
                    "status": report.status,
                    "schema_ok": report.schema_ok,
                    "records": materialized.records if materialized else 0,
                    "blocks": json.dumps(blocks, ensure_ascii=False),
                    "tracking_rows": materialized.tracking_rows if materialized else 0,
                    "excluded_rows": materialized.excluded_rows if materialized else 0,
                    "pending_records": (
                        materialized.pending_records if materialized else 0
                    ),
                    "violations": len(report.violations),
                    "auto_violations": report.auto_total,
                    "semi_violations": report.semi_total,
                    "schema_violations": report.counts.get("SCHEMA", 0),
                    "violation_counts": _counts_text(report.counts),
                }
            )


@dataclass
class ValidateResult:
    coder: str
    batch: str
    articles: int
    outputs: int
    missing_output: int
    parse_errors: int
    schema_errors: int
    violation_total: int
    auto_total: int
    semi_total: int
    pending_total: int
    repair_articles: int
    by_vid: dict[str, int]
    reports: list[ArticleValidation]
    out_dir: Path
    repair_log: Path
    summary_csv: Path
    summary_json: Path
    workbook: Path | None
    workbook_blocks: dict[str, int]
    batch_violations: dict[str, int]


def run_validate(
    root: Path, coder: str, batch: str, *, limit: int | None = None
) -> ValidateResult:
    """跑一个编码员的一个批次；逐篇落报告，最后出整批清单与物化工作簿。"""
    root = Path(root)
    schema = load_schema(root)
    articles = read_articles(root, batch)
    if limit is not None:
        articles = articles[:limit]

    out_dir = root / VALIDATION_DIR / f"coder_{coder}"
    out_dir.mkdir(parents=True, exist_ok=True)

    reports: list[ArticleValidation] = []
    envelopes: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="meta-coding-validate-") as tmp:
        for article in articles:
            report = validate_article(
                root, coder, batch, article, schema=schema, workdir=Path(tmp)
            )
            write_report(report, out_dir)
            reports.append(report)
            if report.envelope is not None and report.materialize is not None:
                envelopes.append(report.envelope)

    repair_log = out_dir / "repair_log.jsonl"
    repair_articles = _write_repair_log(repair_log, reports)
    summary_csv = out_dir / "validation_summary.csv"
    _write_summary_csv(summary_csv, reports)

    workbook: Path | None = None
    workbook_blocks: dict[str, int] = {}
    batch_violations: dict[str, int] = {}
    if envelopes:
        workbook = out_dir / f"{batch}_materialized.xlsx"
        materialized = write_workbook(envelopes, root / TEMPLATE_REL, workbook)
        workbook_blocks = dict(materialized.blocks)
        codebook = load_codebook(
            workbook, root / CONTRACT_REL, keep_sex_placeholders=True
        )
        refs: list[str] = []
        for envelope in envelopes:
            refs.extend(ref for ref in article_references(envelope) if ref not in refs)
        batch_violations = summary(run_all(codebook, {"articles": refs}))

    by_vid: Counter[str] = Counter()
    for report in reports:
        by_vid.update(report.counts)

    result = ValidateResult(
        coder=coder,
        batch=batch,
        articles=len(reports),
        outputs=sum(1 for r in reports if r.status != "missing_output"),
        missing_output=sum(1 for r in reports if r.status == "missing_output"),
        parse_errors=sum(1 for r in reports if r.status == "parse_error"),
        schema_errors=sum(1 for r in reports if r.status == "schema_error"),
        violation_total=sum(len(r.violations) for r in reports),
        auto_total=sum(r.auto_total for r in reports),
        semi_total=sum(r.semi_total for r in reports),
        pending_total=sum(
            r.materialize.pending_records for r in reports if r.materialize is not None
        ),
        repair_articles=repair_articles,
        by_vid=dict(sorted(by_vid.items())),
        reports=reports,
        out_dir=out_dir,
        repair_log=repair_log,
        summary_csv=summary_csv,
        summary_json=out_dir / "run_summary.json",
        workbook=workbook,
        workbook_blocks=workbook_blocks,
        batch_violations=batch_violations,
    )

    summary_doc = {
        "stage": "05_validation",
        "coder": coder,
        "batch": batch,
        "generated_at": utc_now(),
        "articles": result.articles,
        "outputs": result.outputs,
        "missing_output": result.missing_output,
        "parse_errors": result.parse_errors,
        "schema_errors": result.schema_errors,
        "violation_total": result.violation_total,
        "auto_violations": result.auto_total,
        "semi_violations": result.semi_total,
        "pending_records": result.pending_total,
        "repair_articles": result.repair_articles,
        "article_status_counts": dict(
            sorted(Counter(r.article_status or "（未标注）" for r in reports).items())
        ),
        "by_vid": result.by_vid,
        "materialized_workbook": _rel(root, workbook),
        "materialized_blocks": result.workbook_blocks,
        "batch_violation_counts": result.batch_violations,
        "batch_violation_total": sum(result.batch_violations.values()),
    }
    result.summary_json.write_text(
        json.dumps(summary_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


__all__ = [
    "ArticleValidation",
    "ValidateResult",
    "article_dir",
    "article_references",
    "load_schema",
    "read_articles",
    "run_validate",
    "schema_violations",
    "short_id",
    "validate_article",
    "write_report",
]
