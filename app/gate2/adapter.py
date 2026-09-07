"""Map score_v1 components to v2 condition scores (G3).

Runtime-eligible leaf (promoted from app/research/gate2): imports stdlib only.
"""

from typing import Mapping, Optional


def _clip(x: float) -> float:
    return min(max(x, 0.0), 100.0)


def condition_scores_from_v1(
    score_components: Mapping[str, float],
    caps: Mapping[str, float],
) -> dict[str, Optional[float]]:
    """Map v1 ``score_components`` to the 15 v2 condition scores.

    Uses ``_clip(x) = min(max(x, 0), 100)``. Each output is ``None`` when its
    source v1 key is absent (observability gap, distinct from a 0 score).
    ``liquidity_score`` has no v1 source so it is always ``None``
    (to be introduced at the replay feature stage).
    """
    c = score_components

    def direct(key: str) -> Optional[float]:
        return _clip(c[key]) if key in c else None

    def cap_norm(key: str, cap_key: str) -> Optional[float]:
        return _clip(c[key] / caps[cap_key] * 100.0) if key in c else None

    # Cap-normalized 4.
    trend_alignment = cap_norm("trend_alignment_score", "trend_alignment")
    macd_momentum = cap_norm("macd_momentum_score", "macd_momentum")
    cost_quality = cap_norm("cost_quality_score", "cost_quality")
    mean_reversion = cap_norm("mean_reversion_bonus", "mean_reversion")

    # Bipolar 2 (50 == neutral).
    if "velocity_bonus" in c or "velocity_penalty" in c:
        v_bonus = c.get("velocity_bonus", 0.0)
        v_penalty = c.get("velocity_penalty", 0.0)
        price_velocity: Optional[float] = _clip(
            50.0
            + (
                v_bonus / caps["velocity_bonus"]
                - v_penalty / caps["velocity_penalty"]
            )
            * 50.0
        )
    else:
        price_velocity = None

    if "diversification_bonus" in c or "portfolio_correlation_penalty" in c:
        d_bonus = c.get("diversification_bonus", 0.0)
        corr_penalty = c.get("portfolio_correlation_penalty", 0.0)
        portfolio_diversification: Optional[float] = _clip(
            50.0
            + (
                d_bonus / caps["diversification_bonus"]
                - corr_penalty / caps["portfolio_correlation_penalty"]
            )
            * 50.0
        )
    else:
        portfolio_diversification = None

    # Risk inverse (100 == low risk).
    if "variance_increase_penalty" in c:
        volatility_risk: Optional[float] = 100.0 - _clip(
            c["variance_increase_penalty"]
            / caps["variance_increase_penalty"]
            * 100.0
        )
    else:
        volatility_risk = None

    return {
        "pullback_strength_score": direct("intraday_pullback_strength_score"),
        "rebound_strength_score": direct("rebound_from_low_strength_score"),
        "controlled_down_quality_score": direct("controlled_down_strength_score"),
        "gap_down_quality_score": direct("gap_down_open_strength_score"),
        "range_recovery_score": direct("range_recovery_strength_score"),
        "trend_alignment_score": trend_alignment,
        "macd_momentum_score": macd_momentum,
        "volume_rank_score": direct("live_volume_rank_strength_score"),
        "volume_power_score": direct("live_volume_power_rank_strength_score"),
        "cost_quality_score": cost_quality,
        "mean_reversion_score": mean_reversion,
        "price_velocity_score": price_velocity,
        "liquidity_score": None,
        "volatility_risk_score": volatility_risk,
        "portfolio_diversification_score": portfolio_diversification,
    }
