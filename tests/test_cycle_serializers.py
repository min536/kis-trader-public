"""Tests for app.reporting.cycle_serializers (R3-S8).

cycle_snapshots.py의 직렬화기 9종을 verbatim 이동. cycle_snapshots는 facade로
동일 객체를 재수출해야 한다 (build_cycle_snapshot이 모듈 전역 경유로 호출).
"""

from __future__ import annotations

import unittest

from app.reporting import cycle_serializers, cycle_snapshots

_SERIALIZER_NAMES = (
    "_serialize_market_snapshot",
    "_serialize_observed_market_snapshots",
    "_serialize_buy_candidate",
    "_serialize_sell_candidate",
    "_serialize_positions",
    "_serialize_top_scan_candidates",
    "_resolve_primary_action_context",
    "_extract_pre_gating_fields",
    "_extract_staged_scan_fields",
)


class SerializeMarketSnapshotTests(unittest.TestCase):
    def test_serializes_valid_snapshot_and_rejects_invalid(self) -> None:
        from types import SimpleNamespace

        snapshot = SimpleNamespace(
            symbol=" 005930 ",
            current_price=70_000,
            open_price=69_000,
            low_price=68_500,
            prev_day_change_pct=1.25,
        )
        payload = cycle_serializers._serialize_market_snapshot(snapshot)
        self.assertEqual(
            payload,
            {
                "symbol": "005930",
                "current_price": 70_000,
                "open_price": 69_000,
                "low_price": 68_500,
                "prev_day_change_pct": 1.25,
            },
        )
        self.assertIsNone(cycle_serializers._serialize_market_snapshot(None))
        self.assertIsNone(
            cycle_serializers._serialize_market_snapshot(
                SimpleNamespace(symbol="005930", current_price="bad")
            )
        )


class SerializeObservedMarketSnapshotsTests(unittest.TestCase):
    def test_sorts_by_symbol_and_skips_invalid_entries(self) -> None:
        from types import SimpleNamespace

        observed = {
            "005930": SimpleNamespace(
                symbol="005930",
                current_price=70_000,
                open_price=0,
                low_price=0,
                prev_day_change_pct=0.0,
            ),
            "000100": SimpleNamespace(
                symbol="000100",
                current_price=10_000,
                open_price=0,
                low_price=0,
                prev_day_change_pct=0.0,
            ),
            "BAD": SimpleNamespace(symbol="BAD", current_price=0),
        }
        payloads = cycle_serializers._serialize_observed_market_snapshots(observed)
        self.assertEqual(
            [p["symbol"] for p in payloads], ["000100", "005930"]
        )
        self.assertEqual(
            cycle_serializers._serialize_observed_market_snapshots(None), []
        )


def _buy_candidate_fixture():
    from types import SimpleNamespace

    fields = dict(
        symbol="005930",
        name="삼성전자",
        display_name="005930 삼성전자",
        candidate=True,
        passed_count=3,
        signal_quality_count=2,
        enabled_count=4,
        passed_pattern="OOX",
        score=1.5,
        net_profit_buffer_bps=12.0,
        passes_profit_buffer=True,
        expected_fee_krw=10,
        expected_tax_krw=0,
        expected_slippage_krw=5,
        expected_total_cost_krw=15,
        expected_cost_bps=3.0,
        cost_quality_score=0.9,
        expected_cost_penalty=0.1,
        net_edge_bps=9.0,
        cost_block_reason=None,
        final_reason="ok",
        score_components={"a": 1},
        score_highlights=("h1",),
        score_penalties=("p1",),
        score_summary="summary",
        math_score_summary="math",
        mean_reversion_zscore=-1.0,
        reversion_quality_score=0.5,
        overextension_penalty=0.0,
        ou_half_life_estimate=3.2,
        mean_reversion_summary="mr",
        portfolio_avg_correlation=0.2,
        portfolio_max_correlation=0.4,
        variance_increase_estimate=0.01,
        portfolio_risk_summary="risk",
        portfolio_correlation_penalty=0.05,
        variance_increase_penalty=0.02,
        feature_summaries=["f"],
        feature_map={"k": 1.0},
        feature_vector=[1.0],
        market_snapshot=SimpleNamespace(
            symbol="005930",
            current_price=70_000,
            open_price=69_000,
            low_price=68_000,
            prev_day_change_pct=0.5,
        ),
        strategy_result=SimpleNamespace(to_log_payload=lambda: {"strategy": "s"}),
    )
    return SimpleNamespace(**fields)


