"""Payload builders for the v2 ops console preview.

This module is the stable Python-side contract for the v2 site. It consumes the
same ``load_dashboard_data()`` payload as the older dashboard surfaces and emits
JSON-serializable view models for the browser-only v2 UI.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from app.dashboard.metrics import (
    build_anomaly_summary,
    build_blocked_reason_rows,
    build_buy_judgement_snapshot,
    build_engine_state_view_model,
    build_operational_alerts,
    build_top_summary,
)
from app.dashboard.view_models import (
    _build_mission,
    _build_queue_items,
    _build_trace_events,
    _sort_positions,
)


V2_SITE_BASE_CONTRACT_KEYS = (
    "NOW_ISO",
    "TRADING_DATE",
    "ACCOUNT",
    "OTHER_ACCOUNTS",
    "POSITIONS",
    "ORDERS",
    "CYCLES",
    "CYCLE_HIST",
    "ENDPOINTS",
    "BUDGETS",
    "ENGINE",
    "PNL_HIST",
    "INTRADAY",
    "EQUITY_HISTORY",
    "EVENTS",
    "NAV_GROUPS",
)

V2_SITE_PARITY_CONTRACT_KEYS = (
    "ACCOUNT",
    "MISSION",
    "QUEUE",
    "TRIAGE",
    "BOOK",
    "TRACE_EVENTS",
    "LAB",
    "RAW_DIAGNOSTICS",
)

STREAMLIT_PARITY_SURFACES = {
    "Desk/Triage": "TRIAGE",
    "Desk/Execution": "TRACE_EVENTS",
    "Desk/Capital": "ACCOUNT",
    "Desk/Lab": "LAB",
    "Book": "BOOK",
    "Trace": "TRACE_EVENTS",
    "settings/freshness": "RAW_DIAGNOSTICS",
}


def build_v2_site_payload(
    data: dict[str, Any],
    *,
    now: datetime | None = None,
    account_signature: str | None = None,
    account_options: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the complete v2-site payload for already-loaded dashboard data."""

    now_dt = now or datetime.now(timezone.utc)
    summary = build_top_summary(data)
    account = _build_account(summary, data)
    if account_signature:
        account["signature"] = account_signature
    if account_options is not None:
        active_option = next(
            (
                option
                for option in account_options
                if option.get("id") == account.get("signature")
            ),
            None,
        )
        if active_option is not None:
            account["display_name"] = active_option.get("label") or account["display_name"]
            account["env"] = active_option.get("env") or account["env"]
            account["masked"] = active_option.get("label") or account["masked"]
    positions = _build_positions(data, account["total_equity_krw"])
    orders = _build_orders(data)
    cycles = _build_cycles(data)
    if not cycles:
        cycles = [_empty_cycle(account["last_sync_at"])]
    engine = _build_engine(data, summary)
    budgets = _build_budgets(engine, cycles)
    pnl_hist, intraday, equity_history = _build_history(data, account)
    trace_events = _build_trace_events(data, limit=80)
    lab = _build_lab(data)
    book = _build_book(data, summary)
    triage = _build_triage(data)
    queue = _build_queue_items(data, summary)

    return {
        "NOW_ISO": now_dt.isoformat(),
        "TRADING_DATE": now_dt.date().isoformat(),
        "ACCOUNT": account,
        "OTHER_ACCOUNTS": account_options
        if account_options is not None
        else [
            {
                "id": account["signature"],
                "label": account["display_name"],
                "env": account["env"],
                "status": "active",
                "equity": account["total_equity_krw"],
            }
        ],
        "POSITIONS": positions,
        "ORDERS": orders,
        "CYCLES": cycles,
        "CYCLE_HIST": _build_cycle_hist(cycles),
        "ENDPOINTS": _build_endpoints(cycles),
        "BUDGETS": budgets,
        "ENGINE": engine,
        "PNL_HIST": pnl_hist,
        "INTRADAY": intraday,
        "EQUITY_HISTORY": equity_history,
        "EVENTS": _build_events(orders, cycles),
        "NAV_GROUPS": _build_nav_groups(positions, orders, cycles, budgets, lab),
        "MISSION": _build_mission(summary, data),
        "QUEUE": queue,
        "TRIAGE": triage,
        "BOOK": book,
        "TRACE_EVENTS": trace_events,
        "LAB": lab,
        "RAW_DIAGNOSTICS": _build_raw_diagnostics(data, summary),
    }


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return default
        return number
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    return int(round(_num(value, float(default))))


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _iso(value: Any, *, fallback: datetime | None = None) -> str:
    text = _text(value)
    if text:
        return text
    return (fallback or datetime.now(timezone.utc)).isoformat()


