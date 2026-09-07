"""Capital-scale reporting for absolute KRW risk caps.

This module is intentionally pure: it reads an already-built settings object and
portfolio snapshot, then returns operator-facing rows. It never calls broker APIs
and never reads credential files.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from app.core.formatters import format_krw


@dataclass(frozen=True)
class CapitalScaleBand:
    setting_name: str
    attr_name: str
    label: str
    min_pct: float | None
    max_pct: float | None


@dataclass(frozen=True)
class CapitalScaleRow:
    setting_name: str
    label: str
    cap_krw: int
    ratio_pct: float
    min_pct: float | None
    max_pct: float | None
    status: str
    message: str


@dataclass(frozen=True)
class CapitalScaleReport:
    total_equity_krw: int
    rows: tuple[CapitalScaleRow, ...]
    warnings: tuple[str, ...]
    status: str
    reason: str = ""


CAPITAL_SCALE_BANDS: tuple[CapitalScaleBand, ...] = (
    CapitalScaleBand(
        setting_name="BUY_MAX_BUDGET_PER_TRADE_KRW",
        attr_name="buy_max_budget_per_trade_krw",
        label="건당 매수 상한",
        min_pct=0.5,
        max_pct=3.0,
    ),
    CapitalScaleBand(
        setting_name="BUY_DAILY_MAX_NOTIONAL_KRW",
        attr_name="buy_daily_max_notional_krw",
        label="일일 매수 총액 상한",
        min_pct=0.3,
        max_pct=5.0,
    ),
    CapitalScaleBand(
        setting_name="SELL_DAILY_MAX_NOTIONAL_KRW",
        attr_name="sell_daily_max_notional_krw",
        label="일일 매도 총액 상한",
        min_pct=None,
        max_pct=30.0,
    ),
)


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        try:
            return int(float(value or 0))
        except (TypeError, ValueError):
            return 0


def _format_band(row: CapitalScaleRow) -> str:
    if row.min_pct is None and row.max_pct is None:
        return "-"
    if row.min_pct is None:
        return f"<= {row.max_pct:.1f}%"
    if row.max_pct is None:
        return f">= {row.min_pct:.1f}%"
    return f"{row.min_pct:.1f}~{row.max_pct:.1f}%"


def _row_status(*, ratio_pct: float, band: CapitalScaleBand) -> tuple[str, str]:
    if band.min_pct is not None and ratio_pct < band.min_pct:
        return "low", "권장 하한 미만"
    if band.max_pct is not None and ratio_pct > band.max_pct:
        return "high", "권장 상한 초과"
    return "ok", "권장 범위"


def build_capital_scale_report(*, settings: Any, portfolio_snapshot: Any) -> CapitalScaleReport:
    """Compare absolute KRW caps to the current account equity.

    A non-positive ``total_evaluation_amount`` is treated as unjudged; the caller
    can retry on the next usable snapshot.
    """

    total_equity_krw = _int_value(
        getattr(portfolio_snapshot, "total_evaluation_amount", 0)
    )
    if total_equity_krw <= 0:
        return CapitalScaleReport(
            total_equity_krw=total_equity_krw,
            rows=(),
            warnings=(),
            status="unavailable",
            reason="total_evaluation_amount_unavailable",
        )

    rows: list[CapitalScaleRow] = []
    warnings: list[str] = []
    for band in CAPITAL_SCALE_BANDS:
        cap_krw = max(_int_value(getattr(settings, band.attr_name, 0)), 0)
        ratio_pct = (cap_krw / total_equity_krw) * 100.0
        status, message = _row_status(ratio_pct=ratio_pct, band=band)
        row = CapitalScaleRow(
            setting_name=band.setting_name,
            label=band.label,
            cap_krw=cap_krw,
            ratio_pct=ratio_pct,
            min_pct=band.min_pct,
            max_pct=band.max_pct,
            status=status,
            message=message,
        )
        rows.append(row)
        if status != "ok":
            warnings.append(
                "capital scale warning: "
                f"{row.setting_name}={format_krw(row.cap_krw)} "
                f"is {row.ratio_pct:.2f}% of total equity "
                f"(recommended {_format_band(row)}, {row.message})"
            )

    return CapitalScaleReport(
        total_equity_krw=total_equity_krw,
        rows=tuple(rows),
        warnings=tuple(warnings),
        status="warning" if warnings else "ok",
    )


def capital_scale_report_key(*, settings: Any, account_signature: str) -> str:
    """Return a stable key for one account/cap configuration.

    Equity is deliberately excluded so normal mark-to-market movement does not
    re-emit the startup report every cycle.
    """

    parts = [
        str(account_signature or ""),
        str(_int_value(getattr(settings, "buy_max_budget_per_trade_krw", 0))),
        str(_int_value(getattr(settings, "buy_daily_max_notional_krw", 0))),
        str(_int_value(getattr(settings, "sell_daily_max_notional_krw", 0))),
    ]
    return sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def render_capital_scale_report(report: CapitalScaleReport) -> tuple[str, ...]:
    if report.status == "unavailable":
        return ()

    lines = [
        "=== 자본 스케일 점검 ===",
        f"총자산 기준: {format_krw(report.total_equity_krw)}",
    ]
    for row in report.rows:
        prefix = "WARN" if row.status != "ok" else "OK"
        lines.append(
            f"{prefix} {row.setting_name} ({row.label}): "
            f"{format_krw(row.cap_krw)} | {row.ratio_pct:.2f}% | "
            f"권장 {_format_band(row)} | {row.message}"
        )
    lines.append("")
    return tuple(lines)
