"""Characterization tests for rebalance helper functions in app/main.py.

Locks down current behaviour before Stage 2c extraction.
"""

from __future__ import annotations

import io
import unittest
from types import SimpleNamespace

from unittest.mock import patch

from app.execution.rebalance import (
    build_buy_analysis_block_context as _build_buy_analysis_block_context,
    build_concentration_metrics as _build_concentration_metrics,
    build_position_sizing_block_context as _build_position_sizing_block_context,
    build_rebalance_pair_evaluation as _build_rebalance_pair_evaluation,
    build_quality_rebalance_preview as _build_quality_rebalance_preview,
    position_sizing_requires_rebalance as _position_sizing_requires_rebalance,
    print_quality_rebalance_preview as _print_quality_rebalance_preview,
    print_rebalance_preview as _print_rebalance_preview,
    print_rebalance_skip as _print_rebalance_skip,
    serialize_rebalance_holding_option as _serialize_rebalance_holding_option,
    serialize_replacement_candidate_option as _serialize_replacement_candidate_option,
)


def _position_sizing(recommended_qty: int, block_reason_code: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        recommended_qty=recommended_qty,
        reason="",
        details={"block_reason_code": block_reason_code, "block_reason_label": ""},
    )


class PositionSizingRequiresRebalanceTests(unittest.TestCase):

    def test_true_when_qty_zero_and_cash_insufficient(self) -> None:
        ps = _position_sizing(recommended_qty=0, block_reason_code="cash_insufficient")
        self.assertTrue(_position_sizing_requires_rebalance(ps))

    def test_false_when_qty_positive_despite_cash_insufficient(self) -> None:
        ps = _position_sizing(recommended_qty=1, block_reason_code="cash_insufficient")
        self.assertFalse(_position_sizing_requires_rebalance(ps))

    def test_false_when_qty_zero_but_different_block_code(self) -> None:
        ps = _position_sizing(recommended_qty=0, block_reason_code="exposure_limited")
        self.assertFalse(_position_sizing_requires_rebalance(ps))


class BuildConcentrationMetricsTests(unittest.TestCase):

    def test_empty_positions_and_no_cash_returns_zero_equity(self) -> None:
        result = _build_concentration_metrics(cash_orderable_krw=0, position_values={})
        self.assertEqual(result["operating_equity_krw"], 0)
        self.assertEqual(result["top1_weight_pct"], 0.0)
        self.assertEqual(result["positions"], [])

    def test_single_position_weight_100_pct(self) -> None:
        result = _build_concentration_metrics(
            cash_orderable_krw=0,
            position_values={"005930": 1_000_000},
        )
        self.assertEqual(result["top1_weight_pct"], 100.0)
        self.assertEqual(len(result["positions"]), 1)

    def test_positions_sorted_by_value_descending(self) -> None:
        result = _build_concentration_metrics(
            cash_orderable_krw=0,
            position_values={"A": 300_000, "B": 500_000, "C": 200_000},
        )
        symbols = [p["symbol"] for p in result["positions"]]
        self.assertEqual(symbols, ["B", "A", "C"])

    def test_cash_included_in_operating_equity(self) -> None:
        result = _build_concentration_metrics(
            cash_orderable_krw=500_000,
            position_values={"A": 500_000},
        )
        self.assertEqual(result["operating_equity_krw"], 1_000_000)
        self.assertAlmostEqual(result["top1_weight_pct"], 50.0, places=1)

    def test_zero_value_positions_excluded(self) -> None:
        result = _build_concentration_metrics(
            cash_orderable_krw=0,
            position_values={"A": 1_000_000, "B": 0},
        )
        symbols = [p["symbol"] for p in result["positions"]]
        self.assertNotIn("B", symbols)


def _buy_result(cost_block_reason: str = "", final_reason: str = "이유") -> SimpleNamespace:
    return SimpleNamespace(cost_block_reason=cost_block_reason, final_reason=final_reason)


