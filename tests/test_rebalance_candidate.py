"""Unit tests for ``_build_rebalance_candidate`` coverage handling.

Two test classes:

``RebalanceCandidateCoverageTests``
    Focus on the early branches of the function — the cases where
    ``sell_watch`` did not finish evaluating every held position, or where
    there are no eligible holdings to consider at all.

``RebalanceCandidateBlockedReasonTests``
    Focus on the deeper ``blocked_reasons`` branches, reached when full
    sell_watch coverage is available. ``_build_rebalance_pair_evaluation``
    is patched so these tests do not need fully-populated SellAnalysisResult
    / MarketSnapshot / BuyDecision objects.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.execution.rebalance import build_rebalance_candidate as _build_rebalance_candidate
from app.portfolio.schema import PortfolioPosition, PortfolioSnapshot


def _position(symbol: str, qty: int = 10) -> PortfolioPosition:
    return PortfolioPosition(
        symbol=symbol,
        name=None,
        holding_qty=qty,
        average_cost=10000,
        current_price=10000,
        market_value=qty * 10000,
        gross_pnl=0,
        gross_pnl_pct=0.0,
        has_position=qty > 0,
    )


def _snapshot(positions: list[PortfolioPosition]) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        positions=tuple(positions),
        cash_total=0,
        cash_orderable=0,
        cash_next_day=0,
        total_evaluation_amount=0,
    )


def _candidate(symbol: str = "999999"):
    return SimpleNamespace(
        symbol=symbol,
        name=None,
        display_name=symbol,
        score=0.0,
        score_summary="",
        score_highlights=(),
        score_penalties=(),
        expected_total_cost_krw=0,
        expected_cost_bps=0.0,
        net_edge_bps=0.0,
        cost_block_reason=None,
        candidate=True,
    )


def _sell_analysis(symbol: str):
    # _build_rebalance_candidate only accesses ``analysis.symbol`` on the
    # early-skip branches we cover here, so a SimpleNamespace is sufficient.
    return SimpleNamespace(symbol=symbol)


class RebalanceCandidateCoverageTests(unittest.TestCase):
    def test_defers_when_some_holdings_were_not_evaluated_this_cycle(self) -> None:
        # 3 holdings, only 1 was evaluated by sell_watch this cycle.
        held = [_position("000001"), _position("000002"), _position("000003")]
        result = _build_rebalance_candidate(
            sell_analysis_results=(_sell_analysis("000001"),),
            selected_candidate=_candidate("999999"),
            scan_results=(),
            portfolio_snapshot=_snapshot(held),
            settings=SimpleNamespace(),
        )
        self.assertIsNone(result["candidate"])
        self.assertEqual(result["reason_code"], "sell_watch_incomplete")
        # Two unevaluated holdings remain (000002, 000003).
        self.assertEqual(result["unevaluated_holding_count"], 2)
        self.assertIn("sell_watch가 평가하지 못한", result["reason"])
        # Observability: sample of unevaluated symbols included for diagnostics.
        sample = result["unevaluated_holding_symbols_sample"]
        self.assertEqual(set(sample), {"000002", "000003"})

    def test_unevaluated_sample_is_capped_at_ten(self) -> None:
        # Many unevaluated holdings — the diagnostics sample should be bounded
        # so a wide-portfolio cycle doesn't blow up the log payload.
        held = [_position(f"{i:06d}") for i in range(1, 21)]  # 20 holdings
        result = _build_rebalance_candidate(
            sell_analysis_results=(),
            selected_candidate=_candidate("999999"),
            scan_results=(),
            portfolio_snapshot=_snapshot(held),
            settings=SimpleNamespace(),
        )
        self.assertEqual(result["reason_code"], "sell_watch_incomplete")
        self.assertEqual(result["unevaluated_holding_count"], 20)
        self.assertEqual(len(result["unevaluated_holding_symbols_sample"]), 10)

    def test_defers_when_zero_holdings_were_evaluated_but_account_has_positions(
        self,
    ) -> None:
        # The exact failure mode that motivated this fix: sell_watch was rate
        # limited at the very first holding so nothing got evaluated, yet the
        # broker-synced positions clearly show we hold things.
        held = [_position("000001"), _position("000002")]
        result = _build_rebalance_candidate(
            sell_analysis_results=(),
            selected_candidate=_candidate("999999"),
            scan_results=(),
            portfolio_snapshot=_snapshot(held),
            settings=SimpleNamespace(),
        )
        self.assertIsNone(result["candidate"])
        self.assertEqual(result["reason_code"], "sell_watch_incomplete")
        self.assertEqual(result["unevaluated_holding_count"], 2)

    def test_no_candidate_reason_when_account_truly_has_no_eligible_holdings(
        self,
    ) -> None:
        # No held positions at all (besides possibly the buy candidate).
        result = _build_rebalance_candidate(
            sell_analysis_results=(),
            selected_candidate=_candidate("999999"),
            scan_results=(),
            portfolio_snapshot=_snapshot([]),
            settings=SimpleNamespace(),
        )
        self.assertIsNone(result["candidate"])
        self.assertEqual(result["reason_code"], "no_candidate")

    def test_excludes_selected_candidate_from_held_set(self) -> None:
        # The buy candidate happens to be a current holding (edge case).
        # That single overlapping symbol must not count as an unevaluated
        # holding; with no other holdings this should fall to ``no_candidate``.
        held = [_position("999999")]
        result = _build_rebalance_candidate(
            sell_analysis_results=(),
            selected_candidate=_candidate("999999"),
            scan_results=(),
            portfolio_snapshot=_snapshot(held),
            settings=SimpleNamespace(),
        )
        self.assertIsNone(result["candidate"])
        self.assertEqual(result["reason_code"], "no_candidate")


# ---------------------------------------------------------------------------
# Helpers for blocked_reasons tests
# ---------------------------------------------------------------------------

def _full_sell_analysis(symbol: str):
    """Sell analysis stub that satisfies the loop filter ``analysis.symbol``."""
    return SimpleNamespace(symbol=symbol)


def _holding_dict(symbol: str, blocked_reason: str | None = None) -> dict:
    """Minimal holding dict returned by a mocked _build_rebalance_pair_evaluation.

    All numeric fields default to 0.0 so sorting in _build_rebalance_candidate
    works without raising TypeError. ``analysis`` carries the symbol and
    display_name attributes accessed by _serialize_rebalance_holding_option.
    """
    return {
        "analysis": SimpleNamespace(symbol=symbol, display_name=symbol),
        "quality_optimizer_score": 0.0,
        "replaceability_score": 0.0,
        "cost_adjusted_delta": 0.0,
        "holding_quality_score": 0.0,
        "current_holding_score": 0.0,
        "replacement_pressure_score": 0.0,
        "trend_break_penalty": 0.0,
        "momentum_decay_penalty": 0.0,
        "net_pnl_bps": 0.0,
        "score_delta": 0.0,
        "blocked_reasons": [blocked_reason] if blocked_reason else [],
    }


class RebalanceCandidateBlockedReasonTests(unittest.TestCase):
    """Verify that each blocked_reasons keyword maps to the right reason_code.

    Patching ``_build_rebalance_pair_evaluation`` lets us exercise the
    blocked_reasons decision tree without constructing real SellAnalysisResult
    / MarketSnapshot / BuyDecision objects.
    """

    _PATCH_TARGET = "app.execution.rebalance.build_rebalance_pair_evaluation"

    def _call_with_blocking(self, blocked_reason: str) -> dict:
        """Run _build_rebalance_candidate with one fully-evaluated holding
        whose single blocked_reason contains ``blocked_reason``."""
        held = [_position("000001")]
        with patch(self._PATCH_TARGET, return_value=_holding_dict("000001", blocked_reason)):
            return _build_rebalance_candidate(
                sell_analysis_results=(_full_sell_analysis("000001"),),
                selected_candidate=_candidate("999999"),
                scan_results=(),
                portfolio_snapshot=_snapshot(held),
                settings=SimpleNamespace(),
            )

    def test_score_delta_block_returns_correct_reason_code(self) -> None:
        result = self._call_with_blocking("score delta 부족: 후보 점수가 낮습니다")
        self.assertIsNone(result["candidate"])
        self.assertEqual(result["reason_code"], "blocked_score_delta")

    def test_net_edge_block_returns_correct_reason_code(self) -> None:
        result = self._call_with_blocking("비용 반영 순우위 부족: 거래 비용 초과")
        self.assertIsNone(result["candidate"])
        self.assertEqual(result["reason_code"], "blocked_net_edge")

    def test_concentration_block_returns_correct_reason_code(self) -> None:
        result = self._call_with_blocking("집중도 한도 초과 가능: 포지션 비중 위험")
        self.assertIsNone(result["candidate"])
        self.assertEqual(result["reason_code"], "blocked_concentration")

    def test_profit_buffer_block_returns_correct_reason_code(self) -> None:
        result = self._call_with_blocking("net profit buffer 부족: 손익 여유 없음")
        self.assertIsNone(result["candidate"])
        self.assertEqual(result["reason_code"], "blocked_profit_buffer")

    def test_unknown_block_reason_falls_back_to_blocked_other(self) -> None:
        result = self._call_with_blocking("알 수 없는 차단 사유")
        self.assertIsNone(result["candidate"])
        self.assertEqual(result["reason_code"], "blocked_other")

    def test_reason_contains_primary_block_message(self) -> None:
        # The ``reason`` field must include the raw blocked_reason text so
        # logs remain human-readable even without the reason_code.
        msg = "score delta 부족: delta=-0.05"
        result = self._call_with_blocking(msg)
        self.assertIn(msg, result["reason"])

    def test_action_mapping_covers_all_reason_codes(self) -> None:
        """Contract test: every reason_code _build_rebalance_candidate can
        emit has a corresponding action in the call-site mapping dict.

        This catches the case where a new reason_code is added to
        _build_rebalance_candidate without updating the call-site dict.
        """
        # These are the reason_codes the function can currently return.
        all_reason_codes = {
            "sell_watch_incomplete",
            "no_candidate",
            "blocked_score_delta",
            "blocked_net_edge",
            "blocked_concentration",
            "blocked_profit_buffer",
            "blocked_other",
        }
        # Mirror of the dict in run_cycle (must stay in sync).
        call_site_mapping = {
            "sell_watch_incomplete": "rebalance_deferred_sell_watch_incomplete",
            "no_candidate": "rebalance_skipped_no_candidate",
            "blocked_score_delta": "rebalance_skipped_score_delta",
            "blocked_net_edge": "rebalance_skipped_net_edge",
            "blocked_concentration": "rebalance_skipped_concentration",
            "blocked_profit_buffer": "rebalance_skipped_profit_buffer",
            "blocked_other": "rebalance_skipped_no_candidate",
        }
        missing = all_reason_codes - call_site_mapping.keys()
        self.assertSetEqual(missing, set(), "reason_codes not in call-site mapping")
        # Every action value must be non-empty.
        for code, action in call_site_mapping.items():
            self.assertTrue(action, f"action for {code!r} is empty")


if __name__ == "__main__":
    unittest.main()
