from __future__ import annotations

import contextlib
import os
import unittest
from dataclasses import replace
from unittest import mock

from app.auth.settings import get_settings
from app.auth.settings_fields import (
    build_adaptive_degraded_fields,
    build_buy_rule_fields,
    build_cost_model_fields,
    build_order_discipline_fields,
    build_pnl_brake_regime_fields,
    build_risk_limit_fields,
    build_scan_cadence_fields,
    build_sell_rule_fields,
    build_session_runtime_fields,
    build_targeting_fields,
)


class _EnvIsolationTestCase(unittest.TestCase):
    """Snapshot/clear the env keys a builder reads so defaults stay deterministic."""

    KEYS: tuple[str, ...] = ()

    def setUp(self) -> None:
        self._original = {key: os.environ.get(key) for key in self.KEYS}
        for key in self.KEYS:
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        for key, value in self._original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class BuildTargetingFieldsTests(_EnvIsolationTestCase):
    KEYS = (
        "KIS_TARGET_SYMBOL",
        "SCAN_SYMBOLS",
        "BUY_TARGET_SYMBOLS",
        "BUY_EXCLUDED_SYMBOLS",
    )

    def test_defaults_fall_back_to_target_symbol(self) -> None:
        fields = build_targeting_fields()

        self.assertEqual(fields.symbol, "005930")
        self.assertEqual(fields.target_symbols_source, "KIS_TARGET_SYMBOL(fallback)")
        self.assertEqual(fields.target_symbols_raw, "005930")
        self.assertEqual(fields.target_symbols_split_items, ("005930",))
        self.assertEqual(fields.target_symbols, ("005930",))
        self.assertEqual(fields.buy_excluded_symbols, ())

    def test_scan_symbols_take_priority_and_excluded_are_parsed(self) -> None:
        os.environ["SCAN_SYMBOLS"] = "000660, 005930,000660"
        os.environ["BUY_TARGET_SYMBOLS"] = "035420"
        os.environ["BUY_EXCLUDED_SYMBOLS"] = "005930"

        fields = build_targeting_fields()

        self.assertEqual(fields.target_symbols_source, "SCAN_SYMBOLS")
        self.assertEqual(fields.target_symbols_raw, "000660, 005930,000660")
        self.assertEqual(
            fields.target_symbols_split_items, ("000660", "005930", "000660")
        )
        self.assertEqual(fields.target_symbols, ("000660", "005930"))
        self.assertEqual(fields.buy_excluded_symbols, ("005930",))

    def test_buy_target_symbols_fallback_when_scan_symbols_missing(self) -> None:
        os.environ["BUY_TARGET_SYMBOLS"] = "035420"

        fields = build_targeting_fields()

        self.assertEqual(fields.target_symbols_source, "BUY_TARGET_SYMBOLS")
        self.assertEqual(fields.target_symbols, ("035420",))

    def test_empty_target_symbol_raises(self) -> None:
        os.environ["KIS_TARGET_SYMBOL"] = "  "

        with self.assertRaisesRegex(ValueError, "KIS_TARGET_SYMBOL"):
            build_targeting_fields()


class BuildCostModelFieldsTests(_EnvIsolationTestCase):
    KEYS = (
        "KIS_ORDER_QTY",
        "BUY_FEE_BPS",
        "SELL_FEE_BPS",
        "SELL_TAX_BPS",
        "BUY_SLIPPAGE_BPS",
        "SELL_SLIPPAGE_BPS",
        "EXPECTED_SLIPPAGE_BPS_BASE",
        "EXPECTED_COST_BLOCK_BPS",
        "MIN_NET_EDGE_BPS",
        "MIN_NET_PROFIT_BUFFER_BPS",
        "USE_COST_AWARE_PNL",
        "PERFORMANCE_BENCHMARK_SYMBOL",
    )

    def _snapshot(self) -> tuple[object, ...]:
        fields = build_cost_model_fields()
        return (
            fields.qty,
            fields.buy_fee_bps,
            fields.sell_fee_bps,
            fields.sell_tax_bps,
            fields.buy_slippage_bps,
            fields.sell_slippage_bps,
            fields.expected_slippage_bps_base,
            fields.expected_cost_block_bps,
            fields.min_net_edge_bps,
            fields.min_net_profit_buffer_bps,
            fields.use_cost_aware_pnl,
            fields.performance_benchmark_symbol,
        )

    def test_defaults(self) -> None:
        self.assertEqual(
            self._snapshot(),
            (1, 1.5, 1.5, 15.0, 5.0, 5.0, 5.0, 35.0, 10.0, 20.0, True, ""),
        )

    def test_env_overrides(self) -> None:
        os.environ.update(
            {
                "KIS_ORDER_QTY": "3",
                "BUY_FEE_BPS": "2.0",
                "SELL_FEE_BPS": "2.5",
                "SELL_TAX_BPS": "18.0",
                "BUY_SLIPPAGE_BPS": "6.0",
                "SELL_SLIPPAGE_BPS": "7.0",
                "EXPECTED_SLIPPAGE_BPS_BASE": "8.0",
                "EXPECTED_COST_BLOCK_BPS": "40.0",
                "MIN_NET_EDGE_BPS": "12.0",
                "MIN_NET_PROFIT_BUFFER_BPS": "25.0",
                "USE_COST_AWARE_PNL": "false",
                "PERFORMANCE_BENCHMARK_SYMBOL": "069500",
            }
        )

        self.assertEqual(
            self._snapshot(),
            (3, 2.0, 2.5, 18.0, 6.0, 7.0, 8.0, 40.0, 12.0, 25.0, False, "069500"),
        )

    def test_invariant_violations_raise(self) -> None:
        cases = (
            ({"KIS_ORDER_QTY": "0"}, "KIS_ORDER_QTY"),
            ({"BUY_FEE_BPS": "-1"}, "BUY_FEE_BPS, SELL_FEE_BPS, SELL_TAX_BPS"),
            ({"SELL_TAX_BPS": "-1"}, "BUY_FEE_BPS, SELL_FEE_BPS, SELL_TAX_BPS"),
            ({"BUY_SLIPPAGE_BPS": "-1"}, "BUY_SLIPPAGE_BPS, SELL_SLIPPAGE_BPS"),
            ({"EXPECTED_SLIPPAGE_BPS_BASE": "-1"}, "EXPECTED_SLIPPAGE_BPS_BASE"),
            ({"EXPECTED_COST_BLOCK_BPS": "-1"}, "EXPECTED_COST_BLOCK_BPS"),
            ({"MIN_NET_EDGE_BPS": "-1"}, "MIN_NET_EDGE_BPS"),
            ({"MIN_NET_PROFIT_BUFFER_BPS": "-1"}, "MIN_NET_PROFIT_BUFFER_BPS"),
        )
        for overrides, message_part in cases:
            with self.subTest(overrides=overrides):
                for key in self.KEYS:
                    os.environ.pop(key, None)
                os.environ.update(overrides)
                with self.assertRaisesRegex(ValueError, message_part):
                    build_cost_model_fields()


