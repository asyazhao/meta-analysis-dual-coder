"""Stage 9.1 — 一致性指标（纯 Python，无新增依赖）。

统计口径见 ``docs/PROTOCOL.md`` §6：

* 分类字段：Cohen's κ + 95% CI；κ 悖论时同时报 PABAK；
* 有序字段：加权 κ（二次权重）；
* 连续字段：ICC(2,1)（双向随机、绝对一致、单评分者）；
* 全字段：百分比一致率；
* 置信区间：按**文章**做 cluster bootstrap，B = 2000，固定随机种子。

不在依赖里加 scipy / numpy：这些公式本身只有几十行，引一个数值库反而让“可复现”
依赖系统 BLAS 版本。所有重采样都走 ``random.Random(seed)``，同一份数据 + 同一个
种子必然得到同一个区间。

重采样单位是文章而不是单元格：同一篇论文的多行、多块、多个字段不是独立观测，
每次整篇重抽。``cluster_bootstrap_ci`` 的入参就是 ``{文章键: [该文章的全部观测]}``，
调用方负责按文章分组；本模块不猜分组。

缺失值用 ``MISSING_LABEL`` 表示：Cohen's κ 把「一方缺失」当作一个独立类别参与计算，
这是双人编码里对 missing_vs_value 的常规处理；ICC 与加权 κ 则成对删除（见各函数）。
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

MISSING_LABEL = "<missing>"
DEFAULT_BOOTSTRAP_B = 2000
DEFAULT_SEED = 20260101
CI_LEVEL = 0.95
_MISSING_NUMBER = -999.0


# --------------------------------------------------------------------------- #
# 标签规范化
# --------------------------------------------------------------------------- #


def _as_float(text: str) -> float | None:
    try:
        value = float(text.replace(",", "").strip())
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def label_of(value: Any) -> str:
    """单元格值 → 一致性分析用的离散标签。

    规则：空 / ``-999`` → 缺失标签；能解析成数字 → 归一化成 ``433``、``3.4``
    （避免 ``433`` 与 ``433.0`` 被当成两个类别）；其余折叠空白、忽略大小写。
    """
    if value is None:
        return MISSING_LABEL
    if isinstance(value, bool):
        return str(int(value))
    text = str(value).strip()
    if not text:
        return MISSING_LABEL
    number = _as_float(text)
    if number is not None:
        if number == _MISSING_NUMBER:
            return MISSING_LABEL
        return f"{number:g}"
    folded = " ".join(text.split()).casefold()
    return folded.replace("–", "-").replace("—", "-")


def _normalized(pairs: Sequence[tuple[Any, Any]]) -> list[tuple[str, str]]:
    return [(label_of(left), label_of(right)) for left, right in pairs]


# --------------------------------------------------------------------------- #
# 分类字段：Cohen's κ 与 PABAK
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class KappaStats:
    """Cohen's κ 的点估计及其组成。``kappa`` 为 None 表示无定义（全部同类别）。"""

    n: int
    observed: float
    expected: float
    kappa: float | None
    pabak: float

    @property
    def paradox(self) -> bool:
        """κ 悖论：一致率很高，但 κ 被低患病率压到明显偏低。"""
        return self.observed >= 0.8 and (self.kappa is None or self.kappa < 0.6)


def cohen_kappa(pairs: Sequence[tuple[Any, Any]]) -> KappaStats:
    """两名编码员的 Cohen's κ。

    ``Po`` 为观察一致率，``Pe`` 为按边际分布算出的期望一致率；
    ``Pe == 1``（双方都只用一个类别）时 κ 无定义，返回 ``None`` 而不是硬凑 0 或 1。
    """
    observed_pairs = _normalized(pairs)
    n = len(observed_pairs)
    if n == 0:
        raise ValueError("一致性指标需要至少一个单元格")

    matches = sum(1 for left, right in observed_pairs if left == right)
    po = matches / n
    rows: dict[str, int] = {}
    cols: dict[str, int] = {}
    for left, right in observed_pairs:
        rows[left] = rows.get(left, 0) + 1
        cols[right] = cols.get(right, 0) + 1
    pe = sum(count * cols.get(label, 0) for label, count in rows.items()) / (n * n)
    kappa = None if pe >= 1.0 - 1e-12 else (po - pe) / (1.0 - pe)
    return KappaStats(n=n, observed=po, expected=pe, kappa=kappa, pabak=2.0 * po - 1.0)


