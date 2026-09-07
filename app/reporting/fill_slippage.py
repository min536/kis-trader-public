"""Build a fill-side ``SlippageReportSummary`` from order-log records (BP-10 / E7).

Pure + offline aggregation, mirroring the submit-side path in
[`slippage_measure.py`](slippage_measure.py). It reads already-loaded order-log
records and produces the per-side slippage distribution — but instead of the
submit-side ``quote_at_submit`` it uses the **EOD-reconciled fill price**
(``fill_price_krw``). It performs **no I/O and no broker calls**; the bounded
order-log read and the broker fill inquiry are an operator gate (BP-10 S2).

## Scope: FILL-SIDE slippage

Slippage is ``(fill_price - reference_price) / reference_price`` in bps,
sign-adjusted so an **adverse** fill is **positive** (buy filled above reference,
sell filled below reference) — the **same convention** as the submit-side module,
so the two halves are directly comparable. ``missing_reference_count`` measures
coverage: filled orders lacking a usable reference or fill price (e.g. records
produced before EOD reconciliation attached ``fill_price_krw``).
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from app.reporting.slippage_report import SlippageReportSummary, SlippageSideSummary

_BUY_ACTION = "order_succeeded"
_SELL_ACTION = "sell_order_succeeded"


def _coerce_price(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except Exception:
        return None
    if not math.isfinite(result) or result <= 0.0:
        return None
    return result


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Linear-interpolated percentile (numpy default 'linear' method)."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return sorted_values[int(rank)]
    frac = rank - low
    return sorted_values[low] * (1.0 - frac) + sorted_values[high] * frac


def _slippage_bps(side: str, reference: float, fill: float) -> float:
    raw = (fill - reference) / reference * 10_000.0
    # adverse = positive: buy filled above reference, sell filled below reference.
    # ``+ 0.0`` normalizes -0.0 -> 0.0 so an on-reference fill never renders "-0.00bps".
    return (raw if side == "buy" else -raw) + 0.0


def _summarize_side(samples: list[float], missing_reference_count: int) -> SlippageSideSummary:
    if not samples:
        return SlippageSideSummary(
            count=0,
            missing_reference_count=missing_reference_count,
        )
    ordered = sorted(samples)
    return SlippageSideSummary(
        count=len(samples),
        mean_bps=sum(samples) / len(samples),
        p50_bps=_percentile(ordered, 50.0),
        p75_bps=_percentile(ordered, 75.0),
        max_bps=ordered[-1],
        missing_reference_count=missing_reference_count,
    )


def build_fill_slippage_summary(
    records: Iterable[Mapping[str, Any]],
    *,
    report_date: str,
    policy_buy_bps: float,
    policy_sell_bps: float,
) -> SlippageReportSummary:
    """Aggregate fill-side slippage from order-log records.

    ``records`` may contain hostile entries (non-Mapping, missing keys, inf/NaN,
    non-positive prices); those degrade to ``missing_reference_count`` rather than
    raising, matching the submit-side contract.
    """
    samples: dict[str, list[float]] = {"buy": [], "sell": []}
    missing: dict[str, int] = {"buy": 0, "sell": 0}

    for record in records:
        if not isinstance(record, Mapping):
            continue
        action = str(record.get("action", "")).strip()
        if action == _BUY_ACTION:
            side = "buy"
        elif action == _SELL_ACTION:
            side = "sell"
        else:
            continue

        raw = record.get("raw_response")
        raw_map = raw if isinstance(raw, Mapping) else {}
        reference = _coerce_price(raw_map.get("reference_price_krw"))
        fill = _coerce_price(raw_map.get("fill_price_krw"))
        if reference is None or fill is None:
            missing[side] += 1
            continue
        samples[side].append(_slippage_bps(side, reference, fill))

    buy = _summarize_side(samples["buy"], missing["buy"])
    sell = _summarize_side(samples["sell"], missing["sell"])
    return SlippageReportSummary(
        report_date=report_date,
        sample_count=buy.count + sell.count,
        buy=buy,
        sell=sell,
        policy_buy_bps=float(policy_buy_bps),
        policy_sell_bps=float(policy_sell_bps),
    )