class SerializeBuyCandidateTests(unittest.TestCase):
    def test_serializes_candidate_fields_and_nested_payloads(self) -> None:
        payload = cycle_serializers._serialize_buy_candidate(_buy_candidate_fixture())

        self.assertEqual(
            payload,
            {
                "symbol": "005930",
                "name": "삼성전자",
                "display_name": "005930 삼성전자",
                "candidate": True,
                "passed_count": 3,
                "signal_quality_count": 2,
                "enabled_count": 4,
                "passed_pattern": "OOX",
                "score": 1.5,
                "net_profit_buffer_bps": 12.0,
                "passes_profit_buffer": True,
                "expected_fee_krw": 10,
                "expected_tax_krw": 0,
                "expected_slippage_krw": 5,
                "expected_total_cost_krw": 15,
                "expected_cost_bps": 3.0,
                "cost_quality_score": 0.9,
                "expected_cost_penalty": 0.1,
                "net_edge_bps": 9.0,
                "cost_block_reason": None,
                "final_reason": "ok",
                "score_components": {"a": 1},
                "score_highlights": ["h1"],
                "score_penalties": ["p1"],
                "score_summary": "summary",
                "math_score_summary": "math",
                "mean_reversion_zscore": -1.0,
                "reversion_quality_score": 0.5,
                "overextension_penalty": 0.0,
                "ou_half_life_estimate": 3.2,
                "mean_reversion_summary": "mr",
                "portfolio_avg_correlation": 0.2,
                "portfolio_max_correlation": 0.4,
                "variance_increase_estimate": 0.01,
                "portfolio_risk_summary": "risk",
                "portfolio_correlation_penalty": 0.05,
                "variance_increase_penalty": 0.02,
                "feature_summaries": ["f"],
                "feature_map": {"k": 1.0},
                "feature_vector": [1.0],
                "market_snapshot": {
                    "symbol": "005930",
                    "current_price": 70_000,
                    "open_price": 69_000,
                    "low_price": 68_000,
                    "prev_day_change_pct": 0.5,
                },
                "strategy_details": {"strategy": "s"},
            },
        )
        self.assertIsNone(cycle_serializers._serialize_buy_candidate(None))


class SerializeSellCandidateTests(unittest.TestCase):
    def test_serializes_sell_decision_and_strategy_payloads(self) -> None:
        from types import SimpleNamespace

        result = SimpleNamespace(
            symbol="005930",
            name="삼성전자",
            display_name="005930 삼성전자",
            holding_qty=10,
            average_cost=65_000,
            market_snapshot=SimpleNamespace(current_price=70_000),
            sell_decision=SimpleNamespace(
                triggered_rule_name="take_profit",
                should_attempt_sell=True,
                reason="target hit",
                details={
                    "net_pnl_krw": 50_000,
                    "net_pnl_pct": 7.7,
                    "net_pnl_bps": 770,
                    "sell_priority_score": 1.0,
                    "sell_priority_summary": "top",
                },
                to_log_payload=lambda: {"sell": "payload"},
            ),
            buy_strategy_result=SimpleNamespace(
                to_log_payload=lambda: {"buy": "payload"}
            ),
        )

        payload = cycle_serializers._serialize_sell_candidate(result)

        self.assertEqual(
            payload,
            {
                "symbol": "005930",
                "name": "삼성전자",
                "display_name": "005930 삼성전자",
                "holding_qty": 10,
                "average_cost": 65_000,
                "triggered_rule_name": "take_profit",
                "should_attempt_sell": True,
                "reason": "target hit",
                "current_price": 70_000,
                "net_pnl_krw": 50_000,
                "net_pnl_pct": 7.7,
                "net_pnl_bps": 770,
                "sell_priority_score": 1.0,
                "sell_priority_summary": "top",
                "buy_strategy_details": {"buy": "payload"},
                "sell_strategy_details": {"sell": "payload"},
            },
        )
        self.assertIsNone(cycle_serializers._serialize_sell_candidate(None))