class BuildBuyAnalysisBlockContextTests(unittest.TestCase):

    def test_expected_cost_too_high_branch(self) -> None:
        ctx = _build_buy_analysis_block_context(
            _buy_result(cost_block_reason="expected_cost_too_high", final_reason="비용 과다")
        )
        self.assertEqual(ctx["action"], "blocked_buy_expected_cost_too_high")
        self.assertEqual(ctx["cycle_action"], "BUY_BLOCKED_EXPECTED_COST_TOO_HIGH")
        self.assertEqual(ctx["reason"], "비용 과다")

    def test_net_edge_too_low_branch(self) -> None:
        ctx = _build_buy_analysis_block_context(
            _buy_result(cost_block_reason="net_edge_too_low", final_reason="순우위 부족")
        )
        self.assertEqual(ctx["action"], "blocked_buy_net_edge_too_low")
        self.assertEqual(ctx["cycle_action"], "BUY_BLOCKED_NET_EDGE_TOO_LOW")

    def test_fallback_is_hold_no_signal(self) -> None:
        ctx = _build_buy_analysis_block_context(_buy_result(cost_block_reason="", final_reason="전략 거부"))
        self.assertEqual(ctx["action"], "blocked_strategy_rejected")
        self.assertEqual(ctx["cycle_action"], "HOLD_NO_SIGNAL")
        self.assertEqual(ctx["reason"], "전략 거부")


class BuildPositionSizingBlockContextTests(unittest.TestCase):

    def test_cash_insufficient_action(self) -> None:
        ps = _position_sizing(recommended_qty=0, block_reason_code="cash_insufficient")
        ctx = _build_position_sizing_block_context(ps)
        self.assertEqual(ctx["action"], "blocked_buy_cash_insufficient")
        self.assertEqual(ctx["cycle_action"], "BUY_BLOCKED_CASH_INSUFFICIENT")

    def test_exposure_limited_action(self) -> None:
        ps = _position_sizing(recommended_qty=0, block_reason_code="exposure_limited")
        ctx = _build_position_sizing_block_context(ps)
        self.assertEqual(ctx["action"], "blocked_buy_exposure_limited")

    def test_trade_budget_limited_action(self) -> None:
        ps = _position_sizing(recommended_qty=0, block_reason_code="trade_budget_limited")
        ctx = _build_position_sizing_block_context(ps)
        self.assertEqual(ctx["action"], "blocked_buy_trade_budget_limited")

    def test_fallback_code_is_qty_or_price_limited(self) -> None:
        ps = _position_sizing(recommended_qty=0, block_reason_code="")
        ctx = _build_position_sizing_block_context(ps)
        self.assertEqual(ctx["code"], "qty_or_price_limited")
        self.assertEqual(ctx["action"], "blocked_buy_qty_or_price_limited")


def _holding_option(**kwargs) -> dict:
    defaults = dict(
        current_holding_score=7.1234,
        holding_quality_score=3.5678,
        replacement_pressure_score=1.9999,
        trend_break_penalty=0.1111,
        momentum_decay_penalty=0.2222,
        net_pnl_bps=120.456,
        score_delta=0.3333,
        cost_adjusted_delta=0.4444,
        replaceability_score=0.5555,
        quality_optimizer_score=0.6666,
    )
    defaults.update(kwargs)
    defaults["analysis"] = SimpleNamespace(symbol="005930", display_name="삼성전자")
    return defaults


class SerializeRebalanceHoldingOptionTests(unittest.TestCase):

    def test_output_keys_match_expected_schema(self) -> None:
        out = _serialize_rebalance_holding_option(_holding_option())
        expected_keys = {
            "symbol", "display_name", "holding_score", "holding_quality_score",
            "replacement_pressure_score", "trend_break_penalty", "momentum_decay_penalty",
            "net_pnl_bps", "score_delta", "cost_adjusted_delta",
            "replaceability_score", "quality_optimizer_score",
        }
        self.assertEqual(set(out.keys()), expected_keys)

    def test_numeric_fields_rounded_to_two_decimal_places(self) -> None:
        out = _serialize_rebalance_holding_option(_holding_option())
        self.assertEqual(out["holding_score"], round(7.1234, 2))
        self.assertEqual(out["score_delta"], round(0.3333, 2))

    def test_net_pnl_bps_rounded_to_one_decimal(self) -> None:
        out = _serialize_rebalance_holding_option(_holding_option())
        self.assertEqual(out["net_pnl_bps"], round(120.456, 1))


