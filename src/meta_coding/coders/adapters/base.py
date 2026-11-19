"""模型适配层：一个统一契约，三种家族各一个实现，外加离线假模型。

契约只有一件事：给定 system/user，返回原始文本 + usage + 延迟。
适配器不解析业务 JSON，解析与校验交给 Stage 4/5，避免错误被吞掉。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from ...config import ModelSlot


@dataclass
class ChatResult:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    raw_meta: dict[str, Any] = field(default_factory=dict)

    @property
    def response_hash(self) -> str:
        return "sha256:" + hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    def cost_usd(self, price_in: float, price_out: float) -> float:
        return (self.prompt_tokens * price_in + self.completion_tokens * price_out) / 1_000_000


class Adapter(Protocol):
    name: str

    def complete(self, system: str, user: str) -> ChatResult: ...


class FakeAdapter:
    """离线替身：把 packet 里的数字原样抄成一条合法记录，用于 fixtures 与 --dry-run 演练。"""

    name = "fake"

    def __init__(self, slot: ModelSlot) -> None:
        self.slot = slot

    def complete(self, system: str, user: str) -> ChatResult:
        started = time.perf_counter()
        article_key = _extract(user, "- article_key: ")
        text = json.dumps(
            {
                "record_id": f"{article_key}|WBIS|b01|0",
                "article_key": article_key,
                "source_file": _extract(user, "- source_file: "),
                "sheet": "WBIS",
                "block_id": "b01",
                "sex": 0,
                "fields": {
                    "full_reference": "FAKE, A. (2020). Offline fixture record. Journal, 1, 1-2.",
                    "subsample_label": "Overall sample",
                    "source_type": 1,
                    "paper_id": None,
                    "sex": 0,
                    "country": "US",
                    "sample_type": 1,
                    "clinical_type": None,
                    "sampling_method": 2,
                    "publication_year": 2020,
                    "year_data_collection": 2018,
                    "year_imputed_rule": 3,
                    "sample_n": 100,
                    "mean_age": 30.0,
                    "age_calculation": None,
                    "age_group": 4,
                    "mean_bmi": 25.0,
                    "measure_name": "WBIS",
                    "translated_flag": 2,
                    "scale_comparable_flag": 1,
                    "items_n": 11,
                    "response_scale_min": 1,
                    "response_scale_max": 7,
                    "direction": 1,
                    "scale_total": None,
                    "scale_mean": 3.5,
                    "scale_sd": 1.2,
                    "mean_calculation": "fixture",
                },
                "provenance": {
                    "scale_mean": {"page": 1, "table": None, "quote": "fixture only"},
                    "scale_sd": {"page": 1, "table": None, "quote": "fixture only"},
                    "items_n": {"page": 1, "table": None, "quote": "fixture only"},
                    "sample_n": {"page": 1, "table": None, "quote": "fixture only"},
                    "measure_name": {"page": 1, "table": None, "quote": "fixture only"},
                    "publication_year": {"page": 1, "table": None, "quote": "fixture only"},
                    "year_data_collection": {"page": 1, "table": None, "quote": "fixture only"},
                    "sample_type": {"page": 1, "table": None, "quote": "fixture only"},
                    "routing": {"page": 1, "table": None, "quote": "fixture only"},
                },
                "flags": ["fixture"],
                "record_status": "coded",
            },
            ensure_ascii=False,
        )
        elapsed = int((time.perf_counter() - started) * 1000)
        return ChatResult(
            text=text,
            model="fake-v1",
            prompt_tokens=len(system + user) // 4,
            completion_tokens=len(text) // 4,
            latency_ms=elapsed,
        )


def _extract(text: str, prefix: str) -> str:
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ""


class OpenAICompatibleAdapter:
    """OpenAI / DeepSeek / 任何 /chat/completions 兼容端点。"""

    def __init__(self, slot: ModelSlot, timeout: float = 300.0) -> None:
        self.slot = slot
        self.name = slot.provider
        self._timeout = timeout

    def _url(self) -> str:
        base = (self.slot.base_url or "").rstrip("/")
        if not base:
            base = {
                "openai": "https://api.openai.com/v1",
                "deepseek": "https://api.deepseek.com/v1",
            }.get(self.slot.provider, "")
        if not base:
            raise ValueError(f"{self.slot.provider} 需要 base_url")
        return f"{base}/chat/completions"

    def complete(self, system: str, user: str) -> ChatResult:
        started = time.perf_counter()
        payload = {
            "model": self.slot.model,
            "temperature": self.slot.temperature,
            "max_tokens": self.slot.max_output_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.slot.api_key()}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(self._url(), json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        choice = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return ChatResult(
            text=choice,
            model=data.get("model", self.slot.model),
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            latency_ms=int((time.perf_counter() - started) * 1000),
            raw_meta={"id": data.get("id")},
        )


class AnthropicAdapter:
    def __init__(self, slot: ModelSlot, timeout: float = 300.0) -> None:
        self.slot = slot
        self.name = slot.provider
        self._timeout = timeout

    def complete(self, system: str, user: str) -> ChatResult:
        started = time.perf_counter()
        base = (self.slot.base_url or "https://api.anthropic.com").rstrip("/")
        payload = {
            "model": self.slot.model,
            "max_tokens": self.slot.max_output_tokens,
            "temperature": self.slot.temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        headers = {
            "x-api-key": self.slot.api_key(),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(f"{base}/v1/messages", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        text = "".join(block.get("text", "") for block in data.get("content", []))
        usage = data.get("usage", {})
        return ChatResult(
            text=text,
            model=data.get("model", self.slot.model),
            prompt_tokens=int(usage.get("input_tokens", 0)),
            completion_tokens=int(usage.get("output_tokens", 0)),
            latency_ms=int((time.perf_counter() - started) * 1000),
            raw_meta={"id": data.get("id")},
        )


class GoogleAdapter:
    def __init__(self, slot: ModelSlot, timeout: float = 300.0) -> None:
        self.slot = slot
        self.name = slot.provider
        self._timeout = timeout

    def complete(self, system: str, user: str) -> ChatResult:
        started = time.perf_counter()
        base = (self.slot.base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        url = f"{base}/models/{self.slot.model}:generateContent"
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": self.slot.temperature,
                "maxOutputTokens": self.slot.max_output_tokens,
            },
        }
        with httpx.Client(timeout=self._timeout) as client:
            resp = client.post(url, json=payload, params={"key": self.slot.api_key()})
            resp.raise_for_status()
            data = resp.json()
        candidates = data.get("candidates", [])
        text = ""
        if candidates:
            text = "".join(part.get("text", "") for part in candidates[0]["content"]["parts"])
        usage = data.get("usageMetadata", {})
        return ChatResult(
            text=text,
            model=self.slot.model,
            prompt_tokens=int(usage.get("promptTokenCount", 0)),
            completion_tokens=int(usage.get("candidatesTokenCount", 0)),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


def get_adapter(slot: ModelSlot) -> Adapter:
    provider = slot.provider.lower()
    if provider in ("fake", "offline"):
        return FakeAdapter(slot)
    if provider == "anthropic":
        return AnthropicAdapter(slot)
    if provider in ("google", "gemini"):
        return GoogleAdapter(slot)
    if provider in ("openai", "deepseek") or "/" in slot.base_url:
        return OpenAICompatibleAdapter(slot)
    raise ValueError(f"未知 provider：{slot.provider}")
