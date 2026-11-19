from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from meta_coding.cli import build_parser, main

REPO = Path(__file__).resolve().parents[1]


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "meta-coding" in out
    for cmd in ("intake", "ingest", "code", "validate", "diff", "adjudicate",
                "consensus", "qa", "report", "verify"):
        assert cmd in out


def test_every_subcommand_has_help() -> None:
    parser = build_parser()
    for action in parser._subparsers._group_actions:  # noqa: SLF001
        for name in action.choices:
            with pytest.raises(SystemExit) as exc:
                main([name, "--help"])
            assert exc.value.code == 0


def test_unimplemented_command_fails_loudly(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["intake", "--batch", "batch01"])
    assert code == 2
    err = capsys.readouterr().err
    assert "Stage 01" in err
    assert "尚未实现" in err


def test_init_creates_layout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shutil.copytree(REPO / "configs", tmp_path / "configs")
    assert main(["init", "--repo", str(tmp_path), "--quiet"]) == 0
    assert (tmp_path / "data" / "01_manifest").is_dir()
    assert (tmp_path / "data" / "07_analysis").is_dir()
    assert (tmp_path / "artifacts" / "public").is_dir()


def test_init_reports_bad_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shutil.copytree(REPO / "configs", tmp_path / "configs")
    (tmp_path / "configs" / "pipeline.yaml").write_text("pipeline: {}\n", encoding="utf-8")
    assert main(["init", "--repo", str(tmp_path), "--quiet"]) == 2
    assert "配置检查：未通过" in capsys.readouterr().err


def test_verify_without_artifacts_is_ok(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # 用隔离的临时仓库，避免测试依赖真实仓库里是否已有 manifest
    shutil.copytree(REPO / "configs", tmp_path / "configs")
    assert main(["verify", "--all", "--repo", str(tmp_path)]) == 0
    assert "尚无 manifest" in capsys.readouterr().out
