from __future__ import annotations

import unittest
from types import SimpleNamespace

import app.scanner.service as origin
from app.market_data.schema import MarketSnapshot
from app.scanner import presentation
from app.scanner.models import ShallowScanCandidate, SymbolAnalysisResult
from app.strategy.buy_decision import BuyDecision
from app.strategy.schema import BuyDecisionSummary, StrategyEvaluationResult


def _make_analysis_result(
    *,
    symbol: str,
    candidate: bool,
    passed_count: int,
    score: float,
    cost_block_reason: str | None = None,
    current_price: int = 70_000,
) -> SymbolAnalysisResult:
    snapshot = MarketSnapshot(
        symbol=symbol,
        current_price=current_price,
        open_price=69_000,
        low_price=68_500,
        prev_day_change_pct=-0.5,
    )
    rule = StrategyEvaluationResult(
        strategy_name="intraday_pullback", enabled=True, passed=True, reason="ok"
    )
    summary = BuyDecisionSummary(
        should_attempt_buy=candidate,
        passed_count=passed_count,
        total_count=1,
        enabled_count=1,
        required_pass_count=1,
        final_reason="ok",
        passed_strategy_names=("intraday_pullback",),
    )
    decision = BuyDecision(summary=summary, evaluation_results=(rule,))
    return SymbolAnalysisResult(
        symbol=symbol,
        name=None,
        market_snapshot=snapshot,
        strategy_result=decision,
        passed_count=passed_count,
        signal_quality_count=float(passed_count),
        enabled_count=1,
        candidate=candidate,
        final_reason="reason",
        passed_pattern="P",
        score=score,
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
        cost_block_reason=cost_block_reason,
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
        sort_key=(-passed_count, -score, symbol),
    )


class ScannerPresentationPinTests(unittest.TestCase):
    def test_safe_price_is_reexported_identity(self) -> None:
        self.assertIs(origin._safe_price, presentation._safe_price)

    def test_build_universe_console_lines_is_reexported_identity(self) -> None:
        self.assertIs(origin.build_universe_console_lines, presentation.build_universe_console_lines)

    def test_safe_float_is_reexported_identity(self) -> None:
        self.assertIs(origin._safe_float, presentation._safe_float)

    def test_intraday_change_pct_is_reexported_identity(self) -> None:
        self.assertIs(origin._intraday_change_pct, presentation._intraday_change_pct)

    def test_rebound_from_low_pct_is_reexported_identity(self) -> None:
        self.assertIs(origin._rebound_from_low_pct, presentation._rebound_from_low_pct)

    def test_build_shallow_scan_candidates_is_reexported_identity(self) -> None:
        self.assertIs(origin.build_shallow_scan_candidates, presentation.build_shallow_scan_candidates)

    def test_buy_status_letter_is_reexported_identity(self) -> None:
        self.assertIs(origin._buy_status_letter, presentation._buy_status_letter)

    def test_build_buy_strategy_summary_is_reexported_identity(self) -> None:
        self.assertIs(origin.build_buy_strategy_summary, presentation.build_buy_strategy_summary)

    def test_build_candidate_reason_is_reexported_identity(self) -> None:
        self.assertIs(origin.build_candidate_reason, presentation.build_candidate_reason)

    def test_select_top_candidate_is_reexported_identity(self) -> None:
        self.assertIs(origin.select_top_candidate, presentation.select_top_candidate)

    def test_select_top_analysis_result_is_reexported_identity(self) -> None:
        self.assertIs(origin.select_top_analysis_result, presentation.select_top_analysis_result)

    def test_build_selection_reason_is_reexported_identity(self) -> None:
        self.assertIs(origin.build_selection_reason, presentation.build_selection_reason)

    def test_serialize_selection_details_is_reexported_identity(self) -> None:
        self.assertIs(origin.serialize_selection_details, presentation.serialize_selection_details)

    def test_build_scan_console_lines_is_reexported_identity(self) -> None:
        self.assertIs(origin.build_scan_console_lines, presentation.build_scan_console_lines)