class BuildBuyRuleFieldsTests(_EnvIsolationTestCase):
    KEYS = (
        "BUY_RULE_ENABLE_INTRADAY_PULLBACK",
        "BUY_RULE_PRICE_BELOW_OPEN_ONLY",
        "BUY_RULE_ENABLE_REBOUND_FROM_LOW",
        "BUY_RULE_ENABLE_CONTROLLED_DOWN_DAY",
        "BUY_RULE_ENABLE_GAP_DOWN_OPEN",
        "BUY_RULE_ENABLE_RANGE_RECOVERY",
        "BUY_RULE_ENABLE_LIVE_VOLUME_RANK",
        "BUY_RULE_ENABLE_LIVE_VOLUME_POWER_RANK",
        "BUY_RULE_REBOUND_FROM_LOW_PCT",
        "BUY_RULE_CONTROLLED_DOWN_DAY_MIN",
        "BUY_RULE_CONTROLLED_DOWN_DAY_MAX",
        "BUY_RULE_GAP_DOWN_OPEN_MIN_PCT",
        "BUY_RULE_GAP_DOWN_OPEN_MAX_PCT",
        "BUY_RULE_RANGE_RECOVERY_MIN_RATIO",
        "BUY_RULE_REQUIRED_PASS_COUNT",
        "BUY_RULE_REQUIRED_PASS_COUNT_CORE",
        "BUY_MIN_PASSED_COUNT",
        "BUY_MIN_SCORE",
        "BUY_MIN_SCORE_CORE",
        "BUY_MAX_BUDGET_PER_TRADE_KRW",
        "BUY_MAX_ACCOUNT_EXPOSURE_PCT",
        "BUY_MAX_QTY_PER_TRADE",
    )

    def _snapshot(self) -> tuple[object, ...]:
        fields = build_buy_rule_fields()
        return (
            fields.buy_rule_enable_intraday_pullback,
            fields.buy_rule_enable_rebound_from_low,
            fields.buy_rule_enable_controlled_down_day,
            fields.buy_rule_enable_gap_down_open,
            fields.buy_rule_enable_range_recovery,
            fields.buy_rule_enable_live_volume_rank,
            fields.buy_rule_enable_live_volume_power_rank,
            fields.buy_rule_rebound_from_low_pct,
            fields.buy_rule_controlled_down_day_min,
            fields.buy_rule_controlled_down_day_max,
            fields.buy_rule_gap_down_open_min_pct,
            fields.buy_rule_gap_down_open_max_pct,
            fields.buy_rule_range_recovery_min_ratio,
            fields.buy_rule_required_pass_count,
            fields.buy_rule_required_pass_count_core,
            fields.buy_min_passed_count,
            fields.buy_min_score,
            fields.buy_min_score_core,
            fields.buy_max_budget_per_trade_krw,
            fields.buy_max_account_exposure_pct,
            fields.buy_max_qty_per_trade,
        )

    def test_defaults_overrides_and_legacy_fallbacks(self) -> None:
        self.assertEqual(
            self._snapshot(),
            (
                True, True, True, True, True, True, True,
                0.01, -6.0, 0.0, 0.3, 5.0, 0.2,
                3, 3, 3, 3.20, 3.0, 1000000, 10.0, 10,
            ),
        )

        os.environ.update(
            {
                "BUY_RULE_ENABLE_INTRADAY_PULLBACK": "false",
                "BUY_RULE_ENABLE_REBOUND_FROM_LOW": "false",
                "BUY_RULE_ENABLE_CONTROLLED_DOWN_DAY": "false",
                "BUY_RULE_ENABLE_GAP_DOWN_OPEN": "false",
                "BUY_RULE_ENABLE_RANGE_RECOVERY": "false",
                "BUY_RULE_ENABLE_LIVE_VOLUME_RANK": "false",
                "BUY_RULE_ENABLE_LIVE_VOLUME_POWER_RANK": "false",
                "BUY_RULE_REBOUND_FROM_LOW_PCT": "0.05",
                "BUY_RULE_CONTROLLED_DOWN_DAY_MIN": "-4.0",
                "BUY_RULE_CONTROLLED_DOWN_DAY_MAX": "1.0",
                "BUY_RULE_GAP_DOWN_OPEN_MIN_PCT": "0.5",
                "BUY_RULE_GAP_DOWN_OPEN_MAX_PCT": "6.0",
                "BUY_RULE_RANGE_RECOVERY_MIN_RATIO": "0.4",
                "BUY_RULE_REQUIRED_PASS_COUNT": "4",
                "BUY_RULE_REQUIRED_PASS_COUNT_CORE": "2",
                "BUY_MIN_PASSED_COUNT": "2",
                "BUY_MIN_SCORE": "2.50",
                "BUY_MIN_SCORE_CORE": "1.75",
                "BUY_MAX_BUDGET_PER_TRADE_KRW": "2000000",
                "BUY_MAX_ACCOUNT_EXPOSURE_PCT": "20",
                "BUY_MAX_QTY_PER_TRADE": "5",
            }
        )
        self.assertEqual(
            self._snapshot(),
            (
                False, False, False, False, False, False, False,
                0.05, -4.0, 1.0, 0.5, 6.0, 0.4,
                4, 2, 2, 2.50, 1.75, 2000000, 20.0, 5,
            ),
        )

        for key in self.KEYS:
            os.environ.pop(key, None)
        os.environ["BUY_RULE_PRICE_BELOW_OPEN_ONLY"] = "false"
        self.assertFalse(
            build_buy_rule_fields().buy_rule_enable_intraday_pullback
        )

    def test_invariant_violations_raise(self) -> None:
        cases = (
            ({"BUY_RULE_REBOUND_FROM_LOW_PCT": "-0.1"}, "BUY_RULE_REBOUND_FROM_LOW_PCT"),
            (
                {
                    "BUY_RULE_CONTROLLED_DOWN_DAY_MIN": "1.0",
                    "BUY_RULE_CONTROLLED_DOWN_DAY_MAX": "0.0",
                },
                "BUY_RULE_CONTROLLED_DOWN_DAY_MIN",
            ),
            ({"BUY_RULE_GAP_DOWN_OPEN_MIN_PCT": "-0.1"}, "BUY_RULE_GAP_DOWN_OPEN_MIN_PCT"),
            (
                {
                    "BUY_RULE_GAP_DOWN_OPEN_MIN_PCT": "6.0",
                    "BUY_RULE_GAP_DOWN_OPEN_MAX_PCT": "5.0",
                },
                "BUY_RULE_GAP_DOWN_OPEN_MIN_PCT",
            ),
            ({"BUY_RULE_RANGE_RECOVERY_MIN_RATIO": "-0.1"}, "BUY_RULE_RANGE_RECOVERY_MIN_RATIO"),
            ({"BUY_RULE_REQUIRED_PASS_COUNT": "0"}, "BUY_RULE_REQUIRED_PASS_COUNT"),
            ({"BUY_RULE_REQUIRED_PASS_COUNT_CORE": "0"}, "BUY_RULE_REQUIRED_PASS_COUNT_CORE"),
            ({"BUY_MIN_PASSED_COUNT": "0"}, "BUY_MIN_PASSED_COUNT"),
            ({"BUY_MIN_SCORE": "-1"}, "BUY_MIN_SCORE"),
            ({"BUY_MIN_SCORE_CORE": "-1"}, "BUY_MIN_SCORE_CORE"),
            ({"BUY_MAX_BUDGET_PER_TRADE_KRW": "0"}, "BUY_MAX_BUDGET_PER_TRADE_KRW"),
            ({"BUY_MAX_ACCOUNT_EXPOSURE_PCT": "0"}, "BUY_MAX_ACCOUNT_EXPOSURE_PCT"),
            ({"BUY_MAX_QTY_PER_TRADE": "0"}, "BUY_MAX_QTY_PER_TRADE"),
        )
        for overrides, message_part in cases:
            with self.subTest(overrides=overrides):
                for key in self.KEYS:
                    os.environ.pop(key, None)
                os.environ.update(overrides)
                with self.assertRaisesRegex(ValueError, message_part):
                    build_buy_rule_fields()


