"""Stage 4 编排：双盲编码、断点续跑、成本护栏、逐篇落盘。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Config, ModelSlot
from ..ingest.pipeline_ingest import _read_articles
from .adapters.base import get_adapter
from .prompt import RenderedPrompt, assert_parity, render_coder

CODED_DIR = "data/03_coded"
RUN_LOG = f"{CODED_DIR}/run_log.jsonl"


@dataclass
class PromptPreview:
    article_key: str
    source_file: str
    sha256: str
    chars: int
    est_tokens: int


@dataclass
class CodeResult:
    coder: str
    articles: int
    calls: int
    failures: int
    skipped_existing: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    run_dir: Path
    dry_run: bool
    previews: list[PromptPreview]


def packet_dir(root: Path, article_key: str) -> Path:
    return root / "data" / "02_ingest" / article_key.split(":")[-1][:16]


def load_packet(root: Path, article_key: str) -> dict[str, Any]:
    path = packet_dir(root, article_key) / "packet.json"
    if not path.is_file():
        raise FileNotFoundError(f"缺少 packet，请先跑 ingest：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def render_for(root: Path, article_key: str, rules_text: str, rules_hash: str) -> RenderedPrompt:
    packet = load_packet(root, article_key)
    schema = root / "rules" / "schema" / "coder_output.schema.json"
    return render_coder(packet, rules_text, rules_hash, schema)


def preview(
    root: Path, cfg: Config, coder: str, limit: int | None = None
) -> tuple[list[PromptPreview], RenderedPrompt | None]:
    rules_path = root / "rules" / "CODING_PLAN.md"
    if not rules_path.is_file():
        raise FileNotFoundError(f"缺少规则副本：{rules_path}")
    rules_text = rules_path.read_text(encoding="utf-8")
    rules_hash = cfg.rules_hash or "unlocked"

    articles = _read_articles(root)
    if limit:
        articles = articles[:limit]
    out: list[PromptPreview] = []
    last: RenderedPrompt | None = None
    for article in articles:
        rendered = render_for(root, article["article_key"], rules_text, rules_hash)
        last = rendered
        out.append(
            PromptPreview(
                article_key=article["article_key"],
                source_file=article["source_file"],
                sha256=rendered.sha256,
                chars=rendered.chars,
                est_tokens=rendered.chars // 4,
            )
        )
    return out, last


def _slot(cfg: Config, coder: str) -> ModelSlot:
    return cfg.models.coder_a if coder == "a" else cfg.models.coder_b


def run_coder(
    root: Path,
    cfg: Config,
    coder: str,
    *,
    limit: int | None = None,
    dry_run: bool = False,
    max_cost: float | None = None,
) -> CodeResult:
    if coder not in ("a", "b"):
        raise ValueError("coder 必须是 'a' 或 'b'")

    previews, last = preview(root, cfg, coder, limit)
    if dry_run:
        return CodeResult(
            coder=coder, articles=len(previews), calls=0, failures=0, skipped_existing=0,
            prompt_tokens=sum(p.est_tokens for p in previews), completion_tokens=0,
            cost_usd=0.0, run_dir=Path(), dry_run=True, previews=previews,
        )

    slot = _slot(cfg, coder)
    if not slot.bound:
        raise RuntimeError(
            f"coder_{coder} 未绑定 provider/model/family，无法真实调用（见 models.yaml）"
        )
    if max_cost is not None and slot.price_in == 0 and slot.price_out == 0:
        raise RuntimeError("设置了 --max-cost 但 models.yaml 未给 price_in/price_out，无法估算成本")

    adapter = get_adapter(slot)
    run_dir = root / CODED_DIR / f"coder_{coder}"
    run_dir.mkdir(parents=True, exist_ok=True)

    rules_text = (root / "rules" / "CODING_PLAN.md").read_text(encoding="utf-8")
    rules_hash = cfg.rules_hash or "unlocked"
    articles = _read_articles(root)
    if limit:
        articles = articles[:limit]

    calls = failures = skipped = 0
    prompt_tokens = completion_tokens = 0
    cost = 0.0

    with (root / RUN_LOG).open("a", encoding="utf-8") as log:
        for article in articles:
            key = article["article_key"]
            target = run_dir / key.split(":")[-1][:16]
            if (target / "parsed.json").is_file():
                skipped += 1
                continue

            rendered = render_for(root, key, rules_text, rules_hash)
            target.mkdir(parents=True, exist_ok=True)
            started = time.perf_counter()
            try:
                result = adapter.complete(rendered.system, rendered.user)
            except Exception as exc:  # noqa: BLE001 - 单篇失败不阻塞批次
                failures += 1
                log.write(json.dumps({
                    "coder": coder, "article_key": key, "status": "call_failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }, ensure_ascii=False) + "\n")
                log.flush()
                continue

            calls += 1
            prompt_tokens += result.prompt_tokens
            completion_tokens += result.completion_tokens
            cost += result.cost_usd(slot.price_in, slot.price_out)

            (target / "raw_response.txt").write_text(result.text, encoding="utf-8")
            meta = {
                "coder": coder,
                "article_key": key,
                "model": result.model,
                "provider": slot.provider,
                "temperature": slot.temperature,
                "prompt_hash": rendered.sha256,
                "prompt_version": rendered.version,
                "rules_hash": rules_hash,
                "config_hash": cfg.config_hash,
                "input_packet_hash": _packet_hash(root, key),
                "response_hash": result.response_hash,
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "latency_ms": result.latency_ms,
                "wall_ms": int((time.perf_counter() - started) * 1000),
                "cost_usd": round(result.cost_usd(slot.price_in, slot.price_out), 6),
            }
            (target / "meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            _parse_and_store(target, result.text)

            log.write(json.dumps({"coder": coder, **meta, "status": "ok"},
                                 ensure_ascii=False) + "\n")
            log.flush()

            if max_cost is not None and cost >= max_cost:
                log.write(json.dumps({
                    "coder": coder, "status": "stopped_by_max_cost",
                    "cost_usd": round(cost, 6), "limit": max_cost,
                }, ensure_ascii=False) + "\n")
                break

    return CodeResult(
        coder=coder, articles=len(articles), calls=calls, failures=failures,
        skipped_existing=skipped, prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens, cost_usd=round(cost, 6),
        run_dir=run_dir, dry_run=False, previews=previews,
    )


def _packet_hash(root: Path, article_key: str) -> str:
    path = packet_dir(root, article_key) / "PACKET_HASH"
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def _parse_and_store(target: Path, text: str) -> None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        cleaned = cleaned.rsplit("```", 1)[0]
    try:
        parsed = json.loads(cleaned)
        (target / "parsed.json").write_text(
            json.dumps(parsed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except json.JSONDecodeError as exc:
        (target / "parse_error.txt").write_text(
            f"JSON 解析失败：{exc}\n首 500 字符：\n{text[:500]}\n", encoding="utf-8"
        )


def check_parity(root: Path, cfg: Config, limit: int | None = None) -> dict[str, str]:
    """A/B 提示词必须逐字节一致；不一致立即失败。"""
    a, _ = preview(root, cfg, "a", limit)
    b, _ = preview(root, cfg, "b", limit)
    if len(a) != len(b):
        raise AssertionError("两个编码员的文章数不同")
    for left, right in zip(a, b, strict=True):
        if left.sha256 != right.sha256:
            raise AssertionError(
                f"提示词不一致：{left.source_file} {left.sha256[:16]}… vs {right.sha256[:16]}…"
            )
    return {"articles": str(len(a)), "prompt_hash": a[0].sha256 if a else ""}


__all__ = [
    "CodeResult",
    "PromptPreview",
    "assert_parity",
    "check_parity",
    "load_packet",
    "preview",
    "run_coder",
]