class SerializePositionsTests(unittest.TestCase):
    def test_merges_sell_analysis_into_position_payload(self) -> None:
        from types import SimpleNamespace

        portfolio = SimpleNamespace(
            total_evaluation_amount=1_000,
            held_positions=[
                SimpleNamespace(
                    symbol="005930",
                    name="삼성전자",
                    holding_qty=2,
                    average_cost=190,
                    current_price=200,
                    market_value=400,
                    gross_pnl=20,
                    gross_pnl_pct=5.0,
                ),
                SimpleNamespace(
                    symbol="000100",
                    name="유한양행",
                    holding_qty=1,
                    average_cost=100,
                    current_price=110,
                    market_value=110,
                    gross_pnl=10,
                    gross_pnl_pct=10.0,
                ),
            ],
        )
        analysis = SimpleNamespace(
            symbol="005930",
            market_snapshot=SimpleNamespace(current_price=205),
            sell_decision=SimpleNamespace(
                details={
                    "gross_pnl_krw": 30,
                    "gross_pnl_pct": 7.5,
                    "net_pnl_krw": 25,
                    "net_pnl_pct": 6.2,
                }
            ),
        )

        positions = cycle_serializers._serialize_positions(portfolio, [analysis])

        self.assertEqual(len(positions), 2)
        first = positions[0]
        self.assertEqual(first["symbol"], "005930")
        self.assertEqual(first["current_price"], 205)
        self.assertEqual(first["net_pnl_krw"], 25)
        self.assertEqual(first["weight_pct"], 40.0)
        second = positions[1]
        self.assertEqual(second["symbol"], "000100")
        self.assertIsNone(second["net_pnl_krw"])
        self.assertEqual(second["gross_pnl_krw"], 10)
        self.assertEqual(
            cycle_serializers._serialize_positions(None, []), []
        )


class SerializeTopScanCandidatesTests(unittest.TestCase):
    def test_orders_by_sort_key_and_applies_limit(self) -> None:
        first = _buy_candidate_fixture()
        first.sort_key = (0, "005930")
        second = _buy_candidate_fixture()
        second.symbol = "000100"
        second.sort_key = (1, "000100")
        third = _buy_candidate_fixture()
        third.symbol = "035720"
        third.sort_key = (2, "035720")

        payloads = cycle_serializers._serialize_top_scan_candidates(
            [third, first, second], limit=2
        )

        self.assertEqual([p["symbol"] for p in payloads], ["005930", "000100"])
        self.assertEqual(payloads[0]["market_snapshot"]["current_price"], 70_000)
        self.assertNotIn("strategy_details", payloads[0])
        self.assertEqual(
            cycle_serializers._serialize_top_scan_candidates([]), []
        )


class ResolvePrimaryActionContextTests(unittest.TestCase):
    def test_buy_candidate_match_and_action_fallback(self) -> None:
        from types import SimpleNamespace

        buy = SimpleNamespace(symbol="005930", display_name="005930 삼성전자")
        matched = cycle_serializers._resolve_primary_action_context(
            runtime_state={
                "last_selected_symbol": "005930",
                "last_order_side": "",
                "last_action": "BUY_SUBMITTED",
            },
            selected_buy_candidate=buy,
            selected_sell_candidate=None,
        )
        self.assertEqual(
            matched,
            {
                "selected_primary_action_symbol": "005930",
                "selected_primary_action_name": "005930 삼성전자",
                "selected_primary_action_side": "BUY",
            },
        )

        fallback = cycle_serializers._resolve_primary_action_context(
            runtime_state={
                "last_selected_symbol": "000100",
                "last_order_side": "sell",
                "last_action": "SELL_FAILED",
            },
            selected_buy_candidate=None,
            selected_sell_candidate=None,
        )
        self.assertEqual(
            fallback,
            {
                "selected_primary_action_symbol": "000100",
                "selected_primary_action_name": "000100",
                "selected_primary_action_side": "SELL",
            },
        )