class BuildOrderDisciplineFieldsTests(_EnvIsolationTestCase):
    KEYS = (
        "STRICT_SELL_FIRST",
        "BLOCK_REBUY_SYMBOLS_BOUGHT_TODAY",
        "BUY_BLOCK_ON_BLOCKED_PREVIEW",
        "ENABLE_BUY_COOLDOWN",
        "ALLOW_ONE_BUY_PER_SYMBOL_PER_DAY",
        "BUY_REENTRY_COOLDOWN_MINUTES",
        "REBUY_COOLDOWN_MINUTES",
        "BUY_STOP_LOSS_SAME_DAY_MIN_COOLDOWN_MINUTES",
        "BUY_SAME_SYMBOL_MAX_ENTRIES_PER_DAY",
        "SAME_SYMBOL_MAX_BUYS_PER_DAY",
        "BUY_BLOCKED_COOLDOWN_MINUTES",
        "BLOCK_RESELL_SYMBOLS_SOLD_TODAY",
        "ENABLE_SELL_COOLDOWN",
        "ALLOW_ONE_SELL_TRIGGER_PER_SYMBOL_PER_DAY",
        "SELL_BLOCKED_COOLDOWN_MINUTES",
        "ORDER_COOLDOWN_MINUTES",
    )

    def _snapshot(self) -> tuple[object, ...]:
        fields = build_order_discipline_fields()
        return (
            fields.strict_sell_first,
            fields.block_rebuy_symbols_bought_today,
            fields.buy_block_on_blocked_preview,
            fields.enable_buy_cooldown,
            fields.allow_one_buy_per_symbol_per_day,
            fields.rebuy_cooldown_minutes,
            fields.stop_loss_same_day_reentry_min_minutes,
            fields.same_symbol_max_buys_per_day,
            fields.buy_blocked_cooldown_minutes,
            fields.block_resell_symbols_sold_today,
            fields.enable_sell_cooldown,
            fields.allow_one_sell_trigger_per_symbol_per_day,
            fields.sell_blocked_cooldown_minutes,
            fields.order_cooldown_minutes,
        )

    def test_defaults_overrides_and_legacy_fallbacks(self) -> None:
        self.assertEqual(
            self._snapshot(),
            (True, True, False, True, False, 30, 120, 3, 30, True, True, True, 30, 15),
        )

        os.environ.update(
            {
                "STRICT_SELL_FIRST": "false",
                "BLOCK_REBUY_SYMBOLS_BOUGHT_TODAY": "false",
                "BUY_BLOCK_ON_BLOCKED_PREVIEW": "true",
                "ENABLE_BUY_COOLDOWN": "false",
                "ALLOW_ONE_BUY_PER_SYMBOL_PER_DAY": "true",
                "BUY_REENTRY_COOLDOWN_MINUTES": "60",
                "BUY_STOP_LOSS_SAME_DAY_MIN_COOLDOWN_MINUTES": "90",
                "BUY_SAME_SYMBOL_MAX_ENTRIES_PER_DAY": "2",
                "BUY_BLOCKED_COOLDOWN_MINUTES": "45",
                "BLOCK_RESELL_SYMBOLS_SOLD_TODAY": "false",
                "ENABLE_SELL_COOLDOWN": "false",
                "ALLOW_ONE_SELL_TRIGGER_PER_SYMBOL_PER_DAY": "false",
                "SELL_BLOCKED_COOLDOWN_MINUTES": "50",
                "ORDER_COOLDOWN_MINUTES": "20",
            }
        )
        self.assertEqual(
            self._snapshot(),
            (
                False, False, True, False, True,
                60, 90, 2, 45, False, False, False, 50, 20,
            ),
        )

        for key in self.KEYS:
            os.environ.pop(key, None)
        os.environ["REBUY_COOLDOWN_MINUTES"] = "45"
        os.environ["SAME_SYMBOL_MAX_BUYS_PER_DAY"] = "5"
        fields = build_order_discipline_fields()
        self.assertEqual(fields.rebuy_cooldown_minutes, 45)
        self.assertEqual(fields.same_symbol_max_buys_per_day, 5)

    def test_invariant_violations_raise(self) -> None:
        cases = (
            ({"BUY_REENTRY_COOLDOWN_MINUTES": "-1"}, "BUY_REENTRY_COOLDOWN_MINUTES"),
            (
                {"BUY_STOP_LOSS_SAME_DAY_MIN_COOLDOWN_MINUTES": "-1"},
                "BUY_STOP_LOSS_SAME_DAY_MIN_COOLDOWN_MINUTES",
            ),
            (
                {"BUY_SAME_SYMBOL_MAX_ENTRIES_PER_DAY": "-1"},
                "BUY_SAME_SYMBOL_MAX_ENTRIES_PER_DAY",
            ),
            ({"BUY_BLOCKED_COOLDOWN_MINUTES": "-1"}, "BUY_BLOCKED_COOLDOWN_MINUTES"),
            ({"SELL_BLOCKED_COOLDOWN_MINUTES": "-1"}, "SELL_BLOCKED_COOLDOWN_MINUTES"),
            ({"ORDER_COOLDOWN_MINUTES": "-1"}, "ORDER_COOLDOWN_MINUTES"),
        )
        for overrides, message_part in cases:
            with self.subTest(overrides=overrides):
                for key in self.KEYS:
                    os.environ.pop(key, None)
                os.environ.update(overrides)
                with self.assertRaisesRegex(ValueError, message_part):
                    build_order_discipline_fields()


