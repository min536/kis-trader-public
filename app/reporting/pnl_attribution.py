"""Daily PnL attribution builder + renderer (plan §3 S1).

Decomposes one account-day's PnL into per-symbol realized contributions,
unrealized delta, slippage cost, and regime/brake context. Pure + offline:
builders take already-parsed order-log / cycle-snapshot / runtime-state values
(path resolution is the CLI layer's job — ``app/tools/pnl_attribution_report.py``)
and perform no I/O and no broker calls.

Pairing mirrors the dashboard trade-journal semantics
(``app/dashboard/normalizers.py::_build_trade_journal``) without importing the
dashboard package (reporting must not depend on the dashboard layer): BUY
``order_succeeded`` records queue per symbol; a SELL ``sell_order_succeeded``
consumes the immediately-preceding unconsumed BUY of the same symbol. Recorded
realized-PnL fields (``raw_response.sell_strategy_details.details.net_pnl_krw``,
confirmed in the live order log 2026-07-18) take precedence over pairing.
"""

from __future__ import annotations

from dataclasses import asdict

from app.reporting.fill_slippage import build_fill_slippage_summary

_BUY_ACTION = "order_succeeded"
_SELL_ACTION = "sell_order_succeeded"

# Policy bounds for the adopted fill-slippage summary. Mirrors the
# BUY_SLIPPAGE_BPS / SELL_SLIPPAGE_BPS settings defaults
# (app/auth/settings_fields.py:82-83, default "5.0") — the builder stays a pure
# function of parsed values, so it must not resolve Settings/env itself.
_POLICY_BUY_BPS = 5.0
_POLICY_SELL_BPS = 5.0


def _coerce_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)  # type: ignore[arg-type]
    except Exception:
        return None
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result


def _reference_price(record: dict) -> float | None:
    raw = record.get("raw_response")
    if not isinstance(raw, dict):
        return None
    return _coerce_float(raw.get("reference_price_krw"))


def _record_on_date(record: dict, target_date: str) -> bool:
    timestamp = record.get("timestamp")
    return isinstance(timestamp, str) and timestamp.startswith(target_date)


def _sorted_order_mappings(orders: list[dict]) -> list[dict]:
    records = [record for record in orders if isinstance(record, dict)]
    return sorted(records, key=lambda record: str(record.get("timestamp", "")))


def build_daily_attribution(
    *,
    orders: list[dict],
    cycle_tail: list[dict],
    exit_state: dict,
    target_date: str,
) -> dict:
    """Build the daily attribution report. Never raises — missing or hostile
    input degrades the affected section to ``None``/empty."""
    records = _sorted_order_mappings(orders if isinstance(orders, list) else [])

    realized_by_symbol: dict[str, float] = {}
    trades_closed = 0
    trades_opened = 0
    buy_queue: dict[str, list[dict]] = {}

    for record in records:
        action = record.get("action")
        symbol = str(record.get("symbol") or "").strip()
        if not symbol:
            continue
        if action == _BUY_ACTION:
            buy_queue.setdefault(symbol, []).append(record)
            if _record_on_date(record, target_date):
                trades_opened += 1
        elif action == _SELL_ACTION:
            paired_buy = buy_queue[symbol].pop() if buy_queue.get(symbol) else None
            if not _record_on_date(record, target_date):
                continue
            trades_closed += 1
            realized = _realized_for_sell(record, paired_buy)
            if realized is not None:
                realized_by_symbol[symbol] = realized_by_symbol.get(symbol, 0.0) + realized

    realized_total = sum(realized_by_symbol.values(), 0.0)

    day_cycles = [
        record
        for record in (cycle_tail if isinstance(cycle_tail, list) else [])
        if isinstance(record, dict) and _record_on_date(record, target_date)
    ]
    last_cycle = day_cycles[-1] if day_cycles else None

    return {
        "date": target_date,
        "account_signature": _account_signature(day_cycles) or _account_signature(records),
        "realized_by_symbol": realized_by_symbol,
        "unrealized_delta_krw": _unrealized_delta_krw(last_cycle, realized_total),
        "slippage_summary": _slippage_summary(records, target_date),
        "regime_timeline": _regime_timeline(day_cycles),
        "brake_state_last": last_cycle.get("daily_pnl_brake_state") if last_cycle else None,
        "intraday_pnl_pct_last": last_cycle.get("intraday_pnl_pct") if last_cycle else None,
        "totals": {
            "realized_krw": realized_total,
            "trades_closed": trades_closed,
            "trades_opened": trades_opened,
        },
    }


def _unrealized_delta_krw(last_cycle: dict | None, realized_total: float) -> float | None:
    """Unrealized contribution = intraday total PnL minus realized total.

    Intraday total = ``equity_krw - intraday_pnl_baseline_krw`` from the day's
    last cycle snapshot (plan §2 실측 키). ``None`` when either value is absent.
    """
    if last_cycle is None:
        return None
    equity = _coerce_float(last_cycle.get("equity_krw"))
    baseline = _coerce_float(last_cycle.get("intraday_pnl_baseline_krw"))
    if equity is None or baseline is None:
        return None
    return (equity - baseline) - realized_total


