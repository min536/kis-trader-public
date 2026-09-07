"""Overseas (US stock) score_components builder for the gate2 pipeline.

Produces the v1-style ``score_components`` dict consumed by
``app/gate2/adapter.py::condition_scores_from_v1``.
The two US-unavailable volume rules are NEUTRAL-filled = 50.0.
"""

from __future__ import annotations

# mirrors app/scanner/scoring.py


def _clamp(value, lower=0.0, upper=1.0):  # mirrors app/scanner/scoring.py
    return max(lower, min(upper, value))


def _percent_score(value):  # mirrors app/scanner/scoring.py
    return round(_clamp(value) * 100.0, 2)


def _threshold_strength(value, full_score_at):  # mirrors app/scanner/scoring.py
    if full_score_at <= 0:
        return 0.0
    return _percent_score(value / full_score_at)


def _range_strength(value, lower, upper, *, soft_margin):  # mirrors app/scanner/scoring.py
    if upper <= lower:
        return 0.0
    if lower <= value <= upper:
        return 100.0
    margin = max(float(soft_margin), 0.01)
    if value < lower:
        return _percent_score(1.0 - ((lower - value) / margin))
    return _percent_score(1.0 - ((value - upper) / margin))


NEUTRAL_VOLUME_STRENGTH = 50.0


def compute_overseas_score_components(
    snapshot,  # OverseasMarketSnapshot
    *,
    rebound_from_low_pct,
    controlled_down_day_min,
    controlled_down_day_max,
    gap_down_open_min_pct,
    gap_down_open_max_pct,
    range_recovery_min_ratio,
) -> dict[str, float]:
    """Compute gate2 v1 score_components from an OverseasMarketSnapshot.

    Formulas mirror ``calculate_selection_score`` in ``app/scanner/scoring.py``
    but operate on float USD prices (currency-agnostic).
    """
    current = snapshot.current_price
    open_ = snapshot.open_price
    low = snapshot.low_price
    prev_close = snapshot.prev_close
    prev_day_change_pct = snapshot.prev_day_change_pct

    pullback_pct = max((open_ - current) / open_ * 100, 0.0) if open_ > 0 else 0.0
    rebound_pct = max((current - low) / low * 100, 0.0) if low > 0 else 0.0
    rebound_threshold_pct = max(rebound_from_low_pct * 100.0, 0.0)
    gap_down_open_pct = max((prev_close - open_) / prev_close * 100, 0.0) if prev_close > 0 else 0.0
    intraday_range = open_ - low
    range_recovery_ratio = max((current - low) / intraday_range, 0.0) if intraday_range > 0 else 0.0

    controlled_soft = max((controlled_down_day_max - controlled_down_day_min) * 0.25, 1.0)
    gap_soft = max((gap_down_open_max_pct - gap_down_open_min_pct) * 0.25, 0.5)

    return {
        "intraday_pullback_strength_score": round(_threshold_strength(pullback_pct, 5.0), 2),
        "rebound_from_low_strength_score": round(_threshold_strength(rebound_pct, rebound_threshold_pct), 2),
        "controlled_down_strength_score": round(_range_strength(prev_day_change_pct, controlled_down_day_min, controlled_down_day_max, soft_margin=controlled_soft), 2),
        "gap_down_open_strength_score": round(_range_strength(gap_down_open_pct, gap_down_open_min_pct, gap_down_open_max_pct, soft_margin=gap_soft), 2),
        "range_recovery_strength_score": round(_threshold_strength(range_recovery_ratio, range_recovery_min_ratio), 2),
        "live_volume_rank_strength_score": NEUTRAL_VOLUME_STRENGTH,
        "live_volume_power_rank_strength_score": NEUTRAL_VOLUME_STRENGTH,
    }