class BuildSellRuleFieldsTests(_EnvIsolationTestCase):
    KEYS = (
        "SELL_ENABLE",
        "SELL_RULE_STOP_LOSS_PCT",
        "SELL_STOP_LOSS_PCT",
        "SELL_RULE_TAKE_PROFIT_PCT",
        "SELL_TAKE_PROFIT_PCT",
        "SELL_RULE_TRAILING_STOP_PCT",
        "SELL_RULE_ENABLE_LIVE_LEADERSHIP_LOSS",
        "SELL_RULE_ENABLE_LIVE_POWER_BREAKDOWN",
        "ENABLE_SELL_TEST_SCENARIOS",
        "ENABLE_SELL_GUARD_SELFTEST",
        "SELL_TEST_MODE",
        "SELL_EXIT_REQUIRED_PASS_COUNT",
    )

    def _snapshot(self) -> tuple[object, ...]:
        fields = build_sell_rule_fields()
        return (
            fields.sell_enable,
            fields.sell_stop_loss_pct,
            fields.sell_take_profit_pct,
            fields.sell_trailing_stop_pct,
            fields.sell_rule_enable_live_leadership_loss,
            fields.sell_rule_enable_live_power_breakdown,
            fields.enable_sell_test_scenarios,
            fields.enable_sell_guard_selftest,
            fields.sell_test_mode,
            fields.sell_exit_required_pass_count,
        )

    def test_defaults_overrides_and_legacy_fallbacks(self) -> None:
        self.assertEqual(
            self._snapshot(),
            (True, -3.0, 3.0, 1.5, True, True, False, False, "off", 0),
        )

        os.environ.update(
            {
                "SELL_ENABLE": "false",
                "SELL_RULE_STOP_LOSS_PCT": "-4.5",
                "SELL_RULE_TAKE_PROFIT_PCT": "4.5",
                "SELL_RULE_TRAILING_STOP_PCT": "2.0",
                "SELL_RULE_ENABLE_LIVE_LEADERSHIP_LOSS": "false",
                "SELL_RULE_ENABLE_LIVE_POWER_BREAKDOWN": "false",
                "ENABLE_SELL_TEST_SCENARIOS": "true",
                "ENABLE_SELL_GUARD_SELFTEST": "true",
                "SELL_TEST_MODE": "Take_Profit",
                "SELL_EXIT_REQUIRED_PASS_COUNT": "2",
            }
        )
        self.assertEqual(
            self._snapshot(),
            (False, -4.5, 4.5, 2.0, False, False, True, True, "take_profit", 2),
        )

        for key in self.KEYS:
            os.environ.pop(key, None)
        os.environ["SELL_STOP_LOSS_PCT"] = "-5.0"
        os.environ["SELL_TAKE_PROFIT_PCT"] = "5.0"
        fields = build_sell_rule_fields()
        self.assertEqual(fields.sell_stop_loss_pct, -5.0)
        self.assertEqual(fields.sell_take_profit_pct, 5.0)

    def test_invariant_violations_raise(self) -> None:
        cases = (
            ({"SELL_TEST_MODE": "bogus"}, "SELL_TEST_MODE"),
            ({"SELL_EXIT_REQUIRED_PASS_COUNT": "-1"}, "SELL_EXIT_REQUIRED_PASS_COUNT"),
        )
        for overrides, message_part in cases:
            with self.subTest(overrides=overrides):
                for key in self.KEYS:
                    os.environ.pop(key, None)
                os.environ.update(overrides)
                with self.assertRaisesRegex(ValueError, message_part):
                    build_sell_rule_fields()


class BuildRiskLimitFieldsTests(_EnvIsolationTestCase):
    KEYS = (
        "BUY_ENABLE_RISK_GUARDS",
        "BUY_DAILY_MAX_ORDER_SUBMISSIONS",
        "SELL_DAILY_MAX_ORDER_SUBMISSIONS",
        "BUY_DAILY_MAX_NOTIONAL_KRW",
        "SELL_DAILY_MAX_NOTIONAL_KRW",
        "ENABLE_REBALANCE_SELL",
        "ENABLE_QUALITY_REBALANCE_PREVIEW",
        "REBALANCE_SELL_MAX_SUBMISSIONS_PER_DAY",
        "REBALANCE_MIN_SCORE_DELTA",
        "REBALANCE_MIN_PROFIT_BUFFER_BPS",
        "REBALANCE_MAX_CONCENTRATION_PCT",
        "REBALANCE_MIN_NET_EDGE_BPS",
    )

    def _snapshot(self) -> tuple[object, ...]:
        fields = build_risk_limit_fields()
        return (
            fields.buy_enable_risk_guards,
            fields.buy_daily_max_order_submissions,
            fields.sell_daily_max_order_submissions,
            fields.buy_daily_max_notional_krw,
            fields.sell_daily_max_notional_krw,
            fields.enable_rebalance_sell,
            fields.enable_quality_rebalance_preview,
            fields.rebalance_sell_max_submissions_per_day,
            fields.rebalance_min_score_delta,
            fields.rebalance_min_profit_buffer_bps,
            fields.rebalance_max_concentration_pct,
            fields.rebalance_min_net_edge_bps,
        )

    def test_defaults_and_overrides(self) -> None:
        self.assertEqual(
            self._snapshot(),
            (True, 30, 100, 500000, 7000000, False, True, 1, 1.0, 0.0, 35.0, 10.0),
        )

        os.environ.update(
            {
                "BUY_ENABLE_RISK_GUARDS": "false",
                "BUY_DAILY_MAX_ORDER_SUBMISSIONS": "10",
                "SELL_DAILY_MAX_ORDER_SUBMISSIONS": "50",
                "BUY_DAILY_MAX_NOTIONAL_KRW": "1000000",
                "SELL_DAILY_MAX_NOTIONAL_KRW": "9000000",
                "ENABLE_REBALANCE_SELL": "true",
                "ENABLE_QUALITY_REBALANCE_PREVIEW": "false",
                "REBALANCE_SELL_MAX_SUBMISSIONS_PER_DAY": "2",
                "REBALANCE_MIN_SCORE_DELTA": "1.5",
                "REBALANCE_MIN_PROFIT_BUFFER_BPS": "5.0",
                "REBALANCE_MAX_CONCENTRATION_PCT": "40.0",
                "REBALANCE_MIN_NET_EDGE_BPS": "12.0",
            }
        )
        self.assertEqual(
            self._snapshot(),
            (False, 10, 50, 1000000, 9000000, True, False, 2, 1.5, 5.0, 40.0, 12.0),
        )

    def test_invariant_violations_raise(self) -> None:
        cases = (
            ({"BUY_DAILY_MAX_ORDER_SUBMISSIONS": "-1"}, "BUY_DAILY_MAX_ORDER_SUBMISSIONS"),
            ({"SELL_DAILY_MAX_ORDER_SUBMISSIONS": "-1"}, "SELL_DAILY_MAX_ORDER_SUBMISSIONS"),
            ({"BUY_DAILY_MAX_NOTIONAL_KRW": "-1"}, "BUY_DAILY_MAX_NOTIONAL_KRW"),
            ({"SELL_DAILY_MAX_NOTIONAL_KRW": "-1"}, "SELL_DAILY_MAX_NOTIONAL_KRW"),
            (
                {"REBALANCE_SELL_MAX_SUBMISSIONS_PER_DAY": "-1"},
                "REBALANCE_SELL_MAX_SUBMISSIONS_PER_DAY",
            ),
            ({"REBALANCE_MIN_SCORE_DELTA": "-1"}, "REBALANCE_MIN_SCORE_DELTA"),
            ({"REBALANCE_MIN_PROFIT_BUFFER_BPS": "-1"}, "REBALANCE_MIN_PROFIT_BUFFER_BPS"),
            ({"REBALANCE_MAX_CONCENTRATION_PCT": "0"}, "REBALANCE_MAX_CONCENTRATION_PCT"),
            ({"REBALANCE_MIN_NET_EDGE_BPS": "-1"}, "REBALANCE_MIN_NET_EDGE_BPS"),
        )
        for overrides, message_part in cases:
            with self.subTest(overrides=overrides):
                for key in self.KEYS:
                    os.environ.pop(key, None)
                os.environ.update(overrides)
                with self.assertRaisesRegex(ValueError, message_part):
                    build_risk_limit_fields()