def _slippage_summary(records: list[dict], target_date: str) -> dict | None:
    """Adopt ``build_fill_slippage_summary`` over the target date's fill records
    (signature unchanged — plan §2). ``None`` when the date has no fill-action
    records, or on any unexpected failure (never raises)."""
    try:
        day_fills = [
            record
            for record in records
            if record.get("action") in (_BUY_ACTION, _SELL_ACTION)
            and _record_on_date(record, target_date)
        ]
        if not day_fills:
            return None
        summary = build_fill_slippage_summary(
            day_fills,
            report_date=target_date,
            policy_buy_bps=_POLICY_BUY_BPS,
            policy_sell_bps=_POLICY_SELL_BPS,
        )
        return asdict(summary)
    except Exception:
        return None


def _regime_timeline(day_cycles: list[dict]) -> list[tuple[str, int]]:
    """Run-length encode ``current_regime`` over the day's cycle records."""
    timeline: list[tuple[str, int]] = []
    for record in day_cycles:
        regime = record.get("current_regime")
        if not isinstance(regime, str) or not regime:
            continue
        if timeline and timeline[-1][0] == regime:
            timeline[-1] = (regime, timeline[-1][1] + 1)
        else:
            timeline.append((regime, 1))
    return timeline


def _recorded_realized_krw(sell_record: dict) -> float | None:
    """Recorded realized PnL persisted on the sell record, if any.

    Field location confirmed against the live order log (2026-07-18):
    ``raw_response.sell_strategy_details.details.net_pnl_krw`` (net preferred,
    ``gross_pnl_krw`` fallback).
    """
    raw = sell_record.get("raw_response")
    if not isinstance(raw, dict):
        return None
    strategy = raw.get("sell_strategy_details")
    if not isinstance(strategy, dict):
        return None
    details = strategy.get("details")
    if not isinstance(details, dict):
        return None
    net = _coerce_float(details.get("net_pnl_krw"))
    if net is not None:
        return net
    return _coerce_float(details.get("gross_pnl_krw"))


def _realized_for_sell(sell_record: dict, paired_buy: dict | None) -> float | None:
    """Realized KRW for one sell: recorded field first, else paired
    reference-price difference."""
    recorded = _recorded_realized_krw(sell_record)
    if recorded is not None:
        return recorded
    sell_price = _reference_price(sell_record)
    buy_price = _reference_price(paired_buy) if paired_buy is not None else None
    qty = _coerce_float(sell_record.get("qty"))
    if sell_price is None or buy_price is None or qty is None:
        return None
    return (sell_price - buy_price) * qty


def _account_signature(records: list[dict]) -> str | None:
    for record in reversed(records):
        signature = record.get("account_signature")
        if isinstance(signature, str) and signature:
            return signature
    return None


def _format_krw(value: object) -> str:
    number = _coerce_float(value)
    if number is None:
        return "n/a"
    return f"{number:+,.0f}원"


def render_attribution_lines(report: dict) -> list[str]:
    """Korean console/Slack lines for a daily attribution report. Never raises —
    a hostile or degraded report renders with ``n/a`` placeholders."""
    if not isinstance(report, dict):
        return ["📊 일일 PnL 어트리뷰션: 리포트 없음"]

    date = report.get("date") or "unknown"
    signature = report.get("account_signature") or "unknown"
    totals = report.get("totals") if isinstance(report.get("totals"), dict) else {}

    lines = [f"📊 일일 PnL 어트리뷰션 — {date} ({signature})"]

    lines.append(
        "실현손익 합계: "
        f"{_format_krw(totals.get('realized_krw'))} "
        f"(청산 {totals.get('trades_closed', 0)}건 / 진입 {totals.get('trades_opened', 0)}건)"
    )
    realized_by_symbol = report.get("realized_by_symbol")
    if isinstance(realized_by_symbol, dict) and realized_by_symbol:
        for symbol, krw in sorted(
            realized_by_symbol.items(),
            key=lambda item: _coerce_float(item[1]) or 0.0,
            reverse=True,
        ):
            lines.append(f"  · {symbol}: {_format_krw(krw)}")
    else:
        lines.append("  · 실현 청산 없음")

    lines.append(f"미실현 변화: {_format_krw(report.get('unrealized_delta_krw'))}")

    slippage = report.get("slippage_summary")
    if isinstance(slippage, dict):
        buy = slippage.get("buy") if isinstance(slippage.get("buy"), dict) else {}
        sell = slippage.get("sell") if isinstance(slippage.get("sell"), dict) else {}
        lines.append(
            "슬리피지: "
            f"표본 {slippage.get('sample_count', 0)}건 "
            f"(BUY {buy.get('count', 0)} / SELL {sell.get('count', 0)})"
        )
    else:
        lines.append("슬리피지: 표본 없음")

    timeline = report.get("regime_timeline")
    if isinstance(timeline, list) and timeline:
        segments = []
        for entry in timeline:
            try:
                regime, count = entry
                segments.append(f"{regime}×{count}")
            except Exception:
                continue
        if segments:
            lines.append("레짐 타임라인: " + " → ".join(segments))
    else:
        lines.append("레짐 타임라인: 기록 없음")

    brake = report.get("brake_state_last")
    pnl_pct = _coerce_float(report.get("intraday_pnl_pct_last"))
    pnl_pct_text = f"{pnl_pct:+.2f}%" if pnl_pct is not None else "n/a"
    lines.append(f"브레이크 상태: {brake or 'n/a'} / 장중 손익률: {pnl_pct_text}")

    return lines
