"""Stage 3 编排：批量摄取 + QA 表 + manifest。"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..artifacts import build_manifest, git_head, make_run_id, write_manifest
from ..config import Config
from ..paths import CORPUS_MANIFEST
from .extract import build_packet, write_packet

QA_FIELDS = [
    "article_key",
    "source_file",
    "pages",
    "chars_total",
    "empty_pages",
    "tables",
    "scanned_pages",
    "packet_hash",
    "status",
]


@dataclass
class IngestResult:
    articles: int
    packets: int
    total_chars: int
    total_tables: int
    scanned: int
    qa_path: Path
    manifest_path: Path
    run_id: str


def _read_articles(root: Path) -> list[dict[str, str]]:
    path = root / CORPUS_MANIFEST
    if not path.is_file():
        raise FileNotFoundError(f"缺少语料清单，请先跑 intake：{path}")
    with path.open(newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if r.get("role") == "article"]


def run_ingest(
    root: Path,
    cfg: Config,
    *,
    limit: int | None = None,
    dry_run: bool = False,
) -> IngestResult:
    input_dir = Path(cfg.pipeline.input_dir)
    articles = _read_articles(root)
    if limit:
        articles = articles[:limit]

    qa_rows: list[dict[str, Any]] = []
    packets = 0
    total_chars = 0
    total_tables = 0
    scanned = 0
    outputs: list[Path] = []

    for article in articles:
        pdf = input_dir / article["source_file"]
        key = article["article_key"]
        if not pdf.is_file():
            qa_rows.append({
                "article_key": key, "source_file": article["source_file"],
                "pages": 0, "chars_total": 0, "empty_pages": 0, "tables": 0,
                "scanned_pages": 0, "packet_hash": "", "status": "source_missing",
            })
            continue

        packet = build_packet(pdf, key, cfg.pipeline.batch_id)
        total_chars += sum(len(p["text"]) for p in packet["pages"])
        total_tables += len(packet["tables"])
        if packet["material_status"]["ocr"]:
            scanned += 1

        if not dry_run:
            target = write_packet(root, packet, None)
            outputs.append(target / "packet.json")
            packets += 1

        qa_rows.append({
            "article_key": key,
            "source_file": article["source_file"],
            "pages": packet["page_count"],
            "chars_total": sum(len(p["text"]) for p in packet["pages"]),
            "empty_pages": len(packet["material_status"]["scanned_pages"]),
            "tables": len(packet["tables"]),
            "scanned_pages": len(packet["material_status"]["scanned_pages"]),
            "packet_hash": packet["packet_hash"],
            "status": "ok" if packets else "dry_run",
        })

    qa_path = root / "data" / "02_ingest" / "ingest_qa.csv"
    qa_path.parent.mkdir(parents=True, exist_ok=True)
    with qa_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=QA_FIELDS)
        writer.writeheader()
        writer.writerows(qa_rows)

    if dry_run:
        return IngestResult(
            articles=len(articles), packets=0, total_chars=total_chars,
            total_tables=total_tables, scanned=scanned, qa_path=qa_path,
            manifest_path=Path(), run_id="",
        )

    run_id = make_run_id(f"ingest-{cfg.pipeline.batch_id}-{len(articles)}")
    manifest = build_manifest(
        root=root,
        stage="03_ingest",
        run_id=run_id,
        git_commit=git_head(root),
        rules_hash=cfg.rules_hash,
        config_hash=cfg.config_hash,
        outputs=[p for p in outputs if p.is_file()] + [qa_path],
        external=[*outputs, qa_path],
        counts={
            "articles": len(articles),
            "packets": packets,
            "total_chars": total_chars,
            "total_tables": total_tables,
            "scanned_articles": scanned,
        },
        notes=f"input_dir={input_dir}",
    )
    manifest_path = write_manifest(root, "03_ingest", manifest)

    return IngestResult(
        articles=len(articles),
        packets=packets,
        total_chars=total_chars,
        total_tables=total_tables,
        scanned=scanned,
        qa_path=qa_path,
        manifest_path=manifest_path,
        run_id=run_id,
    )
