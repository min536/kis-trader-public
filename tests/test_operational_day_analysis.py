"""Tests for app/tools/operational_day_analysis.py"""
import unittest

from app.tools.operational_day_analysis import (
    detect_same_day_stop_loss_reentries,
    summarize_buy_notional_rows,
    summarize_sell_reason_rows,
)


def _buy_submitted(timestamp: str, symbol: str, notional_krw: int) -> dict:
    return {
        "timestamp": timestamp,
        "action": "order_submitted",
        "order_type": "market_buy",
        "result": "success",
        "symbol": symbol,
        "raw_response": {"order_plan": {"notional_krw": notional_krw}},
    }


def _buy_succeeded(timestamp: str, symbol: str, notional_krw: int) -> dict:
    return {
        "timestamp": timestamp,
        "action": "order_succeeded",
        "order_type": "market_buy",
        "result": "success",
        "symbol": symbol,
        "raw_response": {"order_plan": {"notional_krw": notional_krw}},
    }


def _buy_blocked(timestamp: str, symbol: str) -> dict:
    return {
        "timestamp": timestamp,
        "action": "blocked_buy_daily_notional_limit",
        "order_type": "market_buy",
        "symbol": symbol,
        "raw_response": {},
    }


def _sell_succeeded(timestamp: str, symbol: str, sell_reason: str) -> dict:
    return {
        "timestamp": timestamp,
        "action": "sell_order_succeeded",
        "order_type": "market_sell",
        "result": "success",
        "symbol": symbol,
        "raw_response": {
            "sell_strategy_details": {"triggered_rule_name": sell_reason}
        },
    }


class SummarizeBuyNotionalRowsTests(unittest.TestCase):
    def test_normal_day_remaining_and_utilization(self) -> None:
        rows = [
            _buy_submitted("2026-04-13T09:10:00+09:00", "047810", 30_000_000),
            _buy_succeeded("2026-04-13T09:11:00+09:00", "047810", 30_000_000),
        ]
        result = summarize_buy_notional_rows(rows, daily_limit_krw=100_000_000)

        self.assertEqual(result["submitted_notional_krw"], 30_000_000)
        self.assertEqual(result["successful_notional_krw"], 30_000_000)
        self.assertEqual(result["remaining_notional_krw"], 70_000_000)
        self.assertAlmostEqual(result["utilization_pct"], 30.0)
        self.assertEqual(result["status"], "normal")
        self.assertEqual(result["submitted_order_count"], 1)
        self.assertEqual(result["successful_order_count"], 1)

    def test_critical_status_when_over_95_pct(self) -> None:
        rows = [
            _buy_submitted("2026-04-13T09:10:00+09:00", "047810", 96_000_000),
        ]
        result = summarize_buy_notional_rows(rows, daily_limit_krw=100_000_000)

        self.assertEqual(result["status"], "critical")

    def test_warning_status_when_between_90_and_95_pct(self) -> None:
        rows = [
            _buy_submitted("2026-04-13T09:10:00+09:00", "047810", 91_000_000),
        ]
        result = summarize_buy_notional_rows(rows, daily_limit_krw=100_000_000)

        self.assertEqual(result["status"], "warning")

    def test_late_day_blocked_symbols_and_count(self) -> None:
        rows = [
            _buy_submitted("2026-04-13T09:10:00+09:00", "047810", 50_000_000),
            _buy_blocked("2026-04-13T14:05:00+09:00", "161390"),
            _buy_blocked("2026-04-13T14:20:00+09:00", "010140"),
            _buy_blocked("2026-04-13T13:55:00+09:00", "003670"),  # before 14:00 → not late
        ]
        result = summarize_buy_notional_rows(rows, daily_limit_krw=60_000_000, late_day_hour=14)

        self.assertEqual(result["blocked_daily_notional_limit_count"], 3)
        self.assertEqual(result["late_day_blocked_count"], 2)
        self.assertIn("161390", result["late_day_blocked_symbols"])
        self.assertIn("010140", result["late_day_blocked_symbols"])
        self.assertNotIn("003670", result["late_day_blocked_symbols"])

    def test_threshold_crossed_at_tracks_first_crossing(self) -> None:
        rows = [
            _buy_submitted("2026-04-13T09:10:00+09:00", "047810", 50_000_000),
            _buy_submitted("2026-04-13T10:00:00+09:00", "011200", 40_000_000),
        ]
        result = summarize_buy_notional_rows(rows, daily_limit_krw=100_000_000)

        # 50% after first row, 90% after second
        self.assertIn("80%", result["threshold_crossed_at"])
        self.assertIn("90%", result["threshold_crossed_at"])
        self.assertNotIn("95%", result["threshold_crossed_at"])
        # 80% threshold should be crossed at the second row's timestamp
        self.assertEqual(
            result["threshold_crossed_at"]["80%"],
            "2026-04-13T10:00:00+09:00",
        )

    def test_empty_rows_returns_zero_values(self) -> None:
        result = summarize_buy_notional_rows([], daily_limit_krw=100_000_000)

        self.assertEqual(result["submitted_notional_krw"], 0)
        self.assertEqual(result["remaining_notional_krw"], 100_000_000)
        self.assertEqual(result["utilization_pct"], 0.0)
        self.assertEqual(result["status"], "normal")

    def test_submitted_notional_by_hour_grouping(self) -> None:
        rows = [
            _buy_submitted("2026-04-13T09:10:00+09:00", "047810", 20_000_000),
            _buy_submitted("2026-04-13T09:30:00+09:00", "011200", 10_000_000),
            _buy_submitted("2026-04-13T11:05:00+09:00", "003670", 15_000_000),
        ]
        result = summarize_buy_notional_rows(rows, daily_limit_krw=100_000_000)

        self.assertEqual(result["submitted_notional_by_hour"]["09:00"], 30_000_000)
        self.assertEqual(result["submitted_notional_by_hour"]["11:00"], 15_000_000)

    def test_direct_notional_krw_field_in_raw_response(self) -> None:
        """raw_response에 notional_krw 가 직접 있는 경우도 추출돼야 한다."""
        rows = [
            {
                "timestamp": "2026-04-13T09:10:00+09:00",
                "action": "order_submitted",
                "order_type": "market_buy",
                "result": "success",
                "symbol": "047810",
                "raw_response": {"notional_krw": 25_000_000},
            }
        ]
        result = summarize_buy_notional_rows(rows, daily_limit_krw=100_000_000)
        self.assertEqual(result["submitted_notional_krw"], 25_000_000)


