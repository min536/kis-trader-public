from __future__ import annotations

import unittest

import app.scanner.service as origin
from app.market_data.schema import MarketSnapshot
from app.scanner import models
from app.strategy.buy_decision import BuyDecision
from app.strategy.schema import (
    BuyDecisionSummary,
    StrategyEvaluationResult,
)


def _make_buy_decision() -> BuyDecision:
    rule = StrategyEvaluationResult(
        strategy_name="intraday_pullback",
        enabled=True,
        passed=True,
        reason="ok",
    )
    summary = BuyDecisionSummary(
        should_attempt_buy=True,
        passed_count=1,
        total_count=1,
        enabled_count=1,
        required_pass_count=1,
        final_reason="ok",
        passed_strategy_names=("intraday_pullback",),
    )
    return BuyDecision(summary=summary, evaluation_results=(rule,))


class ScannerModelsPinTests(unittest.TestCase):
    def test_symbol_analysis_result_is_reexported_identity(self) -> None:
        self.assertIs(origin.SymbolAnalysisResult, models.SymbolAnalysisResult)

    def test_scan_diagnostics_is_reexported_identity(self) -> None:
        self.assertIs(origin.ScanDiagnostics, models.ScanDiagnostics)

    def test_shallow_scan_candidate_is_reexported_identity(self) -> None:
        self.assertIs(origin.ShallowScanCandidate, models.ShallowScanCandidate)


class ScanDiagnosticsDefaultsTests(unittest.TestCase):
    def test_default_field_values(self) -> None:
        diagnostics = models.ScanDiagnostics()
        self.assertEqual(diagnostics.requested_count, 0)
        self.assertEqual(diagnostics.evaluated_count, 0)
        self.assertEqual(diagnostics.quote_request_count, 0)
        self.assertEqual(diagnostics.quote_wait_sleep_ms, 0.0)
        self.assertIsNone(diagnostics.throttle_min_sleep_ms)
        self.assertIsNone(diagnostics.interrupted_reason)
        self.assertFalse(diagnostics.rate_limit_triggered)
        self.assertEqual(diagnostics.sample_symbols, [])
        self.assertEqual(diagnostics.parse_error_skipped_symbols, [])

    def test_default_factory_lists_are_independent(self) -> None:
        first = models.ScanDiagnostics()
        second = models.ScanDiagnostics()
        first.sample_symbols.append({"symbol": "005930"})
        self.assertEqual(second.sample_symbols, [])


class ShallowScanCandidateTests(unittest.TestCase):
    def test_construct_and_display_name_with_name(self) -> None:
        candidate = models.ShallowScanCandidate(
            symbol="005930",
            name="삼성전자",
            layer="core",
            profile="momentum",
            shallow_score=1.25,
            summary="상승 추세/장중 강세 우선",
            snapshot_available=True,
            recent_seen=True,
            current_price=70_000,
            open_price=69_000,
            low_price=68_500,
            prev_day_change_pct=-0.5,
        )
        self.assertEqual(candidate.layer, "core")
        self.assertEqual(candidate.shallow_score, 1.25)
        self.assertEqual(candidate.display_name, "005930 삼성전자")

    def test_display_name_without_name(self) -> None:
        candidate = models.ShallowScanCandidate(
            symbol="000660",
            name=None,
            layer="rotating",
            profile="pullback",
            shallow_score=0.1,
            summary="눌림 후 반등 여지 우선",
            snapshot_available=False,
            recent_seen=False,
            current_price=None,
            open_price=None,
            low_price=None,
            prev_day_change_pct=None,
        )
        self.assertEqual(candidate.display_name, "000660")


class SymbolAnalysisResultTests(unittest.TestCase):
    def _make_result(self, *, name: str | None) -> models.SymbolAnalysisResult:
        snapshot = MarketSnapshot(
            symbol="005930",
            current_price=70_000,
            open_price=69_000,
            low_price=68_500,
            prev_day_change_pct=-0.5,
        )
        return models.SymbolAnalysisResult(
            symbol="005930",
            name=name,
            market_snapshot=snapshot,
            strategy_result=_make_buy_decision(),
            passed_count=1,
            signal_quality_count=1.0,
            enabled_count=1,
            candidate=True,
            final_reason="후보로 선정",
            passed_pattern="P",
            score=3.5,
            net_profit_buffer_bps=12.0,
            passes_profit_buffer=True,
            score_components={"trend_quality_score": 0.35},
            score_highlights=("trend_quality",),
            score_penalties=(),
            score_summary="가산: trend_quality | 감점: 주요 감점 요인 없음",
            expected_fee_krw=100,
            expected_tax_krw=50,
            expected_slippage_krw=30,
            expected_total_cost_krw=180,
            expected_cost_bps=2.5,
            cost_quality_score=0.3,
            expected_cost_penalty=0.0,
            net_edge_bps=9.5,
            cost_block_reason=None,
            feature_map={},
            feature_vector={},
            feature_summaries={},
            math_score_summary="base=... | cost=...",
            mean_reversion_zscore=None,
            reversion_quality_score=None,
            overextension_penalty=None,
            ou_half_life_estimate=None,
            mean_reversion_summary=None,
            portfolio_avg_correlation=None,
            portfolio_max_correlation=None,
            variance_increase_estimate=None,
            portfolio_risk_summary=None,
            portfolio_correlation_penalty=None,
            variance_increase_penalty=None,
            hist_percentile_rank=None,
            price_velocity_pct=None,
            price_dynamics_summary=None,
            sort_key=(-1, -3.5, "005930"),
        )

    def test_construct_and_display_name_with_name(self) -> None:
        result = self._make_result(name="삼성전자")
        self.assertEqual(result.symbol, "005930")
        self.assertEqual(result.score, 3.5)
        self.assertEqual(result.sort_key, (-1, -3.5, "005930"))
        self.assertEqual(result.display_name, "005930 삼성전자")

    def test_display_name_without_name(self) -> None:
        result = self._make_result(name=None)
        self.assertEqual(result.display_name, "005930")


if __name__ == "__main__":
    unittest.main()
