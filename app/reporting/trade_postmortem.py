"""Weekly trade postmortem builder + renderer (plan §3 S3).

Reviews the window's CLOSED trades: hold time, exit-reason distribution,
per-symbol repeat performance, and win-rate/profit-factor summary. Pure +
offline — parsed order-log records and runtime-state values in, report dict
out; path resolution belongs to the CLI (``app/tools/trade_postmortem_report.py``).

Pairing mirrors the dashboard trade-journal semantics
(``app/dashboard/normalizers.py::_build_trade_journal`` — BUY ``order_succeeded``
FIFO-paired with SELL ``sell_order_succeeded`` per symbol) without importing the
dashboard package. Recorded PnL fields on the sell record take precedence over
reference-price pairing (same field locations as the attribution builder).

``exit_reason`` prefers the order-log record; when absent, the runtime-state
``last_exit_reason_by_symbol`` fallback can only describe each symbol's MOST
RECENT exit — ``reason_source`` (``"order_log"`` | ``"exit_state"`` | ``None``)
makes the provenance explicit per trade (plan §3/§5).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app.reporting.pnl_attribution import (
    _coerce_float,
    _recorded_realized_krw,
    _reference_price,
    _sorted_order_mappings,
)

_BUY_ACTION = "order_succeeded"
_SELL_ACTION = "sell_order_succeeded"


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def build_weekly_postmortem(
    *,
    orders: list[dict],
    exit_state: dict,
    now: datetime,
    window_days: int = 7,
) -> dict:
    """Build the weekly postmortem report. Never raises — hostile input degrades
    the affected trade/section instead."""
    try:
        window_start = now - timedelta(days=window_days)
        window = {
            "start": window_start.isoformat(),
            "end": now.isoformat(),
            "days": window_days,
        }
    except Exception:
        window_start = None
        window = {"start": None, "end": None, "days": window_days}

    records = _sorted_order_mappings(orders if isinstance(orders, list) else [])
    closed_trades = _closed_trades_within_window(records, window_start, now)
    _apply_exit_state_fallback(closed_trades, exit_state)

    return {
        "window": window,
        "closed_trades": closed_trades,
        "exit_reason_distribution": _exit_reason_distribution(closed_trades),
        "per_symbol": _per_symbol(closed_trades),
        "summary": _summary(closed_trades),
    }


def _summary(closed_trades: list[dict]) -> dict:
    """Win-rate / average win-loss / profit-factor over trades with known PnL.
    Empty or one-sided samples degrade the affected stat to ``None``."""
    pnls = [
        pnl
        for pnl in (_coerce_float(trade.get("pnl_krw")) for trade in closed_trades)
        if pnl is not None
    ]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    gross_loss = -sum(losses)
    return {
        "win_rate": (len(wins) / len(pnls)) if pnls else None,
        "avg_win_krw": (sum(wins) / len(wins)) if wins else None,
        "avg_loss_krw": (sum(losses) / len(losses)) if losses else None,
        "profit_factor": (sum(wins) / gross_loss) if wins and gross_loss > 0 else None,
    }


def _apply_exit_state_fallback(closed_trades: list[dict], exit_state: dict) -> None:
    """Fill missing exit reasons from ``last_exit_reason_by_symbol`` — but only
    for each symbol's MOST RECENT closed trade (the runtime state keeps one exit
    per symbol), marked via ``reason_source = "exit_state"`` (plan §3/§5)."""
    reasons = (
        exit_state.get("last_exit_reason_by_symbol")
        if isinstance(exit_state, dict)
        else None
    )
    if not isinstance(reasons, dict):
        return
    latest_by_symbol: dict[str, dict] = {}
    for trade in closed_trades:
        symbol = trade.get("symbol")
        if not isinstance(symbol, str):
            continue
        current = latest_by_symbol.get(symbol)
        if current is None or str(trade.get("exit_at") or "") > str(
            current.get("exit_at") or ""
        ):
            latest_by_symbol[symbol] = trade
    for symbol, trade in latest_by_symbol.items():
        if trade.get("exit_reason") is not None:
            continue
        reason = reasons.get(symbol)
        if isinstance(reason, str) and reason:
            trade["exit_reason"] = reason
            trade["reason_source"] = "exit_state"


def _per_symbol(closed_trades: list[dict]) -> dict[str, dict]:
    """Per-symbol repeat performance: trade count, wins, and net KRW (trades
    with unknown PnL count as trades but contribute neither win nor net)."""
    per_symbol: dict[str, dict] = {}
    for trade in closed_trades:
        symbol = trade.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            continue
        entry = per_symbol.setdefault(symbol, {"trades": 0, "wins": 0, "net_krw": 0.0})
        entry["trades"] += 1
        pnl = _coerce_float(trade.get("pnl_krw"))
        if pnl is None:
            continue
        entry["net_krw"] += pnl
        if pnl > 0:
            entry["wins"] += 1
    return per_symbol


def _exit_reason_distribution(closed_trades: list[dict]) -> dict[str, int]:
    distribution: dict[str, int] = {}
    for trade in closed_trades:
        reason = trade.get("exit_reason")
        key = reason if isinstance(reason, str) and reason else "unknown"
        distribution[key] = distribution.get(key, 0) + 1
    return distribution


def _closed_trades_within_window(
    records: list[dict],
    window_start: datetime | None,
    window_end: datetime | None,
) -> list[dict]:
    buy_queue: dict[str, list[dict]] = {}
    trades: list[dict] = []

    for record in records:
        action = record.get("action")
        symbol = str(record.get("symbol") or "").strip()
        if not symbol:
            continue
        if action == _BUY_ACTION:
            buy_queue.setdefault(symbol, []).append(record)
        elif action == _SELL_ACTION:
            paired_buy = buy_queue[symbol].pop(0) if buy_queue.get(symbol) else None
            trade = _closed_trade(symbol, paired_buy, record)
            if _within_window(trade["exit_at"], window_start, window_end):
                trades.append(trade)
    return trades


def _within_window(
    exit_at: object,
    window_start: datetime | None,
    window_end: datetime | None,
) -> bool:
    exit_dt = _parse_dt(exit_at)
    if exit_dt is None or window_start is None or window_end is None:
        return False
    try:
        return window_start <= exit_dt <= window_end
    except TypeError:  # naive/aware mismatch
        return False


def _closed_trade(symbol: str, paired_buy: dict | None, sell_record: dict) -> dict:
    entry_at = paired_buy.get("timestamp") if isinstance(paired_buy, dict) else None
    exit_at = sell_record.get("timestamp")

    entry_dt = _parse_dt(entry_at)
    exit_dt = _parse_dt(exit_at)
    hold_minutes: int | None = None
    if entry_dt is not None and exit_dt is not None:
        try:
            hold_minutes = int((exit_dt - entry_dt).total_seconds() / 60)
        except TypeError:
            hold_minutes = None

    pnl_krw, pnl_pct = _trade_pnl(sell_record, paired_buy)
    logged_reason = _order_log_exit_reason(sell_record)

    return {
        "symbol": symbol,
        "entry_at": entry_at if isinstance(entry_at, str) else None,
        "exit_at": exit_at if isinstance(exit_at, str) else None,
        "hold_minutes": hold_minutes,
        "pnl_krw": pnl_krw,
        "pnl_pct": pnl_pct,
        "exit_reason": logged_reason,
        "reason_source": "order_log" if logged_reason is not None else None,
    }


def _order_log_exit_reason(sell_record: dict) -> str | None:
    """Exit reason persisted on the sell record itself — the strategy rule name
    (``sell_strategy_details.triggered_rule_name``) or the submit ``trigger``
    (field locations confirmed in the live order log, 2026-07-18)."""
    raw = sell_record.get("raw_response")
    if not isinstance(raw, dict):
        return None
    strategy = raw.get("sell_strategy_details")
    if isinstance(strategy, dict):
        rule_name = strategy.get("triggered_rule_name")
        if isinstance(rule_name, str) and rule_name:
            return rule_name
    trigger = raw.get("trigger")
    if isinstance(trigger, str) and trigger:
        return trigger
    return None


def _trade_pnl(
    sell_record: dict, paired_buy: dict | None
) -> tuple[float | None, float | None]:
    """(pnl_krw, pnl_pct): recorded sell-record fields first, else
    reference-price pairing."""
    recorded_krw = _recorded_realized_krw(sell_record)
    recorded_pct = _recorded_pnl_pct(sell_record)
    if recorded_krw is not None:
        return recorded_krw, recorded_pct

    sell_price = _reference_price(sell_record)
    buy_price = _reference_price(paired_buy) if paired_buy is not None else None
    qty = _coerce_float(sell_record.get("qty"))
    if sell_price is None or buy_price is None or qty is None or buy_price == 0.0:
        return None, recorded_pct
    pnl_krw = (sell_price - buy_price) * qty
    pnl_pct = (sell_price - buy_price) / buy_price * 100.0
    return pnl_krw, pnl_pct


def _recorded_pnl_pct(sell_record: dict) -> float | None:
    raw = sell_record.get("raw_response")
    if not isinstance(raw, dict):
        return None
    strategy = raw.get("sell_strategy_details")
    if not isinstance(strategy, dict):
        return None
    details = strategy.get("details")
    if not isinstance(details, dict):
        return None
    net = _coerce_float(details.get("net_pnl_pct"))
    if net is not None:
        return net
    return _coerce_float(details.get("pnl_pct"))


def _format_krw(value: object) -> str:
    number = _coerce_float(value)
    if number is None:
        return "n/a"
    return f"{number:+,.0f}원"


def _format_pct(value: object) -> str:
    number = _coerce_float(value)
    if number is None:
        return "n/a"
    return f"{number * 100.0:.1f}%"


def render_postmortem_lines(report: dict) -> list[str]:
    """Korean console/Slack lines for a weekly postmortem report. Never raises —
    degraded sections render with ``n/a``/없음 placeholders."""
    if not isinstance(report, dict):
        return ["🧾 주간 트레이드 포스트모템: 리포트 없음"]

    window = report.get("window") if isinstance(report.get("window"), dict) else {}
    start = str(window.get("start") or "?")[:10]
    end = str(window.get("end") or "?")[:10]
    lines = [f"🧾 주간 트레이드 포스트모템 — {start} ~ {end} ({window.get('days', '?')}일)"]

    closed_trades = (
        report.get("closed_trades")
        if isinstance(report.get("closed_trades"), list)
        else []
    )
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    hold_values = [
        minutes
        for minutes in (
            _coerce_float(trade.get("hold_minutes"))
            for trade in closed_trades
            if isinstance(trade, dict)
        )
        if minutes is not None
    ]
    avg_hold_text = (
        f"{sum(hold_values) / len(hold_values):.0f}분" if hold_values else "n/a"
    )
    lines.append(
        f"청산 트레이드 {len(closed_trades)}건 / 승률 {_format_pct(summary.get('win_rate'))} "
        f"/ 평균 보유 {avg_hold_text}"
    )

    distribution = report.get("exit_reason_distribution")
    if isinstance(distribution, dict) and distribution:
        ordered = sorted(distribution.items(), key=lambda item: (-item[1], item[0]))
        lines.append(
            "청산 사유 분포: "
            + ", ".join(f"{reason}×{count}" for reason, count in ordered)
        )
    else:
        lines.append("청산 사유 분포: 없음")

    per_symbol = report.get("per_symbol")
    if isinstance(per_symbol, dict) and per_symbol:
        lines.append("심볼별 반복 성과:")
        for symbol, stats in sorted(
            per_symbol.items(),
            key=lambda item: _coerce_float((item[1] or {}).get("net_krw")) or 0.0,
            reverse=True,
        ):
            stats = stats if isinstance(stats, dict) else {}
            lines.append(
                f"  · {symbol}: {stats.get('trades', 0)}건 "
                f"(승 {stats.get('wins', 0)}) {_format_krw(stats.get('net_krw'))}"
            )
    else:
        lines.append("심볼별 반복 성과: 청산 없음")

    profit_factor = _coerce_float(summary.get("profit_factor"))
    profit_factor_text = f"{profit_factor:.2f}" if profit_factor is not None else "n/a"
    lines.append(
        f"평균 수익 {_format_krw(summary.get('avg_win_krw'))} / "
        f"평균 손실 {_format_krw(summary.get('avg_loss_krw'))} / "
        f"프로핏 팩터 {profit_factor_text}"
    )

    return lines
