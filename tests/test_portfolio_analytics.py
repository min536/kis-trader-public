from __future__ import annotations

import json
import unittest

from app.portfolio.analytics import compute_portfolio_analytics
from tests.fixtures.portfolio_trade_records import (
    w3_expected_analytics_values,
    w3_trade_records,
)


class PortfolioPackageExportTest(unittest.TestCase):
    def test_analytics_api_is_re_exported_from_package_root(self) -> None:
        import app.portfolio as portfolio

        for name in (
            "compute_portfolio_analytics",
            "PortfolioAnalytics",
            "GroupMetrics",
        ):
            with self.subTest(name=name):
                self.assertIn(name, portfolio.__all__)
                self.assertTrue(hasattr(portfolio, name))


class PortfolioAnalyticsTotalTest(unittest.TestCase):
    def test_total_metrics_match_hand_calculated_fixture(self) -> None:
        analytics = compute_portfolio_analytics(w3_trade_records())
        expected = w3_expected_analytics_values()["total"]
        total = analytics.total

        cases = {
            "trade_count": (total.trade_count, expected["trade_count"]),
            "win_count": (total.win_count, expected["win_count"]),
            "loss_count": (total.loss_count, expected["loss_count"]),
            "win_rate_pct": (total.win_rate_pct, expected["win_rate_pct"]),
            "buy_notional_krw": (total.buy_notional_krw, expected["buy_notional_krw"]),
            "sell_notional_krw": (
                total.sell_notional_krw,
                expected["sell_notional_krw"],
            ),
            "gross_pnl_krw": (total.gross_pnl_krw, expected["gross_pnl_krw"]),
            "net_pnl_krw": (total.net_pnl_krw, expected["net_pnl_krw"]),
            "avg_hold_days": (total.avg_hold_days, expected["avg_hold_days"]),
        }
        for field, (actual, want) in cases.items():
            with self.subTest(field=field):
                self.assertEqual(actual, want)


class PortfolioAnalyticsSymbolTest(unittest.TestCase):
    def test_by_symbol_matches_fixture_and_sums_back_to_total(self) -> None:
        records = w3_trade_records()
        analytics = compute_portfolio_analytics(records)
        expected = w3_expected_analytics_values()["by_symbol"]

        self.assertEqual(set(analytics.by_symbol), set(expected))
        for symbol, want in expected.items():
            metrics = analytics.by_symbol[symbol]
            with self.subTest(symbol=symbol, field="trade_count"):
                self.assertEqual(metrics.trade_count, want["trade_count"])
            with self.subTest(symbol=symbol, field="win_rate_pct"):
                self.assertEqual(metrics.win_rate_pct, want["win_rate_pct"])
            with self.subTest(symbol=symbol, field="net_pnl_krw"):
                self.assertEqual(metrics.net_pnl_krw, want["net_pnl_krw"])
            with self.subTest(symbol=symbol, field="avg_hold_days"):
                self.assertEqual(metrics.avg_hold_days, want["avg_hold_days"])
            with self.subTest(symbol=symbol, field="turnover_krw"):
                self.assertEqual(
                    metrics.turnover_krw,
                    metrics.buy_notional_krw + metrics.sell_notional_krw,
                )

        with self.subTest(invariant="net_pnl_sums_back"):
            self.assertEqual(
                sum(m.net_pnl_krw for m in analytics.by_symbol.values()),
                analytics.total.net_pnl_krw,
            )
        with self.subTest(invariant="turnover_sums_back"):
            self.assertEqual(
                sum(m.turnover_krw for m in analytics.by_symbol.values()),
                analytics.total.turnover_krw,
            )


class PortfolioAnalyticsTriggerTest(unittest.TestCase):
    def test_by_trigger_matches_fixture_and_sums_back_to_total(self) -> None:
        records = w3_trade_records()
        analytics = compute_portfolio_analytics(records)
        expected = w3_expected_analytics_values()["by_trigger"]

        self.assertEqual(set(analytics.by_trigger), set(expected))
        for trigger, want in expected.items():
            metrics = analytics.by_trigger[trigger]
            with self.subTest(trigger=trigger, field="trade_count"):
                self.assertEqual(metrics.trade_count, want["trade_count"])
            with self.subTest(trigger=trigger, field="win_rate_pct"):
                self.assertEqual(metrics.win_rate_pct, want["win_rate_pct"])
            with self.subTest(trigger=trigger, field="net_pnl_krw"):
                self.assertEqual(metrics.net_pnl_krw, want["net_pnl_krw"])
            with self.subTest(trigger=trigger, field="avg_hold_days"):
                self.assertEqual(metrics.avg_hold_days, want["avg_hold_days"])

        with self.subTest(invariant="net_pnl_sums_back"):
            self.assertEqual(
                sum(m.net_pnl_krw for m in analytics.by_trigger.values()),
                analytics.total.net_pnl_krw,
            )


