from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.portfolio.schema import PortfolioPosition, PortfolioSnapshot
from app.reporting import performance as performance_module
from app.portfolio import equity_state as equity_module
from app.risk import pnl_brake


class DailyPnlBrakeEquityTests(unittest.TestCase):
    def test_build_account_state_uses_cash_total_for_deployment_invariant_equity(self) -> None:
        snapshot = PortfolioSnapshot(
            positions=(
                PortfolioPosition(
                    symbol="005930",
                    name="Samsung Electronics",
                    holding_qty=100,
                    average_cost=70000,
                    current_price=70000,
                    market_value=7_000_000,
                    gross_pnl=0,
                    gross_pnl_pct=0.0,
                    has_position=True,
                ),
            ),
            cash_total=93_000_000,
            cash_orderable=8_000_000,
            cash_next_day=93_000_000,
            total_evaluation_amount=100_000_000,
        )

        account_state = equity_module.build_account_state_payload(
            portfolio_snapshot=snapshot,
            sell_analysis_results=(),
        )

        self.assertEqual(account_state["operating_equity_krw"], 15_000_000)
        self.assertEqual(account_state["deployment_invariant_equity_krw"], 100_000_000)
        self.assertEqual(account_state["deployment_invariant_cash_leg_krw"], 93_000_000)

    def test_build_account_state_prefers_next_day_cash_after_same_day_sell(self) -> None:
        snapshot = PortfolioSnapshot(
            positions=(
                PortfolioPosition(
                    symbol="373220",
                    name="LG Energy Solution",
                    holding_qty=10,
                    average_cost=300000,
                    current_price=305000,
                    market_value=3_050_000,
                    gross_pnl=50_000,
                    gross_pnl_pct=1.67,
                    has_position=True,
                ),
            ),
            cash_total=2_000_000,
            cash_orderable=500_000,
            cash_next_day=11_500_000,
            total_evaluation_amount=14_550_000,
        )

        account_state = equity_module.build_account_state_payload(
            portfolio_snapshot=snapshot,
            sell_analysis_results=(),
        )

        self.assertEqual(account_state["deployment_invariant_cash_leg_krw"], 11_500_000)
        self.assertEqual(account_state["deployment_invariant_equity_krw"], 14_550_000)

    def test_build_account_state_does_not_cap_deployment_invariant_to_raw_balance(self) -> None:
        # KIS API's tot_evlu_amt = cash_orderable + holdings, which excludes T+2-pending
        # settlement cash in dnca_tot_amt. We must NOT cap deployment_invariant_equity to
        # tot_evlu_amt; the T+2 cash is real money that will settle.
        snapshot = PortfolioSnapshot(
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

        account_state = equity_module.build_account_state_payload(
            portfolio_snapshot=snapshot,
            sell_analysis_results=(),
        )

        self.assertEqual(account_state["operating_equity_krw"], 109_697_187)
        # deployment_invariant = max(57_409_505, 25_542_687, 25_542_687) + 84_154_500 = 141_564_005
        self.assertEqual(account_state["deployment_invariant_equity_krw"], 141_564_005)
        self.assertEqual(account_state["deployment_invariant_cash_leg_krw"], 57_409_505)

    def test_select_risk_managed_current_equity_falls_back_to_raw_when_snapshot_is_unreliable(self) -> None:
        account_state = {
            "deployment_invariant_equity_krw": 141_564_005,
            "operating_equity_krw": 109_697_187,
            "cash_orderable_krw": 25_542_687,
            "cash_next_day_krw": 25_542_687,
        }

        risk_equity_krw = performance_module.select_risk_managed_current_equity_krw(
            account_state=account_state,
            raw_balance_total_evaluation_amount_krw=109_697_187,
        )

        self.assertEqual(risk_equity_krw, 109_697_187)

    def test_build_performance_report_persists_risk_managed_equity(self) -> None:
        snapshot = PortfolioSnapshot(
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

        with (
            mock.patch.object(
                performance_module,
                "_iter_operating_order_records",
                return_value=[],
            ),
            mock.patch.object(
                performance_module,
                "_load_performance_snapshots",
                return_value=[],
            ),
            mock.patch.object(
                performance_module,
                "get_korean_now",
                return_value=datetime(2026, 4, 20, 15, 5, 0, tzinfo=performance_module.KOREA_TZ),
            ),
        ):
            report = performance_module.build_performance_report(
                portfolio_snapshot=snapshot,
                sell_analysis_results=(),
                benchmark_snapshot=None,
            )

        self.assertEqual(report["equity"]["total_equity_krw"], 109_697_187)
        self.assertEqual(report["account_summary"]["current_equity_krw"], 109_697_187)

    def test_daily_pnl_brake_ignores_legacy_orderable_cash_snapshot_after_large_buy(self) -> None:
        today = datetime(2026, 4, 18, 13, 21, 0, tzinfo=equity_module.KOREA_TZ)
        history = [
            {
                "timestamp": "2026-04-18T09:00:00+09:00",
                "total_equity_krw": 75_000_000,
            },
            {
                "timestamp": "2026-04-18T13:00:00+09:00",
                "total_equity_krw": 100_000_000,
                "equity_basis": equity_module.DEPLOYMENT_INVARIANT_EQUITY_BASIS,
                "cash_deployment_invariant": True,
            },
        ]

        with (
            mock.patch.object(
                equity_module,
                "_load_performance_snapshots",
                return_value=history,
            ),
            mock.patch.object(equity_module, "get_korean_now", return_value=today),
        ):
            pnl_state = equity_module.build_daily_pnl_state(
                current_equity_krw=100_200_000,
                warning_pct=-1.0,
                buy_pause_pct=-3.0,
                hard_stop_pct=-4.5,
            )

        self.assertTrue(pnl_state["evaluated"])
        self.assertEqual(pnl_state["status"], "NORMAL")
        self.assertEqual(pnl_state["baseline_equity_krw"], 100_000_000)
        self.assertEqual(pnl_state["current_equity_krw"], 100_200_000)
        self.assertTrue(pnl_state["cash_deployment_invariant"])
        self.assertEqual(
            pnl_state["equity_basis"],
            equity_module.DEPLOYMENT_INVARIANT_EQUITY_BASIS,
        )
        self.assertAlmostEqual(float(pnl_state["daily_pnl_pct"]), 0.2, places=2)

    def test_daily_pnl_brake_does_not_penalize_unsettled_same_day_sell_cash(self) -> None:
        today = datetime(2026, 4, 18, 13, 21, 0, tzinfo=equity_module.KOREA_TZ)
        history = [
            {
                "timestamp": "2026-04-18T09:30:00+09:00",
                "total_equity_krw": 100_000_000,
                "equity_basis": equity_module.DEPLOYMENT_INVARIANT_EQUITY_BASIS,
                "cash_deployment_invariant": True,
            },
        ]

        with (
            mock.patch.object(
                equity_module,
                "_load_performance_snapshots",
                return_value=history,
            ),
            mock.patch.object(equity_module, "get_korean_now", return_value=today),
        ):
            pnl_state = equity_module.build_daily_pnl_state(
                current_equity_krw=100_150_000,
                warning_pct=-1.0,
                buy_pause_pct=-3.0,
                hard_stop_pct=-4.5,
            )

        self.assertTrue(pnl_state["evaluated"])
        self.assertEqual(pnl_state["status"], "NORMAL")
        self.assertAlmostEqual(float(pnl_state["daily_pnl_pct"]), 0.15, places=2)


    def test_daily_pnl_brake_excludes_pre_settlement_snapshot_from_baseline(self) -> None:
        # Snapshots before 09:30 KST are excluded because KIS T+2 settlement processes
        # in the first ~20 min after market open, temporarily inflating dnca_tot_amt.
        # Using a pre-settlement snapshot as the baseline manufactures a phantom loss.
        today = datetime(2026, 4, 20, 13, 0, 0, tzinfo=equity_module.KOREA_TZ)
        history = [
            {
                "timestamp": "2026-04-20T02:17:00+09:00",
                "total_equity_krw": 141_516_265,
                "equity_basis": equity_module.DEPLOYMENT_INVARIANT_EQUITY_BASIS,
            },
            {
                "timestamp": "2026-04-20T09:15:00+09:00",
                "total_equity_krw": 136_000_000,
                "equity_basis": equity_module.DEPLOYMENT_INVARIANT_EQUITY_BASIS,
            },
            {
                "timestamp": "2026-04-20T09:30:00+09:00",
                "total_equity_krw": 132_000_000,
                "equity_basis": equity_module.DEPLOYMENT_INVARIANT_EQUITY_BASIS,
            },
        ]

        with (
            mock.patch.object(
                equity_module,
                "_load_performance_snapshots",
                return_value=history,
            ),
            mock.patch.object(equity_module, "get_korean_now", return_value=today),
        ):
            pnl_state = equity_module.build_daily_pnl_state(
                current_equity_krw=132_500_000,
                warning_pct=-1.0,
                buy_pause_pct=-3.0,
                hard_stop_pct=-4.5,
            )

        self.assertTrue(pnl_state["evaluated"])
        self.assertEqual(pnl_state["status"], "NORMAL")
        # Baseline must be the 09:30 snapshot (132M), not 02:17 (141.5M) nor 09:15 (136M).
        self.assertEqual(pnl_state["baseline_equity_krw"], 132_000_000)
        self.assertAlmostEqual(float(pnl_state["daily_pnl_pct"]), 0.379, places=2)

    def test_daily_pnl_brake_ignores_inflated_snapshot_not_explained_by_settlement_gap(self) -> None:
        today = datetime(2026, 4, 20, 14, 25, 0, tzinfo=equity_module.KOREA_TZ)
        history = [
            {
                "timestamp": "2026-04-20T09:30:11+09:00",
                "total_equity_krw": 109_858_978,
                "raw_balance_total_evaluation_amount_krw": 109_858_978,
                "cash_orderable_krw": 25_542_687,
                "cash_next_day_krw": 25_542_687,
                "equity_basis": equity_module.DEPLOYMENT_INVARIANT_EQUITY_BASIS,
                "cash_deployment_invariant": True,
            },
            {
                "timestamp": "2026-04-20T10:10:42+09:00",
                "total_equity_krw": 131_665_545,
                "raw_balance_total_evaluation_amount_krw": 109_424_628,
                "cash_orderable_krw": 35_167_238,
                "cash_next_day_krw": 25_542_687,
                "equity_basis": equity_module.DEPLOYMENT_INVARIANT_EQUITY_BASIS,
                "cash_deployment_invariant": True,
            },
        ]

        with (
            mock.patch.object(
                equity_module,
                "_load_performance_snapshots",
                return_value=history,
            ),
            mock.patch.object(equity_module, "get_korean_now", return_value=today),
        ):
            pnl_state = equity_module.build_daily_pnl_state(
                current_equity_krw=109_680_000,
                warning_pct=-2.0,
                buy_pause_pct=-3.0,
                hard_stop_pct=-4.5,
            )

        self.assertTrue(pnl_state["evaluated"])
        self.assertEqual(pnl_state["status"], "NORMAL")
        self.assertEqual(pnl_state["baseline_equity_krw"], 109_858_978)
        self.assertAlmostEqual(float(pnl_state["daily_pnl_pct"]), -0.16, places=2)

    def test_current_drawdown_ignores_inflated_snapshot_not_explained_by_settlement_gap(self) -> None:
        today = datetime(2026, 4, 20, 14, 25, 0, tzinfo=equity_module.KOREA_TZ)
        history = [
            {
                "timestamp": "2026-04-20T09:30:11+09:00",
                "total_equity_krw": 109_858_978,
                "raw_balance_total_evaluation_amount_krw": 109_858_978,
                "cash_orderable_krw": 25_542_687,
                "cash_next_day_krw": 25_542_687,
                "equity_basis": equity_module.DEPLOYMENT_INVARIANT_EQUITY_BASIS,
                "cash_deployment_invariant": True,
            },
            {
                "timestamp": "2026-04-20T10:10:42+09:00",
                "total_equity_krw": 131_665_545,
                "raw_balance_total_evaluation_amount_krw": 109_424_628,
                "cash_orderable_krw": 35_167_238,
                "cash_next_day_krw": 25_542_687,
                "equity_basis": equity_module.DEPLOYMENT_INVARIANT_EQUITY_BASIS,
                "cash_deployment_invariant": True,
            },
        ]

        with (
            mock.patch.object(
                equity_module,
                "_load_performance_snapshots",
                return_value=history,
            ),
            mock.patch.object(equity_module, "get_korean_now", return_value=today),
        ):
            drawdown_state = equity_module.build_current_drawdown_state(
                current_equity_krw=109_680_000,
            )

        self.assertTrue(drawdown_state["evaluated"])
        self.assertAlmostEqual(float(drawdown_state["current_drawdown_pct"]), -0.16, places=2)
        self.assertAlmostEqual(float(drawdown_state["max_drawdown_pct"]), -0.16, places=2)


class DailyPnlBrakeCrossValidationTests(unittest.TestCase):
    """Cross-validation suppresses HARD_STOP when operating_equity is healthy."""

    def _make_settings(self) -> SimpleNamespace:
        return SimpleNamespace(
            enable_daily_pnl_brake=True,
            daily_pnl_warning_pct=-2.0,
            daily_pnl_buy_pause_pct=-3.0,
            daily_pnl_hard_stop_pct=-4.5,
            daily_pnl_cooldown_minutes=10,
        )

    def test_hard_stop_suppressed_when_operating_equity_is_normal(self) -> None:
        # Mirrors the 2026-04-24 incident: deployment_invariant shows -5.55%
        # (baseline captured when cash_orderable was temporarily high after a sell),
        # but operating_equity is only -0.03% down. Brake must be suppressed.
        baseline = 110_983_141
        deploy_invariant = 104_800_000  # -5.55%
        operating = 110_950_000         # -0.03%

        fake_pnl_state = {
            "evaluated": True,
            "status": "HARD_STOP",
            "daily_pnl_pct": round((deploy_invariant / baseline - 1.0) * 100, 2),
            "baseline_equity_krw": baseline,
            "current_equity_krw": deploy_invariant,
            "equity_basis": performance_module.DEPLOYMENT_INVARIANT_EQUITY_BASIS,
            "cash_deployment_invariant": True,
            "reason": "일중 손실률이 hard stop 기준을 하회해 신규 BUY를 중단합니다.",
        }
        now = datetime(2026, 4, 24, 10, 0, 0, tzinfo=timezone.utc)

        with (
            mock.patch("app.risk.pnl_brake.build_daily_pnl_state", return_value=fake_pnl_state),
            mock.patch("app.risk.pnl_brake.get_korean_now", return_value=now),
        ):
            result = pnl_brake.build_daily_pnl_brake_state(
                state={},
                settings=self._make_settings(),
                current_equity_krw=deploy_invariant,
                operating_equity_krw=operating,
            )

        self.assertFalse(result["buy_paused"])
        self.assertNotEqual(result.get("status"), "HARD_STOP")
        self.assertTrue(result.get("brake_suppressed_by_cross_validation"))
        # pnl_pct should now reflect operating equity, not deployment_invariant
        self.assertGreater(float(result["daily_pnl_pct"]), -1.0)

    def test_cross_validation_clears_stale_false_pause_state(self) -> None:
        baseline = 110_983_141
        deploy_invariant = 104_800_000
        operating = 110_950_000
        fake_pnl_state = {
            "evaluated": True,
            "status": "HARD_STOP",
            "daily_pnl_pct": round((deploy_invariant / baseline - 1.0) * 100, 2),
            "baseline_equity_krw": baseline,
            "current_equity_krw": deploy_invariant,
            "equity_basis": performance_module.DEPLOYMENT_INVARIANT_EQUITY_BASIS,
            "cash_deployment_invariant": True,
            "reason": "일중 손실률이 hard stop 기준을 하회해 신규 BUY를 중단합니다.",
        }
        now = datetime(2026, 4, 24, 10, 0, 0, tzinfo=timezone.utc)
        state = {
            "daily_pnl_pause_until": "2026-04-24T10:10:00+00:00",
            "daily_pnl_pause_state": "HARD_STOP",
            "daily_pnl_pause_reason": "stale false hard stop",
        }

        with (
            mock.patch("app.risk.pnl_brake.build_daily_pnl_state", return_value=fake_pnl_state),
            mock.patch("app.risk.pnl_brake.get_korean_now", return_value=now),
        ):
            result = pnl_brake.build_daily_pnl_brake_state(
                state=state,
                settings=self._make_settings(),
                current_equity_krw=deploy_invariant,
                operating_equity_krw=operating,
            )

        self.assertFalse(result["buy_paused"])
        self.assertIsNone(result.get("pause_state"))
        self.assertIsNone(state.get("daily_pnl_pause_until"))
        self.assertIsNone(state.get("daily_pnl_pause_state"))
        self.assertTrue(result.get("brake_suppressed_by_cross_validation"))

    def test_hard_stop_fires_when_both_equities_confirm_loss(self) -> None:
        baseline = 110_000_000
        deploy_invariant = 104_000_000   # -5.45%
        operating = 104_100_000          # -5.36% — both below -4.5% hard_stop

        fake_pnl_state = {
            "evaluated": True,
            "status": "HARD_STOP",
            "daily_pnl_pct": round((deploy_invariant / baseline - 1.0) * 100, 2),
            "baseline_equity_krw": baseline,
            "current_equity_krw": deploy_invariant,
            "equity_basis": performance_module.DEPLOYMENT_INVARIANT_EQUITY_BASIS,
            "cash_deployment_invariant": True,
            "reason": "일중 손실률이 hard stop 기준을 하회해 신규 BUY를 중단합니다.",
        }
        now = datetime(2026, 4, 24, 10, 0, 0, tzinfo=timezone.utc)

        with (
            mock.patch("app.risk.pnl_brake.build_daily_pnl_state", return_value=fake_pnl_state),
            mock.patch("app.risk.pnl_brake.get_korean_now", return_value=now),
        ):
            result = pnl_brake.build_daily_pnl_brake_state(
                state={},
                settings=self._make_settings(),
                current_equity_krw=deploy_invariant,
                operating_equity_krw=operating,
            )

        self.assertTrue(result["buy_paused"])
        self.assertEqual(result.get("pause_state"), "HARD_STOP")
        self.assertNotIn("brake_suppressed_by_cross_validation", result)

    def test_warns_when_baseline_is_much_higher_than_operating_equity(self) -> None:
        baseline = 110_000_000
        deploy_invariant = 110_100_000
        operating = 100_000_000
        fake_pnl_state = {
            "evaluated": True,
            "status": "NORMAL",
            "daily_pnl_pct": round((deploy_invariant / baseline - 1.0) * 100, 2),
            "baseline_equity_krw": baseline,
            "current_equity_krw": deploy_invariant,
            "equity_basis": performance_module.DEPLOYMENT_INVARIANT_EQUITY_BASIS,
            "cash_deployment_invariant": True,
            "reason": "정상 범위입니다.",
        }
        now = datetime(2026, 4, 24, 10, 0, 0, tzinfo=timezone.utc)
        state: dict[str, object] = {}

        with (
            mock.patch("app.risk.pnl_brake.build_daily_pnl_state", return_value=fake_pnl_state),
            mock.patch("app.risk.pnl_brake.get_korean_now", return_value=now),
            mock.patch("builtins.print") as mocked_print,
        ):
            result = pnl_brake.build_daily_pnl_brake_state(
                state=state,
                settings=self._make_settings(),
                current_equity_krw=deploy_invariant,
                operating_equity_krw=operating,
            )

        self.assertFalse(result["buy_paused"])
        self.assertEqual(result.get("status"), "NORMAL")
        self.assertEqual(result.get("baseline_vs_operating_gap_pct"), 10.0)
        self.assertIn("baseline이 운영 equity보다", result.get("baseline_abnormal_warning"))
        self.assertTrue(state.get("daily_pnl_baseline_abnormal_warned"))
        mocked_print.assert_called_once()

    def test_manual_buy_pause_override_clears_pause_but_not_hard_stop(self) -> None:
        baseline = 110_000_000
        deploy_invariant = 106_000_000
        operating = 106_100_000
        fake_pnl_state = {
            "evaluated": True,
            "status": "BUY_PAUSE",
            "daily_pnl_pct": round((deploy_invariant / baseline - 1.0) * 100, 2),
            "baseline_equity_krw": baseline,
            "current_equity_krw": deploy_invariant,
            "equity_basis": performance_module.DEPLOYMENT_INVARIANT_EQUITY_BASIS,
            "cash_deployment_invariant": True,
            "reason": "일중 손실률이 buy pause 기준을 하회해 신규 BUY를 잠시 중단합니다.",
        }
        now = datetime(2026, 4, 24, 10, 0, 0, tzinfo=timezone.utc)
        state = {
            "daily_pnl_pause_until": "2026-04-24T10:10:00+00:00",
            "daily_pnl_pause_state": "BUY_PAUSE",
            "daily_pnl_pause_reason": "existing pause",
        }

        with (
            mock.patch("app.risk.pnl_brake.build_daily_pnl_state", return_value=fake_pnl_state),
            mock.patch("app.risk.pnl_brake.get_korean_now", return_value=now),
            mock.patch("app.risk.pnl_brake.is_manual_buy_pause_override_active", return_value=True),
        ):
            result = pnl_brake.build_daily_pnl_brake_state(
                state=state,
                settings=self._make_settings(),
                current_equity_krw=deploy_invariant,
                operating_equity_krw=operating,
            )

        self.assertFalse(result["buy_paused"])
        self.assertEqual(result.get("status"), "NORMAL")
        self.assertTrue(result.get("manual_buy_pause_override"))
        self.assertIsNone(state.get("daily_pnl_pause_until"))
        self.assertIsNone(state.get("daily_pnl_pause_state"))

    def test_manual_buy_pause_override_does_not_clear_hard_stop(self) -> None:
        baseline = 110_000_000
        deploy_invariant = 104_000_000
        operating = 104_100_000
        fake_pnl_state = {
            "evaluated": True,
            "status": "HARD_STOP",
            "daily_pnl_pct": round((deploy_invariant / baseline - 1.0) * 100, 2),
            "baseline_equity_krw": baseline,
            "current_equity_krw": deploy_invariant,
            "equity_basis": performance_module.DEPLOYMENT_INVARIANT_EQUITY_BASIS,
            "cash_deployment_invariant": True,
            "reason": "일중 손실률이 hard stop 기준을 하회해 신규 BUY를 중단합니다.",
        }
        now = datetime(2026, 4, 24, 10, 0, 0, tzinfo=timezone.utc)

        with (
            mock.patch("app.risk.pnl_brake.build_daily_pnl_state", return_value=fake_pnl_state),
            mock.patch("app.risk.pnl_brake.get_korean_now", return_value=now),
            mock.patch("app.risk.pnl_brake.is_manual_buy_pause_override_active", return_value=True),
        ):
            result = pnl_brake.build_daily_pnl_brake_state(
                state={},
                settings=self._make_settings(),
                current_equity_krw=deploy_invariant,
                operating_equity_krw=operating,
            )

        self.assertTrue(result["buy_paused"])
        self.assertEqual(result.get("pause_state"), "HARD_STOP")
        self.assertFalse(result.get("manual_buy_pause_override", False))


class DailyPnlBrakeHelperCharacterizationTests(unittest.TestCase):
    def _make_settings(self) -> SimpleNamespace:
        return SimpleNamespace(
            enable_daily_pnl_brake=True,
            daily_pnl_warning_pct=-2.0,
            daily_pnl_buy_pause_pct=-3.0,
            daily_pnl_hard_stop_pct=-4.5,
            daily_pnl_cooldown_minutes=10,
        )

    def _fake_pnl_state(
        self,
        status: str,
        *,
        pnl_pct: float = 0.0,
        current_equity_krw: int = 100_000_000,
    ) -> dict[str, object]:
        return {
            "evaluated": True,
            "status": status,
            "daily_pnl_pct": pnl_pct,
            "baseline_equity_krw": 100_000_000,
            "current_equity_krw": current_equity_krw,
            "equity_basis": performance_module.DEPLOYMENT_INVARIANT_EQUITY_BASIS,
            "cash_deployment_invariant": True,
            "reason": f"{status} reason",
        }

    def test_daily_pnl_brake_display_status_mapping(self) -> None:
        cases = (
            (None, "DATA_INSUFFICIENT"),
            ({}, "DATA_INSUFFICIENT"),
            ({"status": "NORMAL"}, "OK"),
            ({"status": "WARNING"}, "WARNING"),
            ({"status": "BUY_PAUSE"}, "BUY_PAUSE"),
            ({"status": "HARD_STOP"}, "HARD_STOP_READY"),
            ({"status": "OFF"}, "OFF"),
        )

        for brake_state, expected in cases:
            with self.subTest(brake_state=brake_state):
                self.assertEqual(
                    pnl_brake.daily_pnl_brake_display_status(brake_state),
                    expected,
                )

    def test_clear_daily_pnl_pause_if_expired_clears_expired_and_invalid_pause(self) -> None:
        now = datetime(2026, 5, 21, 10, 0, 0, tzinfo=timezone.utc)
        cases = (
            ("2026-05-21T09:59:00+00:00", True),
            ("not-a-date", True),
            ("2026-05-21T10:05:00+00:00", False),
        )

        for pause_until, should_clear in cases:
            with self.subTest(pause_until=pause_until):
                state = {
                    "daily_pnl_pause_until": pause_until,
                    "daily_pnl_pause_state": "BUY_PAUSE",
                    "daily_pnl_pause_reason": "existing pause",
                }
                with mock.patch("app.risk.pnl_brake.get_korean_now", return_value=now):
                    pnl_brake.clear_daily_pnl_pause_if_expired(state)

                if should_clear:
                    self.assertIsNone(state.get("daily_pnl_pause_until"))
                    self.assertIsNone(state.get("daily_pnl_pause_state"))
                    self.assertIsNone(state.get("daily_pnl_pause_reason"))
                else:
                    self.assertEqual(state["daily_pnl_pause_until"], pause_until)
                    self.assertEqual(state["daily_pnl_pause_state"], "BUY_PAUSE")
                    self.assertEqual(state["daily_pnl_pause_reason"], "existing pause")

    def test_manual_buy_pause_override_path_uses_runtime_state_scope(self) -> None:
        runtime_path = Path("/tmp/runtime_state_mock_account.json")

        with mock.patch("app.risk.pnl_brake.get_runtime_state_path", return_value=runtime_path):
            path = pnl_brake.manual_buy_pause_override_path()

        self.assertEqual(path, Path("/tmp/manual_buy_pause_override_mock_account.json"))

    def test_manual_buy_pause_override_active_file_behavior(self) -> None:
        now = datetime(2026, 5, 21, 10, 0, 0, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "manual_buy_pause_override.json"

            with (
                mock.patch("app.risk.pnl_brake.manual_buy_pause_override_path", return_value=path),
                mock.patch("app.risk.pnl_brake.get_korean_now", return_value=now),
            ):
                self.assertFalse(pnl_brake.is_manual_buy_pause_override_active())

                path.write_text("{", encoding="utf-8")
                self.assertFalse(pnl_brake.is_manual_buy_pause_override_active())

                path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
                self.assertFalse(pnl_brake.is_manual_buy_pause_override_active())

                path.write_text(json.dumps({"enabled": False}), encoding="utf-8")
                self.assertFalse(pnl_brake.is_manual_buy_pause_override_active())

                path.write_text(json.dumps({"enabled": True, "expires_at": "bad-date"}), encoding="utf-8")
                self.assertFalse(pnl_brake.is_manual_buy_pause_override_active())

                path.write_text(
                    json.dumps({"enabled": True, "expires_at": "2026-05-21T09:59:00+00:00"}),
                    encoding="utf-8",
                )
                self.assertFalse(pnl_brake.is_manual_buy_pause_override_active())

                path.write_text(
                    json.dumps({"enabled": True, "expires_at": "2026-05-21T10:01:00+00:00"}),
                    encoding="utf-8",
                )
                self.assertTrue(pnl_brake.is_manual_buy_pause_override_active())

                path.write_text(json.dumps({"enabled": True}), encoding="utf-8")
                self.assertTrue(pnl_brake.is_manual_buy_pause_override_active())

    def test_build_daily_pnl_brake_state_status_mutations(self) -> None:
        now = datetime(2026, 5, 21, 10, 0, 0, tzinfo=timezone.utc)
        cases = (
            ("NORMAL", "OK", False, None, 100_000_000, 0.0),
            ("WARNING", "WARNING", False, None, 98_000_000, -2.0),
            ("BUY_PAUSE", "BUY_PAUSE", True, "blocked_daily_pnl_pause", 96_800_000, -3.2),
            ("HARD_STOP", "HARD_STOP_READY", True, "blocked_daily_pnl_hard_stop", 95_000_000, -5.0),
        )

        for status, display_status, buy_paused, action, equity_krw, pnl_pct in cases:
            with self.subTest(status=status):
                state: dict[str, object] = {}
                with (
                    mock.patch(
                        "app.risk.pnl_brake.build_daily_pnl_state",
                        return_value=self._fake_pnl_state(
                            status,
                            pnl_pct=pnl_pct,
                            current_equity_krw=equity_krw,
                        ),
                    ),
                    mock.patch("app.risk.pnl_brake.get_korean_now", return_value=now),
                    mock.patch("app.risk.pnl_brake.is_manual_buy_pause_override_active", return_value=False),
                ):
                    result = pnl_brake.build_daily_pnl_brake_state(
                        state=state,
                        settings=self._make_settings(),
                        current_equity_krw=equity_krw,
                        operating_equity_krw=equity_krw,
                    )

                self.assertEqual(result["display_status"], display_status)
                self.assertEqual(state["current_brake_state"], display_status)
                self.assertEqual(result["buy_paused"], buy_paused)
                self.assertEqual(result["action"], action)
                if buy_paused:
                    self.assertEqual(result["pause_state"], status)
                    self.assertEqual(state["daily_pnl_pause_state"], status)
                    self.assertEqual(result["remaining_minutes"], 10)
                else:
                    self.assertIsNone(result["pause_state"])
                    self.assertNotIn("daily_pnl_pause_state", state)

    def test_build_daily_pnl_brake_state_disabled_sets_off_without_evaluation(self) -> None:
        settings = self._make_settings()
        settings.enable_daily_pnl_brake = False
        state: dict[str, object] = {}

        with mock.patch("app.risk.pnl_brake.build_daily_pnl_state") as mocked_build:
            result = pnl_brake.build_daily_pnl_brake_state(
                state=state,
                settings=settings,
                current_equity_krw=100_000_000,
                operating_equity_krw=100_000_000,
            )

        mocked_build.assert_not_called()
        self.assertEqual(state["current_brake_state"], "OFF")
        self.assertEqual(result["status"], "OFF")
        self.assertFalse(result["buy_paused"])

    def test_build_daily_pnl_brake_state_sends_alert_on_escalation_only(self) -> None:
        now = datetime(2026, 5, 21, 10, 0, 0, tzinfo=timezone.utc)
        state = {"last_notified_pnl_state": "WARNING"}

        with (
            mock.patch("app.risk.pnl_brake.build_daily_pnl_state", return_value=self._fake_pnl_state("BUY_PAUSE", pnl_pct=-3.2)),
            mock.patch("app.risk.pnl_brake.get_korean_now", return_value=now),
            mock.patch("app.risk.pnl_brake.is_manual_buy_pause_override_active", return_value=False),
            mock.patch("app.risk.pnl_brake.get_account_scope_context", return_value={"account_signature": "mock-account"}),
            mock.patch("app.risk.pnl_brake._notify_brake_escalation") as mocked_send,
        ):
            pnl_brake.build_daily_pnl_brake_state(
                state=state,
                settings=self._make_settings(),
                current_equity_krw=96_800_000,
                operating_equity_krw=96_800_000,
            )

        mocked_send.assert_called_once()
        self.assertEqual(state["last_notified_pnl_state"], "BUY_PAUSE")
        self.assertIn("mock-account", mocked_send.call_args.args[0])
        self.assertIn("BUY_PAUSE", mocked_send.call_args.args[0])

        with (
            mock.patch("app.risk.pnl_brake.build_daily_pnl_state", return_value=self._fake_pnl_state("WARNING", pnl_pct=-2.2)),
            mock.patch("app.risk.pnl_brake.get_korean_now", return_value=now),
            mock.patch("app.risk.pnl_brake.is_manual_buy_pause_override_active", return_value=False),
            mock.patch("app.risk.pnl_brake._notify_brake_escalation") as mocked_send,
        ):
            pnl_brake.build_daily_pnl_brake_state(
                state=state,
                settings=self._make_settings(),
                current_equity_krw=97_800_000,
                operating_equity_krw=97_800_000,
            )

        mocked_send.assert_not_called()
        self.assertEqual(state["last_notified_pnl_state"], "BUY_PAUSE")


if __name__ == "__main__":
    unittest.main()