def pabak(pairs: Sequence[tuple[Any, Any]]) -> float:
    """PABAK = 2·Po − 1；对调 A/B 完全对称。"""
    return 2.0 * percent_agreement(pairs) - 1.0


def percent_agreement(pairs: Sequence[tuple[Any, Any]]) -> float:
    """原始百分比一致率（未校正机遇一致）。"""
    observed_pairs = _normalized(pairs)
    if not observed_pairs:
        raise ValueError("一致性指标需要至少一个单元格")
    return sum(1 for left, right in observed_pairs if left == right) / len(observed_pairs)


# --------------------------------------------------------------------------- #
# 有序字段：二次加权 κ
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WeightedKappaStats:
    """二次加权 κ。``dropped`` 是不在有序量表内的成对（例如一方缺失）。"""

    n: int
    dropped: int
    observed: float
    expected: float
    kappa: float | None


def weighted_kappa(
    pairs: Sequence[tuple[Any, Any]], order: Sequence[Any]
) -> WeightedKappaStats:
    """有序字段的二次加权 κ。

    权重 ``w_ij = (i - j)² / (K - 1)²``：相邻等级差 1 的惩罚是最大间距的
    ``1/(K-1)²``，因此“差一级”远比“差三级”轻。``order`` 由小到大的等级序列。
    """
    levels = [label_of(item) for item in order]
    if len(levels) < 2:
        raise ValueError("加权 κ 至少需要两个等级")
    index = {label: position for position, label in enumerate(levels)}
    normalized = _normalized(pairs)
    kept = [(left, right) for left, right in normalized if left in index and right in index]
    dropped = len(normalized) - len(kept)
    n = len(kept)
    if n == 0:
        return WeightedKappaStats(n=0, dropped=dropped, observed=0.0, expected=0.0, kappa=None)

    k = len(levels)
    scale = float((k - 1) ** 2)
    rows = [0] * k
    cols = [0] * k
    observed_disagreement = 0.0
    for left, right in kept:
        i, j = index[left], index[right]
        observed_disagreement += ((i - j) ** 2) / scale
        rows[i] += 1
        cols[j] += 1
    expected_disagreement = (
        sum(
            rows[i] * cols[j] * ((i - j) ** 2) / scale
            for i in range(k)
            for j in range(k)
        )
        / (n * n)
    )
    po = 1.0 - observed_disagreement / n
    pe = 1.0 - expected_disagreement
    kappa = None if pe >= 1.0 - 1e-12 else (po - pe) / (1.0 - pe)
    return WeightedKappaStats(n=n, dropped=dropped, observed=po, expected=pe, kappa=kappa)


# --------------------------------------------------------------------------- #
# 连续字段：ICC(2,1)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ICCStats:
    """双向随机效应、绝对一致、单评分者 ICC(2,1) 及其均方分解。"""

    subjects: int
    raters: int
    icc: float | None
    ms_between: float
    ms_raters: float
    ms_error: float


def icc_2_1_matrix(rows: Sequence[Sequence[float]]) -> ICCStats:
    """按「行 = 对象（单元格）、列 = 评分者」的矩阵计算 ICC(2,1)。

    ``ICC(2,1) = (MSR − MSE) / (MSR + (k−1)·MSE + k·(MSC − MSE)/n)``

    * MSR：对象间均方（真正的变异来源）；
    * MSC：评分者间均方（系统偏差，绝对一致口径必须留在分母里）；
    * MSE：残差均方。

    完全一致时 MSE = 0、MSC = MSE = 0，公式退化为 MSR/MSR = 1。ICC 可以为负
    （评分者系统偏差大于对象间差异），不截断到 0，只在数值误差范围内截到 1。
    """
    n = len(rows)
    if n < 2:
        return ICCStats(subjects=n, raters=len(rows[0]) if rows else 0, icc=None,
                        ms_between=0.0, ms_raters=0.0, ms_error=0.0)
    k = len(rows[0])
    if k < 2 or any(len(row) != k for row in rows):
        raise ValueError("ICC(2,1) 需要每行评分者数相同的矩形数据，且至少两名评分者")

    grand = sum(sum(row) for row in rows) / (n * k)
    ss_total = sum((value - grand) ** 2 for row in rows for value in row)
    row_means = [sum(row) / k for row in rows]
    col_means = [sum(rows[i][j] for i in range(n)) / n for j in range(k)]
    ss_rows = k * sum((mean - grand) ** 2 for mean in row_means)
    ss_cols = n * sum((mean - grand) ** 2 for mean in col_means)
    ss_error = ss_total - ss_rows - ss_cols
    if -1e-9 < ss_error < 0.0:  # 浮点残差，不当作负方差
        ss_error = 0.0

    ms_rows = ss_rows / (n - 1)
    ms_cols = ss_cols / (k - 1)
    ms_error = ss_error / ((n - 1) * (k - 1))
    denominator = ms_rows + (k - 1) * ms_error + k * (ms_cols - ms_error) / n
    if abs(denominator) < 1e-12:
        icc = None
    else:
        icc = min((ms_rows - ms_error) / denominator, 1.0)
    return ICCStats(subjects=n, raters=k, icc=icc, ms_between=ms_rows,
                    ms_raters=ms_cols, ms_error=ms_error)


