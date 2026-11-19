"""Repository hygiene gates: the things that must never reach git."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

FORBIDDEN_PATTERNS = [
    "*.pdf",
    "data/00_inbox/",
    ".env",
    "artifacts/private/",
    "__pycache__",
    ".venv",
]


def test_gitignore_covers_hard_constraints() -> None:
    text = (REPO / ".gitignore").read_text(encoding="utf-8")
    for pattern in FORBIDDEN_PATTERNS:
        assert pattern in text, f".gitignore 缺少 {pattern}"


def test_env_example_has_no_secret_values() -> None:
    for line in (REPO / ".env.example").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        _, _, value = line.partition("=")
        assert value.strip() == "", f".env.example 不应含真实值：{line}"


def test_models_yaml_has_no_inline_keys() -> None:
    text = (REPO / "configs" / "models.yaml").read_text(encoding="utf-8")
    assert "api_key:" not in text, "configs/models.yaml 禁止内联密钥，只能写 api_key_env"


def test_no_pdf_tracked() -> None:
    for path in REPO.rglob("*.pdf"):
        assert "data/00_inbox" in path.as_posix(), f"仓库内出现 PDF：{path}"
