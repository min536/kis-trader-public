"""Tests for app.reporting.performance_report (R3-S7).

performance.py의 리포트 계층 중 사이클-프리 부분집합(trade quality,
integrity warnings, persist + _save 2종, console lines)을 verbatim 이동.
build_performance_report 본체는 리스크 클러스터(_build_account_state,
select_risk_managed_current_equity_krw)를 직접 호출하므로 performance에
잔존한다 (R6 게이트 + 순환 import 금지). performance는 facade로 동일
객체를 재수출해야 한다.
"""

from __future__ import annotations

import unittest

from app.reporting import performance, performance_report

_REPORT_NAMES = (
    "_save_performance_snapshot",
    "_save_performance_report",
    "_build_trade_quality_summary",
    "_build_integrity_warnings",
    "persist_performance_report",
    "build_performance_console_lines",
)


class SaveHelpersTests(unittest.TestCase):
    def test_save_performance_snapshot_appends_jsonl_line(self) -> None:
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "snapshots" / "performance_snapshots.jsonl"
            with mock.patch.object(
                performance_report,
                "get_performance_snapshots_path",
                return_value=target,
            ):
                saved = performance_report._save_performance_snapshot(
                    {"total_equity_krw": 1}
                )
            self.assertTrue(saved)
            lines = target.read_text(encoding="utf-8").splitlines()
            self.assertEqual(json.loads(lines[0]), {"total_equity_krw": 1})

    def test_save_performance_snapshot_returns_false_on_oserror(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp_dir:
            with mock.patch.object(
                performance_report,
                "get_performance_snapshots_path",
                return_value=Path(tmp_dir),
            ):
                saved = performance_report._save_performance_snapshot({"k": 1})
        self.assertFalse(saved)

    def test_save_performance_report_appends_jsonl_line(self) -> None:
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "summary" / "performance_summary.jsonl"
            with mock.patch.object(
                performance_report,
                "get_performance_summary_path",
                return_value=target,
            ):
                saved = performance_report._save_performance_report({"report": 1})
            self.assertTrue(saved)
            lines = target.read_text(encoding="utf-8").splitlines()
            self.assertEqual(json.loads(lines[0]), {"report": 1})

    def test_save_performance_report_returns_false_on_oserror(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp_dir:
            with mock.patch.object(
                performance_report,
                "get_performance_summary_path",
                return_value=Path(tmp_dir),
            ):
                saved = performance_report._save_performance_report({"k": 1})
        self.assertFalse(saved)


class PersistPerformanceReportTests(unittest.TestCase):
    def test_persists_equity_snapshot_and_full_report(self) -> None:
        from unittest import mock

        with mock.patch.object(
            performance_report, "_save_performance_snapshot", return_value=True
        ) as save_snapshot, mock.patch.object(
            performance_report, "_save_performance_report", return_value=False
        ) as save_report:
            result = performance_report.persist_performance_report(
                {"equity": {"total_equity_krw": 1}, "other": "x"}
            )

        save_snapshot.assert_called_once_with({"total_equity_krw": 1})
        save_report.assert_called_once_with(
            {"equity": {"total_equity_krw": 1}, "other": "x"}
        )
        self.assertEqual(
            result, {"snapshot_saved": True, "report_saved": False}
        )


class TradeQualitySummaryTests(unittest.TestCase):
    def test_aggregates_buy_costs_and_sell_results(self) -> None:
        records = [
            {
                "action": "order_succeeded",
                "raw_response": {
                    "position_sizing": {
                        "estimated_buy_fee_krw": 10,
                        "estimated_buy_slippage_krw": 5,
                        "recommended_notional_krw": 1_000,
                    }
                },
            },
            {
                "action": "sell_order_succeeded",
                "raw_response": {
                    "sell_plan": {
                        "estimated_sell_fee_krw": 7,
                        "estimated_sell_tax_krw": 3,
                        "estimated_sell_slippage_krw": 2,
                        "notional_krw": 900,
                    },
                    "sell_strategy_details": {
                        "details": {"gross_pnl_krw": 120, "net_pnl_krw": 100}
                    },
                },
            },
            {
                "action": "sell_order_succeeded",
                "raw_response": {
                    "sell_plan": {},
                    "sell_strategy_details": {
                        "details": {"gross_pnl_krw": -40, "net_pnl_krw": -50}
                    },
                },
            },
            {"action": "order_succeeded", "raw_response": "not-a-dict"},
        ]

        summary = performance_report._build_trade_quality_summary(records)

        self.assertEqual(summary["gross_pnl_krw"], 80)
        self.assertEqual(summary["net_pnl_krw"], 50)
        self.assertEqual(summary["cumulative_fee_krw"], 17)
        self.assertEqual(summary["cumulative_tax_krw"], 3)
        self.assertEqual(summary["cumulative_slippage_krw"], 7)
        self.assertEqual(summary["total_buy_notional_krw"], 1_000)
        self.assertEqual(summary["total_sell_notional_krw"], 900)
        self.assertEqual(summary["win_rate_pct"], 50.0)
        self.assertEqual(summary["profit_factor"], 2.0)
        self.assertEqual(summary["average_win_krw"], 100)
        self.assertEqual(summary["average_loss_krw"], -50)


def _minimal_report() -> dict:
    return {
        "account_signature": "sig",
        "masked_account_display": "12******",
        "account_summary": {
            "operating_equity_krw": 1_000,
            "cash_total_krw": 600,
            "orderable_cash_krw": 500,
            "cash_next_day_krw": 550,
            "holdings_market_value_krw": 400,
            "total_cost_basis_krw": 380,
            "realized_gross_pnl_krw": 10,
            "realized_net_pnl_krw": 8,
            "total_unrealized_gross_pnl_krw": 20,
            "total_unrealized_net_pnl_krw": 15,
            "total_return_pct": 1.5,
            "cash_weight_pct": 60.0,
            "positions_count": 1,
            "realized_pnl_basis": "basis-text",
        },
        "positions": [
            {
                "symbol": "005930",
                "name": "삼성전자",
                "holding_qty": 2,
                "average_cost_krw": 190,
                "current_price_krw": 200,
                "evaluation_amount_krw": 400,
                "net_pnl_krw": 20,
                "net_pnl_pct": 5.0,
                "weight_pct": 40.0,
            }
        ],
        "absolute_performance": {
            "cumulative_return_pct": 1.5,
            "daily_return_pct": None,
            "weekly_return_pct": 0.5,
            "monthly_return_pct": None,
        },
        "benchmark_performance": {
            "benchmark_symbol": "069500",
            "benchmark_name": "KODEX200",
            "excess_daily_return_pct": None,
            "excess_weekly_return_pct": 0.1,
            "excess_monthly_return_pct": None,
            "excess_return_pct": 0.2,
            "information_ratio": None,
        },
        "risk_adjusted_performance": {
            "sharpe_ratio": 1.2345,
            "sortino_ratio": None,
        },
        "attribution": {
            "selection_contribution_pct": 0.3,
            "timing_contribution_pct": None,
            "cost_drag_pct": 0.1,
            "gross_alpha_before_cost_pct": None,
        },
        "drawdown": {
            "current_drawdown_pct": -1.0,
            "max_drawdown_pct": -3.0,
            "calmar_ratio": None,
        },
        "cost_and_execution": {
            "gross_pnl_krw": 30,
            "net_pnl_krw": 23,
            "cumulative_fee_krw": 4,
            "cumulative_tax_krw": 2,
            "cumulative_slippage_krw": 1,
            "turnover_pct": 12.0,
            "win_rate_pct": 50.0,
            "profit_factor": None,
            "average_win_krw": 100,
            "average_loss_krw": -50,
        },
        "portfolio_concentration": {
            "cash_weight_pct": 60.0,
            "top3_concentration_pct": 40.0,
            "positions_count": 1,
        },
        "equity": {"total_equity_krw": 1_000},
    }


class ConsoleLinesTests(unittest.TestCase):
    def test_renders_all_sections_from_report(self) -> None:
        lines = performance_report.build_performance_console_lines(
            _minimal_report()
        )

        joined = "\n".join(lines)
        self.assertEqual(lines[0], "=== 성과 요약 ===")
        self.assertIn("운영 equity 1,000원", joined)
        self.assertIn(
            "손익 | 실현 gross +10원 | 실현 net +8원 | "
            "미실현 gross +20원 | 미실현 net +15원",
            lines,
        )
        self.assertIn(
            "계좌 상태 | 누적수익률 +1.50% | 현금 비중 60.00% | 보유 종목 수 1",
            lines,
        )
        self.assertIn(
            "계산 기준 | 운영 equity = 주문가능현금 + 보유 평가금액 | "
            "실현손익 기준: basis-text",
            lines,
        )
        self.assertIn("005930 삼성전자", joined)
        self.assertIn("Profit factor 표본 부족", joined)
        self.assertIn(
            "절대 성과 | 누적 +1.50% | 일별 데이터 부족 | 주별 +0.50% | 월별 데이터 부족",
            lines,
        )
        self.assertIn("069500 KODEX200", joined)
        self.assertIn("Sharpe 1.2345", joined)
        self.assertIn("상위3 집중도 40.00%", joined)


class PerformanceReportFacadeTests(unittest.TestCase):
    def test_performance_binds_canonical_objects(self) -> None:
        for name in _REPORT_NAMES:
            with self.subTest(name):
                self.assertIs(
                    getattr(performance, name),
                    getattr(performance_report, name),
                )


if __name__ == "__main__":
    unittest.main()