class PortfolioAnalyticsHoldDaysTest(unittest.TestCase):
    def test_total_hold_days_distribution(self) -> None:
        # fixture hold_days sorted = [1.0, 1.0, 2.0, 3.0]
        total = compute_portfolio_analytics(w3_trade_records()).total
        with self.subTest(stat="min"):
            self.assertEqual(total.min_hold_days, 1.0)
        with self.subTest(stat="max"):
            self.assertEqual(total.max_hold_days, 3.0)
        with self.subTest(stat="p50"):
            # linear interp at rank 1.5 -> between 1.0 and 2.0
            self.assertEqual(total.hold_days_p50, 1.5)
        with self.subTest(stat="p90"):
            # linear interp at rank 2.7 -> 2.0*0.3 + 3.0*0.7
            self.assertAlmostEqual(total.hold_days_p90, 2.7, places=9)

    def test_single_trade_percentile_is_that_value(self) -> None:
        records = [
            {
                "symbol": "005930",
                "sell_trigger": "take_profit",
                "hold_days": 4.0,
                "buy_notional_krw": 100,
                "sell_notional_krw": 110,
                "gross_pnl_krw": 10,
                "net_pnl_krw": 10,
            }
        ]
        total = compute_portfolio_analytics(records).total
        for stat, value in (
            ("min", total.min_hold_days),
            ("max", total.max_hold_days),
            ("p50", total.hold_days_p50),
            ("p90", total.hold_days_p90),
        ):
            with self.subTest(stat=stat):
                self.assertEqual(value, 4.0)


class PortfolioAnalyticsMatrixTest(unittest.TestCase):
    def test_symbol_trigger_matrix_cells_and_invariants(self) -> None:
        records = w3_trade_records()
        analytics = compute_portfolio_analytics(records)
        matrix = analytics.by_symbol_trigger

        with self.subTest(check="symbol_keys"):
            self.assertEqual(set(matrix), set(analytics.by_symbol))
        with self.subTest(check="005930_triggers"):
            self.assertEqual(set(matrix["005930"]), {"take_profit", "stop_loss"})

        expected_cells = {
            ("005930", "take_profit"): 25_000,
            ("005930", "stop_loss"): -18_000,
            ("000660", "take_profit"): 24_000,
            ("035420", "time_exit"): -7_000,
        }
        for (symbol, trigger), want_net in expected_cells.items():
            cell = matrix[symbol][trigger]
            with self.subTest(cell=(symbol, trigger), field="net_pnl_krw"):
                self.assertEqual(cell.net_pnl_krw, want_net)
            with self.subTest(cell=(symbol, trigger), field="trade_count"):
                self.assertEqual(cell.trade_count, 1)

        with self.subTest(invariant="cells_sum_to_total"):
            self.assertEqual(
                sum(
                    cell.net_pnl_krw
                    for triggers in matrix.values()
                    for cell in triggers.values()
                ),
                analytics.total.net_pnl_krw,
            )
        with self.subTest(invariant="cells_sum_per_symbol"):
            for symbol, triggers in matrix.items():
                self.assertEqual(
                    sum(cell.net_pnl_krw for cell in triggers.values()),
                    analytics.by_symbol[symbol].net_pnl_krw,
                )


