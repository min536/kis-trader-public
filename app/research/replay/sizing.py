"""Research sizing curve for score_v2 (R3).

Leaf research module: imports only stdlib. No imports from app.*.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SizingTier:
    """A score threshold mapped to a budget multiplier."""

    min_score: float
    multiplier: float


DEFAULT_SIZING_TIERS: tuple[SizingTier, ...] = (
    SizingTier(min_score=60.0, multiplier=0.5),
    SizingTier(min_score=70.0, multiplier=1.0),
    SizingTier(min_score=85.0, multiplier=1.25),
)


def score_to_budget_multiplier(
    score: float, tiers: "tuple[SizingTier, ...]" = DEFAULT_SIZING_TIERS
) -> float:
    """Return the stepped budget multiplier for ``score``.

    Below the lowest tier's ``min_score`` the multiplier is ``0.0``. Otherwise
    the multiplier of the highest tier whose ``min_score <= score`` applies.
    Tiers are assumed sorted ascending by ``min_score``.
    """
    multiplier = 0.0
    for tier in tiers:
        if score >= tier.min_score:
            multiplier = tier.multiplier
        else:
            break
    return multiplier


def apply_budget_caps(budget: float, *, cash: float, max_budget: float) -> float:
    """Hard-cap ``budget`` to the minimum of itself, ``cash`` and ``max_budget``."""
    return min(budget, cash, max_budget)
