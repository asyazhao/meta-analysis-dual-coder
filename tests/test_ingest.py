"""Stage 1 逻辑测试：不依赖真实 PDF，用合成的 PdfFacts 覆盖角色判定边界。"""

from __future__ import annotations

from meta_coding.ingest.inventory import PdfFacts, quality_label
from meta_coding.ingest.keys import (
    article_key_from_doi,
    find_dois,
    normalize_doi,
    record_id,
)
from meta_coding.ingest.pipeline import classify


def facts(name: str, *, doi: str = "", sha: str = "", supplement: bool = False,
          scanned: bool = False) -> PdfFacts:
    return PdfFacts(
        source_file=name,
        sha256=sha or f"sha-{name}",
        bytes=1000,
        pages=10,
        chars_total=20000,
        chars_per_page_median=2000.0,
        text_quality="none" if scanned else "good",
        is_scanned=scanned,
        doi=doi,
        filename_title=name,
        looks_like_supplement=supplement,
    )


def test_doi_normalisation_variants() -> None:
    for raw in (
        "10.1037/hea0000123",
        "https://doi.org/10.1037/hea0000123",
        "doi:10.1037/HEA0000123",
        "10.1037/hea0000123.",
    ):
        assert normalize_doi(raw) == "10.1037/hea0000123"


def test_doi_extraction_ignores_duplicates() -> None:
    text = "See https://doi.org/10.1007/s40519-023-01529-4 and 10.1007/S40519-023-01529-4."
    assert find_dois(text) == ["10.1007/s40519-023-01529-4"]


def test_same_doi_same_article_key() -> None:
    a = article_key_from_doi("10.1037/hea0000123")
    b = article_key_from_doi("https://doi.org/10.1037/HEA0000123")
    assert a == b
    assert a.startswith("sha256:")


def test_record_id_shape() -> None:
    assert record_id("sha256:abc", "WBIS", "b01", 1) == "sha256:abc|WBIS|b01|1"


def test_identical_file_is_duplicate() -> None:
    rows = classify(
        [facts("a.pdf", doi="10.1/x", sha="same"), facts("b.pdf", doi="10.1/x", sha="same")]
    )
    roles = {r["source_file"]: r for r in rows}
    assert roles["a.pdf"]["role"] == "article"
    assert roles["b.pdf"]["role"] == "duplicate"
    assert roles["b.pdf"]["duplicate_of"] == "a.pdf"


def test_second_file_same_doi_is_duplicate() -> None:
    rows = classify([facts("a.pdf", doi="10.1/x"), facts("b.pdf", doi="10.1/x")])
    roles = {r["source_file"]: r for r in rows}
    assert roles["a.pdf"]["role"] == "article"
    assert roles["b.pdf"]["role"] == "duplicate"
    assert roles["b.pdf"]["status"] == "duplicate_doi"


def test_supplement_is_not_a_new_article() -> None:
    rows = classify(
        [facts("paper.pdf", doi="10.1/x"), facts("paper_supplement.pdf", doi="10.1/x",
                                                 supplement=True)]
    )
    roles = {r["source_file"]: r for r in rows}
    assert roles["paper_supplement.pdf"]["role"] == "supplement"
    assert roles["paper_supplement.pdf"]["duplicate_of"] == "paper.pdf"
    articles = [r for r in rows if r["role"] == "article"]
    assert len(articles) == 1


def test_every_file_gets_exactly_one_role() -> None:
    rows = classify(
        [
            facts("a.pdf", doi="10.1/a"),
            facts("a_copy.pdf", doi="10.1/a", sha="same-a"),
            facts("b.pdf", doi="10.1/b"),
            facts("b_supp.pdf", doi="10.1/b", supplement=True),
            facts("c.pdf"),
        ]
    )
    assert all(r["role"] in {"article", "supplement", "duplicate"} for r in rows)
    assert len(rows) == 5


def test_quality_labels() -> None:
    assert quality_label(2000, False) == "good"
    assert quality_label(120, False) == "mixed"
    assert quality_label(10, False) == "poor"
    assert quality_label(10, True) == "none"
