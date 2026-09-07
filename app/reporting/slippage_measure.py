"""Build a ``SlippageReportSummary`` from order-log records (W4).

Pure + offline aggregation: it reads already-loaded order-log records (filled
buy/sell events carrying the W2 price-capture fields) and produces the per-side
slippage distribution consumed by
[`format_slippage_report`](slippage_report.py). It performs **no I/O and no broker
calls** — the bounded order-log read lives in the CLI
(``app/tools/measure_slippage.py``).

## Scope today: SUBMIT-SIDE slippage only

Slippage is measured as ``(execution_price - reference_price) / reference_price``
in bps, sign-adjusted so an **adverse** fill is **positive** (buy filled above
reference, or sell filled below reference) and directly comparable to the
configured slippage policy bps.

`execution_price` is taken from ``quote_at_submit`` and `reference_price` from
``reference_price_krw`` — both W2 fields. **Today these are equal** (no re-quote is
fetched between decision and submit, and there is no executed/fill price in the
order-cash ack), so this measures the *submit-side* gap, which is `0` until a
re-quote or a real fill source lands. The infra is correct and ready; the real
slippage **numbers** need live-shadow / a fill source — an **operator gate**
(see W2 notes + the W4 plan). `missing_reference_count` measures coverage: filled
orders that do not carry a usable reference (e.g. pre-W2 records).
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


def _slippage_bps(side: str, reference: float, execution: float) -> float:
    raw = (execution - reference) / reference * 10_000.0
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


def build_slippage_report_summary(
    records: Iterable[Mapping[str, Any]],
    *,
    report_date: str,
    policy_buy_bps: float,
    policy_sell_bps: float,
) -> SlippageReportSummary:
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
        execution = _coerce_price(raw_map.get("quote_at_submit"))
        if reference is None or execution is None:
            missing[side] += 1
            continue
        samples[side].append(_slippage_bps(side, reference, execution))

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