def icc_2_1(pairs: Sequence[tuple[float | None, float | None]]) -> ICCStats:
    """两名评分者的 ICC(2,1)；任一方缺失的单元格成对删除（不插补）。"""
    rows = [
        [float(left), float(right)]
        for left, right in pairs
        if left is not None and right is not None
    ]
    if len(rows) < 2:
        return ICCStats(subjects=len(rows), raters=2, icc=None,
                        ms_between=0.0, ms_raters=0.0, ms_error=0.0)
    return icc_2_1_matrix(rows)


# --------------------------------------------------------------------------- #
# Cluster bootstrap 置信区间
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Interval:
    """置信区间。``replicates`` 为有效重采样次数（统计量无定义时会被跳过）。"""

    low: float
    high: float
    level: float = CI_LEVEL
    replicates: int = 0

    @property
    def width(self) -> float:
        return self.high - self.low


def _quantile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def cluster_bootstrap_ci(
    clusters: Mapping[Hashable, Sequence[Any]],
    statistic: Callable[[Sequence[Any]], float | None],
    *,
    b: int = DEFAULT_BOOTSTRAP_B,
    seed: int = DEFAULT_SEED,
    level: float = CI_LEVEL,
) -> Interval | None:
    """按文章聚类的 percentile bootstrap 置信区间。

    ``clusters`` 必须是 ``{文章键: [该文章的全部观测]}``；每次有放回地抽文章，
    整篇观测一起进入重采样样本，同一文章的单元格不会被拆散。

    区间端点会被夹到包含点估计的位置：percentile bootstrap 偶尔给出不包含点估计
    的区间，报告里那样写会让读者以为点估计落在区间外。夹紧是明确的口径，不隐藏。
    """
    keys = sorted(clusters, key=str)
    pooled = [item for key in keys for item in clusters[key]]
    point = statistic(pooled)
    if point is None or not math.isfinite(point):
        return None
    if len(keys) < 2 or b < 2:
        return Interval(low=point, high=point, level=level, replicates=0)

    rng = random.Random(seed)
    size = len(keys)
    replicates: list[float] = []
    for _ in range(b):
        sample: list[Any] = []
        for _ in range(size):
            sample.extend(clusters[keys[rng.randrange(size)]])
        value = statistic(sample)
        if value is not None and math.isfinite(value):
            replicates.append(value)
    if len(replicates) < 2:
        return Interval(low=point, high=point, level=level, replicates=len(replicates))

    alpha = (1.0 - level) / 2.0
    low = _quantile(replicates, alpha)
    high = _quantile(replicates, 1.0 - alpha)
    return Interval(
        low=min(low, point),
        high=max(high, point),
        level=level,
        replicates=len(replicates),
    )


__all__ = [
    "CI_LEVEL",
    "DEFAULT_BOOTSTRAP_B",
    "DEFAULT_SEED",
    "ICCStats",
    "Interval",
    "KappaStats",
    "MISSING_LABEL",
    "WeightedKappaStats",
    "cluster_bootstrap_ci",
    "cohen_kappa",
    "icc_2_1",
    "icc_2_1_matrix",
    "label_of",
    "pabak",
    "percent_agreement",
    "weighted_kappa",
]
