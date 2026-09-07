from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.pipeline.cycle_budget import LaneBudget
from app.pipeline.intents import SellIntent


SellIntentFactory = Callable[..., SellIntent | None]


@dataclass(frozen=True)
class SellLaneResult:
    due: bool
    intent: SellIntent | None = None
    skipped_reason: str | None = None
    processed: bool = False
    elapsed_ms: float = 0.0


class SellLane:
    def __init__(
        self,
        *,
        intent_factory: SellIntentFactory | None = None,
    ) -> None:
        self._intent_factory = intent_factory

    def run(
        self,
        *,
        due: bool,
        cycle_id: str,
        budget: LaneBudget,
        context: dict[str, Any] | None = None,
    ) -> SellLaneResult:
        if not due:
            return SellLaneResult(due=False, skipped_reason="cadence_not_reached")
        started_remaining = budget.remaining_seconds()
        if budget.expired():
            return SellLaneResult(due=True, skipped_reason="cycle_budget_exceeded")

        budget.checkpoint("sell_lane_start")
        context = context or {}
        intent = None
        if self._intent_factory is not None:
            intent = self._intent_factory(
                cycle_id=cycle_id,
                budget=budget,
                context=context,
            )
        budget.checkpoint("sell_lane_end")
        elapsed_ms = max(0.0, started_remaining - budget.remaining_seconds()) * 1000.0
        skipped_reason = None
        if intent is None:
            skipped_reason = str(context.get("sell_skipped_reason") or "no_sell_intent")
        return SellLaneResult(
            due=True,
            intent=intent,
            skipped_reason=skipped_reason,
            processed=True,
            elapsed_ms=elapsed_ms,
        )
