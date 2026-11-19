"""Stage 1 — turn a folder of PDFs into an article list.

Rules enforced here (CODING_PLAN.md §10.1):
* one article may be reported by several files (supplements, duplicates);
* the batch's N counts articles, not files;
* files + supplements + duplicates == file total, always.
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..artifacts import build_manifest, git_head, make_run_id, write_manifest
from ..config import Config
from ..paths import CORPUS_MANIFEST, REGISTRY
from .inventory import PdfFacts, scan_pdf
from .keys import article_key_from_citation, article_key_from_doi
from .registry import load_registry, merge_rows, save_registry

MANIFEST_FIELDS = [
    "article_key",
    "source_file",
    "sha256",
    "pages",
    "text_quality",
    "is_scanned",
    "role",
    "duplicate_of",
    "batch_id",
    "status",
    "doi",
    "title_guess",
    "author_guess",
    "year_guess",
    "doc_type_hint",
    "chars_total",
    "bytes",
]


@dataclass
class IntakeResult:
    files: int
    articles: int
    supplements: int
    duplicates: int
    scanned: int
    no_doi: int
    unreadable: int
    manifest_path: Path
    registry_path: Path
    report_path: Path
    manifest_json_path: Path


def _fallback_key(facts: PdfFacts) -> str:
    digest = hashlib.sha256(facts.sha256.encode("utf-8")).hexdigest()
    return "sha256:" + digest


def key_for(facts: PdfFacts) -> tuple[str, str]:
    """article_key plus the evidence used to build it.

    DOI first (stable across batches); otherwise normalised title + first author
    + year, which is what dissertations rely on; file hash only as a last resort.
    """
    if facts.doi:
        return article_key_from_doi(facts.doi), "doi"
    if facts.title_guess and facts.author_guess and facts.year_guess:
        return (
            article_key_from_citation(facts.title_guess, facts.author_guess, facts.year_guess),
            "citation",
        )
    return _fallback_key(facts), "file_hash"


def classify(facts: list[PdfFacts]) -> list[dict[str, Any]]:
    """Assign article_key / role / duplicate_of to every file."""
    by_sha: dict[str, list[PdfFacts]] = {}
    for f in facts:
        by_sha.setdefault(f.sha256, []).append(f)

    canonical_by_sha = {sha: group[0] for sha, group in by_sha.items()}

    # first file (deterministic: name order) that carries each DOI becomes the article
    doi_owner: dict[str, PdfFacts] = {}
    for f in sorted(facts, key=lambda x: x.source_file):
        if f.doi and not f.looks_like_supplement and f.doi not in doi_owner:
            doi_owner[f.doi] = f

    rows: list[dict[str, Any]] = []
    for f in sorted(facts, key=lambda x: x.source_file):
        article_key, key_source = key_for(f)
        row: dict[str, Any] = {field: "" for field in MANIFEST_FIELDS}
        row.update(
            source_file=f.source_file,
            sha256=f.sha256,
            pages=f.pages,
            text_quality=f.text_quality,
            is_scanned=f.is_scanned,
            doi=f.doi,
            title_guess=f.title_guess,
            author_guess=f.author_guess,
            year_guess=f.year_guess,
            doc_type_hint=f.doc_type_hint,
            chars_total=f.chars_total,
            bytes=f.bytes,
            article_key=article_key,
        )

        owner = doi_owner.get(f.doi) if f.doi else None
        is_copy = canonical_by_sha[f.sha256] is not f

        if is_copy:
            row["role"] = "duplicate"
            row["duplicate_of"] = canonical_by_sha[f.sha256].source_file
            row["status"] = "duplicate_file"
        elif f.looks_like_supplement and owner is not None:
            row["role"] = "supplement"
            row["duplicate_of"] = owner.source_file
            row["article_key"] = article_key_from_doi(owner.doi)
            row["status"] = "supplement"
        elif owner is not None and owner is not f:
            # second, different file for an already-seen DOI
            row["role"] = "duplicate"
            row["duplicate_of"] = owner.source_file
            row["article_key"] = article_key_from_doi(owner.doi)
            row["status"] = "duplicate_doi"
        else:
            row["role"] = "supplement" if f.looks_like_supplement else "article"
            row["status"] = {
                "doi": "ok",
                "citation": "ok_citation_key",
                "file_hash": "needs_review",
            }[key_source]

        if f.error:
            row["status"] = "source_unreadable"
        row["batch_id"] = ""  # filled by caller
        rows.append(row)
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _render_report(result: IntakeResult, rows: list[dict[str, Any]], batch: str) -> str:
    quality: dict[str, int] = {}
    for row in rows:
        quality[row["text_quality"]] = quality.get(row["text_quality"], 0) + 1
    scanned = [r for r in rows if r["is_scanned"]]
    review = [r for r in rows if r["status"] in {"needs_review", "source_unreadable"}]
    dupes = [r for r in rows if r["role"] == "duplicate"]

    body = result.articles + result.supplements + result.duplicates
    lines = [
        f"# 批次 `{batch}` 语料清点报告",
        "",
        "## 计数",
        "",
        "| 项目 | 数量 |",
        "|---|---|",
        f"| PDF 文件 | {result.files} |",
        f"| 文章（N，按文章计） | **{result.articles}** |",
        f"| 补充材料 | {result.supplements} |",
        f"| 重复文件 | {result.duplicates} |",
        f"| 文件数校验（文章+补充+重复） | {body} |",
        "",
        f"与文件总数一致：{'是' if body == result.files else '**否**'}",
        "",
        "## 文本质量",
        "",
        "| 标签 | 文件数 |",
        "|---|---|",
    ]
    lines += [f"| {k} | {v} |" for k, v in sorted(quality.items())]
    no_doi = [r for r in rows if not r["doi"]]
    lines += [
        "",
        f"- 疑似扫描件：{len(scanned)} 个 → 待 OCR 队列",
        f"- 无 DOI：{len(no_doi)} 个（改用标题+第一作者+年份生成 article_key）",
        f"- 无法解析题录、article_key 退化为文件哈希：{len(review)} 个",
        "",
    ]
    if scanned:
        lines += ["### 待 OCR", "", "| 文件 | 页数 | 每页中位字符 |", "|---|---|---|"]
        lines += [f"| {r['source_file']} | {r['pages']} | {r['chars_total']} |" for r in scanned]
        lines.append("")
    if dupes:
        lines += ["### 重复文件", "", "| 文件 | 原文 | 依据 |", "|---|---|---|"]
        lines += [f"| {r['source_file']} | {r['duplicate_of']} | {r['status']} |" for r in dupes]
        lines.append("")
    if review:
        lines += ["### 需人工复核", "", "| 文件 | 状态 | 原因 |", "|---|---|---|"]
        reason = "无 DOI 且题名/作者/年份无法解析，article_key 退化为文件哈希"
        lines += [f"| {r['source_file']} | {r['status']} | {reason} |" for r in review]
        lines.append("")
    lines += [
        "## 结论",
        "",
        f"本批次实际篇数 **N = {result.articles}**（不以 PDF 文件数计）。",
        "所有文件均已分配且仅分配一个角色，重复项均有 `duplicate_of` 指向。",
        "",
    ]
    return "\n".join(lines)


def run_intake(root: Path, cfg: Config, *, run_id: str | None = None) -> IntakeResult:
    input_dir = Path(cfg.pipeline.input_dir)
    if not input_dir.is_dir():
        raise FileNotFoundError(f"批次目录不存在：{input_dir}")

    pdfs = sorted(p for p in input_dir.rglob("*") if p.suffix.lower() == ".pdf")
    if not pdfs:
        raise FileNotFoundError(f"批次目录内没有 PDF：{input_dir}")

    facts = [scan_pdf(p) for p in pdfs]
    rows = classify(facts)
    batch = cfg.pipeline.batch_id
    for row in rows:
        row["batch_id"] = batch

    articles = [r for r in rows if r["role"] == "article" and r["status"] != "source_unreadable"]
    supplements = [r for r in rows if r["role"] == "supplement"]
    duplicates = [r for r in rows if r["role"] == "duplicate"]

    manifest_path = root / CORPUS_MANIFEST
    _write_csv(manifest_path, rows, MANIFEST_FIELDS)

    registry_path = root / REGISTRY
    old = load_registry(registry_path)
    new_rows = [
        {
            "article_key": r["article_key"],
            "doi": r["doi"],
            "batch_id": batch,
            "source_file": r["source_file"],
            "sha256": r["sha256"],
            "pages": str(r["pages"]),
            "role": r["role"],
            "status": r["status"],
            "first_seen": make_run_id(r["article_key"])[:15],
        }
        for r in rows
        if r["role"] != "duplicate"
    ]
    save_registry(registry_path, merge_rows(old, new_rows))

    report_path = root / "reports" / "intake_report.md"

    result = IntakeResult(
        files=len(rows),
        articles=len(articles),
        supplements=len(supplements),
        duplicates=len(duplicates),
        scanned=sum(1 for r in rows if r["is_scanned"]),
        no_doi=sum(1 for r in rows if not r["doi"]),
        unreadable=sum(1 for r in rows if r["status"] == "source_unreadable"),
        manifest_path=manifest_path,
        registry_path=registry_path,
        report_path=report_path,
        manifest_json_path=Path(),
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_render_report(result, rows, batch), encoding="utf-8")

    run_id = run_id or make_run_id(f"{batch}-{len(rows)}")
    manifest = build_manifest(
        root=root,
        stage="01_intake",
        run_id=run_id,
        git_commit=git_head(root),
        rules_hash=cfg.rules_hash,
        config_hash=cfg.config_hash,
        outputs=[manifest_path, registry_path, report_path],
        counts={
            "files": result.files,
            "articles": result.articles,
            "supplements": result.supplements,
            "duplicates": result.duplicates,
            "scanned": result.scanned,
            "no_doi": result.no_doi,
            "unreadable": result.unreadable,
        },
        checks=[
            {
                "id": "INV-ROLE",
                "status": "pass"
                if result.articles + result.supplements + result.duplicates == result.files
                else "fail",
                "failures": 0
                if result.articles + result.supplements + result.duplicates == result.files
                else 1,
            }
        ],
        notes=f"input_dir={input_dir}; 按文章计 N={result.articles}",
    )
    result.manifest_json_path = write_manifest(root, "01_intake", manifest)
    return result
