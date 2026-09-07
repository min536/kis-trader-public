from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.reporting.capital_scale_report import (
    build_capital_scale_report,
    capital_scale_report_key,
    render_capital_scale_report,
)


def _settings(**overrides):
    defaults = {
        "buy_max_budget_per_trade_krw": 1_000_000,
        "buy_daily_max_notional_krw": 500_000,
        "sell_daily_max_notional_krw": 7_000_000,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _snapshot(total_evaluation_amount: int):
    return SimpleNamespace(total_evaluation_amount=total_evaluation_amount)


class CapitalScaleReportTests(unittest.TestCase):
    def test_in_band_caps_produce_ok_report_without_warnings(self) -> None:
        report = build_capital_scale_report(
            settings=_settings(),
            portfolio_snapshot=_snapshot(100_000_000),
        )

        self.assertEqual(report.status, "ok")
        self.assertEqual(report.total_equity_krw, 100_000_000)
        self.assertEqual(len(report.rows), 3)
        self.assertEqual(report.warnings, ())
        ratios = {row.setting_name: row.ratio_pct for row in report.rows}
        self.assertAlmostEqual(ratios["BUY_MAX_BUDGET_PER_TRADE_KRW"], 1.0)
        self.assertAlmostEqual(ratios["BUY_DAILY_MAX_NOTIONAL_KRW"], 0.5)
        self.assertAlmostEqual(ratios["SELL_DAILY_MAX_NOTIONAL_KRW"], 7.0)

    def test_out_of_band_caps_surface_warnings(self) -> None:
        report = build_capital_scale_report(
            settings=_settings(
                buy_max_budget_per_trade_krw=5_000_000,
                buy_daily_max_notional_krw=100_000,
                sell_daily_max_notional_krw=40_000_000,
            ),
            portfolio_snapshot=_snapshot(100_000_000),
        )

        self.assertEqual(report.status, "warning")
        statuses = {row.setting_name: row.status for row in report.rows}
        self.assertEqual(statuses["BUY_MAX_BUDGET_PER_TRADE_KRW"], "high")
        self.assertEqual(statuses["BUY_DAILY_MAX_NOTIONAL_KRW"], "low")
        self.assertEqual(statuses["SELL_DAILY_MAX_NOTIONAL_KRW"], "high")
        self.assertEqual(len(report.warnings), 3)
        self.assertTrue(
            any("BUY_MAX_BUDGET_PER_TRADE_KRW" in warning for warning in report.warnings)
        )

    def test_zero_or_missing_equity_is_unjudged(self) -> None:
        report = build_capital_scale_report(
            settings=_settings(),
            portfolio_snapshot=_snapshot(0),
        )

        self.assertEqual(report.status, "unavailable")
        self.assertEqual(report.rows, ())
        self.assertEqual(report.warnings, ())
        self.assertEqual(render_capital_scale_report(report), ())

    def test_render_includes_operator_readable_lines(self) -> None:
        report = build_capital_scale_report(
            settings=_settings(),
            portfolio_snapshot=_snapshot(100_000_000),
        )

        lines = render_capital_scale_report(report)
        self.assertIn("=== 자본 스케일 점검 ===", lines[0])
        self.assertTrue(
            any("BUY_MAX_BUDGET_PER_TRADE_KRW" in line and "1.00%" in line for line in lines)
        )

    def test_report_key_changes_with_signature_or_caps_but_not_equity(self) -> None:
        settings = _settings()
        key = capital_scale_report_key(settings=settings, account_signature="sig-a")

        self.assertEqual(
            key,
            capital_scale_report_key(settings=settings, account_signature="sig-a"),
        )
        self.assertNotEqual(
            key,
            capital_scale_report_key(settings=settings, account_signature="sig-b"),
        )
        self.assertNotEqual(
            key,
            capital_scale_report_key(
                settings=_settings(buy_max_budget_per_trade_krw=2_000_000),
                account_signature="sig-a",
            ),
        )


if __name__ == "__main__":
    unittest.main()
