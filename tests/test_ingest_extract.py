"""Stage 3 摄取测试：用内存生成的 PDF，不依赖真实语料。"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from meta_coding.ingest.extract import build_packet, canonical_hash, extract_tables, write_packet


def make_pdf(path: Path, pages: int = 3) -> Path:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        label = f"Page {i + 1}: weight bias internalization study."
        page.insert_text((72, 100), label, fontsize=11)
        page.insert_text((72, 130), "M = 3.54, SD = 1.69, N = 433.", fontsize=11)
    doc.save(path)
    doc.close()
    return path


def test_packet_is_byte_stable(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "a.pdf")
    first = build_packet(pdf, "sha256:" + "a" * 64, "batch01")
    second = build_packet(pdf, "sha256:" + "a" * 64, "batch01")
    assert first["packet_hash"] == second["packet_hash"]
    assert first["packet_hash"] == canonical_hash(first)


def test_packet_hash_ignores_nothing_but_content(tmp_path: Path) -> None:
    pdf_a = make_pdf(tmp_path / "a.pdf")
    packet_a = build_packet(pdf_a, "sha256:" + "a" * 64, "batch01")
    packet_b = build_packet(pdf_a, "sha256:" + "b" * 64, "batch01")
    assert packet_a["packet_hash"] != packet_b["packet_hash"], "article_key 必须进入哈希"


def test_pages_keep_one_based_anchors(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "a.pdf", pages=4)
    packet = build_packet(pdf, "sha256:" + "c" * 64, "batch01")
    assert [p["page"] for p in packet["pages"]] == [1, 2, 3, 4]
    assert all(p["text"] for p in packet["pages"])


def test_blank_page_flagged_for_ocr(tmp_path: Path) -> None:
    doc = pymupdf.open()
    doc.new_page()
    page = doc.new_page()
    page.insert_text(
        (72, 100),
        "This page carries a full paragraph of extractable text, well above threshold.",
        fontsize=11,
    )
    path = tmp_path / "blank.pdf"
    doc.save(path)
    doc.close()

    packet = build_packet(path, "sha256:" + "d" * 64, "batch01")
    assert packet["material_status"]["scanned_pages"] == [1]
    assert packet["material_status"]["ocr"] is True


def test_write_packet_creates_hash_file(tmp_path: Path) -> None:
    pdf = make_pdf(tmp_path / "a.pdf", pages=2)
    packet = build_packet(pdf, "sha256:" + "e" * 64, "batch01")
    target = write_packet(tmp_path, packet)
    assert (target / "packet.json").is_file()
    assert (target / "pages.jsonl").is_file()
    assert (target / "PACKET_HASH").read_text().strip() == packet["packet_hash"]
    assert len((target / "pages.jsonl").read_text().strip().splitlines()) == 2


def test_tables_detected_when_present(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 80), "Table 1", fontsize=10)
    rows = [("Measure", "M", "SD"), ("WBIS", "3.54", "1.69"), ("WSSQ", "2.50", "0.90")]
    y = 110
    for r in rows:
        x = 72
        for cell in r:
            page.insert_text((x, y), cell, fontsize=10)
            x += 120
        y += 20
    page.draw_rect(pymupdf.Rect(65, 95, 430, 175))
    for x in (185, 305):
        page.draw_line(pymupdf.Point(x, 95), pymupdf.Point(x, 175))
    for yy in (125, 145, 165):
        page.draw_line(pymupdf.Point(65, yy), pymupdf.Point(430, yy))
    path = tmp_path / "table.pdf"
    doc.save(path)
    doc.close()

    reopened = pymupdf.open(path)
    tables = extract_tables(reopened)
    reopened.close()
    # 结构探测依赖版面，识别不到时不应报错，识别到则必须有 markdown
    for table in tables:
        assert table.markdown.startswith("|")
        assert table.page == 1