def _replacement_candidate(**kwargs) -> SimpleNamespace:
    defaults = dict(
        symbol="000660",
        display_name="SK하이닉스",
        score=8.75,
        score_summary="요약",
        score_highlights=["항목1"],
        score_penalties=["패널티1"],
        expected_total_cost_krw=15000,
        expected_cost_bps=12.345,
        net_edge_bps=5.678,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class SerializeReplacementCandidateOptionTests(unittest.TestCase):

    def test_output_keys_match_expected_schema(self) -> None:
        out = _serialize_replacement_candidate_option(_replacement_candidate())
        expected_keys = {
            "symbol", "display_name", "score", "score_summary",
            "score_highlights", "score_penalties",
            "expected_total_cost_krw", "expected_cost_bps", "net_edge_bps",
        }
        self.assertEqual(set(out.keys()), expected_keys)

    def test_score_rounded_to_two_decimal_places(self) -> None:
        out = _serialize_replacement_candidate_option(_replacement_candidate(score=8.756))
        self.assertEqual(out["score"], 8.76)

    def test_cost_bps_rounded_to_one_decimal(self) -> None:
        out = _serialize_replacement_candidate_option(_replacement_candidate(expected_cost_bps=12.345))
        self.assertEqual(out["expected_cost_bps"], 12.3)


# ---------------------------------------------------------------------------
# Shared helpers for pair evaluation / quality preview tests
# ---------------------------------------------------------------------------

_PATCH_SELECTION_SCORE = "app.execution.rebalance.calculate_selection_score"
_PATCH_SELL_SIZING = "app.execution.rebalance.calculate_sell_position_sizing"
_PATCH_CONCENTRATION = "app.execution.rebalance.estimate_rebalance_concentration_preview"
_PATCH_PAIR_EVAL = "app.execution.rebalance.build_rebalance_pair_evaluation"


def _fake_concentration_preview(**kwargs) -> dict:
    defaults = dict(
        concentration_penalty=0.0,
        concentration_ok=True,
        comment="집중도 변화 없음",
        before={"top1_weight_pct": 25.0, "top3_weight_pct": 60.0, "positions": []},
        after={"top1_weight_pct": 25.0, "top3_weight_pct": 60.0, "positions": []},
    )
    defaults.update(kwargs)
    return defaults


def _fake_sell_sizing(net_proceeds_krw: int = 200_000) -> SimpleNamespace:
    return SimpleNamespace(
        recommended_sell_qty=10,
        details={"estimated_net_proceeds_krw": net_proceeds_krw},
    )


def _pair_analysis(symbol: str = "000001", sell_details: dict | None = None) -> SimpleNamespace:
    if sell_details is None:
        sell_details = {
            "holding_quality_score": 3.0,
            "replacement_pressure_score": 1.0,
            "trend_break_penalty": 0.0,
            "momentum_decay_penalty": 0.0,
            "net_pnl_bps": 100.0,
        }
    return SimpleNamespace(
        symbol=symbol,
        display_name=symbol,
        holding_qty=10,
        market_snapshot=SimpleNamespace(current_price=10_000),
        buy_strategy_result=SimpleNamespace(),
        sell_decision=SimpleNamespace(details=sell_details),
    )


def _pair_candidate(
    symbol: str = "999999",
    score: float = 8.0,
    net_edge_bps: float = 50.0,
    current_price: int = 10_000,
) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        display_name=symbol,
        score=score,
        net_edge_bps=net_edge_bps,
        cost_quality_score=0.5,
        expected_cost_penalty=0.1,
        market_snapshot=SimpleNamespace(current_price=current_price),
    )


def _fake_portfolio() -> SimpleNamespace:
    return SimpleNamespace(held_positions=[], cash_orderable=100_000)