class PortfolioAnalyticsEdgeCaseTest(unittest.TestCase):
    def test_empty_records_yield_zeroed_total_and_empty_groups(self) -> None:
        analytics = compute_portfolio_analytics(())
        total = analytics.total
        with self.subTest(field="trade_count"):
            self.assertEqual(total.trade_count, 0)
        with self.subTest(field="win_rate_pct"):
            self.assertEqual(total.win_rate_pct, 0.0)
        with self.subTest(field="avg_hold_days"):
            self.assertEqual(total.avg_hold_days, 0.0)
        with self.subTest(field="net_pnl_krw"):
            self.assertEqual(total.net_pnl_krw, 0)
        with self.subTest(field="hold_days_p90"):
            self.assertEqual(total.hold_days_p90, 0.0)
        with self.subTest(group="by_symbol"):
            self.assertEqual(analytics.by_symbol, {})
        with self.subTest(group="by_trigger"):
            self.assertEqual(analytics.by_trigger, {})
        with self.subTest(group="by_symbol_trigger"):
            self.assertEqual(analytics.by_symbol_trigger, {})

    def test_breakeven_trade_is_neither_win_nor_loss(self) -> None:
        records = [
            {"symbol": "A", "sell_trigger": "t", "hold_days": 1.0, "net_pnl_krw": 10},
            {"symbol": "A", "sell_trigger": "t", "hold_days": 1.0, "net_pnl_krw": 0},
        ]
        total = compute_portfolio_analytics(records).total
        with self.subTest(field="win_count"):
            self.assertEqual(total.win_count, 1)
        with self.subTest(field="loss_count"):
            self.assertEqual(total.loss_count, 0)
        with self.subTest(field="breakeven_count"):
            self.assertEqual(total.breakeven_count, 1)
        with self.subTest(field="win_rate_pct"):
            self.assertEqual(total.win_rate_pct, 50.0)

    def test_string_and_invalid_numeric_values_are_coerced(self) -> None:
        records = [
            {
                "symbol": "A",
                "sell_trigger": "t",
                "hold_days": "2.0",
                "buy_notional_krw": "700000",
                "sell_notional_krw": "730000",
                "gross_pnl_krw": "30000",
                "net_pnl_krw": "25000",
            },
            {
                "symbol": "A",
                "sell_trigger": "t",
                "hold_days": None,
                "buy_notional_krw": "oops",
                "sell_notional_krw": None,
                "gross_pnl_krw": "",
                "net_pnl_krw": "bad",
            },
        ]
        total = compute_portfolio_analytics(records).total
        with self.subTest(field="net_pnl_krw"):
            self.assertEqual(total.net_pnl_krw, 25_000)
        with self.subTest(field="buy_notional_krw"):
            self.assertEqual(total.buy_notional_krw, 700_000)
        with self.subTest(field="avg_hold_days"):
            self.assertEqual(total.avg_hold_days, 1.0)  # (2.0 + 0.0) / 2
        with self.subTest(field="win_count"):
            self.assertEqual(total.win_count, 1)
        with self.subTest(field="breakeven_count"):
            self.assertEqual(total.breakeven_count, 1)  # invalid net -> 0

    def test_non_mapping_records_are_skipped_not_fatal(self) -> None:
        records = [
            None,
            "garbage",
            42,
            ["a", "b"],
            {
                "symbol": "A",
                "sell_trigger": "t",
                "hold_days": 1.0,
                "buy_notional_krw": 1000,
                "sell_notional_krw": 1100,
                "gross_pnl_krw": 120,
                "net_pnl_krw": 100,
            },
        ]
        analytics = compute_portfolio_analytics(records)  # must not raise
        with self.subTest(field="trade_count"):
            self.assertEqual(analytics.total.trade_count, 1)
        with self.subTest(field="net_pnl_krw"):
            self.assertEqual(analytics.total.net_pnl_krw, 100)
        with self.subTest(group="by_symbol"):
            self.assertEqual(set(analytics.by_symbol), {"A"})

    def test_non_finite_derived_avg_hold_days_stays_json_safe(self) -> None:
        # Each input hold_days is finite, but their sum overflows to inf; the
        # derived avg must not leak a non-finite value into the JSON surface.
        records = [
            {"symbol": "A", "sell_trigger": "t", "hold_days": 1e308, "net_pnl_krw": 1},
            {"symbol": "A", "sell_trigger": "t", "hold_days": 1e308, "net_pnl_krw": 1},
        ]
        analytics = compute_portfolio_analytics(records)
        with self.subTest(check="avg_coerced_finite"):
            self.assertEqual(analytics.total.avg_hold_days, 0.0)
        with self.subTest(check="strict_json_does_not_raise"):
            json.dumps(analytics.to_dict(), allow_nan=False)

    def test_large_integer_values_are_preserved_exactly(self) -> None:
        big = 9_007_199_254_740_993  # 2**53 + 1: not exactly float-representable
        huge = 10**400  # float(huge) raises OverflowError
        records = [
            {
                "symbol": "A",
                "sell_trigger": "t",
                "hold_days": 1.0,
                "net_pnl_krw": big,
                "buy_notional_krw": huge,
            }
        ]
        total = compute_portfolio_analytics(records).total
        with self.subTest(case="2**53+1_exact"):
            self.assertEqual(total.net_pnl_krw, big)
        with self.subTest(case="10**400_not_zeroed"):
            self.assertEqual(total.buy_notional_krw, huge)

    def test_to_dict_is_json_serializable_and_round_trips(self) -> None:
        analytics = compute_portfolio_analytics(w3_trade_records())
        rendered = json.dumps(analytics.to_dict())
        parsed = json.loads(rendered)
        with self.subTest(path="total.net_pnl_krw"):
            self.assertEqual(parsed["total"]["net_pnl_krw"], 24_000)
        with self.subTest(path="by_symbol.005930.win_rate_pct"):
            self.assertEqual(parsed["by_symbol"]["005930"]["win_rate_pct"], 50.0)
        with self.subTest(path="by_trigger.take_profit.net_pnl_krw"):
            self.assertEqual(parsed["by_trigger"]["take_profit"]["net_pnl_krw"], 49_000)
        with self.subTest(path="matrix.005930.take_profit.net_pnl_krw"):
            self.assertEqual(
                parsed["by_symbol_trigger"]["005930"]["take_profit"]["net_pnl_krw"],
                25_000,
            )


if __name__ == "__main__":
    unittest.main()
