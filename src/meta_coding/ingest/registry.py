"""Cross-batch registry: one row per article ever seen, so no paper is coded twice."""

from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path

REGISTRY_FIELDS = [
    "article_key",
    "doi",
    "batch_id",
    "source_file",
    "sha256",
    "pages",
    "role",
    "status",
    "first_seen",
]


def load_registry(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def save_registry(path: Path, rows: Iterable[dict[str, str]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(rows)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REGISTRY_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in REGISTRY_FIELDS})
    return len(rows)


def merge_rows(existing: list[dict[str, str]], new: list[dict[str, str]]) -> list[dict[str, str]]:
    """Upsert by (article_key, batch_id); existing first-seen timestamps win."""
    index = {(r["article_key"], r["batch_id"]): r for r in existing}
    for row in new:
        key = (row["article_key"], row["batch_id"])
        if key in index:
            merged = dict(index[key])
            merged.update({k: v for k, v in row.items() if v not in ("", None)})
            index[key] = merged
        else:
            index[key] = row
    return sorted(index.values(), key=lambda r: (r["article_key"], r["batch_id"]))


def keys_by_doi(rows: Iterable[dict[str, str]]) -> dict[str, str]:
    return {r["doi"]: r["article_key"] for r in rows if r.get("doi")}


def keys_by_sha(rows: Iterable[dict[str, str]]) -> dict[str, str]:
    return {r["sha256"]: r["article_key"] for r in rows if r.get("sha256")}
