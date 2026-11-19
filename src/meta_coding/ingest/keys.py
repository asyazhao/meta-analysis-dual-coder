"""Stable identifiers for articles, blocks and records.

``article_key`` must be identical across batches and across copies of the same
PDF, so it is derived from the DOI when one exists (normalised), never from the
file path.
"""

from __future__ import annotations

import hashlib
import re

_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.IGNORECASE)
_DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
_TRAILING = ".,;:)]}\"'"


def normalize_doi(raw: str) -> str:
    """Lowercase, strip resolver prefixes and trailing punctuation."""
    doi = raw.strip()
    doi = _DOI_PREFIX_RE.sub("", doi)
    doi = doi.rstrip(_TRAILING)
    return doi.lower()


def find_dois(text: str) -> list[str]:
    seen: list[str] = []
    for match in _DOI_RE.finditer(text):
        doi = normalize_doi(match.group(0))
        if doi and doi not in seen:
            seen.append(doi)
    return seen


def _h(payload: str) -> str:
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def article_key_from_doi(doi: str) -> str:
    return _h("doi:" + normalize_doi(doi))


def normalize_text(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def article_key_from_citation(title: str, first_author: str, year: int | str) -> str:
    payload = f"{normalize_text(title)}|{normalize_text(first_author)}|{year}"
    return _h("cite:" + payload)


def ref_hash(full_reference: str) -> str:
    """Coarse fingerprint used to spot the same paper across batches."""
    return _h("ref:" + normalize_text(full_reference))


def record_id(article_key: str, sheet: str, block_id: str, sex: int) -> str:
    return f"{article_key}|{sheet}|{block_id}|{sex}"
