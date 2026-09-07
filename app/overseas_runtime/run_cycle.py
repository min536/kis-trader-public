"""Overseas runtime cycle orchestrator — Phase 6.

SELL-then-BUY cycle: evaluates explicit sell intents first (mirroring the domestic
SELL-then-BUY ordering in run_cycle), then scans the buy universe, selects the top
gate2 v2 candidate, and runs the buy flow. All broker I/O is injected (fetch_detail,
fetch_orderable, submit_order) so the orchestration is pure and testable; the operator
CLI wires the real fetchers. Sell-trigger evaluation (P&L -> trigger) is a deferred
strategy-layer port — the cycle accepts an explicit ``sell_plan`` for now.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.overseas_stock.session import get_us_eastern_now, is_us_regular_session
from app.overseas_stock.selection import scan_overseas_universe, select_overseas_top
from app.overseas_execution.buy_flow import run_overseas_buy_flow
from app.overseas_execution.sell_flow import run_overseas_sell_flow
from app.overseas_runtime.state import record_overseas_order_in_state


@dataclass(frozen=True)
class OverseasCycleResult:
    now_iso: str
    market_open: bool
    sell_results: tuple = ()
    candidates: tuple = ()
    selected: object | None = None
    buy_result: object | None = None


def run_overseas_cycle(
    *,
    buy_symbols,
    policy,
    state,
    risk_log_path,
    fetch_detail,
    fetch_orderable,
    sell_plan=(),
    confirm=False,
    exchange="NASD",
    now=None,
    market_open=None,
    submit_order=None,
    settings=None,
    token=None,
    notify=None,
    artifact=None,
    scoring_params=None,
) -> OverseasCycleResult:
    now = now if now is not None else get_us_eastern_now()
    if market_open is None:
        market_open = is_us_regular_session(now)

    # SELL pass first (mirrors domestic SELL-then-BUY ordering).
    sell_results = []
    for item in sell_plan:
        result = run_overseas_sell_flow(
            symbol=item["symbol"],
            holding_qty=item["holding_qty"],
            trigger=item["trigger"],
            quote_price=item["quote_price"],
            policy=policy,
            state=state,
            risk_log_path=risk_log_path,
            exchange=exchange,
            confirm_sell=confirm,
            now=now,
            market_open=market_open,
            submit_order=submit_order,
            settings=settings,
            token=token,
            notify=notify,
        )
        if result.action == "submitted":
            record_overseas_order_in_state(
                state, side="sell", symbol=item["symbol"], qty=result.qty, now=now
            )
        sell_results.append(result)

    # BUY pass: scan the universe, select the top gate2 v2 candidate, run the buy flow.
    candidates = scan_overseas_universe(
        buy_symbols,
        fetch_detail=fetch_detail,
        exchange=exchange,
        artifact=artifact,
        params=scoring_params,
    )
    selected = select_overseas_top(candidates)
    buy_result = None
    if selected is not None:
        buy_result = run_overseas_buy_flow(
            candidate=selected,
            policy=policy,
            state=state,
            risk_log_path=risk_log_path,
            fetch_orderable=fetch_orderable,
            exchange=exchange,
            confirm_buy=confirm,
            now=now,
            market_open=market_open,
            submit_order=submit_order,
            settings=settings,
            token=token,
            notify=notify,
        )
        if buy_result.action == "submitted":
            record_overseas_order_in_state(
                state, side="buy", symbol=selected.symbol, qty=buy_result.qty, now=now
            )

    return OverseasCycleResult(
        now_iso=now.isoformat(),
        market_open=market_open,
        sell_results=tuple(sell_results),
        candidates=tuple(candidates),
        selected=selected,
        buy_result=buy_result,
    )