class SafePriceTests(unittest.TestCase):
    def test_safe_price_parses_positive_int(self) -> None:
        self.assertEqual(presentation._safe_price({"current_price": "70000"}, "current_price"), 70_000)

    def test_safe_price_non_positive_returns_none(self) -> None:
        self.assertIsNone(presentation._safe_price({"current_price": "0"}, "current_price"))

    def test_safe_price_invalid_returns_none(self) -> None:
        self.assertIsNone(presentation._safe_price({"current_price": "abc"}, "current_price"))


class SafeFloatTests(unittest.TestCase):
    def test_safe_float_parses_value(self) -> None:
        self.assertEqual(
            presentation._safe_float({"prev_day_change_pct": "-0.5"}, "prev_day_change_pct"),
            -0.5,
        )


class IntradayChangePctTests(unittest.TestCase):
    def test_positive_change(self) -> None:
        self.assertEqual(
            presentation._intraday_change_pct(current_price=10_100, open_price=10_000),
            1.0,
        )

    def test_missing_price_returns_zero(self) -> None:
        self.assertEqual(
            presentation._intraday_change_pct(current_price=None, open_price=10_000),
            0.0,
        )

    def test_nonpositive_open_returns_zero(self) -> None:
        self.assertEqual(
            presentation._intraday_change_pct(current_price=10_100, open_price=-1),
            0.0,
        )


class ReboundFromLowPctTests(unittest.TestCase):
    def test_positive_rebound(self) -> None:
        self.assertEqual(
            presentation._rebound_from_low_pct(current_price=10_200, low_price=10_000),
            2.0,
        )

    def test_missing_price_returns_zero(self) -> None:
        self.assertEqual(
            presentation._rebound_from_low_pct(current_price=10_200, low_price=None),
            0.0,
        )

    def test_nonpositive_low_returns_zero(self) -> None:
        self.assertEqual(
            presentation._rebound_from_low_pct(current_price=10_200, low_price=-1),
            0.0,
        )


class BuyStatusLetterTests(unittest.TestCase):
    def test_disabled_returns_o(self) -> None:
        result = StrategyEvaluationResult(
            strategy_name="intraday_pullback", enabled=False, passed=False, reason="off"
        )
        self.assertEqual(presentation._buy_status_letter(result), "O")

    def test_passed_returns_p(self) -> None:
        result = StrategyEvaluationResult(
            strategy_name="intraday_pullback", enabled=True, passed=True, reason="ok"
        )
        self.assertEqual(presentation._buy_status_letter(result), "P")

    def test_enabled_not_passed_returns_f(self) -> None:
        result = StrategyEvaluationResult(
            strategy_name="intraday_pullback", enabled=True, passed=False, reason="miss"
        )
        self.assertEqual(presentation._buy_status_letter(result), "F")


class BuildBuyStrategySummaryTests(unittest.TestCase):
    def test_summary_joins_status_letters(self) -> None:
        rules = (
            StrategyEvaluationResult(
                strategy_name="intraday_pullback", enabled=True, passed=True, reason="ok"
            ),
            StrategyEvaluationResult(
                strategy_name="rebound_from_low", enabled=True, passed=False, reason="miss"
            ),
            StrategyEvaluationResult(
                strategy_name="gap_down_open", enabled=False, passed=False, reason="off"
            ),
        )
        summary = BuyDecisionSummary(
            should_attempt_buy=False,
            passed_count=1,
            total_count=3,
            enabled_count=2,
            required_pass_count=2,
            final_reason="x",
            passed_strategy_names=("intraday_pullback",),
        )
        decision = BuyDecision(summary=summary, evaluation_results=rules)
        self.assertEqual(presentation.build_buy_strategy_summary(decision), "P F O")


