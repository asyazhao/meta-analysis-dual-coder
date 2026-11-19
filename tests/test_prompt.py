"""提示词与编码运行的离线测试：不需要任何 API key。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from meta_coding.coders.prompt import (
    FORBIDDEN_IN_CODER_PROMPT,
    assert_parity,
    render_coder,
    schema_text,
)
from meta_coding.config import ModelSlot

REPO = Path(__file__).resolve().parents[1]
SCHEMA = REPO / "rules" / "schema" / "record.schema.json"
RULES = (REPO / "rules" / "CODING_PLAN.md").read_text(encoding="utf-8")


def packet(**overrides) -> dict:
    base = {
        "article_key": "sha256:" + "a" * 64,
        "source_file": "x.pdf",
        "page_count": 2,
        "biblio": {"doi": "10.1/x", "title_guess": "T", "author_guess": "A"},
        "material_status": {"text_quality": "good", "scanned_pages": [], "ocr": False,
                            "supplement_files": []},
        "pages": [
            {"page": 1, "text": "Title page. Table 1 shows M = 3.54, SD = 1.69."},
            {"page": 2, "text": "Method: 11-item WBIS."},
        ],
        "tables": [
            {"page": 1, "index": 1, "label": "Table 1", "caption": "Descriptives",
             "n_rows": 2, "n_cols": 3, "markdown": "| M | SD |\n|---|---|\n| 3.54 | 1.69 |"},
        ],
    }
    base.update(overrides)
    return base


def test_prompt_is_identical_for_both_coders() -> None:
    a = render_coder(packet(), RULES, "sha256:deadbeef", SCHEMA)
    b = render_coder(packet(), RULES, "sha256:deadbeef", SCHEMA)
    assert_parity(a, b)
    assert a.sha256 == b.sha256


def test_prompt_carries_page_anchors_and_tables() -> None:
    rendered = render_coder(packet(), RULES, "sha256:deadbeef", SCHEMA)
    assert "--- page 1 ---" in rendered.user
    assert "--- page 2 ---" in rendered.user
    assert "| 3.54 | 1.69 |" in rendered.user
    assert "Table 1" in rendered.user


def test_prompt_changes_when_rules_change() -> None:
    base = render_coder(packet(), RULES, "sha256:deadbeef", SCHEMA)
    other = render_coder(packet(), RULES + "\n<!-- x -->", "sha256:deadbeef", SCHEMA)
    assert base.sha256 != other.sha256


def test_prompt_changes_when_packet_changes() -> None:
    base = render_coder(packet(), RULES, "sha256:deadbeef", SCHEMA)
    other = render_coder(packet(page_count=3), RULES, "sha256:deadbeef", SCHEMA)
    assert base.sha256 != other.sha256


def test_template_has_no_forbidden_words() -> None:
    from meta_coding.coders.prompt import CODER_TEMPLATE, load_template

    system, user_tpl, _ = load_template(CODER_TEMPLATE)
    scaffold = (system + user_tpl).lower()
    for word in FORBIDDEN_IN_CODER_PROMPT:
        assert word.lower() not in scaffold, word


def test_schema_text_lists_all_28_fields() -> None:
    text = schema_text(SCHEMA)
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    for name in schema["properties"]["fields"]["required"]:
        assert name in text


def test_fake_adapter_produces_valid_record_shape() -> None:
    from meta_coding.coders.adapters.base import FakeAdapter

    slot = ModelSlot(name="coder_a", provider="fake", family="fake", model="fake-v1")
    result = FakeAdapter(slot).complete("sys", "- article_key: sha256:" + "b" * 64)
    payload = json.loads(result.text)
    assert payload["article_key"].startswith("sha256:")
    assert payload["record_status"] == "coded"
    assert len(payload["fields"]) == 28


@pytest.mark.parametrize("coder", ["a", "b"])
def test_unbound_slot_refuses_to_run(coder: str, tmp_path: Path) -> None:
    from meta_coding.coders.runner import run_coder
    from meta_coding.config import ConfigError, load_config

    cfg = load_config(REPO)
    with pytest.raises((RuntimeError, ConfigError)):
        run_coder(REPO, cfg, coder, limit=1)