class BuildScanCadenceFieldsTests(_EnvIsolationTestCase):
    KEYS = (
        "SELL_CHECK_INTERVAL_SECONDS",
        "BUY_SCAN_INTERVAL_SECONDS",
        "SCAN_SYMBOLS_MAX_PER_CYCLE",
        "BUY_SCAN_PROFILE_ROTATION_ENABLED",
        "BUY_SCAN_EXPLORATION_RATIO",
        "BUY_SCAN_CORE_FRACTION",
        "BUY_SCAN_ROTATING_FRACTION",
        "BUY_SCAN_SHALLOW_TOP_K",
        "BUY_SCAN_DEEP_EVAL_LIMIT",
        "BUY_SCAN_CORE_MAX",
        "BUY_SCAN_TOP_K_CANDIDATES",
        "LIVE_SNAPSHOT_TTL_SECONDS",
        "LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS",
        "BUY_SCAN_PREFETCH_DEADLINE_ENABLED",
        "BUY_SCAN_QUOTE_PREFETCH_DEADLINE_SECONDS",
        "BUY_SCAN_QUOTE_REQUEST_TIMEOUT_SECONDS",
        "BUY_SCAN_QUOTE_MAX_ATTEMPTS",
        "BUY_SCAN_TOTAL_BUDGET_SECONDS",
        "BUY_SCAN_MIN_REMAINING_BUDGET_SECONDS",
        "API_SOFT_MAX_REQUESTS_PER_SECOND",
        "API_SOFT_MAX_QUOTES_PER_TICK",
        "API_BACKOFF_SECONDS_ON_RATE_LIMIT",
        "API_MIN_INTER_REQUEST_SECONDS",
        "API_BUY_SCAN_MIN_REQUEST_RESERVE",
        "API_BUY_SCAN_MIN_QUOTE_RESERVE",
    )

    def _snapshot(self) -> tuple[object, ...]:
        fields = build_scan_cadence_fields()
        return (
            fields.sell_check_interval_seconds,
            fields.buy_scan_interval_seconds,
            fields.scan_symbols_max_per_cycle,
            fields.buy_scan_profile_rotation_enabled,
            fields.buy_scan_exploration_ratio,
            fields.buy_scan_core_fraction,
            fields.buy_scan_rotating_fraction,
            fields.buy_scan_shallow_top_k,
            fields.buy_scan_deep_eval_limit,
            fields.buy_scan_core_max,
            fields.buy_scan_top_k_candidates,
            fields.live_snapshot_ttl_seconds,
            fields.live_snapshot_refresh_interval_seconds,
            fields.buy_scan_prefetch_deadline_enabled,
            fields.buy_scan_quote_prefetch_deadline_seconds,
            fields.buy_scan_quote_request_timeout_seconds,
            fields.buy_scan_quote_max_attempts,
            fields.buy_scan_total_budget_seconds,
            fields.buy_scan_min_remaining_budget_seconds,
            fields.api_soft_max_requests_per_second,
            fields.api_soft_max_quotes_per_tick,
            fields.api_backoff_seconds_on_rate_limit,
            fields.api_min_inter_request_seconds,
            fields.api_buy_scan_min_request_reserve,
            fields.api_buy_scan_min_quote_reserve,
        )

    def test_defaults_and_overrides(self) -> None:
        self.assertEqual(
            self._snapshot(),
            (
                30, 60, 200, True, 0.2, 0.4, 0.35,
                200, 200, 12, 8, 420, 180, True, 18.0, 2.0, 1, 25.0, 5.0,
                4, 20, 3, 1.1, 3, 4,
            ),
        )

        os.environ.update(
            {
                "SELL_CHECK_INTERVAL_SECONDS": "40",
                "BUY_SCAN_INTERVAL_SECONDS": "300",
                "SCAN_SYMBOLS_MAX_PER_CYCLE": "20",
                "BUY_SCAN_PROFILE_ROTATION_ENABLED": "false",
                "BUY_SCAN_EXPLORATION_RATIO": "0.3",
                "BUY_SCAN_CORE_FRACTION": "0.5",
                "BUY_SCAN_ROTATING_FRACTION": "0.25",
                "BUY_SCAN_SHALLOW_TOP_K": "8",
                "BUY_SCAN_DEEP_EVAL_LIMIT": "6",
                "BUY_SCAN_CORE_MAX": "10",
                "BUY_SCAN_TOP_K_CANDIDATES": "5",
                "LIVE_SNAPSHOT_TTL_SECONDS": "600",
                "LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS": "240",
                "BUY_SCAN_PREFETCH_DEADLINE_ENABLED": "false",
                "BUY_SCAN_QUOTE_PREFETCH_DEADLINE_SECONDS": "12.5",
                "BUY_SCAN_QUOTE_REQUEST_TIMEOUT_SECONDS": "0.75",
                "BUY_SCAN_QUOTE_MAX_ATTEMPTS": "2",
                "BUY_SCAN_TOTAL_BUDGET_SECONDS": "20.5",
                "BUY_SCAN_MIN_REMAINING_BUDGET_SECONDS": "7.5",
                "API_SOFT_MAX_REQUESTS_PER_SECOND": "3",
                "API_SOFT_MAX_QUOTES_PER_TICK": "15",
                "API_BACKOFF_SECONDS_ON_RATE_LIMIT": "5",
                "API_MIN_INTER_REQUEST_SECONDS": "1.5",
                "API_BUY_SCAN_MIN_REQUEST_RESERVE": "2",
                "API_BUY_SCAN_MIN_QUOTE_RESERVE": "3",
            }
        )
        self.assertEqual(
            self._snapshot(),
            (
                40, 300, 20, False, 0.3, 0.5, 0.25,
                8, 6, 10, 5, 600, 240, False, 12.5, 0.75, 2, 20.5, 7.5,
                3, 15, 5, 1.5, 2, 3,
            ),
        )

    def test_invariant_violations_raise(self) -> None:
        cases = (
            ({"SELL_CHECK_INTERVAL_SECONDS": "0"}, "SELL_CHECK_INTERVAL_SECONDS"),
            ({"BUY_SCAN_INTERVAL_SECONDS": "0"}, "BUY_SCAN_INTERVAL_SECONDS"),
            ({"SCAN_SYMBOLS_MAX_PER_CYCLE": "0"}, "SCAN_SYMBOLS_MAX_PER_CYCLE"),
            ({"BUY_SCAN_EXPLORATION_RATIO": "1.5"}, "BUY_SCAN_EXPLORATION_RATIO"),
            ({"BUY_SCAN_CORE_FRACTION": "0"}, "BUY_SCAN_CORE_FRACTION"),
            ({"BUY_SCAN_ROTATING_FRACTION": "1.5"}, "BUY_SCAN_ROTATING_FRACTION"),
            (
                {"BUY_SCAN_CORE_FRACTION": "0.7", "BUY_SCAN_ROTATING_FRACTION": "0.5"},
                "BUY_SCAN_CORE_FRACTION \\+ BUY_SCAN_ROTATING_FRACTION",
            ),
            ({"BUY_SCAN_SHALLOW_TOP_K": "0"}, "BUY_SCAN_SHALLOW_TOP_K"),
            ({"BUY_SCAN_DEEP_EVAL_LIMIT": "0"}, "BUY_SCAN_DEEP_EVAL_LIMIT"),
            ({"BUY_SCAN_CORE_MAX": "0"}, "BUY_SCAN_CORE_MAX"),
            ({"BUY_SCAN_TOP_K_CANDIDATES": "0"}, "BUY_SCAN_TOP_K_CANDIDATES"),
            ({"LIVE_SNAPSHOT_TTL_SECONDS": "0"}, "LIVE_SNAPSHOT_TTL_SECONDS"),
            (
                {"LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS": "0"},
                "LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS",
            ),
            (
                {
                    "LIVE_SNAPSHOT_TTL_SECONDS": "100",
                    "LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS": "100",
                },
                "LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS",
            ),
            (
                {"BUY_SCAN_QUOTE_PREFETCH_DEADLINE_SECONDS": "0"},
                "BUY_SCAN_QUOTE_PREFETCH_DEADLINE_SECONDS",
            ),
            (
                {"BUY_SCAN_QUOTE_REQUEST_TIMEOUT_SECONDS": "0"},
                "BUY_SCAN_QUOTE_REQUEST_TIMEOUT_SECONDS",
            ),
            ({"BUY_SCAN_QUOTE_MAX_ATTEMPTS": "0"}, "BUY_SCAN_QUOTE_MAX_ATTEMPTS"),
            ({"BUY_SCAN_TOTAL_BUDGET_SECONDS": "0"}, "BUY_SCAN_TOTAL_BUDGET_SECONDS"),
            (
                {"BUY_SCAN_MIN_REMAINING_BUDGET_SECONDS": "-1"},
                "BUY_SCAN_MIN_REMAINING_BUDGET_SECONDS",
            ),
            ({"API_SOFT_MAX_REQUESTS_PER_SECOND": "0"}, "API_SOFT_MAX_REQUESTS_PER_SECOND"),
            ({"API_SOFT_MAX_QUOTES_PER_TICK": "0"}, "API_SOFT_MAX_QUOTES_PER_TICK"),
            (
                {"API_BACKOFF_SECONDS_ON_RATE_LIMIT": "-1"},
                "API_BACKOFF_SECONDS_ON_RATE_LIMIT",
            ),
            ({"API_MIN_INTER_REQUEST_SECONDS": "-1"}, "API_MIN_INTER_REQUEST_SECONDS"),
            (
                {"API_BUY_SCAN_MIN_REQUEST_RESERVE": "-1"},
                "API_BUY_SCAN_MIN_REQUEST_RESERVE",
            ),
            ({"API_BUY_SCAN_MIN_QUOTE_RESERVE": "-1"}, "API_BUY_SCAN_MIN_QUOTE_RESERVE"),
        )
        for overrides, message_part in cases:
            with self.subTest(overrides=overrides):
                for key in self.KEYS:
                    os.environ.pop(key, None)
                os.environ.update(overrides)
                with self.assertRaisesRegex(ValueError, message_part):
                    build_scan_cadence_fields()