class SummarizeSellReasonRowsTests(unittest.TestCase):
    def test_defensive_day_when_stop_loss_dominant(self) -> None:
        rows = [
            _sell_succeeded("2026-04-13T09:29:00+09:00", "051910", "stop_loss"),
            _sell_succeeded("2026-04-13T10:35:00+09:00", "012450", "stop_loss"),
            _sell_succeeded("2026-04-13T12:26:00+09:00", "010140", "stop_loss"),
            _sell_succeeded("2026-04-13T12:31:00+09:00", "003670", "take_profit"),
        ]
        result = summarize_sell_reason_rows(rows)

        self.assertEqual(result["sell_success_count"], 4)
        self.assertEqual(result["sell_reason_counts"]["stop_loss"], 3)
        self.assertAlmostEqual(result["stop_loss_share_pct"], 75.0)
        self.assertTrue(result["defensive_day"])
        self.assertEqual(result["dominant_reason"], "stop_loss")

    def test_not_defensive_day_when_stop_loss_low(self) -> None:
        rows = [
            _sell_succeeded("2026-04-13T09:29:00+09:00", "051910", "take_profit"),
            _sell_succeeded("2026-04-13T10:35:00+09:00", "012450", "take_profit"),
            _sell_succeeded("2026-04-13T12:26:00+09:00", "010140", "stop_loss"),
        ]
        result = summarize_sell_reason_rows(rows)

        self.assertFalse(result["defensive_day"])
        self.assertAlmostEqual(result["stop_loss_share_pct"], 33.33, places=1)

    def test_empty_rows_returns_zero_sell_count(self) -> None:
        result = summarize_sell_reason_rows([])

        self.assertEqual(result["sell_success_count"], 0)
        self.assertFalse(result["defensive_day"])
        self.assertIsNone(result["dominant_reason"])

    def test_unknown_reason_when_no_sell_trigger_in_raw_response(self) -> None:
        rows = [
            {
                "timestamp": "2026-04-13T09:29:00+09:00",
                "action": "sell_order_succeeded",
                "order_type": "market_sell",
                "result": "success",
                "symbol": "051910",
                "raw_response": {},
            }
        ]
        result = summarize_sell_reason_rows(rows)

        self.assertIn("unknown", result["sell_reason_counts"])
        self.assertEqual(result["sell_reason_counts"]["unknown"], 1)

    def test_below_min_sell_count_not_defensive(self) -> None:
        """매도 3건 미만이면 stop_loss 비중이 높아도 defensive_day False"""
        rows = [
            _sell_succeeded("2026-04-13T09:29:00+09:00", "051910", "stop_loss"),
            _sell_succeeded("2026-04-13T10:35:00+09:00", "012450", "stop_loss"),
        ]
        result = summarize_sell_reason_rows(
            rows,
            min_sell_count_for_defensive_day=3,
        )

        self.assertFalse(result["defensive_day"])


