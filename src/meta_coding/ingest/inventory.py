"""Per-PDF inventory: hashes, page geometry, text quality, DOI/title guesses.

Read-only on the PDF. Everything returned here is metadata; no text longer than
a short title fragment leaves this module.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pymupdf

from .keys import find_dois, normalize_doi

MIN_CHARS_PER_PAGE = 200
SCANNED_CHARS_PER_PAGE = 50

_SUPPLEMENT_HINTS = re.compile(r"(supplement|supplementary|appendix|_supp\b|_s\d+\.pdf)", re.I)


@dataclass
class PdfFacts:
    source_file: str
    sha256: str
    bytes: int
    pages: int
    chars_total: int
    chars_per_page_median: float
    text_quality: str          # good | mixed | poor | none
    is_scanned: bool
    doi: str
    all_dois: list[str] = field(default_factory=list)
    meta_title: str = ""
    filename_title: str = ""
    title_guess: str = ""
    author_guess: str = ""
    year_guess: str = ""
    doc_type_hint: str = ""
    looks_like_supplement: bool = False
    error: str = ""

    def to_row(self) -> dict[str, object]:
        return asdict(self)


_STOP_TOKENS = (
    "by",
    "a dissertation",
    "a thesis",
    "abstract",
    "submitted",
    "in partial",
    "advisor",
    "director of",
    "major department",
    "university of",
    "department of",
)


def _title_from_page(text: str, max_chars: int = 300) -> str:
    """Collect the leading title block: stop at 'By' / 'A Dissertation' / 'ABSTRACT'."""
    picked: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if picked and (low.startswith(_STOP_TOKENS) or "phd" in low or ", ph.d" in low):
            break
        if not picked and low.startswith(_STOP_TOKENS):
            continue
        picked.append(line)
        if sum(len(p) for p in picked) >= max_chars:
            break
    return " ".join(picked)[:max_chars].strip()


def _first_year(text: str) -> str:
    for match in re.finditer(r"\b(19|20)\d{2}\b", text):
        year = int(match.group(0))
        if 1900 <= year <= 2035:
            return str(year)
    return ""


def _doc_type_hint(text: str) -> str:
    low = text.lower()
    if "dissertation" in low:
        return "dissertation"
    if "thesis" in low:
        return "thesis"
    return ""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _median(values: list[int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def quality_label(median_chars: float, scanned: bool) -> str:
    if scanned:
        return "none"
    if median_chars >= MIN_CHARS_PER_PAGE:
        return "good"
    if median_chars >= SCANNED_CHARS_PER_PAGE:
        return "mixed"
    return "poor"


def scan_pdf(path: Path, *, doi_pages: int = 3) -> PdfFacts:
    stat = path.stat()
    facts = PdfFacts(
        source_file=path.name,
        sha256=_sha256(path),
        bytes=stat.st_size,
        pages=0,
        chars_total=0,
        chars_per_page_median=0.0,
        text_quality="none",
        is_scanned=False,
        doi="",
        filename_title=path.stem,
        looks_like_supplement=bool(_SUPPLEMENT_HINTS.search(path.stem)),
    )
    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # noqa: BLE001 - any failure is a source problem
        facts.error = f"{type(exc).__name__}: {exc}"
        return facts

    try:
        facts.pages = doc.page_count
        per_page: list[int] = []
        head_text: list[str] = []
        for index, page in enumerate(doc):
            text = page.get_text("text") or ""
            per_page.append(len(text))
            if index < doi_pages:
                head_text.append(text)
        facts.chars_total = sum(per_page)
        facts.chars_per_page_median = _median(per_page)
        facts.is_scanned = facts.pages > 0 and facts.chars_per_page_median < SCANNED_CHARS_PER_PAGE
        facts.text_quality = quality_label(facts.chars_per_page_median, facts.is_scanned)

        meta = doc.metadata or {}
        facts.meta_title = (meta.get("title") or "").strip()
        facts.author_guess = (meta.get("author") or "").strip()

        page_one = head_text[0] if head_text else ""
        facts.title_guess = facts.meta_title or _title_from_page(page_one)
        facts.year_guess = _first_year(page_one) or _first_year(facts.meta_title)
        facts.doc_type_hint = _doc_type_hint(page_one)

        dois = find_dois("\n".join(head_text))
        facts.all_dois = dois
        facts.doi = dois[0] if dois else ""
    finally:
        doc.close()
    return facts


def normalise_doi_field(value: str) -> str:
    return normalize_doi(value) if value else ""
