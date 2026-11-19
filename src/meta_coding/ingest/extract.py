"""Stage 3 — 把 PDF 变成两个编码员拿到的**同一份**输入包。

规则（CODING_PLAN.md §3 全文检索范围、§7.1 证据位置）：
* 每页保留页码锚点，任何引文都能回指页码；
* 表格单独抽出，保留页码与表号线索，输出 Markdown + CSV；
* 扫描页不静默跳过，标记 ``ocr=true`` 并进入待 OCR 队列；
* 同一篇文章的 packet 字节级稳定，因此 A/B 拿到的 ``packet_hash`` 必然相同。
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf

CAPTION_RE = re.compile(r"^\s*(table|figure)\s*([0-9]+[a-zA-Z]?)\b", re.IGNORECASE)


@dataclass
class PageText:
    page: int
    text: str
    chars: int
    ocr: bool


@dataclass
class TableBlock:
    page: int
    index: int
    label: str
    caption: str
    rows: list[list[str]]
    markdown: str


def _caption_near(page: pymupdf.Page, bbox) -> tuple[str, str]:
    """Try to find 'Table N' in the lines just above the table bbox."""
    try:
        blocks = page.get_text("blocks")
    except Exception:  # noqa: BLE001
        return "", ""
    best = ""
    _x0, top = float(bbox[0]), float(bbox[1])
    for block in blocks:
        _bx0, y0, _bx1, y1 = block[:4]
        if y1 <= top + 4 and y0 >= top - 90:
            text = str(block[4]).strip().splitlines()
            if text:
                best = text[0].strip()
    match = CAPTION_RE.match(best)
    label = f"{match.group(1).title()} {match.group(2)}" if match else ""
    return label, best


def _to_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    grid = [list(r) + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(grid[0]) + " |",
             "|" + "|".join(["---"] * width) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in grid[1:]]
    return "\n".join(lines)


def extract_pages(doc: pymupdf.Document) -> list[PageText]:
    out: list[PageText] = []
    for index, page in enumerate(doc):
        text = page.get_text("text") or ""
        out.append(
            PageText(
                page=index + 1,
                text=text,
                chars=len(text),
                ocr=len(text.strip()) < 50,
            )
        )
    return out


def extract_tables(doc: pymupdf.Document, max_tables: int = 400) -> list[TableBlock]:
    blocks: list[TableBlock] = []
    for index, page in enumerate(doc):
        try:
            found = page.find_tables()
        except Exception:  # noqa: BLE001 - 表格识别失败不影响正文
            continue
        for table in getattr(found, "tables", [])[:20]:
            try:
                rows = [[" ".join(str(c).split()) if c else "" for c in row]
                        for row in table.extract()]
            except Exception:  # noqa: BLE001
                continue
            if len(rows) < 2 or not any(any(cell for cell in row) for row in rows):
                continue
            label, caption = _caption_near(page, table.bbox)
            blocks.append(
                TableBlock(
                    page=index + 1,
                    index=len(blocks) + 1,
                    label=label,
                    caption=caption[:200],
                    rows=rows,
                    markdown=_to_markdown(rows),
                )
            )
            if len(blocks) >= max_tables:
                return blocks
    return blocks


def canonical_hash(payload: dict[str, Any]) -> str:
    body = {k: v for k, v in payload.items() if k != "packet_hash"}
    blob = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def write_csv(path: Path, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)


def build_packet(pdf: Path, article_key: str, batch: str) -> dict[str, Any]:
    doc = pymupdf.open(pdf)
    try:
        pages = extract_pages(doc)
        tables = extract_tables(doc)
        meta = doc.metadata or {}
    finally:
        doc.close()

    packet = {
        "article_key": article_key,
        "batch_id": batch,
        "source_file": pdf.name,
        "page_count": len(pages),
        "biblio": {
            "doi": "",
            "title_guess": (meta.get("title") or "").strip(),
            "author_guess": (meta.get("author") or "").strip(),
        },
        "material_status": {
            "text_quality": "good" if sum(p.chars for p in pages) > 2000 else "poor",
            "scanned_pages": [p.page for p in pages if p.ocr],
            "ocr": any(p.ocr for p in pages),
            "supplement_files": [],
        },
        "pages": [{"page": p.page, "text": p.text} for p in pages],
        "tables": [
            {
                "page": t.page,
                "index": t.index,
                "label": t.label,
                "caption": t.caption,
                "n_rows": len(t.rows),
                "n_cols": max((len(r) for r in t.rows), default=0),
                "markdown": t.markdown,
            }
            for t in tables
        ],
    }
    packet["packet_hash"] = canonical_hash(packet)
    return packet


def write_packet(
    root: Path, packet: dict[str, Any], tables: list[TableBlock] | None = None
) -> Path:
    target = root / "data" / "02_ingest" / packet["article_key"].split(":")[-1][:16]
    target.mkdir(parents=True, exist_ok=True)

    (target / "packet.json").write_text(
        json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (target / "pages.jsonl").open("w", encoding="utf-8") as fh:
        for page in packet["pages"]:
            fh.write(json.dumps(page, ensure_ascii=False) + "\n")
    if tables:
        for table in tables:
            name = f"p{table.page:03d}_t{table.index:02d}"
            (target / "tables").mkdir(exist_ok=True)
            write_csv(target / "tables" / f"{name}.csv", table.rows)
            (target / "tables" / f"{name}.md").write_text(
                f"# {table.label or 'Table'} (page {table.page})\n\n{table.caption}\n\n"
                f"{table.markdown}\n",
                encoding="utf-8",
            )
    (target / "PACKET_HASH").write_text(packet["packet_hash"] + "\n", encoding="utf-8")
    return target