class DetectSameDayStopLossReentrysTests(unittest.TestCase):
    def test_detects_reentry_after_stop_loss(self) -> None:
        rows = [
            {
                "timestamp": "2026-04-13T09:29:00+09:00",
                "action": "sell_order_succeeded",
                "order_type": "market_sell",
                "symbol": "051910",
                "raw_response": {
                    "sell_strategy_details": {"triggered_rule_name": "stop_loss"}
                },
            },
            {
                "timestamp": "2026-04-13T10:45:00+09:00",
                "action": "order_succeeded",
                "order_type": "market_buy",
                "symbol": "051910",
                "raw_response": {},
            },
        ]
        detections = detect_same_day_stop_loss_reentries(rows)

        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0]["symbol"], "051910")
        self.assertAlmostEqual(detections[0]["minutes_between"], 76.0)

    def test_no_reentry_when_buy_on_different_day(self) -> None:
        rows = [
            {
                "timestamp": "2026-04-13T09:29:00+09:00",
                "action": "sell_order_succeeded",
                "order_type": "market_sell",
                "symbol": "051910",
                "raw_response": {
                    "sell_strategy_details": {"triggered_rule_name": "stop_loss"}
                },
            },
            {
                "timestamp": "2026-04-14T09:00:00+09:00",
                "action": "order_succeeded",
                "order_type": "market_buy",
                "symbol": "051910",
                "raw_response": {},
            },
        ]
        detections = detect_same_day_stop_loss_reentries(rows)

        self.assertEqual(len(detections), 0)

    def test_no_reentry_when_sell_is_take_profit(self) -> None:
        rows = [
            {
                "timestamp": "2026-04-13T09:29:00+09:00",
                "action": "sell_order_succeeded",
                "order_type": "market_sell",
                "symbol": "051910",
                "raw_response": {
                    "sell_strategy_details": {"triggered_rule_name": "take_profit"}
                },
            },
            {
                "timestamp": "2026-04-13T10:45:00+09:00",
                "action": "order_succeeded",
                "order_type": "market_buy",
                "symbol": "051910",
                "raw_response": {},
            },
        ]
        detections = detect_same_day_stop_loss_reentries(rows)

        self.assertEqual(len(detections), 0)

    def test_no_reentry_when_buy_before_stop_loss_exit(self) -> None:
        rows = [
            {
                "timestamp": "2026-04-13T10:45:00+09:00",
                "action": "order_succeeded",
                "order_type": "market_buy",
                "symbol": "051910",
                "raw_response": {},
            },
            {
                "timestamp": "2026-04-13T11:29:00+09:00",
                "action": "sell_order_succeeded",
                "order_type": "market_sell",
                "symbol": "051910",
                "raw_response": {
                    "sell_strategy_details": {"triggered_rule_name": "stop_loss"}
                },
            },
        ]
        detections = detect_same_day_stop_loss_reentries(rows)

        # buy came before the stop_loss — not a reentry
        self.assertEqual(len(detections), 0)

    def test_multiple_symbols_independent(self) -> None:
        rows = [
            {
                "timestamp": "2026-04-13T09:00:00+09:00",
                "action": "sell_order_succeeded",
                "order_type": "market_sell",
                "symbol": "AAA",
                "raw_response": {
                    "sell_strategy_details": {"triggered_rule_name": "stop_loss"}
                },
            },
            {
                "timestamp": "2026-04-13T10:00:00+09:00",
                "action": "order_succeeded",
                "order_type": "market_buy",
                "symbol": "AAA",
                "raw_response": {},
            },
            {
                "timestamp": "2026-04-13T09:00:00+09:00",
                "action": "sell_order_succeeded",
                "order_type": "market_sell",
                "symbol": "BBB",
                "raw_response": {
                    "sell_strategy_details": {"triggered_rule_name": "take_profit"}
                },
            },
            {
                "timestamp": "2026-04-13T10:00:00+09:00",
                "action": "order_succeeded",
                "order_type": "market_buy",
                "symbol": "BBB",
                "raw_response": {},
            },
        ]
        detections = detect_same_day_stop_loss_reentries(rows)

        symbols = {d["symbol"] for d in detections}
        self.assertIn("AAA", symbols)
        self.assertNotIn("BBB", symbols)


if __name__ == "__main__":
    unittest.main()