class BuildCandidateReasonTests(unittest.TestCase):
    def test_expected_cost_too_high_branch(self) -> None:
        reason = presentation.build_candidate_reason(
            passed_count=2,
            min_passed_count=3,
            signal_quality_count=3.6,
            score=3.2,
            min_score=3.1,
            net_profit_buffer_bps=12.0,
            min_net_profit_buffer_bps=8.0,
            passes_profit_buffer=True,
            use_cost_aware_pnl=True,
            expected_cost_bps=50.0,
            expected_cost_block_bps=40.0,
            net_edge_bps=9.0,
            min_net_edge_bps=5.0,
            cost_block_reason="expected_cost_too_high",
        )
        self.assertEqual(
            reason,
            "예상 거래비용 50.00bps가 허용 기준 40.00bps를 초과해 진입을 보류했습니다.",
        )

    def test_buffer_selection_branch(self) -> None:
        reason = presentation.build_candidate_reason(
            passed_count=3,
            min_passed_count=3,
            signal_quality_count=None,
            score=3.5,
            min_score=3.1,
            net_profit_buffer_bps=12.0,
            min_net_profit_buffer_bps=8.0,
            passes_profit_buffer=True,
            use_cost_aware_pnl=True,
            expected_cost_bps=18.0,
            expected_cost_block_bps=40.0,
            net_edge_bps=9.0,
            min_net_edge_bps=5.0,
            cost_block_reason=None,
        )
        self.assertEqual(
            reason,
            "passed_count 3개와 score 3.50가 최소 기준(3, 3.10)을 충족하고 비용 포함 기대수익 버퍼 12.00bps가 최소 기준 8.00bps 이상이라 후보로 선정했습니다.",
        )

    def test_soft_signal_no_cost_filter_branch(self) -> None:
        reason = presentation.build_candidate_reason(
            passed_count=2,
            min_passed_count=3,
            signal_quality_count=3.6,
            score=3.2,
            min_score=3.1,
            net_profit_buffer_bps=12.0,
            min_net_profit_buffer_bps=8.0,
            passes_profit_buffer=True,
            use_cost_aware_pnl=False,
            expected_cost_bps=18.0,
            expected_cost_block_bps=40.0,
            net_edge_bps=9.0,
            min_net_edge_bps=5.0,
            cost_block_reason=None,
        )
        self.assertEqual(
            reason,
            "signal_quality 3.60개 상당와 score 3.20가 최소 기준(3, 3.10)을 충족했고 비용 기반 기대수익 필터는 비활성화되어 후보로 선정했습니다.",
        )

    def test_score_below_min_branch(self) -> None:
        reason = presentation.build_candidate_reason(
            passed_count=3,
            min_passed_count=3,
            signal_quality_count=None,
            score=2.0,
            min_score=3.1,
            net_profit_buffer_bps=12.0,
            min_net_profit_buffer_bps=8.0,
            passes_profit_buffer=True,
            use_cost_aware_pnl=False,
            expected_cost_bps=18.0,
            expected_cost_block_bps=40.0,
            net_edge_bps=9.0,
            min_net_edge_bps=5.0,
            cost_block_reason=None,
        )
        self.assertEqual(
            reason,
            "score 2.00가 최소 기준 3.10에 못 미쳐 후보에서 제외했습니다.",
        )


