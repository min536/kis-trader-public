import unittest

from app.tools.operational_day_diagnostics import (
    detect_same_day_stop_loss_reentries,
    summarize_buy_notional_rows,
    summarize_sell_reason_rows,
)


class OperationalDayDiagnosticsTests(unittest.TestCase):
    def _row(
        self,
        *,
        timestamp: str,
        action: str,
        order_type: str,
        symbol: str,
        notional_krw: int | None = None,
        sell_reason: str | None = None,
    ) -> dict:
        raw_response: dict[str, object] = {}
        if notional_krw is not None:
            raw_response["order_plan"] = {"notional_krw": notional_krw}
        if sell_reason is not None:
            raw_response["sell_strategy_details"] = {"triggered_rule_name": sell_reason}
        return {
            "timestamp": timestamp,
            "action": action,
            "order_type": order_type,
            "symbol": symbol,
            "raw_response": raw_response,
        }

    def test_summarize_buy_notional_rows_reports_remaining_budget_and_late_day_blocks(self) -> None:
        rows = [
            self._row(
                timestamp="2026-04-13T09:10:00+09:00",
                action="order_submitted",
                order_type="market_buy",
                symbol="047810",
                notional_krw=50_000_000,
            ),
            self._row(
                timestamp="2026-04-13T11:05:00+09:00",
                action="order_submitted",
                order_type="market_buy",
                symbol="011200",
                notional_krw=19_900_000,
            ),
            self._row(
                timestamp="2026-04-13T11:06:00+09:00",
                action="order_succeeded",
                order_type="market_buy",
                symbol="011200",
                notional_krw=19_900_000,
            ),
            self._row(
                timestamp="2026-04-13T14:05:00+09:00",
                action="blocked_buy_daily_notional_limit",
                order_type="market_buy",
                symbol="161390",
            ),
            self._row(
                timestamp="2026-04-13T14:20:00+09:00",
                action="blocked_buy_daily_notional_limit",
                order_type="market_buy",
                symbol="010140",
            ),
            self._row(
                timestamp="2026-04-13T15:01:00+09:00",
                action="blocked_buy_daily_notional_limit",
                order_type="market_buy",
                symbol="010140",
            ),
        ]

        summary = summarize_buy_notional_rows(rows, daily_limit_krw=70_000_000)

        self.assertEqual(summary["buy_notional_used_krw"], 69_900_000)
        self.assertEqual(summary["buy_notional_remaining_krw"], 100_000)
        self.assertEqual(summary["buy_order_success_count"], 1)
        self.assertEqual(summary["blocked_buy_daily_notional_limit_count"], 3)
        self.assertEqual(
            summary["blocked_buy_daily_notional_limit_by_hour"],
            {"14:00": 2, "15:00": 1},
        )
        self.assertEqual(summary["late_day_buy_notional_blocked_count"], 3)
        self.assertEqual(
            summary["late_day_buy_notional_blocked_symbols"],
            {"010140": 2, "161390": 1},
        )
        self.assertEqual(summary["buy_notional_pressure_level"], "critical")
        self.assertEqual(
            summary["buy_notional_threshold_crossed_at"]["99%"],
            "2026-04-13T11:05:00+09:00",
        )

    def test_summarize_sell_reason_rows_flags_defensive_day(self) -> None:
        rows = [
            self._row(
                timestamp="2026-04-13T09:29:00+09:00",
                action="sell_order_succeeded",
                order_type="market_sell",
                symbol="051910",
                sell_reason="stop_loss",
            ),
            self._row(
                timestamp="2026-04-13T10:35:00+09:00",
                action="sell_order_succeeded",
                order_type="market_sell",
                symbol="012450",
                sell_reason="stop_loss",
            ),
            self._row(
                timestamp="2026-04-13T12:26:00+09:00",
                action="sell_order_succeeded",
                order_type="market_sell",
                symbol="010140",
                sell_reason="stop_loss",
            ),
            self._row(
                timestamp="2026-04-13T12:31:00+09:00",
                action="sell_order_succeeded",
                order_type="market_sell",
                symbol="003670",
                sell_reason="take_profit",
            ),
        ]

        summary = summarize_sell_reason_rows(rows)

        self.assertEqual(summary["sell_reason_distribution"], {"stop_loss": 3, "take_profit": 1})
        self.assertEqual(summary["stop_loss_ratio_pct"], 75.0)
        self.assertTrue(summary["defensive_day"])

    def test_detect_same_day_stop_loss_reentries(self) -> None:
        rows = [
            self._row(
                timestamp="2026-04-13T09:29:00+09:00",
                action="sell_order_succeeded",
                order_type="market_sell",
                symbol="051910",
                sell_reason="stop_loss",
            ),
            self._row(
                timestamp="2026-04-13T10:45:00+09:00",
                action="order_succeeded",
                order_type="market_buy",
                symbol="051910",
                notional_krw=7_500_000,
            ),
        ]

        detections = detect_same_day_stop_loss_reentries(rows)

        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0]["symbol"], "051910")
        self.assertEqual(detections[0]["minutes_gap"], 76.0)


if __name__ == "__main__":
    unittest.main()
