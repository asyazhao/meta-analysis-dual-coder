from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from meta_coding.config import ConfigError, load_config, sha256_file


def write_repo(tmp_path: Path, pipeline: dict, models: dict) -> Path:
    (tmp_path / "configs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "configs" / "pipeline.yaml").write_text(
        yaml.safe_dump({"pipeline": pipeline}, allow_unicode=True), encoding="utf-8"
    )
    (tmp_path / "configs" / "models.yaml").write_text(
        yaml.safe_dump(models, allow_unicode=True), encoding="utf-8"
    )
    return tmp_path


BASE_PIPELINE = {
    "batch_id": "batch01",
    "input_dir": "/tmp/papers",
    "budget_usd": 50,
    "concurrency": 4,
    "agreement_audit_rate": 0.1,
    "agreement_audit_min_cells": 100,
    "random_seed_a": 1,
    "random_seed_b": 2,
}

UNBOUND = {
    "coder_a": {"provider": "", "family": "", "model": "", "api_key_env": "A_KEY"},
    "coder_b": {"provider": "", "family": "", "model": "", "api_key_env": "B_KEY"},
    "adjudicator": {"provider": "", "family": "", "model": "", "api_key_env": "C_KEY"},
}


def bound(family: str, model: str, key: str) -> dict:
    return {"provider": family, "family": family, "model": model, "api_key_env": key}


def test_loads_unbound_config(tmp_path: Path) -> None:
    root = write_repo(tmp_path, BASE_PIPELINE, UNBOUND)
    cfg = load_config(root)
    assert cfg.pipeline.batch_id == "batch01"
    assert cfg.pipeline.concurrency == 4
    assert cfg.models.fully_bound is False
    assert len(cfg.config_hash) == 64


def test_config_hash_ignores_secret_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = write_repo(tmp_path, BASE_PIPELINE, UNBOUND)
    first = load_config(root).config_hash
    monkeypatch.setenv("A_KEY", "sk-this-should-not-affect-the-hash")
    assert load_config(root).config_hash == first


def test_unknown_pipeline_key_rejected(tmp_path: Path) -> None:
    bad = dict(BASE_PIPELINE, nonsene_key=1)
    root = write_repo(tmp_path, bad, UNBOUND)
    with pytest.raises(ConfigError, match="未知键"):
        load_config(root)


def test_same_seed_for_both_coders_rejected(tmp_path: Path) -> None:
    bad = dict(BASE_PIPELINE, random_seed_b=BASE_PIPELINE["random_seed_a"])
    root = write_repo(tmp_path, bad, UNBOUND)
    with pytest.raises(ConfigError, match="种子"):
        load_config(root)


def test_coders_must_differ_in_family(tmp_path: Path) -> None:
    models = {
        "coder_a": bound("openai", "gpt-x", "A_KEY"),
        "coder_b": bound("openai", "gpt-y", "B_KEY"),
        "adjudicator": bound("google", "gem-x", "C_KEY"),
    }
    root = write_repo(tmp_path, BASE_PIPELINE, models)
    with pytest.raises(ConfigError, match="不同模型家族"):
        load_config(root)


def test_adjudicator_must_differ_from_both(tmp_path: Path) -> None:
    models = {
        "coder_a": bound("openai", "gpt-x", "A_KEY"),
        "coder_b": bound("anthropic", "claude-x", "B_KEY"),
        "adjudicator": bound("anthropic", "claude-y", "C_KEY"),
    }
    root = write_repo(tmp_path, BASE_PIPELINE, models)
    with pytest.raises(ConfigError, match="盲法失效"):
        load_config(root)


def test_require_bound_models(tmp_path: Path) -> None:
    root = write_repo(tmp_path, BASE_PIPELINE, UNBOUND)
    with pytest.raises(ConfigError, match="尚未绑定"):
        load_config(root, require_bound_models=True)


def test_missing_env_key_message(tmp_path: Path) -> None:
    models = {
        "coder_a": bound("openai", "gpt-x", "MISSING_A"),
        "coder_b": bound("anthropic", "claude-x", "MISSING_B"),
        "adjudicator": bound("google", "gem-x", "MISSING_C"),
    }
    root = write_repo(tmp_path, BASE_PIPELINE, models)
    cfg = load_config(root, require_bound_models=True)
    with pytest.raises(ConfigError, match="MISSING_A"):
        cfg.models.coder_a.api_key()


def test_rules_hash_read_from_lock(tmp_path: Path) -> None:
    root = write_repo(tmp_path, BASE_PIPELINE, UNBOUND)
    plan = tmp_path / "rules" / "CODING_PLAN.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text("rules", encoding="utf-8")
    (tmp_path / "configs" / "rules.lock.json").write_text(
        json.dumps({"sha256": sha256_file(plan)}), encoding="utf-8"
    )
    assert load_config(root).rules_hash == sha256_file(plan)