class BuildShallowScanCandidatesTests(unittest.TestCase):
    def test_momentum_rows_produce_sorted_candidate_list(self) -> None:
        snapshots = {
            "005930": {
                "current_price": "70100",
                "open_price": "70000",
                "low_price": "69500",
                "prev_day_change_pct": "0.5",
            },
        }
        result = presentation.build_shallow_scan_candidates(
            symbols=("005930", "000660"),
            profile="momentum",
            layer_by_symbol={"005930": "core", "000660": "rotating"},
            cached_snapshots=snapshots,
            max_cache_age_seconds=None,
        )
        self.assertEqual(
            result,
            (
                ShallowScanCandidate(
                    symbol="005930",
                    name="삼성전자",
                    layer="core",
                    profile="momentum",
                    shallow_score=1.1227,
                    summary="상승 추세/장중 강세 우선",
                    snapshot_available=True,
                    recent_seen=True,
                    current_price=70_100,
                    open_price=70_000,
                    low_price=69_500,
                    prev_day_change_pct=0.5,
                ),
                ShallowScanCandidate(
                    symbol="000660",
                    name="SK하이닉스",
                    layer="rotating",
                    profile="momentum",
                    shallow_score=0.35,
                    summary="시장 데이터 부족",
                    snapshot_available=False,
                    recent_seen=False,
                    current_price=None,
                    open_price=None,
                    low_price=None,
                    prev_day_change_pct=None,
                ),
            ),
        )

    def test_pullback_profile_candidate(self) -> None:
        snapshots = {
            "005930": {
                "current_price": "69800",
                "open_price": "70000",
                "low_price": "69500",
                "prev_day_change_pct": "0.5",
            },
        }
        result = presentation.build_shallow_scan_candidates(
            symbols=("005930",),
            profile="pullback",
            layer_by_symbol={"005930": "core"},
            cached_snapshots=snapshots,
            max_cache_age_seconds=None,
        )
        self.assertEqual(
            result,
            (
                ShallowScanCandidate(
                    symbol="005930",
                    name="삼성전자",
                    layer="core",
                    profile="pullback",
                    shallow_score=0.9876,
                    summary="눌림 후 반등 여지 우선",
                    snapshot_available=True,
                    recent_seen=True,
                    current_price=69_800,
                    open_price=70_000,
                    low_price=69_500,
                    prev_day_change_pct=0.5,
                ),
            ),
        )

    def test_recovery_profile_candidate(self) -> None:
        snapshots = {
            "005930": {
                "current_price": "69800",
                "open_price": "70000",
                "low_price": "69500",
                "prev_day_change_pct": "0.5",
            },
        }
        result = presentation.build_shallow_scan_candidates(
            symbols=("005930",),
            profile="recovery",
            layer_by_symbol={"005930": "core"},
            cached_snapshots=snapshots,
            max_cache_age_seconds=None,
        )
        self.assertEqual(
            result,
            (
                ShallowScanCandidate(
                    symbol="005930",
                    name="삼성전자",
                    layer="core",
                    profile="recovery",
                    shallow_score=0.4522,
                    summary="회복/리커버리 맥락 우선",
                    snapshot_available=True,
                    recent_seen=True,
                    current_price=69_800,
                    open_price=70_000,
                    low_price=69_500,
                    prev_day_change_pct=0.5,
                ),
            ),
        )


class BuildUniverseConsoleLinesTests(unittest.TestCase):
    def test_lines_match_expected(self) -> None:
        settings = SimpleNamespace(
            target_symbols_source="env",
            target_symbols_raw="005930,000660",
            target_symbols_split_items=("005930", "000660"),
            target_symbols=("005930", "000660"),
            scan_symbols_max_per_cycle=10,
            buy_scan_profile_rotation_enabled=True,
            buy_scan_exploration_ratio=0.2,
            buy_scan_core_fraction=0.6,
            buy_scan_rotating_fraction=0.3,
            buy_scan_shallow_top_k=8,
            buy_scan_deep_eval_limit=4,
            buy_scan_top_k_candidates=3,
        )
        self.assertEqual(
            presentation.build_universe_console_lines(settings),
            [
                "=== 유니버스 설정 ===",
                "유니버스 소스: env",
                "원본 유니버스 문자열: '005930,000660'",
                "split 결과(2개): ['005930', '000660']",
                "정제 후 유니버스(2개): ['005930', '000660']",
                "최종 스캔 유니버스(2개): ['005930', '000660']",
                "SCAN_SYMBOLS_MAX_PER_CYCLE=10",
                "BUY_SCAN_PROFILE_ROTATION_ENABLED=true",
                "BUY_SCAN_EXPLORATION_RATIO=0.2",
                "BUY_SCAN_CORE_FRACTION=0.6",
                "BUY_SCAN_ROTATING_FRACTION=0.3",
                "BUY_SCAN_SHALLOW_TOP_K=8",
                "BUY_SCAN_DEEP_EVAL_LIMIT=4",
                "BUY_SCAN_TOP_K_CANDIDATES=3",
            ],
        )