def _settings(**kwargs) -> SimpleNamespace:
    defaults = dict(
        rebalance_min_score_delta=0.05,
        rebalance_min_profit_buffer_bps=-50.0,
        rebalance_min_net_edge_bps=0.0,
        rebalance_max_concentration_pct=40.0,
        buy_rule_rebound_from_low_pct=0.0,
        buy_rule_controlled_down_day_min=0,
        buy_rule_controlled_down_day_max=0,
        buy_rule_gap_down_open_min_pct=0.0,
        buy_rule_gap_down_open_max_pct=0.0,
        buy_rule_range_recovery_min_ratio=0.0,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _call_pair_eval(
    analysis=None,
    candidate=None,
    settings=None,
    holding_score: float = 5.0,
    sell_proceeds: int = 200_000,
    concentration_kwargs: dict | None = None,
) -> dict:
    analysis = analysis or _pair_analysis()
    candidate = candidate or _pair_candidate()
    settings = settings or _settings()
    concentration = _fake_concentration_preview(**(concentration_kwargs or {}))
    with (
        patch(_PATCH_SELECTION_SCORE, return_value=(holding_score, {})),
        patch(_PATCH_SELL_SIZING, return_value=_fake_sell_sizing(sell_proceeds)),
        patch(_PATCH_CONCENTRATION, return_value=concentration),
    ):
        return _build_rebalance_pair_evaluation(
            analysis=analysis,
            replacement_candidate=candidate,
            portfolio_snapshot=_fake_portfolio(),
            settings=settings,
        )


class BuildRebalancePairEvaluationTests(unittest.TestCase):

    def test_output_contains_required_keys(self) -> None:
        result = _call_pair_eval()
        required = {
            "analysis", "replacement_candidate", "current_holding_score",
            "score_delta", "replaceability_score", "quality_optimizer_score",
            "blocked_reasons", "selection_reason",
        }
        self.assertTrue(required.issubset(result.keys()))

    def test_score_delta_is_candidate_minus_holding(self) -> None:
        result = _call_pair_eval(holding_score=5.0, candidate=_pair_candidate(score=8.0))
        self.assertAlmostEqual(result["score_delta"], 3.0, places=5)

    def test_blocked_when_score_delta_below_minimum(self) -> None:
        result = _call_pair_eval(holding_score=9.0, candidate=_pair_candidate(score=8.0))
        self.assertTrue(any("score delta 부족" in r for r in result["blocked_reasons"]))

    def test_not_blocked_when_score_delta_above_minimum(self) -> None:
        result = _call_pair_eval(holding_score=5.0, candidate=_pair_candidate(score=8.0))
        self.assertFalse(any("score delta 부족" in r for r in result["blocked_reasons"]))

    def test_blocked_when_net_pnl_below_profit_buffer(self) -> None:
        sell_details = {
            "holding_quality_score": 3.0,
            "replacement_pressure_score": 1.0,
            "trend_break_penalty": 0.0,
            "momentum_decay_penalty": 0.0,
            "net_pnl_bps": -200.0,
        }
        result = _call_pair_eval(analysis=_pair_analysis(sell_details=sell_details))
        self.assertTrue(any("net profit buffer 부족" in r for r in result["blocked_reasons"]))

    def test_blocked_when_concentration_exceeds_limit(self) -> None:
        result = _call_pair_eval(
            concentration_kwargs={"concentration_penalty": 5.0, "concentration_ok": False}
        )
        self.assertTrue(any("집중도 한도 초과" in r for r in result["blocked_reasons"]))

    def test_expected_cash_unlock_equals_sell_proceeds(self) -> None:
        result = _call_pair_eval(sell_proceeds=300_000)
        self.assertEqual(result["expected_cash_unlock_krw"], 300_000)


def _scan_candidate(symbol: str = "999999", candidate: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        display_name=symbol,
        score=7.0,
        score_summary="",
        score_highlights=(),
        score_penalties=(),
        expected_total_cost_krw=10_000,
        expected_cost_bps=10.0,
        net_edge_bps=20.0,
        candidate=candidate,
    )


def _dummy_pair_result(
    sell_symbol: str = "000001",
    buy_symbol: str = "999999",
    blocked: bool = False,
) -> dict:
    reasons = ["score delta 부족: 점수 낮음"] if blocked else []
    return {
        "analysis": SimpleNamespace(symbol=sell_symbol, display_name=sell_symbol),
        "replacement_candidate": SimpleNamespace(symbol=buy_symbol, display_name=buy_symbol),
        "score_delta": -0.5 if blocked else 0.5,
        "cost_adjusted_delta": 0.3,
        "quality_optimizer_score": 1.0,
        "holding_quality_score": 3.0,
        "replaceability_score": 1.0,
        "current_holding_score": 4.0,
        "replacement_pressure_score": 1.0,
        "trend_break_penalty": 0.0,
        "momentum_decay_penalty": 0.0,
        "net_pnl_bps": 50.0,
        "expected_cash_unlock_krw": 200_000,
        "next_cycle_buyable_qty": 20,
        "selection_reason": "통과" if not blocked else reasons[0],
        "blocked_reasons": reasons,
        "concentration_preview": _fake_concentration_preview(),
    }


class BuildQualityRebalancePreviewTests(unittest.TestCase):

    def test_skipped_when_no_replacement_candidates(self) -> None:
        result = _build_quality_rebalance_preview(
            sell_analysis_results=(SimpleNamespace(symbol="000001", display_name="000001"),),
            scan_results=[_scan_candidate(candidate=False)],
            portfolio_snapshot=_fake_portfolio(),
            settings=_settings(),
        )
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["preview_type"], "quality_improvement")

    def test_skipped_when_no_sell_analysis_results(self) -> None:
        result = _build_quality_rebalance_preview(
            sell_analysis_results=(),
            scan_results=[_scan_candidate()],
            portfolio_snapshot=_fake_portfolio(),
            settings=_settings(),
        )
        self.assertEqual(result["status"], "skipped")

    def test_preview_status_when_best_pair_unblocked(self) -> None:
        analysis = SimpleNamespace(symbol="000001", display_name="000001")
        pair_result = _dummy_pair_result("000001", "999999", blocked=False)
        with patch(_PATCH_PAIR_EVAL, return_value=pair_result):
            result = _build_quality_rebalance_preview(
                sell_analysis_results=(analysis,),
                scan_results=[_scan_candidate(symbol="999999")],
                portfolio_snapshot=_fake_portfolio(),
                settings=_settings(),
            )
        self.assertEqual(result["status"], "preview")
        self.assertEqual(result["selected_pair"]["sell_symbol"], "000001")

    def test_skipped_status_when_best_pair_blocked(self) -> None:
        analysis = SimpleNamespace(symbol="000001", display_name="000001")
        pair_result = _dummy_pair_result("000001", "999999", blocked=True)
        with patch(_PATCH_PAIR_EVAL, return_value=pair_result):
            result = _build_quality_rebalance_preview(
                sell_analysis_results=(analysis,),
                scan_results=[_scan_candidate(symbol="999999")],
                portfolio_snapshot=_fake_portfolio(),
                settings=_settings(),
            )
        self.assertEqual(result["status"], "skipped")


