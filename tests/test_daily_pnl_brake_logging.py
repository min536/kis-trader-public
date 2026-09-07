from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout

from app import main as main_module
from app.portfolio.schema import PortfolioPosition, PortfolioSnapshot
from app.risk import pnl_brake


class DailyPnlBrakeLoggingTests(unittest.TestCase):
    def test_print_account_balance_interpretation_uses_orderable_aware_brake_equity(self) -> None:
        portfolio_snapshot = PortfolioSnapshot(
            positions=(
                PortfolioPosition(
                    symbol="005930",
                    name="Samsung Electronics",
                    holding_qty=100,
                    average_cost=80_000,
                    current_price=82_000,
                    market_value=8_200_000,
                    gross_pnl=200_000,
                    gross_pnl_pct=2.5,
                    has_position=True,
                ),
            ),
            cash_total=92_000_000,
            cash_orderable=95_000_000,
            cash_next_day=91_500_000,
            total_evaluation_amount=103_200_000,
        )

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            main_module._print_account_balance_interpretation(
                portfolio_snapshot=portfolio_snapshot,
                sell_analysis_results=(),
            )

        rendered = buffer.getvalue()
        self.assertIn("브레이크 equity: 103,200,000원", rendered)
        self.assertIn("운영 equity: 103,200,000원", rendered)

    def test_build_daily_pnl_brake_observability_includes_required_fields(self) -> None:
        brake_state = {
            "baseline_equity_krw": 100_000_000,
            "current_equity_krw": 100_200_000,
            "daily_pnl_pct": 0.2,
            "display_status": "OK",
            "reason": "일중 손실 브레이크 기준을 통과했습니다.",
            "cash_deployment_invariant": True,
        }
        account_state_payload = {
            "deployment_invariant_equity_krw": 100_200_000,
            "deployment_invariant_cash_leg_krw": 92_000_000,
            "holdings_market_value_krw": 8_200_000,
            "total_unrealized_net_pnl_krw": 120_000,
        }
        realized_summary = {"realized_net_pnl_krw": 330_000}
        portfolio_snapshot = PortfolioSnapshot(
            positions=(
                PortfolioPosition(
                    symbol="005930",
                    name="Samsung Electronics",
                    holding_qty=100,
                    average_cost=80000,
                    current_price=82000,
                    market_value=8_200_000,
                    gross_pnl=200_000,
                    gross_pnl_pct=2.5,
                    has_position=True,
                ),
            ),
            cash_total=92_000_000,
            cash_orderable=5_000_000,
            cash_next_day=92_000_000,
            total_evaluation_amount=100_200_000,
        )

        payload = pnl_brake.build_daily_pnl_brake_observability(
            brake_state=brake_state,
            account_state_payload=account_state_payload,
            realized_summary=realized_summary,
            portfolio_snapshot=portfolio_snapshot,
        )

        self.assertEqual(payload["baseline_equity_krw"], 100_000_000)
        self.assertEqual(payload["current_equity_krw"], 100_200_000)
        self.assertEqual(payload["realized_pnl_krw"], 330_000)
        self.assertEqual(payload["unrealized_pnl_krw"], 120_000)
        self.assertEqual(payload["cash_krw"], 92_000_000)
        self.assertEqual(payload["holdings_value_krw"], 8_200_000)
        self.assertEqual(payload["brake_state"], "OK")
        self.assertTrue(payload["current_equity_accounting_consistent"])
        self.assertTrue(payload["current_equity_cash_deployment_invariant"])
        self.assertFalse(payload["snapshot_degraded"])
        self.assertIsNone(payload["snapshot_warning"])

    def test_build_daily_pnl_brake_observability_flags_degraded_snapshot(self) -> None:
        brake_state = {
            "baseline_equity_krw": None,
            "current_equity_krw": 0,
            "daily_pnl_pct": None,
            "status": "DATA_INSUFFICIENT",
            "reason": "데이터 부족",
            "cash_deployment_invariant": True,
        }
        account_state_payload = {
            "deployment_invariant_equity_krw": 0,
            "deployment_invariant_cash_leg_krw": 0,
            "holdings_market_value_krw": 0,
            "total_unrealized_net_pnl_krw": 0,
        }
        realized_summary = {"realized_net_pnl_krw": 0}
        portfolio_snapshot = PortfolioSnapshot(
            positions=(),
            cash_total=0,
            cash_orderable=0,
            cash_next_day=0,
            total_evaluation_amount=0,
        )

        payload = pnl_brake.build_daily_pnl_brake_observability(
            brake_state=brake_state,
            account_state_payload=account_state_payload,
            realized_summary=realized_summary,
            portfolio_snapshot=portfolio_snapshot,
        )

        self.assertTrue(payload["snapshot_degraded"])
        self.assertFalse(payload["current_equity_accounting_consistent"])
        self.assertIn("잔고 raw 총평가금액", str(payload["snapshot_warning"]))

    def test_print_daily_pnl_brake_state_smoke(self) -> None:
        brake_state = {
            "baseline_equity_krw": 100_000_000,
            "current_equity_krw": 97_000_000,
            "realized_pnl_krw": -120_000,
            "unrealized_pnl_krw": -2_880_000,
            "cash_krw": 50_000_000,
            "holdings_value_krw": 47_000_000,
            "daily_pnl_pct": -3.0,
            "status": "BUY_PAUSE",
            "reason": "BUY pause reason",
            "buy_paused": True,
            "remaining_minutes": 7,
            "equity_basis": "deployment_invariant",
            "current_equity_accounting_consistent": True,
            "current_equity_cash_deployment_invariant": True,
        }
        settings = type(
            "Settings",
            (),
            {
                "daily_pnl_warning_pct": -2.0,
                "daily_pnl_buy_pause_pct": -3.0,
                "daily_pnl_hard_stop_pct": -4.5,
            },
        )()

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            pnl_brake.print_daily_pnl_brake_state(
                brake_state=brake_state,
                current_equity_krw=97_000_000,
                settings=settings,
            )

        rendered = buffer.getvalue()
        self.assertIn("일중 손실 브레이크 상태", rendered)
        self.assertIn("daily pnl brake: BUY_PAUSE", rendered)
        self.assertIn("신규 BUY 가능 여부: NO", rendered)
        self.assertIn("-120,000원", rendered)
        self.assertTrue(rendered.endswith("\n\n"))


if __name__ == "__main__":
    unittest.main()
