from __future__ import annotations

from pathlib import Path

from meta_coding.artifacts import build_manifest, verify_all, verify_manifest, write_manifest


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    return tmp_path


def test_manifest_roundtrip_ok(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    out = root / "data" / "artifact.json"
    out.write_text('{"ok": true}', encoding="utf-8")
    manifest = build_manifest(
        root=root,
        stage="00_baseline",
        run_id="20260101T000000Z-deadbeef",
        git_commit="abc123",
        rules_hash=None,
        config_hash="0" * 64,
        outputs=[out],
        counts={"files": 1},
        checks=[{"id": "V01", "status": "pass", "failures": 0}],
    )
    path = write_manifest(root, "00_baseline", manifest)
    result = verify_manifest(path, root)
    assert result.ok, result.problems


def test_tampered_artifact_detected(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    out = root / "data" / "artifact.json"
    out.write_text("original", encoding="utf-8")
    manifest = build_manifest(
        root=root,
        stage="00_baseline",
        run_id="20260101T000000Z-deadbeef",
        git_commit=None,
        rules_hash=None,
        config_hash=None,
        outputs=[out],
    )
    path = write_manifest(root, "00_baseline", manifest)
    out.write_text("tampered", encoding="utf-8")
    result = verify_manifest(path, root)
    assert not result.ok
    assert any("哈希不符" in p for p in result.problems)


def test_missing_artifact_detected(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    out = root / "data" / "artifact.json"
    out.write_text("x", encoding="utf-8")
    manifest = build_manifest(
        root=root,
        stage="00_baseline",
        run_id="20260101T000000Z-deadbeef",
        git_commit=None,
        rules_hash=None,
        config_hash=None,
        outputs=[out],
    )
    path = write_manifest(root, "00_baseline", manifest)
    out.unlink()
    result = verify_manifest(path, root)
    assert not result.ok
    assert any("缺少" in p for p in result.problems)


def test_verify_all_walks_stage_dirs(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    out = root / "data" / "a.json"
    out.write_text("{}", encoding="utf-8")
    manifest = build_manifest(
        root=root,
        stage="00_baseline",
        run_id="20260101T000000Z-deadbeef",
        git_commit=None,
        rules_hash=None,
        config_hash=None,
        outputs=[out],
    )
    write_manifest(root, "00_baseline", manifest)
    results = verify_all(root)
    assert len(results) == 1 and results[0].ok