class SelectTopCandidateTests(unittest.TestCase):
    def test_returns_best_candidate_by_sort_key(self) -> None:
        a = _make_analysis_result(symbol="005930", candidate=True, passed_count=2, score=3.0)
        b = _make_analysis_result(symbol="000660", candidate=True, passed_count=3, score=4.0)
        c = _make_analysis_result(symbol="035720", candidate=False, passed_count=4, score=5.0)
        self.assertIs(presentation.select_top_candidate((a, b, c)), b)

    def test_no_candidates_returns_none(self) -> None:
        c = _make_analysis_result(symbol="035720", candidate=False, passed_count=4, score=5.0)
        self.assertIsNone(presentation.select_top_candidate((c,)))


class SelectTopAnalysisResultTests(unittest.TestCase):
    def test_returns_top_result_ignoring_candidate_flag(self) -> None:
        a = _make_analysis_result(symbol="005930", candidate=False, passed_count=2, score=3.0)
        b = _make_analysis_result(symbol="000660", candidate=False, passed_count=4, score=5.0)
        self.assertIs(presentation.select_top_analysis_result((a, b)), b)

    def test_empty_returns_none(self) -> None:
        self.assertIsNone(presentation.select_top_analysis_result(()))


class BuildSelectionReasonTests(unittest.TestCase):
    def test_no_results_no_candidate_message(self) -> None:
        self.assertEqual(
            presentation.build_selection_reason(selected_result=None, results=()),
            "최소 pass_count와 최소 score 기준을 만족한 후보가 없어 최종 선택 종목이 없습니다.",
        )

    def test_none_selected_with_cost_block_top(self) -> None:
        top = _make_analysis_result(
            symbol="005930",
            candidate=False,
            passed_count=3,
            score=4.0,
            cost_block_reason="net_edge_too_low",
        )
        self.assertEqual(
            presentation.build_selection_reason(selected_result=None, results=(top,)),
            "기본 전략 score는 상위권이었지만 비용 필터에서 보류되어 최종 선택 종목이 없습니다. 보류 사유: reason",
        )

    def test_sole_candidate(self) -> None:
        sel = _make_analysis_result(symbol="005930", candidate=True, passed_count=3, score=4.0)
        self.assertEqual(
            presentation.build_selection_reason(selected_result=sel, results=(sel,)),
            "pass_count와 score 기준을 동시에 만족한 유일한 후보라 선택했습니다. 선택 근거: 가산: trend_quality | 감점: 주요 감점 요인 없음",
        )

    def test_unique_passed_count(self) -> None:
        sel = _make_analysis_result(symbol="005930", candidate=True, passed_count=4, score=4.0)
        other = _make_analysis_result(symbol="000660", candidate=True, passed_count=2, score=3.0)
        self.assertEqual(
            presentation.build_selection_reason(selected_result=sel, results=(sel, other)),
            "passed_count 및 score 기준으로 후보 중 최고 순위라 선택했습니다. 선택 근거: 가산: trend_quality | 감점: 주요 감점 요인 없음",
        )

    def test_score_tiebreak(self) -> None:
        sel = _make_analysis_result(symbol="005930", candidate=True, passed_count=3, score=5.0)
        other = _make_analysis_result(symbol="000660", candidate=True, passed_count=3, score=3.0)
        self.assertEqual(
            presentation.build_selection_reason(selected_result=sel, results=(sel, other)),
            "passed_count 동률 후보 중 score가 가장 높아 선택했습니다. 선택 근거: 가산: trend_quality | 감점: 주요 감점 요인 없음",
        )

    def test_full_tiebreak_by_symbol(self) -> None:
        sel = _make_analysis_result(symbol="000660", candidate=True, passed_count=3, score=4.0)
        other = _make_analysis_result(symbol="005930", candidate=True, passed_count=3, score=4.0)
        self.assertEqual(
            presentation.build_selection_reason(selected_result=sel, results=(sel, other)),
            "passed_count와 score 동률 후보 중 종목코드 오름차순으로 선택했습니다. 선택 근거: 가산: trend_quality | 감점: 주요 감점 요인 없음",
        )