class PrintRebalanceSkipTests(unittest.TestCase):

    def test_prints_skip_marker_and_reason(self) -> None:
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            _print_rebalance_skip("테스트 사유")
        out = buf.getvalue()
        self.assertIn("리밸런싱 미검토", out)
        self.assertIn("테스트 사유", out)


def _print_rebalance_preview_defaults() -> dict:
    return dict(
        sell_analysis=SimpleNamespace(display_name="삼성전자"),
        sell_sizing=SimpleNamespace(
            recommended_sell_qty=5,
            details={"needed_cash_krw": None},
        ),
        buy_candidate=SimpleNamespace(display_name="SK하이닉스", score=8.5),
        score_delta=0.5,
        current_holding_score=5.0,
        holding_quality_score=3.0,
        replaceability_score=1.0,
        cost_adjusted_delta=0.4,
        quality_optimizer_score=0.9,
        net_pnl_bps=80.0,
        expected_cash_unlock_krw=200_000,
        concentration_preview=None,
        selection_reason="선정 이유",
        consideration_reason="검토 사유",
    )


class PrintRebalancePreviewTests(unittest.TestCase):

    def test_prints_sell_and_buy_names(self) -> None:
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            _print_rebalance_preview(**_print_rebalance_preview_defaults())
        out = buf.getvalue()
        self.assertIn("삼성전자", out)
        self.assertIn("SK하이닉스", out)

    def test_needed_cash_line_absent_when_none(self) -> None:
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            _print_rebalance_preview(**_print_rebalance_preview_defaults())
        self.assertNotIn("추가 확보 필요 현금", buf.getvalue())

    def test_needed_cash_line_present_when_set(self) -> None:
        kwargs = _print_rebalance_preview_defaults()
        kwargs["sell_sizing"] = SimpleNamespace(
            recommended_sell_qty=5,
            details={"needed_cash_krw": 50_000},
        )
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            _print_rebalance_preview(**kwargs)
        self.assertIn("추가 확보 필요 현금", buf.getvalue())

    def test_concentration_lines_present_when_preview_provided(self) -> None:
        kwargs = _print_rebalance_preview_defaults()
        kwargs["concentration_preview"] = {
            "before": {"top1_weight_pct": 20.0, "top3_weight_pct": 50.0},
            "after": {"top1_weight_pct": 22.0, "top3_weight_pct": 52.0},
            "comment": "집중도 증가",
        }
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            _print_rebalance_preview(**kwargs)
        out = buf.getvalue()
        self.assertIn("집중도 변화", out)
        self.assertIn("집중도 해석", out)


