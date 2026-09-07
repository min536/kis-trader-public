from __future__ import annotations

import unittest
from types import SimpleNamespace

from app import main as main_module


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        target_symbols=("005930", "000660", "035420"),
        buy_rule_enable_intraday_pullback=True,
        buy_rule_enable_rebound_from_low=True,
        buy_rule_enable_controlled_down_day=True,
        buy_rule_enable_gap_down_open=True,
        buy_rule_enable_range_recovery=True,
        buy_rule_enable_live_volume_rank=True,
        buy_rule_enable_live_volume_power_rank=True,
        buy_rule_rebound_from_low_pct=0.8,
        buy_rule_controlled_down_day_min=-1.5,
        buy_rule_controlled_down_day_max=-0.1,
        buy_rule_gap_down_open_min_pct=-2.0,
        buy_rule_gap_down_open_max_pct=-0.2,
        buy_rule_range_recovery_min_ratio=0.35,
        buy_rule_required_pass_count=2,
        sell_enable=True,
        sell_stop_loss_pct=-2.0,
        sell_take_profit_pct=1.5,
        sell_trailing_stop_pct=1.0,
        sell_rule_enable_live_leadership_loss=True,
        sell_rule_enable_live_power_breakdown=True,
        use_cost_aware_pnl=True,
        buy_fee_bps=1.0,
        sell_fee_bps=1.0,
        sell_tax_bps=18.0,
        buy_slippage_bps=3.0,
        sell_slippage_bps=3.0,
        buy_scan_shallow_top_k=2,
        buy_scan_deep_eval_limit=1,
        scan_symbols_max_per_cycle=3,
        sell_check_interval_seconds=20,
        buy_scan_interval_seconds=180,
        adaptive_midday_enabled=True,
        adaptive_midday_window="11:00-13:00",
        adaptive_midday_buy_scan_interval_seconds=240,
        adaptive_midday_sell_check_interval_seconds=25,
        adaptive_midday_scan_symbols_max_per_cycle=24,
        adaptive_midday_buy_scan_deep_eval_limit=4,
        degraded_mode_enabled=True,
        degraded_mode_rate_limit_hits_in_10m=3,
        degraded_mode_consecutive_backoff_cycles=4,
        degraded_mode_duration_seconds=600,
        degraded_mode_buy_scan_interval_seconds=300,
        degraded_mode_sell_check_interval_seconds=30,
        degraded_mode_scan_symbols_max_per_cycle=16,
        degraded_mode_buy_scan_deep_eval_limit=2,
        degraded_mode_sell_watch_max_holdings_per_tick=4,
    )


class ScanOnlyDiagnosticTests(unittest.TestCase):
    def test_build_scan_only_diagnostic_portfolio_snapshot_derives_position_values(self) -> None:
        snapshot = main_module._build_scan_only_diagnostic_portfolio_snapshot(
            settings=_settings(),
            symbols=("111111", "222222"),
        )

        first, second = snapshot.positions
        self.assertEqual(first.symbol, "111111")
        self.assertEqual(second.symbol, "222222")
        self.assertEqual(first.market_value, first.holding_qty * first.current_price)
        self.assertEqual(
            first.gross_pnl,
            (first.current_price - first.average_cost) * first.holding_qty,
        )
        self.assertEqual(
            snapshot.total_evaluation_amount,
            snapshot.cash_total
            + sum(position.market_value for position in snapshot.positions),
        )

    def test_build_scan_only_diagnostic_portfolio_snapshot_avoids_duplicate_fallback_symbol(self) -> None:
        snapshot = main_module._build_scan_only_diagnostic_portfolio_snapshot(
            settings=_settings(),
            symbols=("005930",),
        )

        self.assertEqual([position.symbol for position in snapshot.positions], ["005930", "000660"])

    def test_build_scan_only_runtime_mode_preview_covers_all_modes(self) -> None:
        preview = main_module._build_scan_only_runtime_mode_preview(
            settings=_settings()
        )

        labels = [row["label"] for row in preview]
        self.assertEqual(labels, ["normal", "adaptive_midday", "degraded"])
        self.assertEqual(preview[0]["mode"], "normal")
        self.assertEqual(preview[1]["mode"], "midday")
        self.assertEqual(preview[2]["mode"], "degraded")

    def test_build_scan_only_diagnostic_summary_flags_snapshot_fallback(self) -> None:
        summary = main_module._build_scan_only_diagnostic_summary(
            runtime_rate_control={"mode": "degraded"},
            snapshot_info={"used": False, "detail": "refresh_stopped_beyond_ttl"},
            daily_pnl_brake_state={"status": "WARNING"},
            buy_scan_summary={
                "requested_symbols": ("005930", "000660"),
                "deep_eval_symbols": ("005930",),
            },
            sell_analysis_results=(),
            fallback_reason="token issue",
        )

        self.assertTrue(summary["rate_control_engaged"])
        self.assertFalse(summary["snapshot_used"])
        self.assertTrue(summary["fallback_occurred"])
        self.assertEqual(summary["daily_pnl_brake_state"], "WARNING")
        self.assertEqual(summary["buy_scan_requested_count"], 2)
        self.assertEqual(summary["buy_scan_deep_eval_count"], 1)

    def test_buy_pre_gating_excludes_configured_and_runtime_untradable_symbols(self) -> None:
        settings = SimpleNamespace(buy_excluded_symbols=("252710",))
        state = {
            "buy_untradable_symbols_today": ["123456"],
            "recent_market_snapshots_by_symbol": {},
        }

        pre_gating = main_module._build_buy_scan_pre_gating(
            candidate_symbols=("252710", "123456"),
            state=state,
            settings=settings,
            portfolio_snapshot=None,
            daily_pnl_brake_state=None,
            regime_state=None,
            mode="trade",
        )
        normalized = main_module._normalize_pre_gating_payload(pre_gating)

        self.assertEqual(pre_gating["allowed_symbols"], ())
        self.assertEqual(
            pre_gating["reason_counts"],
            {"excluded_symbol": 1, "untradable_today": 1},
        )
        self.assertEqual(
            normalized["reasons_by_symbol"],
            {"252710": "excluded_symbol", "123456": "untradable_today"},
        )
        self.assertEqual(
            main_module._normalize_buy_funnel_reason("untradable_today"),
            "untradable_symbol",
        )


if __name__ == "__main__":
    unittest.main()