class SerializeSelectionDetailsTests(unittest.TestCase):
    def test_serializes_compact_payload(self) -> None:
        sel = _make_analysis_result(symbol="005930", candidate=True, passed_count=3, score=4.0)
        self.assertEqual(
            presentation.serialize_selection_details(selected_result=sel, results=(sel,)),
            {
                "selected_symbol": "005930",
                "selected_name": None,
                "selection_reason": "pass_count와 score 기준을 동시에 만족한 유일한 후보라 선택했습니다. 선택 근거: 가산: trend_quality | 감점: 주요 감점 요인 없음",
                "requested_universe_count": 1,
                "evaluated_count": 1,
                "top_candidate_limit": None,
                "candidates_schema": "compact_v1",
                "candidates": [
                    {
                        "symbol": "005930",
                        "name": None,
                        "candidate": True,
                        "passed_count": 3,
                        "score": 4.0,
                        "expected_cost_bps": 2.5,
                        "net_edge_bps": 9.5,
                        "cost_block_reason": None,
                        "score_summary": "가산: trend_quality | 감점: 주요 감점 요인 없음",
                        "market_snapshot": {
                            "symbol": "005930",
                            "current_price": 70_000,
                            "open_price": 69_000,
                            "low_price": 68_500,
                            "prev_day_change_pct": -0.5,
                        },
                    }
                ],
            },
        )

    def test_compact_drops_bulk_feature_fields(self) -> None:
        sel = _make_analysis_result(symbol="005930", candidate=True, passed_count=3, score=4.0)
        payload = presentation.serialize_selection_details(selected_result=sel, results=(sel,))
        candidate = payload["candidates"][0]
        for dropped in (
            "feature_map",
            "feature_vector",
            "feature_summaries",
            "score_components",
            "score_highlights",
            "score_penalties",
            "math_score_summary",
            "mean_reversion_summary",
            "portfolio_risk_summary",
        ):
            self.assertNotIn(dropped, candidate)

    def test_compact_preserves_consumer_contract_fields(self) -> None:
        # app/math_models/history.py and analyze_technical_feature_activation.py
        # read exactly symbol + market_snapshot 4-tuple from each candidate.
        sel = _make_analysis_result(symbol="005930", candidate=True, passed_count=3, score=4.0)
        candidate = presentation.serialize_selection_details(
            selected_result=sel, results=(sel,)
        )["candidates"][0]
        self.assertEqual(candidate["symbol"], "005930")
        self.assertEqual(
            candidate["market_snapshot"],
            {
                "symbol": "005930",
                "current_price": 70_000,
                "open_price": 69_000,
                "low_price": 68_500,
                "prev_day_change_pct": -0.5,
            },
        )

    def test_large_universe_serializes_under_cap(self) -> None:
        import json

        results = tuple(
            _make_analysis_result(
                symbol=f"{i:06d}", candidate=True, passed_count=3, score=4.0
            )
            for i in range(120)
        )
        payload = presentation.serialize_selection_details(
            selected_result=results[0], results=results
        )
        encoded = json.dumps(payload, ensure_ascii=False, default=str)
        # 120 candidates must stay far below the 1MB strict read cap so the
        # order-log/snapshot lines never trip fail-closed blocking again.
        self.assertLess(len(encoded.encode("utf-8")), 200_000)
        self.assertEqual(payload["candidates_schema"], "compact_v1")


