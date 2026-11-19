"""Web 控制台的数据层：密钥、配置、论文目录、文档读取与下载边界。

这一层不含任何业务编排，只负责：
* ``.env`` 的读写与「只回显是否已设置」；
* ``configs/pipeline.yaml`` 的保留注释改写（预算 / 并发 / 论文目录）；
* PDF 目录清单与安全路径判定；
* 规则文档（md / pdf / docx）正文提取；
* 任何文本响应的密钥脱敏。

所有读取都对缺失文件宽容：阶段没跑到就返回空视图 + 一句人话解释，
绝不用占位数据冒充真实结果。
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any

import yaml

from ..config import Config, ConfigError, load_config

PIPELINE_REL = Path("configs/pipeline.yaml")
MODELS_REL = Path("configs/models.yaml")
CORPUS_REL = Path("data/01_manifest/corpus_manifest.csv")

SLOT_LABELS = {"coder_a": "编码员 A", "coder_b": "编码员 B", "adjudicator": "裁决员"}
DEFAULT_KEY_ENVS = {
    "coder_a": "META_CODER_A_API_KEY",
    "coder_b": "META_CODER_B_API_KEY",
    "adjudicator": "META_ADJUDICATOR_API_KEY",
}
PDF_LIMIT = 800
DOC_SUFFIXES = {".md", ".markdown", ".txt", ".pdf", ".docx"}
TEXT_LIMIT = 400_000


# --------------------------------------------------------------------------- #
# 密钥与 .env
# --------------------------------------------------------------------------- #


def parse_env_text(text: str) -> dict[str, str]:
    """解析 .env（支持注释、export 前缀与成对引号），不执行任何内容。"""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def read_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        return parse_env_text(path.read_text(encoding="utf-8"))
    except OSError:
        return {}


def slot_env_names(root: Path) -> dict[str, str]:
    """槽位 -> 环境变量名，以 models.yaml 为准，缺失时退回默认名。"""
    names = dict(DEFAULT_KEY_ENVS)
    path = root / MODELS_REL
    if not path.is_file():
        return names
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        return names
    for slot in names:
        block = raw.get(slot) if isinstance(raw, dict) else None
        if isinstance(block, dict) and str(block.get("api_key_env") or "").strip():
            names[slot] = str(block["api_key_env"]).strip()
    return names


def key_status(root: Path) -> dict[str, bool]:
    """只返回「是否已设置」，绝不返回密钥值。"""
    env = read_env(root / ".env")
    return {slot: bool(env.get(name, "").strip()) for slot, name in slot_env_names(root).items()}


def update_env_file(path: Path, updates: dict[str, str], example: Path | None = None) -> list[str]:
    """写入 .env：已有键原位替换，缺键追加；文件不存在时以 .env.example 为骨架。"""
    if path.is_file():
        text = path.read_text(encoding="utf-8")
    elif example is not None and example.is_file():
        text = example.read_text(encoding="utf-8")
    else:
        text = "# 由 Web 控制台创建；不要提交到 git。\n"

    lines = text.splitlines()
    written: list[str] = []
    pending = {k: v for k, v in updates.items() if v is not None}
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name = stripped.split("=", 1)[0].strip()
        if name in pending:
            lines[index] = f"{name}={pending.pop(name)}"
            written.append(name)

    if pending:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("# 由 Web 控制台写入")
        for name, value in pending.items():
            lines.append(f"{name}={value}")
            written.append(name)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return written


class SecretScrubber:
    """把密钥原文从任何文本中抹掉；.env 变更后自动刷新。"""

    def __init__(self, env_path: Path) -> None:
        self.env_path = env_path
        self._sig: tuple[int, int] | None = None
        self._secrets: tuple[str, ...] = ()

    def secrets(self) -> tuple[str, ...]:
        try:
            stat = self.env_path.stat()
        except OSError:
            self._secrets = self._from_environment()
            self._sig = None
            return self._secrets
        sig = (stat.st_mtime_ns, stat.st_size)
        if sig != self._sig:
            values = {v.strip() for v in read_env(self.env_path).values() if len(v.strip()) >= 6}
            merged = values | self._from_environment()
            self._secrets = tuple(sorted(merged, key=len, reverse=True))
            self._sig = sig
        return self._secrets

    @staticmethod
    def _from_environment() -> set[str]:
        out = set()
        for name in DEFAULT_KEY_ENVS.values():
            value = os.environ.get(name, "").strip()
            if len(value) >= 6:
                out.add(value)
        return out

    def scrub(self, text: str) -> str:
        for secret in self.secrets():
            if secret in text:
                text = text.replace(secret, "***")
        return text


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #


def load_config_safe(root: Path) -> tuple[Config | None, str | None]:
    try:
        return load_config(root), None
    except ConfigError as exc:
        return None, str(exc)


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    return json.dumps(str(value), ensure_ascii=False)


def update_pipeline_text(text: str, updates: dict[str, Any]) -> str:
    """按行改写 pipeline 段，保留原有注释与键顺序。"""
    lines = text.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if re.match(r"^pipeline:\s*$", line)),
        None,
    )
    if start is None:
        raise ValueError("configs/pipeline.yaml 缺少顶层 pipeline:")

    end = len(lines)
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if line.strip() and not line.startswith((" ", "\t", "#")):
            end = i
            break

    remaining = {k: v for k, v in updates.items() if v is not None}
    replaced: set[str] = set()
    block = lines[start + 1:end]
    for index, line in enumerate(block):
        match = re.match(r"^(\s+)([A-Za-z_][A-Za-z0-9_]*)\s*:.*$", line)
        if not match or line.lstrip().startswith("#"):
            continue
        key = match.group(2)
        if key in remaining:
            block[index] = f"{match.group(1)}{key}: {_yaml_scalar(remaining[key])}"
            replaced.add(key)

    missing = [key for key in updates if key not in replaced and updates[key] is not None]
    if missing:
        insert_at = len(block)
        while insert_at > 0 and not block[insert_at - 1].strip():
            insert_at -= 1
        for offset, key in enumerate(missing):
            block.insert(insert_at + offset, f"  {key}: {_yaml_scalar(updates[key])}")

    lines[start + 1:end] = block
    return "\n".join(lines) + "\n"


def apply_settings(
    root: Path, *, budget_usd: float, concurrency: int, keys: dict[str, str]
) -> dict[str, Any]:
    """写回 .env（密钥）与 pipeline.yaml（预算/并发）；配置非法时回滚。"""
    if not 0 <= budget_usd <= 100000:
        raise ValueError("预算必须落在 0–100000 USD 之间")
    if not 1 <= concurrency <= 32:
        raise ValueError("并发数必须落在 1–32 之间")

    pipeline_path = root / PIPELINE_REL
    original = pipeline_path.read_text(encoding="utf-8")
    pipeline_path.write_text(
        update_pipeline_text(
            original, {"budget_usd": float(budget_usd), "concurrency": int(concurrency)}
        ),
        encoding="utf-8",
    )
    cfg, error = load_config_safe(root)
    if cfg is None:
        pipeline_path.write_text(original, encoding="utf-8")
        raise ValueError(f"配置未通过校验，已回滚：{error}")

    env_names = slot_env_names(root)
    env_updates: dict[str, str] = {}
    for slot, value in keys.items():
        value = (value or "").strip()
        if not value or value == "****":
            continue
        name = env_names.get(slot)
        if name:
            env_updates[name] = value
    written = update_env_file(root / ".env", env_updates, example=root / ".env.example")
    return {
        "config_hash": cfg.config_hash,
        "budget_usd": cfg.pipeline.budget_usd,
        "concurrency": cfg.pipeline.concurrency,
        "written_keys": written,
    }


def set_input_dir(root: Path, raw: str) -> tuple[Path, str]:
    """切换论文目录并写回 pipeline.yaml。"""
    directory = resolve_user_dir(root, raw)
    if not directory.is_dir():
        raise ValueError(f"目录不存在或不是目录：{directory}")
    pipeline_path = root / PIPELINE_REL
    original = pipeline_path.read_text(encoding="utf-8")
    pipeline_path.write_text(
        update_pipeline_text(original, {"input_dir": str(directory)}), encoding="utf-8"
    )
    cfg, error = load_config_safe(root)
    if cfg is None:
        pipeline_path.write_text(original, encoding="utf-8")
        raise ValueError(f"配置未通过校验，已回滚：{error}")
    return directory, cfg.config_hash


# --------------------------------------------------------------------------- #
# 路径安全
# --------------------------------------------------------------------------- #


def resolve_user_dir(root: Path, raw: str) -> Path:
    text = (raw or "").strip()
    if not text:
        raise ValueError("目录不能为空")
    candidate = Path(os.path.expanduser(text))
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        return candidate.resolve()
    except OSError as exc:  # pragma: no cover - 极少见
        raise ValueError(f"路径无法解析：{exc}") from exc


def is_within(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def allowed_path(root: Path, target: Path, extra_dirs: list[Path] | None = None) -> bool:
    """仓库内，或用户显式配置的目录内——其余一律拒绝，防目录穿越。"""
    if is_within(target, root):
        return True
    for directory in extra_dirs or []:
        if is_within(target, directory):
            return True
    return False


def resolve_input_dir(root: Path, cfg: Config | None) -> Path | None:
    if cfg is None or not cfg.pipeline.input_dir:
        return None
    try:
        return resolve_user_dir(root, cfg.pipeline.input_dir)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# 论文目录
# --------------------------------------------------------------------------- #


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"  # pragma: no cover - 不可达


@dataclass(frozen=True)
class PdfEntry:
    name: str
    size: int
    mtime: str

    @property
    def size_text(self) -> str:
        return human_size(self.size)


def list_pdfs(directory: Path, limit: int = PDF_LIMIT) -> tuple[list[PdfEntry], bool, str | None]:
    if not directory.is_dir():
        return [], False, "目录不存在"
    entries: list[PdfEntry] = []
    try:
        children = sorted(directory.iterdir(), key=lambda p: p.name.casefold())
    except OSError as exc:
        return [], False, f"无法读取目录：{exc}"
    for child in children:
        if child.name.startswith(".") or not child.is_file():
            continue
        if child.suffix.casefold() != ".pdf":
            continue
        try:
            stat = child.stat()
        except OSError:
            continue
        stamp = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        entries.append(PdfEntry(child.name, stat.st_size, stamp))
        if len(entries) >= limit:
            return entries, True, None
    return entries, False, None


def read_corpus(root: Path) -> list[dict[str, str]]:
    path = root / CORPUS_REL
    if not path.is_file():
        return []
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except OSError:
        return []


def sha16(article_key: str) -> str:
    return article_key.split(":")[-1][:16]


def read_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# 规则文档读取
# --------------------------------------------------------------------------- #


def resolve_doc(root: Path, raw: str, input_dir: Path | None) -> Path:
    """规则文档路径：只允许仓库内或当前论文目录内。"""
    path = Path(os.path.expanduser((raw or "").strip()))
    if not raw or not str(path):
        raise ValueError("规则文档路径不能为空")
    if not path.is_absolute():
        path = root / path
    try:
        path = path.resolve()
    except OSError as exc:  # pragma: no cover
        raise ValueError(f"路径无法解析：{exc}") from exc
    if not path.is_file():
        raise ValueError(f"规则文档不存在：{path}")
    if path.suffix.casefold() not in DOC_SUFFIXES:
        raise ValueError("规则文档只支持 .md / .markdown / .txt / .pdf / .docx")
    if not allowed_path(root, path, [input_dir] if input_dir else []):
        raise PermissionError("规则文档必须放在仓库或当前论文目录内")
    return path


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        try:
            xml = archive.read("word/document.xml").decode("utf-8", "ignore")
        except KeyError as exc:
            raise ValueError("docx 缺少 word/document.xml，无法读取正文") from exc
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    return unescape(text)


def read_document(path: Path) -> tuple[str, int | None]:
    """返回 (正文, 页数)。PDF 用 pymupdf，docx 直接解 XML，md/txt 直读。"""
    suffix = path.suffix.casefold()
    if suffix in {".md", ".markdown", ".txt"}:
        return path.read_text(encoding="utf-8", errors="replace")[:TEXT_LIMIT], None
    if suffix == ".pdf":
        try:
            import fitz  # pymupdf
        except ImportError as exc:  # pragma: no cover - 依赖缺失
            raise ValueError("读取 PDF 需要 pymupdf，请先安装依赖") from exc
        with fitz.open(path) as doc:
            pages = [page.get_text() for page in doc]
            count = len(doc)
        return "\n".join(pages)[:TEXT_LIMIT], count
    if suffix == ".docx":
        return _docx_text(path)[:TEXT_LIMIT], None
    raise ValueError(f"不支持的文档类型：{path.suffix}")


def source_document_choices(root: Path, input_dir: Path | None) -> list[dict[str, str]]:
    """候选规则文档：仓库 docs/rules 下的 md，以及论文目录里的规则类文档。"""
    seen: set[Path] = set()
    out: list[dict[str, str]] = []
    for base in (root / "docs", root / "rules"):
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix.casefold() not in DOC_SUFFIXES:
                continue
            resolved = path.resolve()
            if resolved in seen or resolved.name.startswith("."):
                continue
            seen.add(resolved)
            out.append({"path": str(path.relative_to(root)), "name": path.name, "origin": "repo"})
    if input_dir and input_dir.is_dir():
        for path in sorted(input_dir.iterdir()):
            if (
                path.is_file()
                and path.suffix.casefold() in DOC_SUFFIXES
                and re.search(r"rule|code|plan|方案|规则|编码", path.name, re.I)
            ):
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                out.append({"path": str(path), "name": path.name, "origin": "papers"})
    return out


# --------------------------------------------------------------------------- #
# 下载
# --------------------------------------------------------------------------- #

DOWNLOAD_PREFIXES = ("runs", "rulepacks", "data/07_analysis", "reports")


def safe_download(root: Path, rel: str) -> Path | None:
    try:
        target = (root / rel).resolve()
    except OSError:
        return None
    if not is_within(target, root) or not target.is_file():
        return None
    parts = target.relative_to(root).parts
    if any(part.startswith(".") for part in parts):
        return None
    posix = "/".join(parts)
    if not any(posix == prefix or posix.startswith(prefix + "/") for prefix in DOWNLOAD_PREFIXES):
        return None
    return target


# --------------------------------------------------------------------------- #
# 子进程环境
# --------------------------------------------------------------------------- #


def run_env(root: Path) -> dict[str, str]:
    """.env 里的密钥只在这里进入子进程环境，不落到任何响应里。"""
    env = dict(os.environ)
    for name, value in read_env(root / ".env").items():
        if value:
            env[name] = value
    src = str(root / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src + (os.pathsep + existing if existing else "")
    env["PYTHONUNBUFFERED"] = "1"
    return env


__all__ = [
    "DEFAULT_KEY_ENVS",
    "DOC_SUFFIXES",
    "PDF_LIMIT",
    "PdfEntry",
    "SLOT_LABELS",
    "SecretScrubber",
    "allowed_path",
    "apply_settings",
    "human_size",
    "is_within",
    "key_status",
    "list_pdfs",
    "load_config_safe",
    "parse_env_text",
    "read_corpus",
    "read_document",
    "read_env",
    "read_json",
    "resolve_doc",
    "resolve_input_dir",
    "resolve_user_dir",
    "run_env",
    "safe_download",
    "set_input_dir",
    "sha16",
    "sha256_file",
    "sha256_text",
    "slot_env_names",
    "source_document_choices",
    "update_env_file",
    "update_pipeline_text",
]
