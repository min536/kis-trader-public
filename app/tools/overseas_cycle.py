"""Operator CLI: run one overseas (US) SELL-then-BUY cycle."""
from __future__ import annotations

import argparse
from typing import Optional, Sequence

from app.overseas_runtime.run_cycle import run_overseas_cycle


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="app.tools.overseas_cycle")
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--exchange", default="NASD")
    parser.add_argument("--max-budget-usd", type=float, default=1000.0)
    parser.add_argument("--max-exposure-pct", type=float, default=20.0)
    parser.add_argument("--max-qty", type=int, default=10)
    parser.add_argument("--limit-offset-bps", type=float, default=0.0)
    parser.add_argument("--max-orders", type=int, default=5)
    parser.add_argument("--max-notional-usd", type=float, default=10000.0)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)

    from app.overseas_execution.policy import OverseasFlowPolicy
    from app.overseas_runtime.state import new_overseas_runtime_state
    from app.overseas_stock.order import get_overseas_order_log_path
    from app.overseas_stock.market_data import fetch_overseas_price_detail, fetch_overseas_orderable

    policy = OverseasFlowPolicy(
        max_budget_per_trade_usd=args.max_budget_usd,
        max_account_exposure_pct=args.max_exposure_pct,
        max_qty_per_trade=args.max_qty,
        limit_offset_bps=args.limit_offset_bps,
        max_orders=args.max_orders,
        max_notional_usd=args.max_notional_usd,
    )
    state = new_overseas_runtime_state()
    risk_log_path = str(get_overseas_order_log_path())

    if not args.confirm:
        print("[PREVIEW] Running in preview mode — no orders will be submitted.")

    result = run_overseas_cycle(
        buy_symbols=args.symbols,
        policy=policy,
        state=state,
        risk_log_path=risk_log_path,
        fetch_detail=lambda sym, exc: fetch_overseas_price_detail(sym, exchange=exc),
        fetch_orderable=lambda sym, price, exc: fetch_overseas_orderable(sym, price, exchange=exc),
        exchange=args.exchange,
        confirm=args.confirm,
    )

    print(f"market_open={result.market_open}  now={result.now_iso}")
    if result.selected is not None:
        print(f"selected={result.selected.symbol}  score={result.selected.final_score:.1f}")
    else:
        print("selected=None (no candidate passed threshold)")
    if result.buy_result is not None:
        print(f"buy_result.action={result.buy_result.action}")
    for i, sr in enumerate(result.sell_results):
        print(f"sell_results[{i}].action={sr.action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
