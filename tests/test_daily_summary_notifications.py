from __future__ import annotations

import unittest

from app.notifications.daily_summary import (
    DailySummaryMetrics,
    build_conclusion,
    build_daily_summary_text,
)


class DailySummaryBuilderTests(unittest.TestCase):
    def test_clean_day_conclusion(self) -> None:
        metrics = DailySummaryMetrics(
            date_str="2026-05-01",
            session_status="ENDED",
            buy_submitted=3,
            sell_submitted=2,
            order_accepted=5,
            order_rejected=0,
            bottlenecks={},
            exception_count=0,
        )
        text = build_daily_summary_text(metrics)
        self.assertIn("[daily_summary]", text)
        self.assertIn("2026-05-01", text)
        self.assertIn("status=ENDED", text)
        self.assertIn("BUY=3", text)
        self.assertIn("SELL=2", text)
        self.assertIn("accepted=5", text)
        self.assertIn("rejected=0", text)
        self.assertIn("bottlenecks: none", text)
        self.assertIn(
            "conclusion: Session ended without major operational anomalies.",
            text,
        )

    def test_rejected_orders_change_conclusion(self) -> None:
        metrics = DailySummaryMetrics(order_rejected=2)
        self.assertIn(
            "Rejected orders occurred",
            build_conclusion(metrics),
        )

    def test_exceptions_change_conclusion(self) -> None:
        metrics = DailySummaryMetrics(exception_count=1, order_rejected=99)
        self.assertIn(
            "Exceptions occurred",
            build_conclusion(metrics),
        )

    def test_high_rate_limit_changes_conclusion(self) -> None:
        metrics = DailySummaryMetrics(
            bottlenecks={"KIS_RATE_LIMIT_BACKOFF": 7},
        )
        self.assertIn(
            "Elevated KIS rate-limit",
            build_conclusion(metrics, rate_limit_high_threshold=5),
        )

    def test_low_rate_limit_does_not_trigger_pressure_conclusion(self) -> None:
        metrics = DailySummaryMetrics(
            bottlenecks={"KIS_RATE_LIMIT_BACKOFF": 2},
        )
        self.assertIn(
            "without major operational anomalies",
            build_conclusion(metrics, rate_limit_high_threshold=5),
        )

    def test_priority_ladder_exception_beats_rate_limit(self) -> None:
        metrics = DailySummaryMetrics(
            exception_count=1,
            bottlenecks={"KIS_RATE_LIMIT_BACKOFF": 50},
        )
        self.assertIn("Exceptions occurred", build_conclusion(metrics))

    def test_pnl_lines_omitted_when_unavailable(self) -> None:
        metrics = DailySummaryMetrics()
        self.assertNotIn("pnl(", build_daily_summary_text(metrics))

    def test_pnl_lines_included_when_available(self) -> None:
        metrics = DailySummaryMetrics(
            realized_pnl_krw=12345.0,
            unrealized_pnl_krw=-678.0,
        )
        text = build_daily_summary_text(metrics)
        self.assertIn("pnl(krw):", text)
        self.assertIn("realized=+12,345", text)
        self.assertIn("unrealized=-678", text)

    def test_position_line_omitted_when_unavailable(self) -> None:
        text = build_daily_summary_text(DailySummaryMetrics())
        self.assertNotIn("positions:", text)

    def test_position_line_included_when_available(self) -> None:
        metrics = DailySummaryMetrics(start_positions=4, end_positions=3)
        text = build_daily_summary_text(metrics)
        self.assertIn("positions: start=4 end=3", text)


if __name__ == "__main__":
    unittest.main()
