"""Stage 9 — 报告渲染：一致性、错误分析、方法学段落。

输入（只读，不修改任何上游产物）：

* ``data/05_adjudication/diff_cells.csv`` —— Stage 6 逐格比对（**必需**）；
* ``data/05_adjudication/disagreements.csv`` —— 实质分歧清单（可选，用于行数核对）；
* ``data/05_adjudication/blocks.csv`` —— 块对齐审计（可选；缺它就无法把单元格归并到
  论文，聚类与去向一致率会降级并在报告中明确标注）；
* ``data/05_adjudication/<batch>_adjudication.jsonl`` —— 裁决记录（可选，错误归因）；
* ``data/05_adjudication/<batch>_audit.jsonl`` —— 一致样本审计（可选，共享错误率）；
* ``data/06_consensus/provenance.csv`` 与共识工作簿（可选，取值来源与文章特征分层）。

输出（默认 ``reports/``）：

* ``agreement_report.md`` —— 样本描述、逐字段一致性、分表去向一致率；
* ``error_analysis.md`` —— E01–E12 分布、按字段/文章特征分层、共享错误率；
* ``methods_paragraph.md`` —— 可直接放入论文的中文方法学段落 + 占位符清单。

三份报告头部都写明数据来源 run_id 与生成时间；manifest 落在
``artifacts/stage-09_reports/<run_id>/manifest.json``。

报告只搬运数字，不做任何补值：缺什么就写「未执行」并说明缺哪个输入，
绝不用一致率冒充正确率，也不用看起来合理的假数字填方法学段落。
"""

from __future__ import annotations

import csv
import json
import re
import zlib
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from ..adjudicate.classify import SUBSTANTIVE_MATCH_TYPES
from ..analysis.export import find_consensus_workbook
from ..artifacts import build_manifest, git_head, make_run_id, write_manifest
from ..config import Config, sha256_file
from ..consensus.build import paper_key
from ..validate.model import load_codebook
from .metrics import (
    DEFAULT_BOOTSTRAP_B,
    DEFAULT_SEED,
    ICCStats,
    Interval,
    WeightedKappaStats,
    cluster_bootstrap_ci,
    cohen_kappa,
    icc_2_1,
    label_of,
    weighted_kappa,
)

DIFF_REL = Path("data") / "05_adjudication"
CONSENSUS_REL = Path("data") / "06_consensus"
REPORTS_REL = Path("reports")
FIELDS_REL = Path("rules") / "fields.yaml"
AGREEMENT_NAME = "agreement_report.md"
ERROR_NAME = "error_analysis.md"
METHODS_NAME = "methods_paragraph.md"

KIND_CATEGORICAL = "categorical"
KIND_ORDINAL = "ordinal"
KIND_CONTINUOUS = "continuous"
KIND_TEXT = "text"
KIND_LABELS = {
    KIND_CATEGORICAL: "分类",
    KIND_ORDINAL: "有序",
    KIND_CONTINUOUS: "连续",
    KIND_TEXT: "文本",
}
KIND_RANK = {KIND_CATEGORICAL: 0, KIND_ORDINAL: 1, KIND_CONTINUOUS: 2, KIND_TEXT: 3}

NUMERIC_TYPES = frozenset({"integer", "number", "number_or_missing", "number_or_null"})
ENUM_TYPES = frozenset({"enum", "enum_or_missing"})

# 有序字段的等级序列；rules/fields.yaml 的 allowed 是权威来源，这里只是兜底。
ORDINAL_ORDER: dict[str, tuple[Any, ...]] = {"age_group": (1, 2, 3, 4)}
# 自由文本：类别数接近单元格数，κ/ICC 没有解释意义，只报百分比一致率。
TEXT_FIELDS = frozenset({"mean_calculation", "age_calculation", "clinical_type"})

REQUIRED_DIFF_COLUMNS = ("record_id", "field", "coder_a_value", "coder_b_value", "match_type")

# 错误分类法：docs/ERROR_TAXONOMY.md 固定 12 类；新增类别必须先改文档。
ERROR_CODES: tuple[tuple[str, str, str], ...] = (
    ("E01", "value_extraction", "critical"),
    ("E02", "scale_unit", "critical"),
    ("E03", "sample_mismatch", "critical"),
    ("E04", "timepoint_baseline", "critical"),
    ("E05", "measure_version", "major"),
    ("E06", "rule_application", "major"),
    ("E07", "eligibility_decision", "critical"),
    ("E08", "evidence_mismatch", "major"),
    ("E09", "omission", "major"),
    ("E10", "fabrication", "critical"),
    ("E11", "formatting_normalization", "cosmetic"),
    ("E12", "source_ambiguity", "非模型错误"),
)
ERROR_CODE_SET = frozenset(code for code, _, _ in ERROR_CODES)
PLACEHOLDER_PATTERN = re.compile(r"【待填：[^】]+】")

NOT_EXECUTED = "未执行"


class ReportDataError(RuntimeError):
    """报告所需数据缺失或不可读；消息面向人，含缺失文件与应先跑的命令。"""


# --------------------------------------------------------------------------- #
# 字段画像
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FieldProfile:
    name: str
    kind: str
    order: tuple[Any, ...] = ()
    labels: dict[str, str] = field(default_factory=dict)

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    def label_for(self, value: Any) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        if not text:
            return ""
        return self.labels.get(text, text)


def load_field_profiles(path: Path, *, root: Path | None = None) -> dict[str, FieldProfile]:
    """读 ``rules/fields.yaml``，把字段类型映射到本报告使用的四类画像。"""
    if not path.is_file():
        where = _rel(root, path) if root is not None else str(path)
        raise ReportDataError(
            f"缺少字段定义：{where}\n"
            "报告的指标选择依赖字段类型（分类/有序/连续/文本），请确认仓库 rules/ 完整。"
        )
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    profiles: dict[str, FieldProfile] = {}
    for item in payload.get("fields") or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        declared = str(item.get("type") or "string")
        labels = {
            str(key): str(value) for key, value in (item.get("labels") or {}).items()
        }
        order: tuple[Any, ...] = ()
        if name in ORDINAL_ORDER:
            allowed = [value for value in item.get("allowed") or [] if value != -999]
            order = tuple(allowed) if len(allowed) >= 2 else ORDINAL_ORDER[name]
            kind = KIND_ORDINAL
        elif declared in NUMERIC_TYPES:
            kind = KIND_CONTINUOUS
        elif name in TEXT_FIELDS:
            kind = KIND_TEXT
        elif declared in ENUM_TYPES or declared in {"string", "string_or_null"}:
            kind = KIND_CATEGORICAL
        else:
            kind = KIND_CATEGORICAL
        profiles[name] = FieldProfile(name=name, kind=kind, order=order, labels=labels)
    if not profiles:
        raise ReportDataError(f"字段定义为空：{path}；无法确定任何字段的指标。")
    return profiles


# --------------------------------------------------------------------------- #
# 输入装载
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Cell:
    record_id: str
    field_name: str
    value_a: str
    value_b: str
    match_type: str


