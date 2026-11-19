"""Stage 9 — 一致性指标与报告渲染。

* :mod:`meta_coding.report.metrics` —— κ / PABAK / 加权 κ / ICC(2,1) / cluster bootstrap；
* :mod:`meta_coding.report.render` —— 三份报告与 Stage 9 manifest。

本包只读上游产物，不调用任何模型 API，也不修改工作簿。
"""

from .render import ReportDataError, ReportResult, generate_reports

__all__ = ["ReportDataError", "ReportResult", "generate_reports"]
