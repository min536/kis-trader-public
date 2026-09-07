"""Cycle-context builders and serializers relocated from app.main (Stage A A1).

Each function is a thin delegator to app.execution.rebalance, kept under its
historical underscore name so app.main can re-bind it without touching any call
site. Behavior is identical to the pre-move helpers: prepared data in, plain
dict/context out — no broker calls, no state mutation.
"""

from __future__ import annotations

from app.strategy.sell_decision import SellAnalysisResult


def _build_position_sizing_block_context(position_sizing) -> dict[str, str]:
    from app.execution.rebalance import build_position_sizing_block_context
    return build_position_sizing_block_context(position_sizing)


def _build_buy_analysis_block_context(result) -> dict[str, str]:
    from app.execution.rebalance import build_buy_analysis_block_context
    return build_buy_analysis_block_context(result)


def _build_concentration_metrics(
    *,
    cash_orderable_krw: int,
    position_values: dict[str, int],
) -> dict[str, object]:
    from app.execution.rebalance import build_concentration_metrics
    return build_concentration_metrics(
        cash_orderable_krw=cash_orderable_krw,
        position_values=position_values,
    )


def _serialize_rebalance_holding_option(option: dict[str, object]) -> dict[str, object]:
    from app.execution.rebalance import serialize_rebalance_holding_option
    return serialize_rebalance_holding_option(option)


def _serialize_replacement_candidate_option(candidate) -> dict[str, object]:
    from app.execution.rebalance import serialize_replacement_candidate_option
    return serialize_replacement_candidate_option(candidate)


def _build_rebalance_pair_evaluation(
    *,
    analysis: SellAnalysisResult,
    replacement_candidate,
    portfolio_snapshot,
    settings,
) -> dict[str, object]:
    from app.execution.rebalance import build_rebalance_pair_evaluation
    return build_rebalance_pair_evaluation(
        analysis=analysis,
        replacement_candidate=replacement_candidate,
        portfolio_snapshot=portfolio_snapshot,
        settings=settings,
    )
