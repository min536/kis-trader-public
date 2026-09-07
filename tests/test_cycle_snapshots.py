from __future__ import annotations

import unittest

from app.reporting.cycle_snapshots import build_cycle_snapshot


class CycleSnapshotIntegrityWarningTests(unittest.TestCase):
    def _build_snapshot(self, **overrides):
        kwargs = {
            "cycle_id": 1,
            "timestamp": "2026-04-24T09:10:00+09:00",
            "environment": "mock",
            "market_session": None,
            "portfolio_snapshot": None,
            "scan_results": [],
            "selected_buy_candidate": None,
            "selected_sell_candidate": None,
            "sell_watch_final_review": None,
            "sell_analysis_results": [],
            "selection_details": {},
            "buy_execution_snapshot": None,
            "buy_position_sizing": None,
            "sell_position_sizing": None,
            "buy_risk_guard": None,
            "sell_risk_guard": None,
            "rebalance_preview": None,
            "scheduler_state": None,
            "daily_pnl_brake_state": None,
            "regime_state": None,
            "runtime_state": {},
            "observed_market_snapshots": {},
            "error": None,
            "buy_scan_requested_count": 5,
            "buy_scan_evaluated_count": 0,
        }
        kwargs.update(overrides)
        return build_cycle_snapshot(**kwargs)

    def test_warns_when_buy_scan_requested_but_not_evaluated_without_reason(self) -> None:
        snapshot = self._build_snapshot()

        self.assertIn(
            "BUY scan 요청 수는 있는데 실제 평가 수가 0입니다.",
            snapshot["integrity_warnings"],
        )

    def test_suppresses_empty_buy_scan_warning_during_rate_limit_backoff(self) -> None:
        snapshot = self._build_snapshot(
            rate_limit_triggered=True,
            rate_limit_source="buy_scan",
        )

        self.assertNotIn(
            "BUY scan 요청 수는 있는데 실제 평가 수가 0입니다.",
            snapshot["integrity_warnings"],
        )


if __name__ == "__main__":
    unittest.main()
