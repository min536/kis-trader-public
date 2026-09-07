"""Pure portfolio analytics over closed-trade records (W3).

This module is intentionally **pure and offline**: it takes already-normalized
closed-trade records (one round trip per record) and produces realized-PnL
attribution, win rate, holding-time distribution, and turnover. It performs
**no I/O and no broker calls**. Equity-curve / MDD / Sharpe live in
``app/reporting/performance.py`` and are deliberately *not* duplicated here.

Win/loss is attributed on ``net_pnl_krw`` (cost-aware): ``> 0`` win, ``< 0`` loss,
``== 0`` breakeven. ``win_rate_pct`` uses ``trade_count`` as the denominator.

Defensive contract (so a messy record can never abort the report or poison the
JSON artifact consumed downstream by W7):

- Non-``Mapping`` records are skipped.
- Every numeric field is coerced to a finite number; unparseable/non-finite/bool
  values become ``0``/``0.0``. Exact integers are preserved (no float round-trip),
  and a derived average is re-coerced so an overflowing sum cannot leak ``inf``.
- A record missing ``symbol`` / ``sell_trigger`` falls back to the ``UNKNOWN`` /
  ``unknown`` bucket. (Korea symbols are 6-digit codes, so this never collides
  with a real symbol; if it ever did, the sums-back invariants still hold.)
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping

UNKNOWN_SYMBOL = "UNKNOWN"
UNKNOWN_TRIGGER = "unknown"


@dataclass(frozen=True)
class GroupMetrics:
    trade_count: int
    win_count: int
    loss_count: int
    breakeven_count: int
    win_rate_pct: float
    buy_notional_krw: int
    sell_notional_krw: int
    turnover_krw: int
    gross_pnl_krw: int
    net_pnl_krw: int
    avg_hold_days: float
    min_hold_days: float
    max_hold_days: float
    hold_days_p50: float
    hold_days_p90: float


@dataclass(frozen=True)
class PortfolioAnalytics:
    total: GroupMetrics
    by_symbol: dict[str, GroupMetrics]
    by_trigger: dict[str, GroupMetrics]
    by_symbol_trigger: dict[str, dict[str, GroupMetrics]]

    def to_dict(self) -> dict[str, Any]:
        """Render to a JSON-serializable nested dict (for W7 EOD/Slack/dashboard)."""
        return {
            "total": asdict(self.total),
            "by_symbol": {
                symbol: asdict(metrics) for symbol, metrics in self.by_symbol.items()
            },
            "by_trigger": {
                trigger: asdict(metrics)
                for trigger, metrics in self.by_trigger.items()
            },
            "by_symbol_trigger": {
                symbol: {
                    trigger: asdict(metrics) for trigger, metrics in triggers.items()
                }
                for symbol, triggers in self.by_symbol_trigger.items()
            },
        }


def _to_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value  # preserve exact magnitude (avoid float-precision loss)
    try:
        return int(round(float(value)))
    except Exception:
        return 0


def _to_float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        result = float(value)
    except Exception:
        return 0.0
    if not math.isfinite(result):
        return 0.0
    return result


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Linear-interpolated percentile (numpy default 'linear' method)."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return sorted_values[int(rank)]
    frac = rank - low
    return sorted_values[low] * (1.0 - frac) + sorted_values[high] * frac


def _aggregate(records: list[Mapping[str, Any]]) -> GroupMetrics:
    trade_count = len(records)
    win_count = 0
    loss_count = 0
    breakeven_count = 0
    buy_notional = 0
    sell_notional = 0
    gross_pnl = 0
    net_pnl = 0
    hold_days: list[float] = []

    for record in records:
        net = _to_int(record.get("net_pnl_krw"))
        if net > 0:
            win_count += 1
        elif net < 0:
            loss_count += 1
        else:
            breakeven_count += 1
        buy_notional += _to_int(record.get("buy_notional_krw"))
        sell_notional += _to_int(record.get("sell_notional_krw"))
        gross_pnl += _to_int(record.get("gross_pnl_krw"))
        net_pnl += net
        hold_days.append(_to_float(record.get("hold_days")))

    win_rate_pct = (win_count / trade_count * 100.0) if trade_count else 0.0
    # Re-coerce the derived average: each input is finite, but their sum can
    # overflow to inf, which would otherwise leak into to_dict()/json.dumps.
    avg_hold_days = _to_float(sum(hold_days) / trade_count) if trade_count else 0.0
    sorted_holds = sorted(hold_days)

    return GroupMetrics(
        trade_count=trade_count,
        win_count=win_count,
        loss_count=loss_count,
        breakeven_count=breakeven_count,
        win_rate_pct=win_rate_pct,
        buy_notional_krw=buy_notional,
        sell_notional_krw=sell_notional,
        turnover_krw=buy_notional + sell_notional,
        gross_pnl_krw=gross_pnl,
        net_pnl_krw=net_pnl,
        avg_hold_days=avg_hold_days,
        min_hold_days=sorted_holds[0] if sorted_holds else 0.0,
        max_hold_days=sorted_holds[-1] if sorted_holds else 0.0,
        hold_days_p50=_percentile(sorted_holds, 50.0),
        hold_days_p90=_percentile(sorted_holds, 90.0),
    )


def _norm_key(value: Any, default: str) -> str:
    text = str(value if value is not None else "").strip()
    return text or default


def _group_by(
    records: list[Mapping[str, Any]], field: str, default: str
) -> dict[str, GroupMetrics]:
    buckets: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        key = _norm_key(record.get(field), default)
        buckets.setdefault(key, []).append(record)
    return {key: _aggregate(buckets[key]) for key in sorted(buckets)}


def _matrix_by_symbol_trigger(
    records: list[Mapping[str, Any]],
) -> dict[str, dict[str, GroupMetrics]]:
    rows_by_symbol: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        symbol = _norm_key(record.get("symbol"), UNKNOWN_SYMBOL)
        rows_by_symbol.setdefault(symbol, []).append(record)
    return {
        symbol: _group_by(rows_by_symbol[symbol], "sell_trigger", UNKNOWN_TRIGGER)
        for symbol in sorted(rows_by_symbol)
    }


def compute_portfolio_analytics(
    records: Iterable[Mapping[str, Any]],
) -> PortfolioAnalytics:
    record_list = [record for record in records if isinstance(record, Mapping)]
    return PortfolioAnalytics(
        total=_aggregate(record_list),
        by_symbol=_group_by(record_list, "symbol", UNKNOWN_SYMBOL),
        by_trigger=_group_by(record_list, "sell_trigger", UNKNOWN_TRIGGER),
        by_symbol_trigger=_matrix_by_symbol_trigger(record_list),
    )