class BuildScanConsoleLinesTests(unittest.TestCase):
    def test_selected_candidate_lines(self) -> None:
        sel = _make_analysis_result(symbol="005930", candidate=True, passed_count=3, score=4.0)
        self.assertEqual(
            presentation.build_scan_console_lines(
                results=(sel,),
                selected_result=sel,
                requested_count=5,
                evaluated_count=3,
                top_k=2,
            ),
            [
                "=== 종목 스캔 결과 ===",
                "BUY scan universe requested: 5",
                "BUY scan universe evaluated: 3",
                "상위 후보 비교 수(top K): 2",
                "BUY scan budget limited: YES",
                "분석 대상 종목 수: 3",
                "실제 출력 종목 수: 1",
                "005930 | B: P | 3/1 (soft 3.00) | score=4.00 | candidate",
                "  score 요약: 가산: trend_quality | 감점: 주요 감점 요인 없음",
                "  비용 요약: cost 2.5bps | net edge 9.5bps | 예상 총비용 180원",
                "  평균회귀 요약: 데이터 부족 | z=- | half-life=-",
                "  포트폴리오 위험 요약: 데이터 부족 | avg corr=- | var+=-",
                "  feature 요약: base=... | cost=...",
                "최종 선택 종목: 005930",
                "최종 score: 4.00",
                "핵심 가산 요인: trend_quality",
                "핵심 감점 요인: 없음",
                "비용 반영 요약: cost 2.5bps | net edge 9.5bps",
                "선택 근거: pass_count와 score 기준을 동시에 만족한 유일한 후보라 선택했습니다. 선택 근거: 가산: trend_quality | 감점: 주요 감점 요인 없음",
            ],
        )

    def test_no_selection_lines(self) -> None:
        rej = _make_analysis_result(symbol="005930", candidate=False, passed_count=1, score=2.0)
        self.assertEqual(
            presentation.build_scan_console_lines(results=(rej,), selected_result=None),
            [
                "=== 종목 스캔 결과 ===",
                "BUY scan universe requested: 1",
                "BUY scan universe evaluated: 1",
                "상위 후보 비교 수(top K): 1",
                "BUY scan budget limited: NO",
                "분석 대상 종목 수: 1",
                "실제 출력 종목 수: 1",
                "005930 | B: P | 1/1 (soft 1.00) | score=2.00 | reject",
                "  score 요약: 가산: trend_quality | 감점: 주요 감점 요인 없음",
                "  비용 요약: cost 2.5bps | net edge 9.5bps | 예상 총비용 180원",
                "  평균회귀 요약: 데이터 부족 | z=- | half-life=-",
                "  포트폴리오 위험 요약: 데이터 부족 | avg corr=- | var+=-",
                "  feature 요약: base=... | cost=...",
                "최종 선택 종목: 없음",
                "선택 근거: 최소 pass_count와 최소 score 기준을 만족한 후보가 없어 최종 선택 종목이 없습니다.",
            ],
        )


class SelectTopCandidatePriceFloorTests(unittest.TestCase):
    def test_excludes_candidate_at_or_below_floor(self) -> None:
        cheap = _make_analysis_result(
            symbol="000001", candidate=True, passed_count=3, score=9.0,
            current_price=1_000,
        )
        ok = _make_analysis_result(
            symbol="000002", candidate=True, passed_count=2, score=1.0,
            current_price=1_001,
        )

        selected = presentation.select_top_candidate(
            (cheap, ok), min_price_krw=1_000
        )

        self.assertIsNotNone(selected)
        self.assertEqual(selected.symbol, "000002")

    def test_returns_none_when_all_candidates_below_floor(self) -> None:
        cheap = _make_analysis_result(
            symbol="000001", candidate=True, passed_count=3, score=9.0,
            current_price=950,
        )

        self.assertIsNone(
            presentation.select_top_candidate((cheap,), min_price_krw=1_000)
        )

    def test_default_floor_zero_keeps_legacy_behavior(self) -> None:
        cheap = _make_analysis_result(
            symbol="000001", candidate=True, passed_count=3, score=9.0,
            current_price=500,
        )

        selected = presentation.select_top_candidate((cheap,))

        self.assertIsNotNone(selected)
        self.assertEqual(selected.symbol, "000001")

    def test_resolve_floor_mock_blocks_live_allows(self) -> None:
        mock_settings = SimpleNamespace(
            base_url="https://openapivts.koreainvestment.com:9443"
        )
        live_settings = SimpleNamespace(
            base_url="https://openapi.koreainvestment.com:9443"
        )

        self.assertEqual(
            presentation.resolve_mock_buy_price_floor_krw(mock_settings),
            presentation.MOCK_MIN_BUY_PRICE_KRW,
        )
        self.assertEqual(presentation.MOCK_MIN_BUY_PRICE_KRW, 1_000)
        self.assertEqual(
            presentation.resolve_mock_buy_price_floor_krw(live_settings), 0
        )


if __name__ == "__main__":
    unittest.main()