def _seconds_since(value: Any) -> int:
    text = _text(value)
    if not text:
        return 0
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return 0
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    return max(0, int((now - dt).total_seconds()))


def _pct(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator * 100.0


def _today_return_pct(equity: float, today_krw: float) -> float:
    """RC12: today's P&L as a % of the prior-day base (equity − today); 0.0 if ≤ 0."""
    base = equity - today_krw
    if base <= 0:
        return 0.0
    return _pct(today_krw, base)


def _unrealized_return_pct(unrealized_krw: float, cost_base_krw: float) -> float:
    """RC12: unrealized P&L as a % of the cost base (market value − unrealized); 0.0 if ≤ 0."""
    if cost_base_krw <= 0:
        return 0.0
    return _pct(unrealized_krw, cost_base_krw)


def _pctl(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[index]


def _spark(seed: str, n: int = 34) -> list[float]:
    state = sum(ord(ch) for ch in seed) or 1
    values: list[float] = []
    current = 0.0
    for _ in range(n):
        state = (state * 1103515245 + 12345) % (2**31)
        current += ((state % 1000) / 1000.0) - 0.48
        values.append(current)
    low = min(values)
    high = max(values)
    if high == low:
        return [0.5 for _ in values]
    return [(item - low) / (high - low) for item in values]


def _build_account(summary: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    account = data.get("account_view") or {}
    equity = _int(summary.get("total_equity_krw") or account.get("operating_equity_krw"))
    cash = _int(summary.get("cash_total_krw") or account.get("cash_total_krw"))
    orderable = _int(summary.get("orderable_cash_krw") or account.get("orderable_cash_krw"))
    holdings = _int(summary.get("holdings_market_value_krw") or account.get("holdings_market_value_krw"))
    env = _text(summary.get("environment_label"), "LOCAL").upper()
    signature = _text(account.get("masked_account_display"), env.lower())
    masked = signature.replace("mock_", "").replace("live_", "")
    if len(masked) > 7:
        masked = f"{masked[:4]}***{masked[-5:]}"

    account_dq = account.get("data_quality")
    account_dq = account_dq if isinstance(account_dq, dict) else {}
    is_insufficient = equity <= 0 or bool(account_dq.get("is_data_insufficient"))
    # D2: prefer the precise equity-unavailable reason from _build_account_view
    # (names the missing source) over the generic snapshot fallback text.
    equity_source = account_dq.get("equity_source")
    equity_unavailable_reason = _text(account_dq.get("equity_unavailable_reason")) or None
    dq_reason = equity_unavailable_reason or _text(summary.get("snapshot_health_reason"))
    if is_insufficient and not dq_reason:
        dq_reason = "잔고 미확보 — 마지막 성공 동기화 확인 필요"

    # RC12: build_top_summary exposes only cumulative total_return_pct (no
    # today-scoped source key), so derive per-basis pct fields below.
    today_pnl_krw = _int(summary.get("total_pnl_krw"))
    unrealized_pnl_krw = _int(summary.get("unrealized_net_pnl_krw"))
    cost_base_krw = holdings - unrealized_pnl_krw
    account_sync_at = summary.get("account_sync_at") or summary.get("recent_cycle_at")

    return {
        "signature": signature,
        "env": env,
        "masked": masked or "local",
        "broker": "KIS · Korea Investment",
        "display_name": f"{env.title()} · {masked or signature}",
        "market": "KRX",
        "base_ccy": "KRW",
        "total_equity_krw": equity,
        "cash_total_krw": cash,
        "cash_orderable_krw": orderable,
        "cash_next_day_krw": _int(account.get("cash_next_day_krw"), orderable),
        "holdings_market_value_krw": holdings,
        "total_cost_basis_krw": max(0, holdings - _int(summary.get("unrealized_net_pnl_krw"))),
        "total_unrealized_pnl_krw": _int(summary.get("unrealized_net_pnl_krw")),
        "total_unrealized_pnl_pct": _unrealized_return_pct(unrealized_pnl_krw, cost_base_krw),
        "total_return_today_krw": _int(summary.get("total_pnl_krw")),
        "total_return_today_pct": _today_return_pct(equity, today_pnl_krw),
        "positions_count": _int(summary.get("positions_count")),
        "cash_weight_pct": _pct(orderable or cash, equity),
        "current_drawdown_pct": _num(summary.get("current_drawdown_pct")),
        "max_drawdown_pct_30d": _num(summary.get("max_drawdown_pct")),
        "last_sync_at": _iso(account_sync_at),
        "data_quality": {
            "is_insufficient": is_insufficient,
            "snapshot_health": _text(summary.get("snapshot_health"), "—"),
            "reason": dq_reason,
            "equity_source": equity_source,
            "equity_unavailable_reason": equity_unavailable_reason,
            "last_sync_at": _iso(account_sync_at),
        },
    }


def _build_positions(data: dict[str, Any], total_equity: int) -> list[dict[str, Any]]:
    rows = []
    for item in data.get("positions") or []:
        symbol = _text(item.get("symbol"), "-")
        mv = _int(item.get("evaluation_amount_krw") or item.get("market_value"))
        pnl_krw = _int(item.get("net_pnl_krw") or item.get("net_pnl"))
        pnl_pct = _num(item.get("net_pnl_pct"))
        weight = _num(item.get("account_weight_pct") or item.get("weight_pct"))
        if not weight and total_equity:
            weight = _pct(mv, total_equity)
        rows.append(
            {
                "symbol": symbol,
                "name": _text(item.get("symbol_name"), symbol),
                "tags": tuple(item.get("tags") or ("KOSPI/KOSDAQ", "LiveData")),
                "qty": _int(item.get("holding_qty") or item.get("quantity")),
                "avg": _int(item.get("average_cost_krw") or item.get("average_price")),
                "last": _int(item.get("current_price_krw") or item.get("current_price")),
                "mv": mv,
                "pnl_krw": pnl_krw,
                "pnl_pct": pnl_pct,
                "weight": weight,
                "daily_chg_pct": _num(item.get("daily_chg_pct"), pnl_pct),
                "spark": _spark(symbol),
            }
        )
    return sorted(rows, key=lambda row: row["weight"], reverse=True)


def _build_orders(data: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in (data.get("orders") or [])[:200]:
        action = _text(item.get("action"), "unknown").upper()
        result = _text(item.get("result"), "unknown").lower()
        lowered = action.lower()
        if "succeeded" in lowered or result == "success":
            result = "filled"
        elif "blocked" in lowered or "rejected" in lowered:
            result = "rejected"
        elif "failed" in lowered:
            result = "failed"
        elif "skipped" in lowered:
            result = "skipped"
        rows.append(
            {
                "ts": _iso(item.get("timestamp")),
                "action": action,
                "side": _text(item.get("side"), "-").upper(),
                "symbol": _text(item.get("symbol"), "-"),
                "name": _text(item.get("symbol_name") or item.get("symbol"), "-"),
                "qty": _int(item.get("qty")),
                "price": _int(item.get("price")),
                "value": _int(item.get("value")),
                "result": result,
                "latency_ms": _num(item.get("latency_ms")),
                "cycle": _text(item.get("cycle_id"), "-"),
                "reason": _text(item.get("reason"), "-"),
            }
        )
    return rows


def _build_cycles(data: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in (data.get("cycles") or [])[:80]:
        action = _text(item.get("final_action"), "HOLD")
        selected_name = _text(
            item.get("selected_buy_candidate")
            or item.get("selected_sell_candidate")
            or item.get("selected_primary_action_name")
        )
        selected_symbol = _text(
            item.get("selected_buy_symbol")
            or item.get("selected_sell_symbol")
            or item.get("selected_primary_action_symbol")
        )
        selected_buy = None
        selected_sell = None
        if selected_name or selected_symbol:
            selected = {
                "symbol": selected_symbol or selected_name,
                "name": selected_name or selected_symbol,
                "qty": _int((item.get("buy_position_sizing") or {}).get("qty")),
                "score": _num((item.get("selected_buy_candidate_data") or {}).get("score")),
            }
            if "SELL" in action.upper():
                selected_sell = selected
            else:
                selected_buy = selected
        elapsed = _num(item.get("cycle_elapsed_ms"))
        rows.append(
            {
                "id": _text(item.get("cycle_id"), _iso(item.get("timestamp"))),
                "ts": _iso(item.get("timestamp")),
                "final_action": action,
                "final_reason": _text(item.get("final_reason"), "-"),
                "market_session": _text(item.get("market_session"), "-"),
                "elapsed_ms": elapsed,
                "api_requests": _int(item.get("api_request_count")),
                "quote_requests": _int(item.get("buy_scan_evaluated_count")),
                "rate_limit": "rate" in _text(item.get("final_reason")).lower()
                or "backoff" in _text(item.get("final_reason")).lower(),
                "profile": _text((item.get("raw") or {}).get("buy_scan_profile"), "-"),
                "regime": _text((item.get("raw") or {}).get("current_regime"), "-"),
                "brake": _text((item.get("raw") or {}).get("current_brake_state"), "OK"),
                "selected_buy": selected_buy,
                "selected_sell": selected_sell,
                "funnel": _cycle_funnel(item),
                "pre_gate_reasons": _cycle_pre_gate_reasons(item),
                "timing": _cycle_timing(elapsed),
            }
        )
    return rows


def _empty_cycle(ts: str) -> dict[str, Any]:
    return {
        "id": "no-cycle",
        "ts": ts,
        "final_action": "NO_DATA",
        "final_reason": "No cycle snapshots loaded.",
        "market_session": "-",
        "elapsed_ms": 0,
        "api_requests": 0,
        "quote_requests": 0,
        "rate_limit": False,
        "profile": "-",
        "regime": "-",
        "brake": "OK",
        "selected_buy": None,
        "selected_sell": None,
        "funnel": {
            "universe": 0,
            "layered": 0,
            "pre_gate_pass": 0,
            "pre_gate_reject": 0,
            "shallow_rank": 0,
            "deep_eval": 0,
            "finalists": 0,
            "selected": 0,
            "executed": 0,
        },
        "pre_gate_reasons": [],
        "timing": _cycle_timing(0),
    }


def _cycle_funnel(cycle: dict[str, Any]) -> dict[str, int]:
    universe = _int(cycle.get("buy_scan_requested_count") or cycle.get("sell_evaluated_count"))
    evaluated = _int(cycle.get("buy_scan_evaluated_count"))
    top = _int(cycle.get("top_candidate_count"))
    selected = 1 if (
        cycle.get("selected_primary_action_name")
        or cycle.get("selected_buy_candidate")
        or cycle.get("selected_sell_candidate")
    ) else 0
    executed = 1 if "ORDER" in _text(cycle.get("final_action")).upper() else 0
    return {
        "universe": universe,
        "layered": max(evaluated, top, selected),
        "pre_gate_pass": evaluated,
        "pre_gate_reject": max(0, universe - evaluated),
        "shallow_rank": top,
        "deep_eval": top,
        "finalists": min(top, 3),
        "selected": selected,
        "executed": executed,
    }


def _cycle_pre_gate_reasons(cycle: dict[str, Any]) -> list[dict[str, Any]]:
    funnel = _cycle_funnel(cycle)
    if funnel["pre_gate_reject"] <= 0:
        return []
    code = _text(cycle.get("buy_scan_skipped_reason"), "not_available")
    return [
        {
            "code": code,
            "count": funnel["pre_gate_reject"],
            "label": code,
        }
    ]


def _cycle_timing(elapsed: float) -> dict[str, float]:
    return {
        "settings": 1.0 if elapsed else 0.0,
        "session_check": 0.0,
        "balance_inquiry": elapsed * 0.18,
        "sell_eval": elapsed * 0.18,
        "buy_scan": elapsed * 0.48,
        "ranking": elapsed * 0.10,
        "reporting": elapsed * 0.06,
        "total": elapsed,
    }


def _build_cycle_hist(cycles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "ts": cycle["ts"],
            "elapsed": _int(cycle["elapsed_ms"]),
            "api": _int(cycle["api_requests"]),
            "rate_limit": bool(cycle["rate_limit"]),
        }
        for cycle in reversed(cycles[:60])
    ]


def _build_engine(data: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    engine = build_engine_state_view_model(data.get("engine_state_view") or {})
    budget = engine.get("budget_status") if isinstance(engine.get("budget_status"), dict) else {}
    runtime = data.get("runtime_state") or {}
    account_view = data.get("account_view") or {}
    equity_krw = _int(summary.get("total_equity_krw") or account_view.get("operating_equity_krw"))
    return {
        "session": _text(engine.get("market_session") or summary.get("market_session"), "-"),
        "session_label": _text(engine.get("market_reason"), "KRX"),
        "cycle_freshness_s": _seconds_since(summary.get("recent_cycle_at")),
        "scheduler_decision": _text(engine.get("scheduler_decision"), "-"),
        "regime": _text(engine.get("current_regime"), "-"),
        "regime_multiplier": _num(engine.get("regime_multiplier"), 1.0),
        "brake_state": _text(engine.get("current_brake_state"), "OK"),
        "daily_pnl_pct": _today_return_pct(equity_krw, _int(summary.get("total_pnl_krw"))),
        "effective_buy_max_budget_per_trade_krw": _int(
            runtime.get("effective_buy_max_budget_per_trade_krw")
        ),
        "effective_buy_max_account_exposure_pct": _num(
            runtime.get("effective_buy_max_account_exposure_pct")
        ),
        "effective_rebuy_cooldown_minutes": _int(
            runtime.get("effective_rebuy_cooldown_minutes")
        ),
        "buy_scan_profile": _text(runtime.get("buy_scan_profile"), "-"),
        "buy_scan_universe": _int(engine.get("buy_scan_requested_count")),
        "next_buy_scan_in_s": 0 if engine.get("buy_scan_due") else 30,
        "next_sell_check_in_s": 0 if engine.get("sell_check_due") else 10,
        "uptime_h": 0.0,
        "process_pid": 0,
        "host": "local-v2",
        "version": "v2-site",
        "_budget": budget,
    }


def _build_budgets(engine: dict[str, Any], cycles: list[dict[str, Any]]) -> dict[str, Any]:
    budget = engine.pop("_budget", {}) or {}
    rate_hits = sum(1 for cycle in cycles if cycle["rate_limit"])
    last_rate = next((cycle for cycle in cycles if cycle["rate_limit"]), None)
    return {
        "request_window_used": _int(
            budget.get("recent_request_count") or budget.get("requests_used_this_tick")
        ),
        "request_window_cap": 4,
        "quote_window_used": _int(budget.get("quotes_used_this_tick")),
        "quote_window_cap": 20,
        "backoff_remaining_s": _int(budget.get("backoff_remaining_seconds")),
        "rate_limit_hits_today": rate_hits,
        "last_backoff_at": _iso((last_rate or {}).get("ts")),
        "last_backoff_duration_s": _int(budget.get("last_backoff_duration_seconds"), 0),
        "status": "warn" if rate_hits else "ok",
    }


def _build_history(
    data: dict[str, Any],
    account: dict[str, Any],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    snapshots = list(data.get("performance_snapshots") or [])
    equity_history = []
    for item in snapshots:
        timestamp = _text(item.get("timestamp"))
        raw_equity = item.get("total_equity_krw") or item.get("equity")
        if not timestamp or raw_equity is None:
            continue
        equity_history.append({"ts": timestamp, "v": _int(raw_equity)})

    intraday = []
    for index, item in enumerate(snapshots[-78:]):
        raw_equity = item.get("total_equity_krw") or item.get("equity")
        if raw_equity is None:
            continue
        intraday.append({"i": index, "v": _int(raw_equity)})

    if not intraday:
        return [], [], equity_history

    values = [row["v"] for row in intraday] or [account["total_equity_krw"] or 1]
    first = values[0] or 1
    pnl_hist = [
        {
            "day": index,
            "equity_idx": round(value / first * 100, 3),
            "ret": round(_pct(value - values[index - 1], values[index - 1]) if index else 0, 2),
        }
        for index, value in enumerate(values[-30:])
    ]
    return pnl_hist, intraday, equity_history


def _build_endpoints(cycles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    count = sum(_int(cycle["api_requests"]) for cycle in cycles)
    latencies = [_num(cycle["elapsed_ms"]) for cycle in cycles]
    return [
        {
            "name": "cycle/total",
            "category": "cycle",
            "count_24h": count,
            "p50": _int(_pctl(latencies, 0.50)),
            "p95": _int(_pctl(latencies, 0.95)),
            "p99": _int(_pctl(latencies, 0.99)),
            "errors_24h": sum(1 for cycle in cycles if "ERROR" in cycle["final_action"].upper()),
            "rate_hits": sum(1 for cycle in cycles if cycle["rate_limit"]),
        },
        {
            "name": "trading/inquire-balance",
            "category": "balance",
            "count_24h": max(0, count // 3),
            "p50": 0,
            "p95": 0,
            "p99": 0,
            "errors_24h": 0,
            "rate_hits": 0,
        },
        {
            "name": "quotations/inquire-price",
            "category": "quote",
            "count_24h": max(0, count),
            "p50": 0,
            "p95": 0,
            "p99": 0,
            "errors_24h": 0,
            "rate_hits": sum(1 for cycle in cycles if cycle["rate_limit"]),
        },
        {
            "name": "trading/order-cash",
            "category": "order",
            "count_24h": sum(1 for cycle in cycles if "ORDER" in cycle["final_action"].upper()),
            "p50": 0,
            "p95": 0,
            "p99": 0,
            "errors_24h": 0,
            "rate_hits": 0,
        },
    ]


def _build_events(
    orders: list[dict[str, Any]],
    cycles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for order in orders[:8]:
        rows.append(
            {
                "ts": order["ts"][11:19] if len(order["ts"]) >= 19 else order["ts"],
                "kind": "ORDER",
                "tone": _order_event_tone(order["result"]),
                "title": f"{order['action']} · {order['name']}",
                "body": order["reason"],
            }
        )
    for cycle in cycles[:4]:
        rows.append(
            {
                "ts": cycle["ts"][11:19] if len(cycle["ts"]) >= 19 else cycle["ts"],
                "kind": "CYCLE",
                "tone": "neg" if cycle["rate_limit"] else "neutral",
                "title": cycle["final_action"],
                "body": cycle["final_reason"],
            }
        )
    return sorted(rows, key=lambda row: row["ts"], reverse=True)[:12]


def _order_event_tone(result: str) -> str:
    if result == "filled":
        return "pos"
    if result == "failed":
        return "neg"
    if result == "rejected":
        return "warn"
    return "neutral"


def _build_nav_groups(
    positions: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    cycles: list[dict[str, Any]],
    budgets: dict[str, Any],
    lab: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        {
            "label": "Overview",
            "items": [
                {
                    "id": "dashboard",
                    "name": "Operations",
                    "icon": "grid",
                    "count": None,
                    "dotTone": "pos",
                },
                {
                    "id": "account",
                    "name": "Account",
                    "icon": "wallet",
                    "count": len(positions),
                    "dotTone": None,
                },
            ],
        },
        {
            "label": "Activity",
            "items": [
                {
                    "id": "orders",
                    "name": "Orders & Fills",
                    "icon": "list",
                    "count": len(orders),
                    "dotTone": None,
                },
                {
                    "id": "trace",
                    "name": "Cycle Trace",
                    "icon": "branch",
                    "count": len(cycles),
                    "dotTone": None,
                },
            ],
        },
        {
            "label": "Health",
            "items": [
                {
                    "id": "api",
                    "name": "API & Latency",
                    "icon": "pulse",
                    "count": None,
                    "dotTone": "warn" if budgets["rate_limit_hits_today"] else None,
                },
                {
                    "id": "lab",
                    "name": "Lab",
                    "icon": "database",
                    "count": lab["proposal_count"],
                    "dotTone": "warn" if lab["stale_baseline_count"] else None,
                },
            ],
        },
    ]


def _build_lab(data: dict[str, Any]) -> dict[str, Any]:
    research = data.get("research") or {}
    proposals = list(research.get("proposals") or [])
    baselines = list(research.get("baselines") or [])
    stale = list((research.get("snapshot_meta") or {}).get("stale_baselines") or [])
    labels = list(research.get("ml_labels") or [])
    return {
        "proposal_count": len(proposals),
        "baseline_count": len(baselines),
        "stale_baseline_count": len(stale),
        "snapshot_as_of": _text(research.get("snapshot_as_of"), "—"),
        "ml_label_count": len(labels),
        "proposals": proposals[:20],
        "baselines": baselines[:20],
        "stale_baselines": stale,
        "native_backtest": dict(research.get("native_backtest") or {}),
    }


def _build_book(data: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    positions = list(data.get("positions") or [])
    account = data.get("account_view") or {}
    sorts = {
        mode: _sort_positions(positions, mode)
        for mode in ("priority", "size", "market_value", "pnl")
    }
    selected = (sorts["priority"] or [{}])[0]
    return {
        "sorts": sorts,
        "selected_context": selected,
        "account_context": {
            "total_equity_krw": summary.get("total_equity_krw"),
            "settlement_equity_krw": summary.get("settlement_equity_krw"),
            "orderable_cash_krw": account.get("orderable_cash_krw"),
            "positions_count": summary.get("positions_count"),
        },
    }


def _build_triage(data: dict[str, Any]) -> dict[str, Any]:
    orders = data.get("orders") or []
    return {
        "anomaly": build_anomaly_summary(data),
        "buy_judgement": build_buy_judgement_snapshot(data),
        "blocked_reasons": build_blocked_reason_rows(orders, limit=10),
        "action_distribution": build_action_distribution_rows(orders, limit=10),
        "operational_alerts": build_operational_alerts(data),
    }


def build_action_distribution_rows(
    orders: list[dict[str, Any]],
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for order in orders:
        action = str(order.get("action", "")).strip()
        if not action:
            continue
        counts[action] = counts.get(action, 0) + 1
    rows = [{"label": key, "value": value} for key, value in counts.items()]
    return sorted(rows, key=lambda item: int(item["value"]), reverse=True)[:limit]


def _build_raw_diagnostics(data: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    errors = {
        "runtime_state": data.get("runtime_state_error"),
        "orders": data.get("orders_error"),
        "cycles": data.get("cycles_error"),
        "performance_snapshots": data.get("performance_snapshots_error"),
        "performance_summary": data.get("performance_summary_error"),
    }
    return {
        "snapshot_health": summary.get("snapshot_health"),
        "snapshot_health_reason": summary.get("snapshot_health_reason"),
        "recent_cycle_at": summary.get("recent_cycle_at"),
        "errors": {key: value for key, value in errors.items() if value},
        "counts": {
            "orders": len(data.get("orders") or []),
            "cycles": len(data.get("cycles") or []),
            "positions": len(data.get("positions") or []),
            "performance_snapshots": len(data.get("performance_snapshots") or []),
        },
    }
