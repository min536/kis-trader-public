"""TDD tests for app/overseas_stock/scoring.py."""

import pytest


def _make_snapshot(*, symbol="AAPL", current=97.0, open_=100.0, high=100.0, low=96.0, prev_close=100.0, pct=-3.0, currency="USD"):
    from app.overseas_stock.market_snapshot import OverseasMarketSnapshot
    return OverseasMarketSnapshot(
        symbol=symbol,
        current_price=current,
        open_price=open_,
        high_price=high,
        low_price=low,
        prev_close=prev_close,
        prev_day_change_pct=pct,
        currency=currency,
    )


PARAMS = dict(
    rebound_from_low_pct=0.01,
    controlled_down_day_min=-3.0,
    controlled_down_day_max=-0.5,
    gap_down_open_min_pct=0.3,
    gap_down_open_max_pct=2.0,
    range_recovery_min_ratio=0.5,
)


def test_range_recovery_strength_matches_formula():
    from app.overseas_stock.scoring import compute_overseas_score_components, _threshold_strength

    # open=100, low=96, current=97 → intraday_range=4, recovery=(97-96)/4=0.25
    snap = _make_snapshot()
    components = compute_overseas_score_components(snap, **PARAMS)
    intraday_range = 100.0 - 96.0
    range_recovery_ratio = max((97.0 - 96.0) / intraday_range, 0.0)
    expected = _threshold_strength(range_recovery_ratio, 0.5)
    assert components["range_recovery_strength_score"] == expected


def test_gap_down_open_strength_matches_formula():
    from app.overseas_stock.scoring import compute_overseas_score_components, _range_strength

    # open=100, prev_close=100 → gap_down_pct = 0.0 → below min 0.3 → score < 100
    snap = _make_snapshot()
    components = compute_overseas_score_components(snap, **PARAMS)
    gap_down_open_pct = max((100.0 - 100.0) / 100.0 * 100, 0.0)
    gap_soft = max((2.0 - 0.3) * 0.25, 0.5)
    expected = _range_strength(gap_down_open_pct, 0.3, 2.0, soft_margin=gap_soft)
    assert components["gap_down_open_strength_score"] == expected


def test_controlled_down_strength_matches_formula():
    from app.overseas_stock.scoring import compute_overseas_score_components, _range_strength

    # prev_day_change_pct=-3.0, min=-3.0, max=-0.5 → exactly at lower bound → 100.0
    snap = _make_snapshot()
    components = compute_overseas_score_components(snap, **PARAMS)
    soft = max((-0.5 - -3.0) * 0.25, 1.0)
    expected = _range_strength(-3.0, -3.0, -0.5, soft_margin=soft)
    assert components["controlled_down_strength_score"] == expected


def test_rebound_from_low_strength_matches_formula():
    from app.overseas_stock.scoring import compute_overseas_score_components, _threshold_strength

    # current=97, low=96 → rebound_pct = (97-96)/96*100 = 1.0417...
    # rebound_threshold_pct = 0.01*100 = 1.0
    snap = _make_snapshot()
    components = compute_overseas_score_components(snap, **PARAMS)
    rebound_pct = max((97.0 - 96.0) / 96.0 * 100, 0.0)
    rebound_threshold_pct = max(0.01 * 100.0, 0.0)
    expected = _threshold_strength(rebound_pct, rebound_threshold_pct)
    assert components["rebound_from_low_strength_score"] == expected


def test_components_feed_gate2_v2_end_to_end():
    from app.overseas_stock.scoring import compute_overseas_score_components
    from app.gate2.adapter import condition_scores_from_v1
    from app.gate2.schema import default_artifact
    from app.gate2.score_v2 import weighted_gate2_score

    snap = _make_snapshot()
    components = compute_overseas_score_components(snap, **PARAMS)
    artifact = default_artifact()
    conditions = condition_scores_from_v1(components, artifact.normalization_caps)
    result = weighted_gate2_score(conditions, artifact.weights)

    assert conditions["volume_rank_score"] == 50.0
    assert conditions["volume_power_score"] == 50.0
    for key in ("pullback_strength_score", "rebound_strength_score", "controlled_down_quality_score", "gap_down_quality_score", "range_recovery_score"):
        assert conditions[key] is not None, f"{key} should not be None"
    assert isinstance(result.final_score, float)
    assert result.final_score > 0
    assert "trend_alignment_score" in result.missing
    assert "liquidity_score" in result.missing


def test_component_keys_are_adapter_direct_inputs():
    from app.overseas_stock.scoring import compute_overseas_score_components

    snap = _make_snapshot()
    components = compute_overseas_score_components(snap, **PARAMS)
    expected_keys = {
        "intraday_pullback_strength_score",
        "rebound_from_low_strength_score",
        "controlled_down_strength_score",
        "gap_down_open_strength_score",
        "range_recovery_strength_score",
        "live_volume_rank_strength_score",
        "live_volume_power_rank_strength_score",
    }
    assert set(components.keys()) == expected_keys


def test_volume_rules_neutral_fifty():
    from app.overseas_stock.scoring import compute_overseas_score_components

    snap = _make_snapshot()
    components = compute_overseas_score_components(snap, **PARAMS)
    assert components["live_volume_rank_strength_score"] == 50.0
    assert components["live_volume_power_rank_strength_score"] == 50.0


def test_pullback_strength_matches_formula():
    from app.overseas_stock.scoring import compute_overseas_score_components, _threshold_strength

    snap = _make_snapshot()
    components = compute_overseas_score_components(snap, **PARAMS)
    expected = _threshold_strength(3.0, 5.0)  # == 60.0
    assert components["intraday_pullback_strength_score"] == expected