class BuildAdaptiveDegradedFieldsTests(_EnvIsolationTestCase):
    KEYS = (
        "ADAPTIVE_MIDDAY_ENABLED",
        "ADAPTIVE_MIDDAY_WINDOW",
        "ADAPTIVE_MIDDAY_BUY_SCAN_INTERVAL_SECONDS",
        "ADAPTIVE_MIDDAY_SELL_CHECK_INTERVAL_SECONDS",
        "ADAPTIVE_MIDDAY_SCAN_SYMBOLS_MAX_PER_CYCLE",
        "ADAPTIVE_MIDDAY_BUY_SCAN_DEEP_EVAL_LIMIT",
        "DEGRADED_MODE_ENABLED",
        "DEGRADED_MODE_RATE_LIMIT_HITS_IN_10M",
        "DEGRADED_MODE_CONSECUTIVE_BACKOFF_CYCLES",
        "DEGRADED_MODE_DURATION_SECONDS",
        "DEGRADED_MODE_BUY_SCAN_INTERVAL_SECONDS",
        "DEGRADED_MODE_SELL_CHECK_INTERVAL_SECONDS",
        "DEGRADED_MODE_SCAN_SYMBOLS_MAX_PER_CYCLE",
        "DEGRADED_MODE_BUY_SCAN_DEEP_EVAL_LIMIT",
        "DEGRADED_MODE_SELL_WATCH_MAX_HOLDINGS_PER_TICK",
    )

    def _snapshot(self) -> tuple[object, ...]:
        fields = build_adaptive_degraded_fields()
        return (
            fields.adaptive_midday_enabled,
            fields.adaptive_midday_window,
            fields.adaptive_midday_buy_scan_interval_seconds,
            fields.adaptive_midday_sell_check_interval_seconds,
            fields.adaptive_midday_scan_symbols_max_per_cycle,
            fields.adaptive_midday_buy_scan_deep_eval_limit,
            fields.degraded_mode_enabled,
            fields.degraded_mode_rate_limit_hits_in_10m,
            fields.degraded_mode_consecutive_backoff_cycles,
            fields.degraded_mode_duration_seconds,
            fields.degraded_mode_buy_scan_interval_seconds,
            fields.degraded_mode_sell_check_interval_seconds,
            fields.degraded_mode_scan_symbols_max_per_cycle,
            fields.degraded_mode_buy_scan_deep_eval_limit,
            fields.degraded_mode_sell_watch_max_holdings_per_tick,
        )

    def test_defaults_and_overrides(self) -> None:
        self.assertEqual(
            self._snapshot(),
            (
                True, "11:00-13:00", 60, 25, 200, 200,
                True, 3, 4, 600, 300, 30, 16, 2, 4,
            ),
        )

        os.environ.update(
            {
                "ADAPTIVE_MIDDAY_ENABLED": "false",
                "ADAPTIVE_MIDDAY_WINDOW": "12:00-14:00",
                "ADAPTIVE_MIDDAY_BUY_SCAN_INTERVAL_SECONDS": "300",
                "ADAPTIVE_MIDDAY_SELL_CHECK_INTERVAL_SECONDS": "30",
                "ADAPTIVE_MIDDAY_SCAN_SYMBOLS_MAX_PER_CYCLE": "20",
                "ADAPTIVE_MIDDAY_BUY_SCAN_DEEP_EVAL_LIMIT": "3",
                "DEGRADED_MODE_ENABLED": "false",
                "DEGRADED_MODE_RATE_LIMIT_HITS_IN_10M": "5",
                "DEGRADED_MODE_CONSECUTIVE_BACKOFF_CYCLES": "6",
                "DEGRADED_MODE_DURATION_SECONDS": "900",
                "DEGRADED_MODE_BUY_SCAN_INTERVAL_SECONDS": "360",
                "DEGRADED_MODE_SELL_CHECK_INTERVAL_SECONDS": "40",
                "DEGRADED_MODE_SCAN_SYMBOLS_MAX_PER_CYCLE": "12",
                "DEGRADED_MODE_BUY_SCAN_DEEP_EVAL_LIMIT": "1",
                "DEGRADED_MODE_SELL_WATCH_MAX_HOLDINGS_PER_TICK": "2",
            }
        )
        self.assertEqual(
            self._snapshot(),
            (
                False, "12:00-14:00", 300, 30, 20, 3,
                False, 5, 6, 900, 360, 40, 12, 1, 2,
            ),
        )

    def test_invariant_violations_raise(self) -> None:
        cases = (
            ({"ADAPTIVE_MIDDAY_WINDOW": "bogus"}, "ADAPTIVE_MIDDAY_WINDOW"),
            (
                {"ADAPTIVE_MIDDAY_BUY_SCAN_INTERVAL_SECONDS": "0"},
                "ADAPTIVE_MIDDAY_BUY_SCAN_INTERVAL_SECONDS",
            ),
            (
                {"ADAPTIVE_MIDDAY_SELL_CHECK_INTERVAL_SECONDS": "0"},
                "ADAPTIVE_MIDDAY_SELL_CHECK_INTERVAL_SECONDS",
            ),
            (
                {"ADAPTIVE_MIDDAY_SCAN_SYMBOLS_MAX_PER_CYCLE": "0"},
                "ADAPTIVE_MIDDAY_SCAN_SYMBOLS_MAX_PER_CYCLE",
            ),
            (
                {"ADAPTIVE_MIDDAY_BUY_SCAN_DEEP_EVAL_LIMIT": "0"},
                "ADAPTIVE_MIDDAY_BUY_SCAN_DEEP_EVAL_LIMIT",
            ),
            (
                {"DEGRADED_MODE_RATE_LIMIT_HITS_IN_10M": "-1"},
                "DEGRADED_MODE_RATE_LIMIT_HITS_IN_10M",
            ),
            (
                {"DEGRADED_MODE_CONSECUTIVE_BACKOFF_CYCLES": "-1"},
                "DEGRADED_MODE_CONSECUTIVE_BACKOFF_CYCLES",
            ),
            ({"DEGRADED_MODE_DURATION_SECONDS": "0"}, "DEGRADED_MODE_DURATION_SECONDS"),
            (
                {"DEGRADED_MODE_BUY_SCAN_INTERVAL_SECONDS": "0"},
                "DEGRADED_MODE_BUY_SCAN_INTERVAL_SECONDS",
            ),
            (
                {"DEGRADED_MODE_SELL_CHECK_INTERVAL_SECONDS": "0"},
                "DEGRADED_MODE_SELL_CHECK_INTERVAL_SECONDS",
            ),
            (
                {"DEGRADED_MODE_SCAN_SYMBOLS_MAX_PER_CYCLE": "0"},
                "DEGRADED_MODE_SCAN_SYMBOLS_MAX_PER_CYCLE",
            ),
            (
                {"DEGRADED_MODE_BUY_SCAN_DEEP_EVAL_LIMIT": "0"},
                "DEGRADED_MODE_BUY_SCAN_DEEP_EVAL_LIMIT",
            ),
            (
                {"DEGRADED_MODE_SELL_WATCH_MAX_HOLDINGS_PER_TICK": "0"},
                "DEGRADED_MODE_SELL_WATCH_MAX_HOLDINGS_PER_TICK",
            ),
        )
        for overrides, message_part in cases:
            with self.subTest(overrides=overrides):
                for key in self.KEYS:
                    os.environ.pop(key, None)
                os.environ.update(overrides)
                with self.assertRaisesRegex(ValueError, message_part):
                    build_adaptive_degraded_fields()


