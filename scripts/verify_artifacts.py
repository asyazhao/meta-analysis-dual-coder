#!/usr/bin/env python3
"""Verify every artifact manifest under artifacts/.

Thin CLI wrapper so CI and humans use the same code path as `meta-coding verify`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from meta_coding.artifacts import verify_all  # noqa: E402
from meta_coding.paths import repo_root  # noqa: E402


def main() -> int:
    root = repo_root(Path(__file__).resolve().parent)
    results = verify_all(root)
    if not results:
        print("尚无 manifest。")
        return 0
    failed = 0
    for res in results:
        print(res.summary())
        failed += 0 if res.ok else 1
    print(f"\n共 {len(results)} 份，失败 {failed} 份")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