@dataclass(frozen=True)
class BlockRow:
    record_id: str
    paper_key: str
    sheet: str
    status: str
    category: str


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [
            {key: (value if value is not None else "") for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]


def _csv_header(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return next(csv.reader(handle), [])


def _load_cells(path: Path, *, root: Path, batch: str) -> list[Cell]:
    if not path.is_file():
        raise ReportDataError(
            f"缺少逐格比对结果：{_rel(root, path)}\n"
            f"请先运行：meta-coding diff --batch {batch}\n"
            "（Stage 6 会写出 diff_cells.csv / disagreements.csv / blocks.csv）"
        )
    rows = _read_csv(path)
    header = set(rows[0]) if rows else set(_csv_header(path))
    missing = [column for column in REQUIRED_DIFF_COLUMNS if column not in header]
    if missing:
        raise ReportDataError(
            f"{_rel(root, path)} 缺少列：{', '.join(missing)}\n"
            f"该文件应由 meta-coding diff --batch {batch} 生成，请重新运行 Stage 6。"
        )
    if not rows:
        raise ReportDataError(
            f"{_rel(root, path)} 只有表头、没有数据行，无法计算一致性。\n"
            f"请先完成编码与比对：meta-coding diff --batch {batch}"
        )
    cells: list[Cell] = []
    for row in rows:
        if not row.get("field") or not row.get("record_id"):
            continue
        cells.append(
            Cell(
                record_id=row.get("record_id", ""),
                field_name=row.get("field", ""),
                value_a=row.get("coder_a_value", ""),
                value_b=row.get("coder_b_value", ""),
                match_type=row.get("match_type", ""),
            )
        )
    if not cells:
        raise ReportDataError(
            f"{_rel(root, path)} 没有可用的单元格（record_id/field 全为空）。\n"
            f"请重新运行：meta-coding diff --batch {batch}"
        )
    return cells


def _load_blocks(path: Path) -> dict[str, BlockRow]:
    if not path.is_file():
        return {}
    rows = _read_csv(path)
    blocks: dict[str, BlockRow] = {}
    for row in rows:
        record_id = row.get("record_id", "")
        if not record_id:
            continue
        blocks[record_id] = BlockRow(
            record_id=record_id,
            paper_key=row.get("paper_key", ""),
            sheet=row.get("sheet", ""),
            status=row.get("status", ""),
            category=row.get("category", ""),
        )
    return blocks


def _load_jsonl(paths: Iterable[Path]) -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    bad_lines = 0
    for path in paths:
        with path.open(encoding="utf-8-sig") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    bad_lines += 1
                    continue
                if isinstance(payload, dict):
                    records.append(payload)
                else:
                    bad_lines += 1
    return records, bad_lines


def _jsonl_candidates(directory: Path, batch: str, kind: str) -> list[Path]:
    """先找 ``<batch>_<kind>.jsonl``，否则取唯一的 ``*_<kind>.jsonl``。"""
    if not directory.is_dir():
        return []
    exact = directory / f"{batch}_{kind}.jsonl"
    if exact.is_file():
        return [exact]
    matches = sorted(directory.glob(f"*_{kind}.jsonl"))
    return matches


def _count_rows(path: Path) -> int | None:
    if not path.is_file():
        return None
    try:
        return len(_read_csv(path))
    except (OSError, csv.Error):
        return None


def _provenance_counts(path: Path) -> dict[str, int] | None:
    if not path.is_file():
        return None
    counts: Counter[str] = Counter()
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            counts[row.get("value_source") or "（空）"] += 1
    return dict(counts) if counts else None


@dataclass(frozen=True)
class PaperFeature:
    year: Any
    country: str
    sample_type: Any


def _load_paper_features(root: Path, batch: str) -> dict[str, PaperFeature]:
    """从共识工作簿取文章特征；缺工作簿时返回空表（相关小节标未执行）。"""
    try:
        workbook = find_consensus_workbook(root, batch)
    except FileNotFoundError:
        return {}
    try:
        codebook = load_codebook(workbook)
    except (OSError, ValueError):
        return {}
    features: dict[str, PaperFeature] = {}
    for sheet in ("WBIS", "WSSQ"):
        for block in codebook.blocks.get(sheet, []):
            row = block.total
            if row is None:
                continue
            key = paper_key(row.get("full_reference"))
            if not key:
                continue
            features.setdefault(
                key,
                PaperFeature(
                    year=row.get("publication_year"),
                    country=str(row.get("country") or ""),
                    sample_type=row.get("sample_type"),
                ),
            )
    return features


# --------------------------------------------------------------------------- #
# 一致性计算
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FieldAgreement:
    field_name: str
    kind: str
    cells: int
    papers: int
    raters: int
    observed: float
    kappa: float | None
    kappa_ci: Interval | None
    pabak: float
    weighted: float | None
    weighted_ci: Interval | None
    icc: float | None
    icc_ci: Interval | None
    note: str

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)


def _number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text.replace(",", ""))
    except ValueError:
        return None
    return None if number == -999.0 else number


def _pair_of(cell: Cell) -> tuple[str, str]:
    """单元格 → (A 标签, B 标签)。

    Stage 6 已按规范化/容差判过等：``exact`` / ``equivalent`` /
    ``numeric_within_tolerance`` 视为同一标签（保留 A 的书写），只有 ``mismatch``
    与 ``missing_vs_value`` 构成不同标签。这样 κ 的一致率与逐格比对口径完全一致。
    """
    left = label_of(cell.value_a)
    right = label_of(cell.value_b)
    if cell.match_type not in SUBSTANTIVE_MATCH_TYPES:
        right = left
    return left, right


def _cluster_key(cell: Cell, blocks: dict[str, BlockRow]) -> str:
    """单元格的论文聚类键；缺 blocks.csv 时退回块 ID（报告会标注降级）。"""
    block_id = cell.record_id.rsplit("-s", 1)[0]
    row = blocks.get(block_id)
    if row is not None and row.paper_key:
        return row.paper_key
    return block_id


def _grouped(
    entries: Sequence[tuple[Cell, tuple[str, str]]], blocks: dict[str, BlockRow]
) -> dict[str, list[tuple[str, str]]]:
    clusters: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for cell, pair in entries:
        clusters[_cluster_key(cell, blocks)].append(pair)
    return dict(clusters)


def _field_seed(field_name: str, base: int) -> int:
    """每个字段一个稳定子种子：新增字段不会改变其他字段的区间。"""
    return (base ^ zlib.crc32(field_name.encode("utf-8"))) & 0xFFFFFFFF


