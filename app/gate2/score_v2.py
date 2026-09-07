"""Weighted gate2 score_v2 pure scoring function (G2).

Runtime-eligible leaf (promoted from app/research/gate2): imports stdlib only.
"""

from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass(frozen=True)
class Gate2ScoreResult:
    final_score: float
    contributions: dict[str, float]
    missing: tuple[str, ...]


def weighted_gate2_score(
    scores: Mapping[str, Optional[float]],
    weights: Mapping[str, float],
) -> Gate2ScoreResult:
    """Compute the deterministic weighted gate2 v2 score.

    ``final_score = sum(score_i * weight_i) / sum(weight_i)`` — a weight-
    normalized average, so ``final_score`` stays on the same [0, 100] scale as
    each input score REGARDLESS of the weights' magnitude (``buy_threshold`` is
    schema-capped to [0, 100] and is compared directly against ``final_score``,
    so an unnormalized sum would make the threshold non-discriminating once
    ``sum(weights) > 1`` — e.g. ``default_artifact()``'s weights sum to 10.75).
    ``contributions`` is normalized the same way, so
    ``sum(contributions.values()) == final_score``. None / missing scores
    contribute 0 and are recorded in ``missing``.

    Raises ValueError if ``scores`` keys are not a subset of ``weights`` keys,
    if any present score falls outside [0, 100], or if ``sum(weights) <= 0``
    (no valid average exists).
    """
    score_keys = set(scores)
    weight_keys = set(weights)
    if not score_keys <= weight_keys:
        extra = sorted(score_keys - weight_keys)
        raise ValueError(f"scores keys not a subset of weights keys: {extra}")

    weight_total = sum(weights.values())
    if weight_total <= 0:
        raise ValueError(f"sum of weights must be positive: {weight_total}")

    contributions: dict[str, float] = {}
    missing: list[str] = []
    raw_total = 0.0
    for name in weights:
        value = scores.get(name)
        if value is None:
            missing.append(name)
            contributions[name] = 0.0
            continue
        if not (0.0 <= value <= 100.0):
            raise ValueError(f"score out of range [0, 100]: {name}={value}")
        contribution = value * weights[name]
        contributions[name] = contribution
        raw_total += contribution
    contributions = {
        name: contribution / weight_total
        for name, contribution in contributions.items()
    }
    return Gate2ScoreResult(
        final_score=raw_total / weight_total,
        contributions=contributions,
        missing=tuple(missing),
    )


def passes_threshold(result: Gate2ScoreResult, artifact) -> bool:
    """Return True if ``result.final_score`` meets the artifact buy threshold."""
    return result.final_score >= artifact.buy_threshold