class PrintQualityRebalancePreviewTests(unittest.TestCase):

    def _make_skipped_preview(self) -> dict:
        return {"status": "skipped", "reason": "조건 미충족", "selected_pair": None}

    def test_prints_section_header(self) -> None:
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            _print_quality_rebalance_preview(preview=self._make_skipped_preview())
        self.assertIn("quality rebalance", buf.getvalue())

    def test_prints_status_and_reason(self) -> None:
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            _print_quality_rebalance_preview(preview=self._make_skipped_preview())
        out = buf.getvalue()
        self.assertIn("skipped", out)
        self.assertIn("조건 미충족", out)


class RebalanceModuleExtractionTests(unittest.TestCase):
    """Contract tests: extracted functions in app.execution.rebalance produce
    identical output to the app.main compatibility wrappers."""

    def test_calculate_rebalance_sell_sizing_direct_adjusts_to_needed_cash(self) -> None:
        from app.execution.rebalance import calculate_rebalance_sell_sizing
        from app.execution.sell_position_sizing import SellPositionSizingResult

        def fake_sell_sizing(*, trigger, holding_qty, current_price, settings):
            return SellPositionSizingResult(
                recommended_sell_qty=holding_qty,
                sell_reason="",
                sell_trigger=trigger,
                sell_fraction=1.0,
                available_holding_qty=holding_qty,
                recommended_notional_krw=holding_qty * current_price,
                details={"estimated_net_proceeds_krw": holding_qty * 10_000},
            )

        analysis = _pair_analysis()
        position_sizing = SimpleNamespace(details={"estimated_entry_cost_krw": 25_000})
        execution_snapshot = SimpleNamespace(orderable_cash=0)
        with patch(_PATCH_SELL_SIZING, side_effect=fake_sell_sizing) as sizing:
            result = calculate_rebalance_sell_sizing(
                weakest_analysis=analysis,
                position_sizing=position_sizing,
                execution_snapshot=execution_snapshot,
                settings=_settings(),
            )

        self.assertEqual(sizing.call_count, 2)
        self.assertEqual(result.recommended_sell_qty, 3)
        self.assertEqual(result.details["needed_cash_krw"], 25_000)
        self.assertEqual(result.details["target_rebalance_sell_qty"], 3)

    def test_estimate_rebalance_concentration_preview_direct(self) -> None:
        from app.execution.rebalance import estimate_rebalance_concentration_preview

        portfolio = SimpleNamespace(
            cash_orderable=0,
            held_positions=[
                SimpleNamespace(symbol="000001", market_value=60_000),
                SimpleNamespace(symbol="000002", market_value=40_000),
            ],
        )
        preview = estimate_rebalance_concentration_preview(
            portfolio_snapshot=portfolio,
            sell_symbol="000001",
            replacement_symbol="999999",
            replacement_market_value_krw=60_000,
            settings=_settings(rebalance_max_concentration_pct=50.0),
        )

        self.assertEqual(preview["before"]["top1_weight_pct"], 60.0)
        self.assertEqual(preview["after"]["top1_weight_pct"], 60.0)
        self.assertEqual(preview["concentration_penalty"], 10.0)
        self.assertFalse(preview["concentration_ok"])

    def test_build_rebalance_pair_evaluation_direct_uses_module_dependencies(self) -> None:
        from app.execution.rebalance import build_rebalance_pair_evaluation

        concentration = _fake_concentration_preview()
        with (
            patch(_PATCH_SELECTION_SCORE, return_value=(5.0, {})),
            patch(_PATCH_SELL_SIZING, return_value=_fake_sell_sizing(300_000)),
            patch(_PATCH_CONCENTRATION, return_value=concentration),
        ):
            result = build_rebalance_pair_evaluation(
                analysis=_pair_analysis(),
                replacement_candidate=_pair_candidate(score=8.0),
                portfolio_snapshot=_fake_portfolio(),
                settings=_settings(),
            )

        self.assertAlmostEqual(result["score_delta"], 3.0)
        self.assertEqual(result["expected_cash_unlock_krw"], 300_000)
        self.assertEqual(result["concentration_preview"], concentration)

    def test_build_position_sizing_block_context_cash_insufficient(self) -> None:
        from app.execution.rebalance import build_position_sizing_block_context
        ps = SimpleNamespace(
            details={"block_reason_code": "cash_insufficient", "block_reason_label": ""},
            reason=None,
        )
        ctx = build_position_sizing_block_context(ps)
        self.assertEqual(ctx["action"], "blocked_buy_cash_insufficient")
        self.assertEqual(ctx["cycle_action"], "BUY_BLOCKED_CASH_INSUFFICIENT")

    def test_build_position_sizing_block_context_exposure_limited(self) -> None:
        from app.execution.rebalance import build_position_sizing_block_context
        ps = SimpleNamespace(
            details={"block_reason_code": "exposure_limited", "block_reason_label": ""},
            reason=None,
        )
        ctx = build_position_sizing_block_context(ps)
        self.assertEqual(ctx["action"], "blocked_buy_exposure_limited")
        self.assertEqual(ctx["cycle_action"], "BUY_BLOCKED_EXPOSURE_LIMITED")

    def test_build_position_sizing_block_context_trade_budget_limited(self) -> None:
        from app.execution.rebalance import build_position_sizing_block_context
        ps = SimpleNamespace(
            details={"block_reason_code": "trade_budget_limited", "block_reason_label": ""},
            reason=None,
        )
        ctx = build_position_sizing_block_context(ps)
        self.assertEqual(ctx["action"], "blocked_buy_trade_budget_limited")
        self.assertEqual(ctx["cycle_action"], "BUY_BLOCKED_TRADE_BUDGET_LIMITED")

    def test_build_position_sizing_block_context_fallback(self) -> None:
        from app.execution.rebalance import build_position_sizing_block_context
        ps = SimpleNamespace(
            details={"block_reason_code": "unknown_code", "block_reason_label": ""},
            reason=None,
        )
        ctx = build_position_sizing_block_context(ps)
        self.assertEqual(ctx["action"], "blocked_buy_qty_or_price_limited")
        self.assertEqual(ctx["cycle_action"], "BUY_BLOCKED_QTY_OR_PRICE_LIMITED")

    def test_build_buy_analysis_block_context_expected_cost_too_high(self) -> None:
        from app.execution.rebalance import build_buy_analysis_block_context
        result = SimpleNamespace(cost_block_reason="expected_cost_too_high", final_reason="비용 초과")
        ctx = build_buy_analysis_block_context(result)
        self.assertEqual(ctx["action"], "blocked_buy_expected_cost_too_high")
        self.assertEqual(ctx["cycle_action"], "BUY_BLOCKED_EXPECTED_COST_TOO_HIGH")

    def test_build_buy_analysis_block_context_net_edge_too_low(self) -> None:
        from app.execution.rebalance import build_buy_analysis_block_context
        result = SimpleNamespace(cost_block_reason="net_edge_too_low", final_reason="순우위 부족")
        ctx = build_buy_analysis_block_context(result)
        self.assertEqual(ctx["action"], "blocked_buy_net_edge_too_low")
        self.assertEqual(ctx["cycle_action"], "BUY_BLOCKED_NET_EDGE_TOO_LOW")

    def test_build_buy_analysis_block_context_strategy_fallback(self) -> None:
        from app.execution.rebalance import build_buy_analysis_block_context
        result = SimpleNamespace(cost_block_reason="", final_reason="전략 거부")
        ctx = build_buy_analysis_block_context(result)
        self.assertEqual(ctx["action"], "blocked_strategy_rejected")
        self.assertEqual(ctx["cycle_action"], "HOLD_NO_SIGNAL")
        self.assertEqual(ctx["reason"], "전략 거부")


if __name__ == "__main__":
    unittest.main()
