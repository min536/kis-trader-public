"""Behavior-lock tests for the cycle-context builders relocated from app.main.

Stage A slice A1 moves the six rebalance builder/serializer delegators out of
app.main into app.reporting.cycle_context. These pin that the new module
exposes them under their historical underscore names and that they preserve
the delegation to app.execution.rebalance.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock


def test_build_concentration_metrics_single_position_weight() -> None:
    from app.reporting.cycle_context import _build_concentration_metrics

    result = _build_concentration_metrics(
        cash_orderable_krw=0,
        position_values={"005930": 1_000_000},
    )

    assert result["top1_weight_pct"] == 100.0
    assert len(result["positions"]) == 1


def test_build_position_sizing_block_context_delegates() -> None:
    from app.reporting import cycle_context

    sentinel = SimpleNamespace()
    with mock.patch(
        "app.execution.rebalance.build_position_sizing_block_context",
        return_value={"ok": "yes"},
    ) as delegate:
        out = cycle_context._build_position_sizing_block_context(sentinel)

    delegate.assert_called_once_with(sentinel)
    assert out == {"ok": "yes"}


def test_build_buy_analysis_block_context_delegates() -> None:
    from app.reporting import cycle_context

    sentinel = SimpleNamespace()
    with mock.patch(
        "app.execution.rebalance.build_buy_analysis_block_context",
        return_value={"ctx": 1},
    ) as delegate:
        out = cycle_context._build_buy_analysis_block_context(sentinel)

    delegate.assert_called_once_with(sentinel)
    assert out == {"ctx": 1}


def test_serialize_rebalance_holding_option_delegates() -> None:
    from app.reporting import cycle_context

    option = {"symbol": "005930"}
    with mock.patch(
        "app.execution.rebalance.serialize_rebalance_holding_option",
        return_value={"serialized": True},
    ) as delegate:
        out = cycle_context._serialize_rebalance_holding_option(option)

    delegate.assert_called_once_with(option)
    assert out == {"serialized": True}


def test_serialize_replacement_candidate_option_delegates() -> None:
    from app.reporting import cycle_context

    candidate = SimpleNamespace()
    with mock.patch(
        "app.execution.rebalance.serialize_replacement_candidate_option",
        return_value={"serialized": True},
    ) as delegate:
        out = cycle_context._serialize_replacement_candidate_option(candidate)

    delegate.assert_called_once_with(candidate)
    assert out == {"serialized": True}


def test_build_rebalance_pair_evaluation_delegates() -> None:
    from app.reporting import cycle_context

    analysis = SimpleNamespace()
    replacement = SimpleNamespace()
    snapshot = SimpleNamespace()
    settings = SimpleNamespace()
    with mock.patch(
        "app.execution.rebalance.build_rebalance_pair_evaluation",
        return_value={"pair": "ok"},
    ) as delegate:
        out = cycle_context._build_rebalance_pair_evaluation(
            analysis=analysis,
            replacement_candidate=replacement,
            portfolio_snapshot=snapshot,
            settings=settings,
        )

    delegate.assert_called_once_with(
        analysis=analysis,
        replacement_candidate=replacement,
        portfolio_snapshot=snapshot,
        settings=settings,
    )
    assert out == {"pair": "ok"}