def _field_agreement(
    profile: FieldProfile,
    cells: Sequence[Cell],
    blocks: dict[str, BlockRow],
    *,
    b: int,
    seed: int,
) -> FieldAgreement:
    pairs = [_pair_of(cell) for cell in cells]
    entries = list(zip(cells, pairs, strict=True))
    clusters = _grouped(entries, blocks)
    stats = cohen_kappa(pairs)
    field_seed = _field_seed(profile.name, seed)
    kappa_ci = cluster_bootstrap_ci(
        clusters, lambda sample: cohen_kappa(sample).kappa, b=b, seed=field_seed
    )
    notes: list[str] = []
    if profile.kind == KIND_CATEGORICAL:
        if stats.kappa is None:
            notes.append("双方均只出现一个类别，κ 无定义")
        elif stats.paradox:
            notes.append("κ 悖论：一致率高而 κ 偏低，应同时看 PABAK")

    weighted_value: float | None = None
    weighted_ci: Interval | None = None
    icc_value: float | None = None
    icc_ci: Interval | None = None

    if profile.kind == KIND_ORDINAL:
        levels = {label_of(item) for item in profile.order}
        ordinal_stats: WeightedKappaStats = weighted_kappa(pairs, profile.order)
        ordinal_entries = [
            (cell, pair) for cell, pair in entries if pair[0] in levels and pair[1] in levels
        ]
        weighted_value = ordinal_stats.kappa
        weighted_ci = cluster_bootstrap_ci(
            _grouped(ordinal_entries, blocks),
            lambda sample: weighted_kappa(sample, profile.order).kappa,
            b=b,
            seed=field_seed ^ 0x11,
        )
        if ordinal_stats.dropped:
            notes.append(f"{ordinal_stats.dropped} 对因缺失或超出量表未纳入加权 κ")
        if ordinal_stats.kappa is None:
            notes.append("等级对不足，加权 κ 无定义")
    elif profile.kind == KIND_CONTINUOUS:
        numeric_entries: list[tuple[Cell, tuple[float, float]]] = []
        for cell in cells:
            left = _number(cell.value_a)
            right = _number(cell.value_b)
            if left is not None and right is not None:
                numeric_entries.append((cell, (left, right)))
        numeric_clusters: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for cell, pair in numeric_entries:
            numeric_clusters[_cluster_key(cell, blocks)].append(pair)
        icc_stats: ICCStats = icc_2_1([pair for _, pair in numeric_entries])
        icc_value = icc_stats.icc
        icc_ci = cluster_bootstrap_ci(
            dict(numeric_clusters), lambda sample: icc_2_1(sample).icc, b=b, seed=field_seed ^ 0x22
        )
        dropped = len(cells) - len(numeric_entries)
        if dropped:
            notes.append(f"ICC 成对删除 {dropped} 格（单侧缺失或非数值）")
        if icc_stats.subjects < 2:
            notes.append("有效对象不足 2 个，ICC 未计算")
        elif icc_value is None:
            notes.append("对象间与残差均方退化为 0，ICC 无定义")
    elif profile.kind == KIND_TEXT:
        notes.append("自由文本：仅报百分比一致率，κ/ICC 不适用")

    return FieldAgreement(
        field_name=profile.name,
        kind=profile.kind,
        cells=len(cells),
        papers=len(clusters),
        raters=2,
        observed=stats.observed,
        kappa=stats.kappa,
        kappa_ci=kappa_ci,
        pabak=stats.pabak,
        weighted=weighted_value,
        weighted_ci=weighted_ci,
        icc=icc_value,
        icc_ci=icc_ci,
        note="；".join(part for part in notes if part),
    )


def _agreement_table(
    by_field: dict[str, list[Cell]],
    profiles: dict[str, FieldProfile],
    blocks: dict[str, BlockRow],
    *,
    b: int,
    seed: int,
) -> list[FieldAgreement]:
    rows: list[FieldAgreement] = []
    for field_name in sorted(by_field):
        profile = profiles.get(
            field_name, FieldProfile(name=field_name, kind=KIND_CATEGORICAL)
        )
        rows.append(_field_agreement(profile, by_field[field_name], blocks, b=b, seed=seed))
    rows.sort(key=lambda item: (KIND_RANK.get(item.kind, 9), item.field_name))
    return rows


@dataclass(frozen=True)
class RoutingSummary:
    blocks: int
    matched: int
    routing: int
    structure: int
    rate: float
    interval: Interval | None
    papers: int


def _routing_summary(
    blocks: dict[str, BlockRow], *, b: int, seed: int
) -> RoutingSummary | None:
    if not blocks:
        return None
    rows = list(blocks.values())
    total = len(rows)
    routing = sum(1 for row in rows if row.category == "routing_mismatch")
    structure = sum(1 for row in rows if row.category == "block_structure_mismatch")
    matched = sum(1 for row in rows if row.status == "matched")
    values: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        values[row.paper_key or row.record_id].append(
            0.0 if row.category == "routing_mismatch" else 1.0
        )
    rate = (total - routing) / total if total else 0.0
    interval = cluster_bootstrap_ci(
        dict(values), lambda sample: sum(sample) / len(sample), b=b, seed=seed
    )
    papers = len({row.paper_key for row in rows if row.paper_key})
    return RoutingSummary(
        blocks=total,
        matched=matched,
        routing=routing,
        structure=structure,
        rate=rate,
        interval=interval,
        papers=papers,
    )


# --------------------------------------------------------------------------- #
# 错误分析
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Stratum:
    dimension: str
    label: str
    records: int
    codes: int


@dataclass
class ErrorStats:
    records: int
    attributed: int
    bad_lines: int
    by_code: dict[str, dict[str, int]]
    by_field: dict[str, Counter[str]]
    strata: list[Stratum]
    sources: list[Path]

    @property
    def executed(self) -> bool:
        return self.attributed > 0


@dataclass(frozen=True)
class AuditStats:
    records: int
    both_wrong: int
    sources: list[Path]

    @property
    def rate(self) -> float:
        return self.both_wrong / self.records if self.records else 0.0


def _error_entries(record: dict[str, Any]) -> list[tuple[str, str]]:
    payload = record.get("error_types")
    entries: list[tuple[str, str]] = []
    if isinstance(payload, dict):
        for coder, codes in payload.items():
            if not isinstance(codes, list):
                continue
            for code in codes:
                normalized = str(code).strip().upper()
                if normalized in ERROR_CODE_SET:
                    entries.append((str(coder), normalized))
    elif isinstance(payload, list):
        for code in payload:
            normalized = str(code).strip().upper()
            if normalized in ERROR_CODE_SET:
                entries.append(("audited", normalized))
    return entries


def _year_bucket(value: Any) -> str:
    try:
        year = int(float(value))
    except (TypeError, ValueError):
        return "未填"
    if year <= 2010:
        return "≤2010"
    if year <= 2015:
        return "2011–2015"
    if year <= 2020:
        return "2016–2020"
    return "≥2021"


def _strata_labels(
    feature: PaperFeature | None, profiles: dict[str, FieldProfile]
) -> tuple[tuple[str, str], ...]:
    if feature is None:
        return (
            ("发表年份", "无法关联论文"),
            ("国家", "无法关联论文"),
            ("样本类型", "无法关联论文"),
        )
    sample_profile = profiles.get("sample_type")
    sample = sample_profile.label_for(feature.sample_type) if sample_profile else ""
    return (
        ("发表年份", _year_bucket(feature.year)),
        ("国家", feature.country or "未填"),
        ("样本类型", sample or "未填"),
    )


def _error_stats(
    records: Sequence[dict[str, Any]],
    features: dict[str, PaperFeature],
    profiles: dict[str, FieldProfile],
    sources: Sequence[Path],
    *,
    bad_lines: int = 0,
) -> ErrorStats:
    by_code: dict[str, dict[str, int]] = {
        code: {"coder_1": 0, "coder_2": 0} for code, _, _ in ERROR_CODES
    }
    by_field: dict[str, Counter[str]] = defaultdict(Counter)
    buckets: dict[str, dict[str, list[int]]] = defaultdict(
        lambda: defaultdict(lambda: [0, 0])
    )
    attributed = 0
    for record in records:
        entries = _error_entries(record)
        if entries:
            attributed += 1
        field_name = str(record.get("field") or "（未标注字段）")
        key = paper_key(record.get("full_reference") or record.get("article_key") or "")
        feature = features.get(key)
        for dimension, label in _strata_labels(feature, profiles):
            buckets[dimension][label][0] += 1
        for coder, code in entries:
            counters = by_code.setdefault(code, {"coder_1": 0, "coder_2": 0})
            counters[coder] = counters.get(coder, 0) + 1
            by_field[field_name][code] += 1
            for dimension, label in _strata_labels(feature, profiles):
                buckets[dimension][label][1] += 1

    strata = [
        Stratum(dimension=dimension, label=label, records=counts[0], codes=counts[1])
        for dimension, labels in buckets.items()
        for label, counts in sorted(labels.items(), key=lambda item: item[0])
    ]
    order = {"发表年份": 0, "国家": 1, "样本类型": 2}
    strata.sort(key=lambda item: (order.get(item.dimension, 9), item.label))
    return ErrorStats(
        records=len(records),
        attributed=attributed,
        bad_lines=bad_lines,
        by_code=by_code,
        by_field=dict(by_field),
        strata=strata,
        sources=list(sources),
    )


