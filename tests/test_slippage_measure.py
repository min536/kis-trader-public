from __future__ import annotations

import unittest

from app.reporting.slippage_measure import build_slippage_report_summary


class SlippageMeasureTest(unittest.TestCase):
    def test_build_summary_computes_per_side_slippage_bps(self) -> None:
        records = [
            # buy: exec 70035 vs ref 70000 -> +5.0 bps adverse (paid above ref)
            {
                "action": "order_succeeded",
                "order_type": "market_buy",
                "raw_response": {
                    "reference_price_krw": 70000,
                    "quote_at_submit": 70035,
                },
            },
            # buy: exec == ref -> 0.0 bps
            {
                "action": "order_succeeded",
                "order_type": "market_buy",
                "raw_response": {
                    "reference_price_krw": 70000,
                    "quote_at_submit": 70000,
                },
            },
            # buy missing reference -> counted as missing, excluded from stats
            {
                "action": "order_succeeded",
                "order_type": "market_buy",
                "raw_response": {"quote_at_submit": 70000},
            },
            # sell: exec 79960 vs ref 80000 -> +5.0 bps adverse (sold below ref)
            {
                "action": "sell_order_succeeded",
                "order_type": "market_sell",
                "raw_response": {
                    "reference_price_krw": 80000,
                    "quote_at_submit": 79960,
                },
            },
            # non-fill records are ignored entirely
            {"action": "order_submitted", "raw_response": {"reference_price_krw": 1}},
            {"action": "order_failed", "raw_response": {}},
        ]
        summary = build_slippage_report_summary(
            records,
            report_date="20260613",
            policy_buy_bps=5.0,
            policy_sell_bps=5.0,
        )

        with self.subTest(field="buy.count"):
            self.assertEqual(summary.buy.count, 2)
        with self.subTest(field="buy.missing_reference_count"):
            self.assertEqual(summary.buy.missing_reference_count, 1)
        with self.subTest(field="buy.mean_bps"):
            self.assertAlmostEqual(summary.buy.mean_bps, 2.5, places=9)
        with self.subTest(field="buy.max_bps"):
            self.assertAlmostEqual(summary.buy.max_bps, 5.0, places=9)
        with self.subTest(field="sell.count"):
            self.assertEqual(summary.sell.count, 1)
        with self.subTest(field="sell.missing_reference_count"):
            self.assertEqual(summary.sell.missing_reference_count, 0)
        with self.subTest(field="sell.max_bps"):
            self.assertAlmostEqual(summary.sell.max_bps, 5.0, places=9)
        with self.subTest(field="sample_count"):
            self.assertEqual(summary.sample_count, 3)
        with self.subTest(field="policy_buy_bps"):
            self.assertEqual(summary.policy_buy_bps, 5.0)
        with self.subTest(field="report_date"):
            self.assertEqual(summary.report_date, "20260613")


    def test_summary_composes_with_the_report_formatter(self) -> None:
        from app.reporting.slippage_report import format_slippage_report

        records = [
            {
                "action": "order_succeeded",
                "raw_response": {
                    "reference_price_krw": 70000,
                    "quote_at_submit": 70035,
                },
            },
            {
                "action": "sell_order_succeeded",
                "raw_response": {
                    "reference_price_krw": 80000,
                    "quote_at_submit": 79960,
                },
            },
        ]
        summary = build_slippage_report_summary(
            records,
            report_date="20260613",
            policy_buy_bps=5.0,
            policy_sell_bps=5.0,
        )
        text = format_slippage_report(summary)
        with self.subTest(line="header"):
            self.assertIn("Slippage report 20260613", text)
        with self.subTest(line="buy"):
            self.assertIn("BUY  n=1", text)
        with self.subTest(line="sell"):
            self.assertIn("SELL n=1", text)


    def test_equal_quote_and_reference_yields_zero_slippage_today(self) -> None:
        # Production reality: W2 stamps quote_at_submit == reference_price_krw (no
        # re-quote / no fill source yet), so submit-side slippage is 0 until that
        # lands. This locks the honest "infra ready, numbers await live-shadow" scope.
        records = [
            {
                "action": "order_succeeded",
                "raw_response": {
                    "reference_price_krw": 70000,
                    "quote_at_submit": 70000,
                },
            },
            {
                "action": "sell_order_succeeded",
                "raw_response": {
                    "reference_price_krw": 80000,
                    "quote_at_submit": 80000,
                },
            },
        ]
        summary = build_slippage_report_summary(
            records,
            report_date="20260613",
            policy_buy_bps=5.0,
            policy_sell_bps=5.0,
        )
        for field, value in (
            ("buy.mean", summary.buy.mean_bps),
            ("buy.max", summary.buy.max_bps),
            ("sell.mean", summary.sell.mean_bps),
            ("sell.max", summary.sell.max_bps),
        ):
            with self.subTest(field=field):
                self.assertEqual(value, 0.0)


    def test_defensive_against_messy_records_and_empty_input(self) -> None:
        records = [
            None,
            "garbage",
            42,
            {"action": "order_succeeded"},  # no raw_response -> missing
            {
                "action": "order_succeeded",
                "raw_response": {"reference_price_krw": "bad", "quote_at_submit": 70000},
            },  # unparseable reference -> missing
            {
                "action": "order_succeeded",
                "raw_response": {"reference_price_krw": 0, "quote_at_submit": 70000},
            },  # non-positive reference -> missing
            {
                "action": "sell_order_succeeded",
                "raw_response": {"reference_price_krw": 80000},
            },  # missing quote -> missing
        ]
        summary = build_slippage_report_summary(
            records,
            report_date="20260613",
            policy_buy_bps=5.0,
            policy_sell_bps=5.0,
        )
        with self.subTest(field="buy.count"):
            self.assertEqual(summary.buy.count, 0)
        with self.subTest(field="buy.missing_reference_count"):
            self.assertEqual(summary.buy.missing_reference_count, 3)
        with self.subTest(field="buy.mean_bps_is_none"):
            self.assertIsNone(summary.buy.mean_bps)
        with self.subTest(field="sell.missing_reference_count"):
            self.assertEqual(summary.sell.missing_reference_count, 1)
        with self.subTest(field="sample_count"):
            self.assertEqual(summary.sample_count, 0)

        empty = build_slippage_report_summary(
            [], report_date="20260613", policy_buy_bps=5.0, policy_sell_bps=5.0
        )
        with self.subTest(case="empty_input"):
            self.assertEqual(empty.sample_count, 0)


    def test_on_reference_sell_does_not_render_negative_zero(self) -> None:
        from app.reporting.slippage_report import format_slippage_report

        records = [
            {
                "action": "sell_order_succeeded",
                "raw_response": {
                    "reference_price_krw": 80000,
                    "quote_at_submit": 80000,
                },
            }
        ]
        summary = build_slippage_report_summary(
            records, report_date="20260613", policy_buy_bps=5.0, policy_sell_bps=5.0
        )
        self.assertNotIn("-0.00bps", format_slippage_report(summary))


if __name__ == "__main__":
    unittest.main()
