"""Configuration loading, validation and hashing.

Two YAML files drive the pipeline:

* ``configs/pipeline.yaml`` — batch scope, budget, concurrency, audit settings.
* ``configs/models.yaml`` — the three model slots. Credentials are never stored
  in YAML; each slot names the environment variable that holds the key.

``config_hash`` covers both files with API-key *names* only, so it stays stable
across machines and never leaks a secret into a manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .paths import MODELS_CONFIG, PIPELINE_CONFIG, RULES_LOCK


class ConfigError(ValueError):
    """Raised when configuration is missing, malformed or violates a hard rule."""


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"配置文件不存在：{path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # pragma: no cover - passthrough
        raise ConfigError(f"YAML 解析失败：{path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"配置文件顶层必须是映射：{path}")
    return data


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class ModelSlot:
    """One callable model. ``bound`` is False until provider+model are filled."""

    name: str
    provider: str = ""
    family: str = ""
    model: str = ""
    temperature: float = 0.0
    max_output_tokens: int = 16000
    api_key_env: str = ""
    base_url: str = ""
    context_tokens: int = 64000
    price_in_per_mtok: float = 0.0
    price_out_per_mtok: float = 0.0
    price_in: float = 0.0      # USD / 1M prompt tokens
    price_out: float = 0.0     # USD / 1M completion tokens

    @property
    def bound(self) -> bool:
        return bool(self.provider and self.model and self.family)

    def api_key(self) -> str:
        if not self.api_key_env:
            raise ConfigError(f"模型槽位 {self.name} 未配置 api_key_env")
        value = os.environ.get(self.api_key_env, "")
        if not value:
            raise ConfigError(
                f"模型槽位 {self.name} 缺少凭据：请设置环境变量 {self.api_key_env}"
            )
        return value

    def public_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["bound"] = self.bound
        return d


@dataclass(frozen=True)
class PipelineConfig:
    batch_id: str
    input_dir: str
    repo_name: str = "meta-analysis-dual-coder"
    repo_visibility: str = "private"
    budget_usd: float = 0.0
    concurrency: int = 4
    agreement_audit_rate: float = 0.10
    agreement_audit_min_cells: int = 100
    max_retries: int = 2
    pilot_n: int = 3
    random_seed_a: int = 20260101
    random_seed_b: int = 20260202
    # 裁决模型与编码员同家族时，必须显式打开并记入 DEVIATIONS（默认拒绝）
    allow_same_family_adjudicator: bool = False

    def validate(self) -> None:
        if not self.batch_id:
            raise ConfigError("pipeline.batch_id 不能为空")
        if not self.input_dir:
            raise ConfigError("pipeline.input_dir 不能为空（本批次 PDF 目录）")
        if not 0.0 <= self.agreement_audit_rate <= 1.0:
            raise ConfigError("agreement_audit_rate 必须落在 [0, 1]")
        if self.agreement_audit_min_cells < 0:
            raise ConfigError("agreement_audit_min_cells 不能为负")
        if self.concurrency < 1:
            raise ConfigError("concurrency 至少为 1")
        if self.budget_usd < 0:
            raise ConfigError("budget_usd 不能为负")
        if self.random_seed_a == self.random_seed_b:
            raise ConfigError("两个编码员的打乱种子必须不同（顺序去耦）")


@dataclass(frozen=True)
class ModelsConfig:
    coder_a: ModelSlot
    coder_b: ModelSlot
    adjudicator: ModelSlot

    def slots(self) -> tuple[ModelSlot, ...]:
        return (self.coder_a, self.coder_b, self.adjudicator)

    def validate(self, *, allow_same_family_adjudicator: bool = False) -> None:
        for slot in self.slots():
            if slot.bound and not slot.bound:  # pragma: no cover - defensive
                raise ConfigError("unreachable")
            if slot.bound and not slot.api_key_env:
                raise ConfigError(f"模型槽位 {slot.name} 已绑定但未指定 api_key_env")
        a, b, adj = self.coder_a, self.coder_b, self.adjudicator
        if a.bound and b.bound and a.family == b.family:
            raise ConfigError(
                f"编码员 A 与 B 必须来自不同模型家族（当前都是 {a.family}）"
            )
        if adj.bound and not allow_same_family_adjudicator:
            for other in (a, b):
                if other.bound and other.family == adj.family:
                    raise ConfigError(
                        f"裁决模型与 {other.name} 同家族（{adj.family}），盲法失效；"
                        "若确需如此，请在 pipeline.yaml 设 allow_same_family_adjudicator: true "
                        "并在 docs/DEVIATIONS.md 留痕"
                    )
        if adj.bound and a.bound and b.bound:
            if adj.model == a.model or adj.model == b.model:
                raise ConfigError("裁决模型不能与任一编码员使用完全相同的模型")

    @property
    def fully_bound(self) -> bool:
        return all(slot.bound for slot in self.slots())


@dataclass(frozen=True)
class Config:
    pipeline: PipelineConfig
    models: ModelsConfig
    rules_hash: str | None = None
    config_hash: str = field(default="")

    def manifest_fragment(self) -> dict[str, Any]:
        return {
            "config_hash": self.config_hash,
            "rules_hash": self.rules_hash,
            "batch_id": self.pipeline.batch_id,
            "models": {s.name: s.public_dict() for s in self.models.slots()},
        }


def _slot(name: str, raw: dict[str, Any] | None) -> ModelSlot:
    raw = raw or {}
    return ModelSlot(
        name=name,
        provider=str(raw.get("provider") or ""),
        family=str(raw.get("family") or ""),
        model=str(raw.get("model") or ""),
        temperature=float(raw.get("temperature", 0.0)),
        max_output_tokens=int(raw.get("max_output_tokens", 16000)),
        api_key_env=str(raw.get("api_key_env") or ""),
        base_url=str(raw.get("base_url") or ""),
        context_tokens=int(raw.get("context_tokens", 64000)),
        price_in=float(raw.get("price_in", 0.0)),
        price_out=float(raw.get("price_out", 0.0)),
    )


def load_config(root: Path, *, require_bound_models: bool = False) -> Config:
    """Load and validate both config files under ``root``.

    ``require_bound_models`` is False for Stages 0-3 (no API traffic yet) and
    True for Stages 4-6, where an unbound slot is a hard stop.
    """
    raw_pipeline = _read_yaml(root / PIPELINE_CONFIG)
    raw_models = _read_yaml(root / MODELS_CONFIG)

    pipeline_raw = raw_pipeline.get("pipeline", raw_pipeline)
    if not isinstance(pipeline_raw, dict):
        raise ConfigError("pipeline.yaml 的 pipeline 段必须是映射")
    unknown = set(pipeline_raw) - set(PipelineConfig.__dataclass_fields__)
    if unknown:
        raise ConfigError(f"pipeline.yaml 存在未知键：{sorted(unknown)}")
    missing = {"batch_id", "input_dir"} - set(pipeline_raw)
    if missing:
        raise ConfigError(f"pipeline.yaml 缺少必填键：{sorted(missing)}")
    try:
        pipeline = PipelineConfig(**pipeline_raw)
    except TypeError as exc:
        raise ConfigError(f"pipeline.yaml 字段类型不符：{exc}") from exc
    pipeline.validate()

    models = ModelsConfig(
        coder_a=_slot("coder_a", raw_models.get("coder_a")),
        coder_b=_slot("coder_b", raw_models.get("coder_b")),
        adjudicator=_slot("adjudicator", raw_models.get("adjudicator")),
    )
    models.validate(
        allow_same_family_adjudicator=pipeline.allow_same_family_adjudicator
    )
    if require_bound_models and not models.fully_bound:
        missing = [s.name for s in models.slots() if not s.bound]
        raise ConfigError(f"以下模型槽位尚未绑定 provider/model/family：{missing}")

    rules_hash = None
    lock_path = root / RULES_LOCK
    if lock_path.is_file():
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        rules_hash = lock.get("sha256")

    payload = {
        "pipeline": asdict(pipeline),
        "models": {s.name: s.public_dict() for s in models.slots()},
    }
    config_hash = sha256_text(canonical_json(payload))

    return Config(
        pipeline=pipeline,
        models=models,
        rules_hash=rules_hash,
        config_hash=config_hash,
    )