def _audit_stats(records: Sequence[dict[str, Any]], sources: Sequence[Path]) -> AuditStats | None:
    if not records:
        return None
    both_wrong = sum(1 for record in records if str(record.get("verdict") or "") == "both_wrong")
    return AuditStats(records=len(records), both_wrong=both_wrong, sources=list(sources))


# --------------------------------------------------------------------------- #
# 输出数据源清单
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SourceFile:
    label: str
    path: Path | None
    rows: int | None = None
    note: str = ""


# --------------------------------------------------------------------------- #
# markdown 基础件
# --------------------------------------------------------------------------- #


def _rel(root: Path, path: Path | None) -> str:
    if path is None:
        return "—"
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _sha_prefix(path: Path) -> str:
    try:
        return sha256_file(path)[:12]
    except OSError:  # pragma: no cover - 权限问题极少见
        return "—"


def _cell(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_cell(item) for item in row) + " |")
    return lines


def _num(value: float | None, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _ci(interval: Interval | None, digits: int = 3) -> str:
    if interval is None:
        return "—"
    low = 0.0 if abs(interval.low) < 10 ** (-digits) else interval.low
    high = 0.0 if abs(interval.high) < 10 ** (-digits) else interval.high
    return f"[{low:.{digits}f}, {high:.{digits}f}]"


def _pct(value: float, digits: int = 1) -> str:
    return f"{value * 100:.{digits}f}%"


def _range_text(values: Sequence[float]) -> str:
    if not values:
        return "无可用估计"
    ordered = sorted(values)
    if len(ordered) == 1:
        return f"仅 1 个估计：{ordered[0]:.3f}"
    median = ordered[len(ordered) // 2] if len(ordered) % 2 else (
        ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]
    ) / 2
    return f"中位数 {median:.3f}，范围 {ordered[0]:.3f}–{ordered[-1]:.3f}（n = {len(ordered)}）"


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _header(*, title: str, batch: str, run_id: str, created_at: str) -> list[str]:
    return [
        f"# {title} — {batch}",
        "",
        f"> 数据来源 run_id：`{run_id}`",
        f"> 生成时间：{created_at}（UTC）",
        f"> 批次：`{batch}`",
        f"> 生成命令：`meta-coding report --batch {batch}`",
        "",
    ]


def _source_table(root: Path, sources: Sequence[SourceFile]) -> list[str]:
    rows: list[list[Any]] = []
    for item in sources:
        if item.path is None or not item.path.is_file():
            rows.append([item.label, "—", "—", "—", item.note or "缺失"])
            continue
        rows.append(
            [
                item.label,
                f"`{_rel(root, item.path)}`",
                "—" if item.rows is None else item.rows,
                _sha_prefix(item.path),
                "已读取",
            ]
        )
    return _table(["输入", "文件", "行数", "sha256 前 12 位", "状态"], rows)


# --------------------------------------------------------------------------- #
# 占位符
# --------------------------------------------------------------------------- #


class Placeholders:
    """方法学段落的占位符登记表：用到的才登记，登记的一定会出现在清单里。"""

    def __init__(self) -> None:
        self._notes: dict[str, str] = {}

    def token(self, key: str, note: str) -> str:
        token = f"【待填：{key}】"
        self._notes.setdefault(token, note)
        return token

    @property
    def tokens(self) -> tuple[str, ...]:
        return tuple(sorted(self._notes))

    def rows(self) -> list[list[str]]:
        return [[f"`{token}`", self._notes[token]] for token in sorted(self._notes)]


def _model_label(
    slot: Any, placeholders: Placeholders, key: str, note: str
) -> str:
    if slot is not None and getattr(slot, "bound", False):
        return f"{slot.provider} 的 {slot.model}（{slot.family}）"
    return placeholders.token(key, note)


def _setting(
    value: Any, placeholders: Placeholders, key: str, note: str
) -> str:
    if value is None or value == "":
        return placeholders.token(key, note)
    return f"{value:g}" if isinstance(value, float) else str(value)


def _inline(label: str) -> str:
    """中文句子里的占位符不加空格；真实模型名前后留空格。"""
    return label if label.startswith("【") else f" {label} "


# --------------------------------------------------------------------------- #
# 一致性报告
# --------------------------------------------------------------------------- #


