"""R6 equity-flow integration pin (E1 S4 / risk register R6-02).

The relocated risk cluster drives two LIVE money paths, not just the report:
``build_account_state_payload`` -> ``select_risk_managed_current_equity_krw`` ->
``build_daily_pnl_state`` (HARD_STOP brake). A byte-diff of report output would
NOT catch a break in that flow. This pin asserts the equity produced by the
account-state payload flows unchanged into the brake decision, through the
symbols now living in ``app/portfolio/equity_state.py`` — guarding the
2026-04-23 false-HARD_STOP regression against the relocation.
"""

from __future__ import annotations

from datetime import datetime
from unittest import mock

from app.portfolio import equity_state
from app.portfolio.schema import PortfolioPosition, PortfolioSnapshot


def _snapshot() -> PortfolioSnapshot:
    return PortfolioSnapshot(
        positions=(
            PortfolioPosition(
                symbol="005930",
                name="Samsung Electronics",
                holding_qty=100,
                average_cost=70000,
                current_price=841_545,
                market_value=84_154_500,
                gross_pnl=0,
                gross_pnl_pct=0.0,
                has_position=True,
            ),
        ),
        cash_total=57_409_505,
        cash_orderable=25_542_687,
        cash_next_day=25_542_687,
        total_evaluation_amount=109_697_187,
    )


def test_equity_flows_from_account_state_into_brake_via_leaf() -> None:
    # 1) account-state payload (moved) produces deployment-invariant equity.
    payload = equity_state.build_account_state_payload(
        portfolio_snapshot=_snapshot(), sell_analysis_results=()
    )
    assert payload["deployment_invariant_equity_krw"] == 141_564_005

    # 2) risk-managed selection (moved) falls back to raw balance when the
    #    deployment-invariant figure is inflated by T+2 settlement cash.
    risk_equity = equity_state.select_risk_managed_current_equity_krw(
        account_state=payload,
        raw_balance_total_evaluation_amount_krw=109_697_187,
    )
    assert risk_equity == 109_697_187

    # 3) that same risk equity, as today's baseline, flows into the brake with
    #    NO phantom loss (baseline == current -> NORMAL, not HARD_STOP).
    today = datetime(2026, 4, 20, 15, 5, 0, tzinfo=equity_state.KOREA_TZ)
    history = [
        {
            "timestamp": datetime(2026, 4, 20, 9, 35, 0, tzinfo=equity_state.KOREA_TZ).isoformat(),
            "total_equity_krw": risk_equity,
            "raw_balance_total_evaluation_amount_krw": 109_697_187,
            "cash_orderable_krw": 25_542_687,
            "cash_next_day_krw": 25_542_687,
            "equity_basis": equity_state.DEPLOYMENT_INVARIANT_EQUITY_BASIS,
            "cash_deployment_invariant": True,
        }
    ]
    with (
        mock.patch.object(equity_state, "get_korean_now", return_value=today),
        mock.patch.object(equity_state, "_load_performance_snapshots", return_value=history),
    ):
        pnl_state = equity_state.build_daily_pnl_state(
            current_equity_krw=risk_equity,
            warning_pct=-1.0,
            buy_pause_pct=-3.0,
            hard_stop_pct=-4.5,
        )
    assert pnl_state["evaluated"] is True
    assert pnl_state["status"] == "NORMAL"
    assert pnl_state["baseline_equity_krw"] == risk_equity
    assert pnl_state["daily_pnl_pct"] == 0.0