class ExtractPreGatingFieldsTests(unittest.TestCase):
    def test_builds_summary_and_field_payload(self) -> None:
        details = {
            "pre_gating": {
                "requested_count": 5,
                "allowed_count": 3,
                "early_reject_count": 2,
                "rejected": ["a", "b"],
                "rejected_symbols": ["000100", "035720"],
                "reasons_by_symbol": {"000100": "cooldown"},
                "reason_counts": {"cooldown": 2},
                "scan_allowed": False,
                "scan_block_reason": "daily-loss",
                "stage": "pre",
                "reentry_state_by_symbol": {"000100": "blocked"},
                "last_exit_reason_by_symbol": {"000100": "stop"},
                "residual_position_present_by_symbol": {"000100": True},
                "reentry_block_reason_counts": {"cooldown": 1},
                "reentry_allowed_count": 1,
                "reentry_blocked_count": 1,
            }
        }

        fields = cycle_serializers._extract_pre_gating_fields(details)

        self.assertEqual(
            fields["pre_gating_summary"],
            "requested=5 | allowed=3 | rejected=2 | reasons=cooldown=2 | blocked=daily-loss",
        )
        self.assertEqual(fields["pre_gating_rejected_count"], 2)
        self.assertEqual(
            fields["pre_gating_rejected_symbols"], ["000100", "035720"]
        )
        self.assertEqual(fields["pre_gating_stage"], "pre")
        self.assertEqual(fields["reentry_allowed_count"], 1)
        self.assertEqual(fields["reentry_blocked_count"], 1)


class ExtractStagedScanFieldsTests(unittest.TestCase):
    def test_maps_staged_scan_counters_with_defaults(self) -> None:
        fields = cycle_serializers._extract_staged_scan_fields(
            {
                "staged_scan": {
                    "profile": "default",
                    "core_count": 10,
                    "rotating_count": 5,
                    "exploration_count": 2,
                    "pre_gating_count": 12,
                    "shallow_ranked_count": 8,
                    "deep_eval_limit": 4,
                    "exploration_quota_used": 1,
                    "layered_symbols_preview": {"core": ["005930"]},
                    "shallow_shortlist_preview": ["005930"],
                }
            }
        )
        self.assertEqual(
            fields,
            {
                "buy_scan_profile": "default",
                "buy_scan_universe_core_count": 10,
                "buy_scan_universe_rotating_count": 5,
                "buy_scan_universe_exploration_count": 2,
                "buy_scan_pre_gating_count": 12,
                "buy_scan_shallow_ranked_count": 8,
                "buy_scan_deep_eval_limit": 4,
                "buy_scan_exploration_quota_used": 1,
                "buy_scan_layered_symbols_preview": {"core": ["005930"]},
                "buy_scan_shallow_shortlist_preview": ["005930"],
            },
        )
        empty = cycle_serializers._extract_staged_scan_fields(None)
        self.assertIsNone(empty["buy_scan_profile"])
        self.assertEqual(empty["buy_scan_universe_core_count"], 0)


class CycleSerializersFacadeTests(unittest.TestCase):
    def test_cycle_snapshots_binds_canonical_objects(self) -> None:
        for name in _SERIALIZER_NAMES:
            with self.subTest(name):
                self.assertIs(
                    getattr(cycle_snapshots, name),
                    getattr(cycle_serializers, name),
                )


if __name__ == "__main__":
    unittest.main()