def _render_agreement(
    *,
    root: Path,
    batch: str,
    run_id: str,
    created_at: str,
    cells: Sequence[Cell],
    agreements: Sequence[FieldAgreement],
    routing: RoutingSummary | None,
    provenance: dict[str, int] | None,
    sources: Sequence[SourceFile],
    papers: int,
    blocks: int,
    bootstrap_b: int,
    seed: int,
) -> str:
    substantive = sum(1 for cell in cells if cell.match_type in SUBSTANTIVE_MATCH_TYPES)
    formatting = len(cells) - substantive
    lines = _header(
        title="编码一致性报告", batch=batch, run_id=run_id, created_at=created_at
    )

    lines += ["## 1. 样本描述", ""]
    lines += _table(
        ["项目", "值"],
        [
            ["论文数（`blocks.csv` 的 `paper_key`）", papers or "—（缺 blocks.csv）"],
            ["编码块数", blocks or "—（缺 blocks.csv）"],
            ["比对单元格", len(cells)],
            ["涉及字段", len(agreements)],
            [
                "实质分歧（`mismatch` / `missing_vs_value`）",
                f"{substantive}（{_pct(substantive / len(cells))}）",
            ],
            [
                "仅格式差异（`exact` / `equivalent` / `numeric_within_tolerance`）",
                f"{formatting}（{_pct(formatting / len(cells))}）",
            ],
        ],
    )
    lines += [
        "",
        "单元格计数来自 `diff_cells.csv`：双方都缺失的格不写入该表，只在 Stage 6 的",
        "manifest 里以 `both_absent_cells` 计数，因此本报告不把它放进分母，也不反推。",
        "",
    ]

    lines += ["### 取值来源（Stage 7 `provenance.csv`）", ""]
    if provenance:
        total = sum(provenance.values())
        rows = [
            [source, count, _pct(count / total)]
            for source, count in sorted(provenance.items(), key=lambda item: -item[1])
        ]
        lines += _table(["来源", "单元格数", "占比"], rows)
    else:
        lines.append(
            f"{NOT_EXECUTED}：未找到 `data/06_consensus/provenance.csv`，"
            "无法给出共识取值的来源构成。"
        )
    lines.append("")

    lines += ["## 2. 数据来源核对", ""]
    lines += _source_table(root, sources)
    lines.append("")

    lines += ["## 3. 逐字段一致性", ""]
    kappas = [
        item.kappa
        for item in agreements
        if item.kappa is not None and item.kind == KIND_CATEGORICAL
    ]
    weighted_values = [item.weighted for item in agreements if item.weighted is not None]
    icc_values = [item.icc for item in agreements if item.icc is not None]
    lines += [
        f"- 分类字段 κ：{_range_text(kappas)}",
        f"- 有序字段加权 κ：{_range_text(weighted_values)}",
        f"- 连续字段 ICC(2,1)：{_range_text(icc_values)}",
        "",
        "标签口径：凡 Stage 6 判为 `exact` / `equivalent` / `numeric_within_tolerance`",
        "的单元格，按同一标签计入（与逐格比对口径一致）；`mismatch` 与",
        "`missing_vs_value` 计入不一致。**重数**指每个单元格的评分数，本流程固定为 2。",
        "",
    ]
    rows: list[list[str]] = []
    for item in agreements:
        if item.kind == KIND_CATEGORICAL:
            main = f"κ {_num(item.kappa)}" if item.kappa is not None else "κ 无定义"
            main_ci = _ci(item.kappa_ci)
            aux = f"PABAK {_num(item.pabak)}"
        elif item.kind == KIND_ORDINAL:
            main = f"加权 κ {_num(item.weighted)}"
            main_ci = _ci(item.weighted_ci)
            aux = f"PABAK {_num(item.pabak)}"
        elif item.kind == KIND_CONTINUOUS:
            main = f"ICC(2,1) {_num(item.icc)}"
            main_ci = _ci(item.icc_ci)
            aux = "一致率列为 ±0.01 容差内比例"
        else:
            main = "—"
            main_ci = "—"
            aux = "—"
        rows.append(
            [
                f"`{item.field_name}`",
                item.kind_label,
                item.cells,
                item.papers,
                item.raters,
                _pct(item.observed),
                main,
                main_ci,
                aux,
                item.note or "—",
            ]
        )
    lines += _table(
        ["字段", "类型", "单元格", "论文", "重数", "一致率", "主指标", "95% CI", "辅助", "备注"],
        rows,
    )
    lines += [
        "",
        f"置信区间口径：按论文聚类的 percentile bootstrap（B = {bootstrap_b}，"
        f"固定随机种子 {seed}），",
        "每次有放回地抽取论文并整篇带入其全部单元格；区间端点夹到包含点估计。",
        "",
    ]

    lines += ["## 4. 分表去向一致率", ""]
    if routing is None:
        lines.append(
            f"{NOT_EXECUTED}：缺少 `data/05_adjudication/blocks.csv`，"
            "无法重建块级去向决定。请重新运行 `meta-coding diff` 以生成该文件。"
        )
    else:
        lines += _table(
            ["指标", "值", "95% CI"],
            [
                ["块决定总数", routing.blocks, "—"],
                [
                    "块级完全一致（`status=matched`）",
                    f"{routing.matched}（{_pct(routing.matched / routing.blocks)}）",
                    "—",
                ],
                ["去向分歧（`routing_mismatch`）", routing.routing, "—"],
                ["结构错位（`block_structure_mismatch`）", routing.structure, "—"],
                ["分表去向一致率", _pct(routing.rate), _ci(routing.interval)],
            ],
        )
        lines += [
            "",
            f"分母是全部块决定（{routing.blocks} 个，来自 {routing.papers} 篇论文）；"
            "去向一致率 = 1 − 去向分歧 / 块决定总数。结构错位与去向分歧分开计数：",
            "前者是块对不上，后者是同一块被放进不同工作表或追踪页。",
        ]
    lines.append("")

    lines += ["## 5. 未执行 / 未报告项", ""]
    lines += [
        "- **Krippendorff's α**：本阶段未实现，报告不给出该数值（PROTOCOL §6 的目标指标之一）。",
        "- **CCC（一致性相关系数）**：同上，未实现；连续字段目前只报 ICC(2,1) 与容差内一致率。",
        "- **一致样本审计（共享错误率）**：见《错误分析》；本报告不重复，也不用一致率代替。",
        "- **双方均缺失的单元格**：Stage 6 只计数，不进入本报告的分母。",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 错误分析报告
# --------------------------------------------------------------------------- #


def _render_errors(
    *,
    root: Path,
    batch: str,
    run_id: str,
    created_at: str,
    errors: ErrorStats,
    audit: AuditStats | None,
    sources: Sequence[SourceFile],
    audit_rate: float | None,
    audit_min_cells: int | None,
) -> str:
    lines = _header(
        title="错误分析报告", batch=batch, run_id=run_id, created_at=created_at
    )
    lines += [
        "> 错误归因来自裁决记录的 `error_types`（E01–E12，见 `docs/ERROR_TAXONOMY.md`）。",
        "> 一致率不等于正确率：双方一致的单元格仍可能同时错误，因此共享错误率只能由",
        "> 一致样本审计估计，不能用 κ 或百分比一致率替代。",
        "",
    ]

    lines += ["## 1. 数据来源", ""]
    lines += _source_table(root, sources)
    lines.append("")
    if errors.executed:
        lines.append(
            f"裁决记录 {errors.records} 条，其中 {errors.attributed} 条带错误归因"
            f"（{_pct(errors.attributed / errors.records)}）；无法解析的行 {errors.bad_lines} 条。"
        )
    else:
        if errors.records:
            lines.append(
                f"**{NOT_EXECUTED}**：裁决记录 {errors.records} 条，但没有任何一条带 "
                "`error_types` 归因，错误类型分布、按字段分层与按文章特征分层均无法计算。"
            )
        else:
            lines.append(
                f"**{NOT_EXECUTED}**：没有读到裁决记录，"
                "错误类型分布、按字段分层与按文章特征分层均无法计算。"
            )
        lines.append("")
        lines.append(
            "补齐路径：完成 Stage 6 第三方裁决后，把裁决输出写成 "
            f"`data/05_adjudication/{batch}_adjudication.jsonl`（每行一个 JSON，"
            "含 `field`、`error_types` 与 `verdict`），再重跑 "
            f"`meta-coding report --batch {batch}`。"
        )
    lines.append("")

    lines += ["## 2. 错误类型分布（E01–E12）", ""]
    rows: list[list[Any]] = []
    total_codes = 0
    for code, name, severity in ERROR_CODES:
        counters = errors.by_code.get(code, {"coder_1": 0, "coder_2": 0})
        coder_1 = counters.get("coder_1", 0)
        coder_2 = counters.get("coder_2", 0)
        total = coder_1 + coder_2 + counters.get("audited", 0)
        total_codes += total
        fields_hit = sum(1 for counter in errors.by_field.values() if counter.get(code))
        if errors.executed:
            rows.append([code, name, severity, coder_1, coder_2, total, fields_hit])
        else:
            rows.append([code, name, severity, "—", "—", "—", "—"])
    lines += _table(["代码", "名称", "严重度", "编码员 A", "编码员 B", "合计", "涉及字段"], rows)
    if errors.executed:
        lines += [
            "",
            f"共 {total_codes} 个错误代码，来自 {errors.attributed} 条可归因记录；"
            "同一争议项可对应多个代码，因此代码数不等于争议项数。",
        ]
    else:
        lines += ["", f"**{NOT_EXECUTED}**：上表计数为空，不代表“没有错误”，只代表没有审计数据。"]
    lines.append("")

    lines += ["## 3. 按字段分层", ""]
    if errors.executed:
        field_rows = [
            [
                f"`{field_name}`",
                sum(counter.values()),
                "、".join(
                    f"{code}×{count}"
                    for code, count in counter.most_common(3)
                ),
            ]
            for field_name, counter in sorted(
                errors.by_field.items(), key=lambda item: -sum(item[1].values())
            )
        ]
        lines += _table(["字段", "错误代码数", "主要代码"], field_rows)
    else:
        lines.append(f"{NOT_EXECUTED}：无 `error_types` 归因，无法按字段分层。")
    lines.append("")

    lines += ["## 4. 按文章特征分层", ""]
    if errors.executed and errors.strata:
        strata_rows = [
            [item.dimension, item.label, item.records, item.codes]
            for item in errors.strata
        ]
        lines += _table(["维度", "分层", "有归因记录", "错误代码数"], strata_rows)
        lines.append("")
        lines.append(
            "文章特征取自 Stage 7 共识工作簿（发表年份按 ≤2010 / 2011–2015 / 2016–2020 / "
            "≥2021 分桶；国家与样本类型沿用工作簿取值）。无法通过 `paper_key` 关联到"
            "共识论文的记录统一归入「无法关联论文」，不丢弃、不猜测。"
        )
    elif errors.executed:
        lines.append(f"{NOT_EXECUTED}：没有可用的文章特征（缺共识工作簿或无法 join）。")
    else:
        lines.append(f"{NOT_EXECUTED}：无错误归因数据。")
    lines.append("")

    lines += ["## 5. 共享错误率（一致样本审计）", ""]
    if audit is not None and audit.records:
        lines += _table(
            ["指标", "值"],
            [
                ["审计单元格数", audit.records],
                ["双方均判错（`verdict=both_wrong`）", audit.both_wrong],
                ["共享错误率", _pct(audit.rate)],
            ],
        )
        lines += [
            "",
            f"来源：{_rel(root, audit.sources[0]) if audit.sources else '—'}。"
            "共享错误率只覆盖被审计的一致单元格，不能外推到全部一致格。",
        ]
    else:
        plan = []
        if audit_rate is not None:
            plan.append(f"抽样比例 {_pct(audit_rate)}")
        if audit_min_cells is not None:
            plan.append(f"下限 {audit_min_cells} 个一致单元格")
        plan_text = f"计划抽样：{'、'.join(plan)}。" if plan else ""
        lines += [
            f"**{NOT_EXECUTED}**。{plan_text}",
            "",
            "共享错误率 = 审计中 `both_wrong` 的单元格数 / 审计单元格数。该审计需要对"
            "“双方一致”的单元格重新独立判读，仅裁分歧会系统性低估错误率（PROTOCOL §5）。"
            "在当前数据下本报告不给出任何估计值。",
        ]
    lines.append("")

    lines += ["## 6. 口径说明", ""]
    lines += [
        "- 错误率分母是「该编码员在该字段上有裁决或审计覆盖的单元格数」，不是总单元格数；"
        "未覆盖的格不假设正确（`docs/ERROR_TAXONOMY.md` 归因规则 4）。",
        "- E11（仅格式差异）与 E12（原文歧义）不计入编码员错误率，应分别报告为"
        "“书写差异”与“原文歧义”（归因规则 1）。",
        "- 同一字段在多篇文章重复出现同一代码时，属于系统性错误，应单列而不是摊入平均错误率"
        "（归因规则 3）；本报告的按字段分层即为该口径的入口。",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 方法学段落
# --------------------------------------------------------------------------- #


def _render_methods(
    *,
    batch: str,
    run_id: str,
    created_at: str,
    papers: int,
    blocks: int,
    cells: Sequence[Cell],
    agreements: Sequence[FieldAgreement],
    routing: RoutingSummary | None,
    config: Config | None,
    bootstrap_b: int,
    seed: int,
) -> tuple[str, Placeholders]:
    placeholders = Placeholders()
    models = config.models if config is not None else None
    slot_a = models.coder_a if models is not None else None
    slot_b = models.coder_b if models is not None else None
    slot_adj = models.adjudicator if models is not None else None
    pipeline = config.pipeline if config is not None else None

    label_a = _model_label(
        slot_a, placeholders, "编码员 A 的模型与版本", "configs/models.yaml → coder_a"
    )
    label_b = _model_label(
        slot_b, placeholders, "编码员 B 的模型与版本", "configs/models.yaml → coder_b"
    )
    label_adj = _model_label(
        slot_adj, placeholders, "裁决模型与版本", "configs/models.yaml → adjudicator"
    )
    temp_a = _setting(
        slot_a.temperature if slot_a is not None else None,
        placeholders,
        "解码温度",
        "configs/models.yaml 三个槽位的 temperature",
    )
    seed_a = _setting(
        pipeline.random_seed_a if pipeline is not None else None,
        placeholders,
        "编码员 A 的文章打乱种子",
        "configs/pipeline.yaml → random_seed_a",
    )
    seed_b = _setting(
        pipeline.random_seed_b if pipeline is not None else None,
        placeholders,
        "编码员 B 的文章打乱种子",
        "configs/pipeline.yaml → random_seed_b",
    )
    start_date = placeholders.token("编码起止日期", "编码运行记录（data/03_coded/run_log.jsonl）")
    reviewer = placeholders.token("人工复核执行人与日期", "人工复核记录")

    substantive = sum(1 for cell in cells if cell.match_type in SUBSTANTIVE_MATCH_TYPES)
    share = substantive / len(cells) if cells else 0.0
    papers_text = (
        str(papers)
        if papers
        else placeholders.token("论文数", "blocks.csv 缺失，无法折算论文数")
    )
    blocks_text = (
        str(blocks)
        if blocks
        else placeholders.token("编码块数", "blocks.csv 缺失，无法计数")
    )
    routing_text = ""
    if routing is not None:
        routing_text = f"分表去向一致率为 {_pct(routing.rate)}（{routing.blocks} 个块决定）。"

    kappas = [
        item.kappa
        for item in agreements
        if item.kappa is not None and item.kind == KIND_CATEGORICAL
    ]
    icc_values = [item.icc for item in agreements if item.icc is not None]

    lines = _header(
        title="方法学段落", batch=batch, run_id=run_id, created_at=created_at
    )
    lines += ["## 可直接使用的方法学段落", ""]
    lines += [
        "本研究的数据提取采用双模型独立编码、第三方裁决与人工复核三阶段流程。"
        f"编码员 A 与编码员 B 分别由{_inline(label_a)}与{_inline(label_b)}担任，"
        "二者来自不同模型家族，"
        f"解码温度固定为 {temp_a}，使用逐字一致的结构化提示词与同一份空白编码手册。"
        "为消除顺序效应，两位编码员的文章呈现顺序由不同随机种子独立打乱"
        f"（{seed_a} 与 {seed_b}）。编码过程严格盲法：任一编码员的提示词中都不含关于"
        "另一编码员、裁决结果或一致性检验的任何信息，两次编码在互不可见的条件下独立完成，"
        f"编码时间为 {start_date}。",
        "",
        "两名编码员的结果先按「工作表 → 论文身份（DOI 优先）→ 子样本 → 量表」对齐，"
        "再逐格比对；连续字段允许 ±0.01 的数值容差，规范化后仍不相等或一方缺失的单元格"
        "构成实质分歧，交由独立第三方裁决。裁决员由"
        f"{_inline(label_adj)}担任，必须先依据原文独立给出取值与证据，再查看两份编码结果；"
        "无法从原文确定的条目转入人工队列，而不由裁决模型自行决定。最终共识取值的"
        "优先级为人工复核 > 第三方裁决 > 双方一致 > 留空待人工，任何冲突单元格都不会"
        "由程序自行选定取值。",
        "",
        f"本批次共纳入 {papers_text} 篇论文、{blocks_text} 个编码块、{len(cells)} 个比对单元格，"
        f"其中 {substantive} 个单元格出现实质分歧（{_pct(share)}）；这些单元格在共识工作簿中"
        "留空并标记为待人工，不作插补。",
        "",
        "一致性以单元格为分析单位、以论文为重采样单位评估。分类字段报告 Cohen's κ 及其 "
        "95% 置信区间；当 κ 因类别分布高度偏斜而出现悖论（一致率高而 κ 偏低）时，"
        "同时报告 PABAK（prevalence-adjusted bias-adjusted kappa）。有序字段（年龄组）"
        "报告二次加权 κ。连续字段报告 ICC(2,1)（双向随机效应、绝对一致、单评分者）"
        "以及 ±0.01 容差内的百分比一致率；自由文本字段（均值/年龄计算说明等）仅报告"
        "百分比一致率。所有置信区间均由按论文聚类的 bootstrap 得到（B = "
        f"{bootstrap_b}，固定随机种子 {seed}）：每次有放回地抽取论文并整篇带入其全部"
        "单元格，避免把同一论文内的相关单元格当作独立观测。",
        "",
        "人工复核分两部分：一是逐条处理裁决后仍标记为待人工的单元格；二是一致样本审计，"
        "按预设比例对「双方一致」的单元格分层抽样并重新独立判读，用于估计两个模型同时"
        "出错的共享错误率。后者由"
        f"{_inline(reviewer)}执行；未执行时，报告中的共享错误率明确标注为「未执行」，"
        "不以一致性指标代替。",
        "",
        "需要说明的是，κ 与 ICC 刻画的是随机一致性而非正确性：双方一致的单元格仍可能"
        "同时错误，因此一致性指标不能替代裁决与审计得到的错误率。按预先登记的错误分类法，"
        "仅格式差异（E11）与原文歧义（E12）不计入编码员错误率，分别作为书写差异与"
        "原文歧义单独报告。",
        "",
    ]
    if kappas or icc_values:
        lines += [
            "本批次实测："
            + f"分类字段 κ {_range_text(kappas)}；"
            + f"连续字段 ICC(2,1) {_range_text(icc_values)}。"
            + (f"{routing_text}" if routing_text else ""),
            "",
        ]

    lines += ["## 占位符清单", ""]
    lines += _table(["占位符", "填写来源"], placeholders.rows())
    lines += [
        "",
        "投稿前必须替换全部占位符。占位符在正文中统一以「待填：…」的方括号形式出现，"
        "便于全文检索；本清单由生成器自动登记，正文里出现而未登记的占位符会导致 "
        "manifest 自检失败。",
        "",
        "## 使用说明",
        "",
        "- 段落顺序对应论文方法部分的“数据提取—一致性—人工复核”，可按期刊结构调整。",
        "- 文中的论文数、块数、单元格数与分歧数直接来自 Stage 6/7 产物，重跑报告会自动更新。",
        "- 未绑定模型时保留模型占位符，不要手填一个“看起来合理”的模型名。",
        "",
    ]
    return "\n".join(lines), placeholders


# --------------------------------------------------------------------------- #
# 对外接口
# --------------------------------------------------------------------------- #


@dataclass
class ReportResult:
    run_id: str
    created_at: str
    batch: str
    root: Path
    agreement_path: Path
    error_path: Path
    methods_path: Path
    manifest_path: Path
    papers: int
    blocks: int
    cells: int
    fields: int
    disagreements: int
    audit_executed: bool
    placeholders: int
    checks: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> list[str]:
        ok = all(check["ok"] for check in self.checks)
        return [
            f"一致性：{self.fields} 个字段 / {self.cells} 个单元格 / {self.papers} 篇论文",
            f"实质分歧：{self.disagreements}",
            f"一致性报告：{_rel(self.root, self.agreement_path)}",
            f"错误分析：{_rel(self.root, self.error_path)}"
            f"（{'含审计数据' if self.audit_executed else '无审计数据，共享错误率标为未执行'}）",
            f"方法学段落：{_rel(self.root, self.methods_path)}"
            f"（{self.placeholders} 个占位符待填）",
            f"自检：{'全部通过' if ok else '存在失败项'}",
        ]

    def counts(self) -> dict[str, Any]:
        return {
            "papers": self.papers,
            "blocks": self.blocks,
            "cells": self.cells,
            "fields": self.fields,
            "substantive_disagreements": self.disagreements,
            "audit_executed": self.audit_executed,
            "placeholders": self.placeholders,
        }


def _ci_tuple(interval: Interval | None) -> tuple[float, float, int] | None:
    if interval is None:
        return None
    return (round(interval.low, 12), round(interval.high, 12), interval.replicates)


def _bootstrap_reproducible(
    agreements: Sequence[FieldAgreement],
    by_field: dict[str, list[Cell]],
    profiles: dict[str, FieldProfile],
    blocks: dict[str, BlockRow],
    *,
    b: int,
    seed: int,
) -> tuple[bool, str]:
    """同一份数据 + 同一个种子重算一个字段，区间必须逐位相同。"""
    if not agreements:
        return True, "没有字段可核对"
    target = agreements[0]
    profile = profiles.get(target.field_name, FieldProfile(target.field_name, KIND_CATEGORICAL))
    again = _field_agreement(profile, by_field[target.field_name], blocks, b=b, seed=seed)
    same = (
        _ci_tuple(target.kappa_ci) == _ci_tuple(again.kappa_ci)
        and _ci_tuple(target.weighted_ci) == _ci_tuple(again.weighted_ci)
        and _ci_tuple(target.icc_ci) == _ci_tuple(again.icc_ci)
    )
    detail = (
        f"字段 {target.field_name} 以同一种子重算，区间一致"
        if same
        else f"字段 {target.field_name} 重算的区间不一致（不可复现）"
    )
    return same, detail


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def generate_reports(
    root: Path,
    *,
    batch: str,
    config: Config | None = None,
    diff_dir: Path | None = None,
    out_dir: Path | None = None,
    bootstrap_b: int = DEFAULT_BOOTSTRAP_B,
    seed: int = DEFAULT_SEED,
    now: datetime | None = None,
) -> ReportResult:
    """生成三份报告并写 Stage 9 manifest。缺数据时抛 :class:`ReportDataError`。"""
    root = Path(root)
    diff_root = Path(diff_dir) if diff_dir is not None else root / DIFF_REL
    report_root = Path(out_dir) if out_dir is not None else root / REPORTS_REL

    cells_path = diff_root / "diff_cells.csv"
    cells = _load_cells(cells_path, root=root, batch=batch)
    blocks_path = diff_root / "blocks.csv"
    blocks = _load_blocks(blocks_path)
    disagreements_path = diff_root / "disagreements.csv"
    disagreements_rows = _count_rows(disagreements_path)
    profiles = load_field_profiles(root / FIELDS_REL, root=root)

    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    created_at = _stamp(moment)
    substantive = sum(1 for cell in cells if cell.match_type in SUBSTANTIVE_MATCH_TYPES)
    run_id = make_run_id(f"09:{batch}:{len(cells)}:{substantive}", moment)

    by_field: dict[str, list[Cell]] = defaultdict(list)
    for cell in cells:
        by_field[cell.field_name].append(cell)
    agreements = _agreement_table(by_field, profiles, blocks, b=bootstrap_b, seed=seed)
    routing = _routing_summary(blocks, b=bootstrap_b, seed=seed ^ 0x5EED)

    provenance_path = root / CONSENSUS_REL / "provenance.csv"
    provenance = _provenance_counts(provenance_path)
    consensus_path: Path | None
    try:
        consensus_path = find_consensus_workbook(root, batch)
    except FileNotFoundError:
        consensus_path = None

    adjudication_paths = _jsonl_candidates(diff_root, batch, "adjudication")
    audit_paths = _jsonl_candidates(diff_root, batch, "audit")
    adjudication_records, bad_lines = _load_jsonl(adjudication_paths)
    audit_records, _ = _load_jsonl(audit_paths)
    features = _load_paper_features(root, batch)
    errors = _error_stats(
        adjudication_records, features, profiles, adjudication_paths, bad_lines=bad_lines
    )
    audit = _audit_stats(audit_records, audit_paths)

    papers = len({row.paper_key for row in blocks.values() if row.paper_key})
    sources = [
        SourceFile("逐格比对", cells_path, len(cells)),
        SourceFile(
            "实质分歧清单",
            disagreements_path if disagreements_path.is_file() else None,
            disagreements_rows,
            "缺失（不影响一致性指标）",
        ),
        SourceFile(
            "块对齐审计",
            blocks_path if blocks_path.is_file() else None,
            len(blocks) if blocks else None,
            "缺失：聚类退化为按块、去向一致率未执行",
        ),
        SourceFile(
            "共识工作簿",
            consensus_path,
            None,
            "缺失：文章特征分层未执行",
        ),
        SourceFile(
            "取值来源",
            provenance_path if provenance_path.is_file() else None,
            sum(provenance.values()) if provenance else None,
            "缺失：取值来源未执行",
        ),
        SourceFile(
            "裁决记录（错误归因）",
            adjudication_paths[0] if adjudication_paths else None,
            len(adjudication_records) if adjudication_paths else None,
            "缺失：错误分析未执行",
        ),
        SourceFile(
            "一致样本审计",
            audit_paths[0] if audit_paths else None,
            len(audit_records) if audit_paths else None,
            "缺失：共享错误率未执行",
        ),
    ]

    audit_rate = config.pipeline.agreement_audit_rate if config is not None else None
    audit_min = config.pipeline.agreement_audit_min_cells if config is not None else None

    agreement_text = _render_agreement(
        root=root,
        batch=batch,
        run_id=run_id,
        created_at=created_at,
        cells=cells,
        agreements=agreements,
        routing=routing,
        provenance=provenance,
        sources=sources,
        papers=papers,
        blocks=len(blocks),
        bootstrap_b=bootstrap_b,
        seed=seed,
    )
    error_text = _render_errors(
        root=root,
        batch=batch,
        run_id=run_id,
        created_at=created_at,
        errors=errors,
        audit=audit,
        sources=sources,
        audit_rate=audit_rate,
        audit_min_cells=audit_min,
    )
    methods_text, placeholders = _render_methods(
        batch=batch,
        run_id=run_id,
        created_at=created_at,
        papers=papers,
        blocks=len(blocks),
        cells=cells,
        agreements=agreements,
        routing=routing,
        config=config,
        bootstrap_b=bootstrap_b,
        seed=seed,
    )

    report_root.mkdir(parents=True, exist_ok=True)
    agreement_path = report_root / AGREEMENT_NAME
    error_path = report_root / ERROR_NAME
    methods_path = report_root / METHODS_NAME
    for path, text in (
        (agreement_path, agreement_text),
        (error_path, error_text),
        (methods_path, methods_text),
    ):
        path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")

    checks: list[dict[str, Any]] = []
    checks.append(
        {
            "id": "fields_have_metric",
            "ok": len(agreements) == len(by_field),
            "detail": f"{len(agreements)} 个字段有指标，{len(by_field)} 个字段出现在 diff_cells",
        }
    )
    if blocks:
        cluster_keys = {_cluster_key(cell, blocks) for cell in cells}
        paper_like = sum(1 for key in cluster_keys if key.startswith(("doi:", "ref:")))
        checks.append(
            {
                "id": "cluster_unit_is_paper",
                "ok": paper_like == len(cluster_keys),
                "detail": f"{paper_like}/{len(cluster_keys)} 个聚类键来自 blocks.csv 的 paper_key",
            }
        )
    else:
        checks.append(
            {
                "id": "cluster_unit_is_paper",
                "ok": False,
                "detail": "缺 blocks.csv：聚类退化为按块，无法保证同篇论文整篇重抽",
            }
        )
    reproducible, detail = _bootstrap_reproducible(
        agreements, by_field, profiles, blocks, b=bootstrap_b, seed=seed
    )
    checks.append({"id": "bootstrap_reproducible", "ok": reproducible, "detail": detail})
    if disagreements_rows is None or not blocks:
        checks.append(
            {
                "id": "disagreements_row_count",
                "ok": True,
                "detail": "跳过核对（缺 disagreements.csv 或 blocks.csv）",
            }
        )
    else:
        block_findings = sum(1 for row in blocks.values() if row.category)
        expected = substantive + block_findings
        checks.append(
            {
                "id": "disagreements_row_count",
                "ok": expected == disagreements_rows,
                "detail": f"disagreements.csv {disagreements_rows} 行；"
                f"期望 {expected}（实质分歧 {substantive} + 块级发现 {block_findings}）",
            }
        )

    declared = set(placeholders.tokens)
    found = set(PLACEHOLDER_PATTERN.findall(methods_text))
    checks.append(
        {
            "id": "methods_placeholders_listed",
            "ok": found == declared,
            "detail": f"正文 {len(found)} 个占位符，清单 {len(declared)} 个；"
            f"未登记 {sorted(found - declared)}，清单多余 {sorted(declared - found)}",
        }
    )

    headers_ok = True
    for path in (agreement_path, error_path, methods_path):
        head = "\n".join(_read_text(path).splitlines()[:8])
        headers_ok = headers_ok and run_id in head and created_at in head
    checks.append(
        {
            "id": "report_headers_carry_run_id",
            "ok": headers_ok,
            "detail": f"三份报告头部均含 run_id {run_id} 与生成时间 {created_at}",
        }
    )

    if audit is None:
        audit_ok = "共享错误率" in error_text and NOT_EXECUTED in error_text
        audit_detail = "无审计数据：错误分析把共享错误率明确写为未执行"
    else:
        audit_ok = "共享错误率" in error_text and _pct(audit.rate) in error_text
        audit_detail = f"审计 {audit.records} 条，共享错误率 {_pct(audit.rate)} 已写入报告"
    checks.append({"id": "audit_gap_explicit", "ok": audit_ok, "detail": audit_detail})

    manifest = build_manifest(
        root=root,
        stage="09_reports",
        run_id=run_id,
        git_commit=git_head(root),
        rules_hash=config.rules_hash if config is not None else None,
        config_hash=config.config_hash if config is not None else None,
        inputs=[
            path
            for path in (
                cells_path,
                disagreements_path,
                blocks_path,
                consensus_path,
                provenance_path,
                *adjudication_paths,
                *audit_paths,
            )
            if path is not None and path.is_file()
        ],
        outputs=[agreement_path, error_path, methods_path],
        counts={
            "papers": papers,
            "blocks": len(blocks),
            "cells": len(cells),
            "fields": len(agreements),
            "substantive_disagreements": substantive,
            "formatting_only": len(cells) - substantive,
            "bootstrap_b": bootstrap_b,
            "bootstrap_seed": seed,
            "error_records": errors.records,
            "attributed_records": errors.attributed,
            "audit_executed": audit is not None,
            "placeholders": len(placeholders.tokens),
        },
        checks=checks,
        status="complete" if all(check["ok"] for check in checks) else "failed",
        notes=(
            "报告层只读上游产物：一致性按文章 cluster bootstrap（B、种子写入 counts）；"
            "缺裁决/审计数据时错误分析明确写「未执行」，不用一致率代替正确率；"
            "方法学段落只含实测计数与已登记占位符"
        ),
    )
    manifest["created_at"] = created_at
    manifest_path = write_manifest(root, "09_reports", manifest)

    result = ReportResult(
        run_id=run_id,
        created_at=created_at,
        batch=batch,
        root=root,
        agreement_path=agreement_path,
        error_path=error_path,
        methods_path=methods_path,
        manifest_path=manifest_path,
        papers=papers,
        blocks=len(blocks),
        cells=len(cells),
        fields=len(agreements),
        disagreements=substantive,
        audit_executed=audit is not None,
        placeholders=len(placeholders.tokens),
        checks=checks,
    )
    return result


__all__ = [
    "ERROR_CODES",
    "ReportDataError",
    "ReportResult",
    "generate_reports",
    "load_field_profiles",
]