class BuildPnlBrakeRegimeFieldsTests(_EnvIsolationTestCase):
    KEYS = (
        "ENABLE_DAILY_PNL_BRAKE",
        "DAILY_PNL_WARNING_PCT",
        "DAILY_BUY_PAUSE_PCT",
        "DAILY_PNL_BUY_PAUSE_PCT",
        "DAILY_HARD_STOP_PCT",
        "DAILY_PNL_HARD_STOP_PCT",
        "DAILY_PNL_COOLDOWN_MINUTES",
        "REGIME_CAUTION_DRAWDOWN_PCT",
        "REGIME_RISK_OFF_DRAWDOWN_PCT",
        "REGIME_NORMAL_MULTIPLIER",
        "REGIME_CAUTION_MULTIPLIER",
        "REGIME_RISK_OFF_MULTIPLIER",
    )

    def _snapshot(self) -> tuple[object, ...]:
        fields = build_pnl_brake_regime_fields()
        return (
            fields.enable_daily_pnl_brake,
            fields.daily_pnl_warning_pct,
            fields.daily_pnl_buy_pause_pct,
            fields.daily_pnl_hard_stop_pct,
            fields.daily_pnl_cooldown_minutes,
            fields.regime_caution_drawdown_pct,
            fields.regime_risk_off_drawdown_pct,
            fields.regime_normal_multiplier,
            fields.regime_caution_multiplier,
            fields.regime_risk_off_multiplier,
        )

    def test_defaults_overrides_and_legacy_fallbacks(self) -> None:
        self.assertEqual(
            self._snapshot(),
            (True, -1.5, -2.5, -4.0, 30, -2.0, -4.0, 1.0, 0.7, 0.2),
        )

        os.environ.update(
            {
                "ENABLE_DAILY_PNL_BRAKE": "false",
                "DAILY_PNL_WARNING_PCT": "-1.0",
                "DAILY_BUY_PAUSE_PCT": "-2.0",
                "DAILY_HARD_STOP_PCT": "-3.0",
                "DAILY_PNL_COOLDOWN_MINUTES": "45",
                "REGIME_CAUTION_DRAWDOWN_PCT": "-1.5",
                "REGIME_RISK_OFF_DRAWDOWN_PCT": "-3.5",
                "REGIME_NORMAL_MULTIPLIER": "1.2",
                "REGIME_CAUTION_MULTIPLIER": "0.8",
                "REGIME_RISK_OFF_MULTIPLIER": "0.3",
            }
        )
        self.assertEqual(
            self._snapshot(),
            (False, -1.0, -2.0, -3.0, 45, -1.5, -3.5, 1.2, 0.8, 0.3),
        )

        for key in self.KEYS:
            os.environ.pop(key, None)
        os.environ["DAILY_PNL_BUY_PAUSE_PCT"] = "-2.2"
        os.environ["DAILY_PNL_HARD_STOP_PCT"] = "-4.4"
        fields = build_pnl_brake_regime_fields()
        self.assertEqual(fields.daily_pnl_buy_pause_pct, -2.2)
        self.assertEqual(fields.daily_pnl_hard_stop_pct, -4.4)

    def test_invariant_violations_raise(self) -> None:
        cases = (
            ({"DAILY_PNL_COOLDOWN_MINUTES": "-1"}, "DAILY_PNL_COOLDOWN_MINUTES"),
            (
                {"DAILY_PNL_WARNING_PCT": "-3.0", "DAILY_BUY_PAUSE_PCT": "-2.5"},
                "DAILY_PNL_WARNING_PCT",
            ),
            (
                {"DAILY_BUY_PAUSE_PCT": "-5.0", "DAILY_HARD_STOP_PCT": "-4.0"},
                "DAILY_BUY_PAUSE_PCT",
            ),
            (
                {
                    "REGIME_CAUTION_DRAWDOWN_PCT": "-5.0",
                    "REGIME_RISK_OFF_DRAWDOWN_PCT": "-4.0",
                },
                "REGIME_CAUTION_DRAWDOWN_PCT",
            ),
            ({"REGIME_NORMAL_MULTIPLIER": "0"}, "REGIME_"),
            (
                {"REGIME_NORMAL_MULTIPLIER": "0.5", "REGIME_CAUTION_MULTIPLIER": "0.7"},
                "REGIME_NORMAL_MULTIPLIER",
            ),
            (
                {"REGIME_CAUTION_MULTIPLIER": "0.1", "REGIME_RISK_OFF_MULTIPLIER": "0.2"},
                "REGIME_CAUTION_MULTIPLIER",
            ),
        )
        for overrides, message_part in cases:
            with self.subTest(overrides=overrides):
                for key in self.KEYS:
                    os.environ.pop(key, None)
                os.environ.update(overrides)
                with self.assertRaisesRegex(ValueError, message_part):
                    build_pnl_brake_regime_fields()


