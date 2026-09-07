from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Any, Mapping

BUY_INTENT = "BUY"
SELL_INTENT = "SELL"


class IntentPriority(IntEnum):
    DIAGNOSTIC = 0
    BUY = 100
    SELL = 200
    EMERGENCY_SELL = 300


@dataclass(frozen=True, kw_only=True)
class OrderIntent:
    intent_id: str
    source_cycle_id: str
    source_lane: str
    intent_type: str
    symbol: str
    priority: int
    reason: str
    created_at: datetime
    expires_at: datetime | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    quote_age_ms: float | None = None
    candidate_score: float | None = None
    guard_context: Mapping[str, Any] | None = None

    def is_expired(self, *, now: datetime) -> bool:
        return self.expires_at is not None and now >= self.expires_at


@dataclass(frozen=True, kw_only=True)
class BuyIntent(OrderIntent):
    intent_type: str = field(default=BUY_INTENT, init=False)
    priority: int = int(IntentPriority.BUY)


@dataclass(frozen=True, kw_only=True)
class SellIntent(OrderIntent):
    intent_type: str = field(default=SELL_INTENT, init=False)
    priority: int = int(IntentPriority.SELL)


@dataclass(frozen=True, kw_only=True)
class OrderGateDecision:
    intent_id: str | None
    intent_type: str | None
    symbol: str | None
    status: str
    reason: str
    source_lane: str | None = None
    processed_at: datetime | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class OrderGateResult:
    decisions: tuple[OrderGateDecision, ...]
    queue_depth_before: int
    stopped_due_to_budget: bool = False
    budget_exceeded: bool = False
    budget_exceeded_stage: str | None = None
    budget_skip_stage: str | None = None

    @property
    def processed_count(self) -> int:
        return sum(1 for decision in self.decisions if decision.status == "processed")

    @property
    def skipped_count(self) -> int:
        return sum(1 for decision in self.decisions if decision.status != "processed")

    @property
    def last_decision(self) -> OrderGateDecision | None:
        return self.decisions[-1] if self.decisions else None
