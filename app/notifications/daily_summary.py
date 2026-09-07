from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


DAILY_SUMMARY_EVENT_TYPE = "daily_summary"


@dataclass
class DailySummaryMetrics:
    date_str: str = ""
    session_status: str = "ENDED"
    buy_submitted: int = 0
    sell_submitted: int = 0
    order_accepted: int = 0
    order_rejected: int = 0
    bottlenecks: dict[str, int] = field(default_factory=dict)
    exception_count: int = 0
    start_positions: int | None = None
    end_positions: int | None = None
    realized_pnl_krw: float | None = None
    unrealized_pnl_krw: float | None = None


def _format_pnl(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{int(round(value)):+,}"


def _format_bottlenecks(bottlenecks: Mapping[str, int]) -> str:
    items = [(k, int(v)) for k, v in bottlenecks.items() if int(v) > 0]
    if not items:
        return "none"
    items.sort(key=lambda kv: kv[1], reverse=True)
    return ", ".join(f"{k}={v}" for k, v in items[:6])


def build_conclusion(
    metrics: DailySummaryMetrics,
    *,
    rate_limit_high_threshold: int = 5,
) -> str:
    if metrics.exception_count > 0:
        return (
            "Exceptions occurred during the session — inspect operational logs."
        )
    if metrics.order_rejected > 0:
        return (
            "Rejected orders occurred — check orderability and the KIS API "
            "response chain."
        )
    rate_limit_count = int(metrics.bottlenecks.get("KIS_RATE_LIMIT_BACKOFF", 0))
    if rate_limit_count >= rate_limit_high_threshold:
        return (
            "Elevated KIS rate-limit backoffs — review API pressure and "
            "request cadence."
        )
    return "Session ended without major operational anomalies."


def build_daily_summary_text(
    metrics: DailySummaryMetrics,
    *,
    rate_limit_high_threshold: int = 5,
) -> str:
    lines: list[str] = []
    header_parts = ["[daily_summary]"]
    if metrics.date_str:
        header_parts.append(metrics.date_str)
    header_parts.append(f"status={metrics.session_status}")
    lines.append(" ".join(header_parts))

    lines.append(
        f"orders submitted: BUY={metrics.buy_submitted} "
        f"SELL={metrics.sell_submitted} "
        f"accepted={metrics.order_accepted} rejected={metrics.order_rejected}"
    )
    lines.append(
        f"bottlenecks: {_format_bottlenecks(metrics.bottlenecks)} "
        f"| exceptions={metrics.exception_count}"
    )

    if metrics.start_positions is not None or metrics.end_positions is not None:
        lines.append(
            f"positions: start={metrics.start_positions if metrics.start_positions is not None else '-'} "
            f"end={metrics.end_positions if metrics.end_positions is not None else '-'}"
        )

    pnl_parts: list[str] = []
    realized = _format_pnl(metrics.realized_pnl_krw)
    unrealized = _format_pnl(metrics.unrealized_pnl_krw)
    if realized is not None:
        pnl_parts.append(f"realized={realized}")
    if unrealized is not None:
        pnl_parts.append(f"unrealized={unrealized}")
    if pnl_parts:
        lines.append("pnl(krw): " + " ".join(pnl_parts))

    lines.append(
        "conclusion: "
        + build_conclusion(
            metrics, rate_limit_high_threshold=rate_limit_high_threshold
        )
    )
    return "\n".join(lines)