class BuildSessionRuntimeFieldsTests(_EnvIsolationTestCase):
    KEYS = (
        "ENABLE_PREMARKET_WAIT",
        "RUN_MODE",
        "RUN_ONCE",
        "RUN_INTERVAL_SECONDS",
        "CONFIRM_BUY",
        "LANE_SCHEDULER_ENABLED",
        "ORDER_GATE_ENABLED",
        "SESSION_CYCLE_HARD_BUDGET_SECONDS",
    )

    def _snapshot(self) -> tuple[object, ...]:
        fields = build_session_runtime_fields()
        return (
            fields.enable_premarket_wait,
            fields.run_mode,
            fields.run_once,
            fields.run_interval_seconds,
            fields.confirm_buy,
            fields.lane_scheduler_enabled,
            fields.order_gate_enabled,
            fields.session_cycle_hard_budget_seconds,
        )

    def test_defaults_and_overrides(self) -> None:
        self.assertEqual(
            self._snapshot(),
            (True, "trade", True, 60, "NO", False, True, 60.0),
        )

        os.environ.update(
            {
                "ENABLE_PREMARKET_WAIT": "false",
                "RUN_MODE": "Scan_Only",
                "RUN_ONCE": "false",
                "RUN_INTERVAL_SECONDS": "120",
                "CONFIRM_BUY": "yes",
                "LANE_SCHEDULER_ENABLED": "true",
                "ORDER_GATE_ENABLED": "false",
                "SESSION_CYCLE_HARD_BUDGET_SECONDS": "45.5",
            }
        )
        self.assertEqual(
            self._snapshot(),
            (False, "scan_only", False, 120, "YES", True, False, 45.5),
        )

    def test_invariant_violations_raise(self) -> None:
        cases = (
            ({"RUN_MODE": "bogus"}, "RUN_MODE"),
            ({"RUN_INTERVAL_SECONDS": "0"}, "RUN_INTERVAL_SECONDS"),
            (
                {"SESSION_CYCLE_HARD_BUDGET_SECONDS": "0"},
                "SESSION_CYCLE_HARD_BUDGET_SECONDS",
            ),
        )
        for overrides, message_part in cases:
            with self.subTest(overrides=overrides):
                for key in self.KEYS:
                    os.environ.pop(key, None)
                os.environ.update(overrides)
                with self.assertRaisesRegex(ValueError, message_part):
                    build_session_runtime_fields()


class GetSettingsUsesFieldGroupBuildersTests(unittest.TestCase):
    """get_settings()는 settings_fields 빌더 seam을 통해 그룹 값을 조립한다."""

    _CRED_ENV = {
        "KIS_ENV": "mock",
        "KIS_APP_KEY": "seam-key",
        "KIS_APP_SECRET": "seam-secret",
        "KIS_BASE_URL": "https://openapivts.koreainvestment.com:9443",
        "KIS_CANO": "12345678",
        "KIS_ACNT_PRDT_CD": "01",
    }

    def test_get_settings_composes_from_field_group_builders(self) -> None:
        sentinels = {
            "build_targeting_fields": replace(
                build_targeting_fields(), symbol="123456"
            ),
            "build_cost_model_fields": replace(build_cost_model_fields(), qty=77),
            "build_buy_rule_fields": replace(
                build_buy_rule_fields(), buy_min_score=9.9
            ),
            "build_order_discipline_fields": replace(
                build_order_discipline_fields(), order_cooldown_minutes=99
            ),
            "build_sell_rule_fields": replace(
                build_sell_rule_fields(), sell_test_mode="hold"
            ),
            "build_risk_limit_fields": replace(
                build_risk_limit_fields(), buy_daily_max_order_submissions=123
            ),
            "build_scan_cadence_fields": replace(
                build_scan_cadence_fields(), buy_scan_interval_seconds=777
            ),
            "build_adaptive_degraded_fields": replace(
                build_adaptive_degraded_fields(), adaptive_midday_window="01:00-02:00"
            ),
            "build_pnl_brake_regime_fields": replace(
                build_pnl_brake_regime_fields(), daily_pnl_cooldown_minutes=55
            ),
            "build_session_runtime_fields": replace(
                build_session_runtime_fields(), run_mode="scan_only"
            ),
        }
        with mock.patch.dict(os.environ, self._CRED_ENV):
            with contextlib.ExitStack() as stack:
                for name, value in sentinels.items():
                    stack.enter_context(
                        mock.patch(
                            f"app.auth.settings.{name}", return_value=value
                        )
                    )
                settings = get_settings()

        self.assertEqual(settings.symbol, "123456")
        self.assertEqual(settings.qty, 77)
        self.assertEqual(settings.buy_min_score, 9.9)
        self.assertEqual(settings.order_cooldown_minutes, 99)
        self.assertEqual(settings.sell_test_mode, "hold")
        self.assertEqual(settings.buy_daily_max_order_submissions, 123)
        self.assertEqual(settings.buy_scan_interval_seconds, 777)
        self.assertEqual(settings.adaptive_midday_window, "01:00-02:00")
        self.assertEqual(settings.daily_pnl_cooldown_minutes, 55)
        self.assertEqual(settings.run_mode, "scan_only")


class GetSettingsErrorPrecedenceTests(_EnvIsolationTestCase):
    """자격증명 누락 검증은 필드그룹 파싱 오류보다 먼저 보고되어야 한다."""

    KEYS = (
        "KIS_ENV",
        "KIS_APP_KEY",
        "KIS_APP_SECRET",
        "KIS_BASE_URL",
        "KIS_CANO",
        "KIS_ACNT_PRDT_CD",
        "KIS_APP_MOCK_KEY",
        "KIS_APP_MOCK_SECRET",
        "KIS_BASE_MOCK_URL",
        "KIS_CANO_MOCK",
        "KIS_ACNT_PRDT_CD_MOCK",
        "KIS_MOCK_APP_KEY",
        "KIS_MOCK_APP_SECRET",
        "KIS_MOCK_BASE_URL",
        "KIS_MOCK_CANO",
        "KIS_MOCK_ACNT_PRDT_CD",
        "TELEGRAM_NOTIFY_SUCCESS",
    )

    def test_missing_credentials_reported_before_group_parse_errors(self) -> None:
        os.environ["TELEGRAM_NOTIFY_SUCCESS"] = "bogus"

        with self.assertRaisesRegex(ValueError, "환경변수가 비어 있습니다"):
            get_settings()


if __name__ == "__main__":
    unittest.main()
