"""KR gate2 v2 offline rescorer tests (BP-6 / E5 S1).

Offline, read-only: replays recorded v1 ``score_components`` through the v2
pipeline (adapter -> weighted score -> threshold) and diffs the v2 selection
against the recorded v1 selection. No runtime mutation, no I/O, no broker calls.
See master_blueprint_20260704.md §9.
"""

from __future__ import annotations

import copy

from app.gate2.schema import CONDITION_SCORE_NAMES, ScoreV2Artifact
from app.research.gate2.kr_v2_rescore import rescore_records


def _one_weight_artifact(active: str, threshold: float) -> ScoreV2Artifact:
    weights = {name: 0.0 for name in CONDITION_SCORE_NAMES}
    weights[active] = 1.0
    return ScoreV2Artifact(
        version="test_w",
        weights=weights,
        buy_threshold=threshold,
        normalization_caps={},
    )


def test_diff_matches_hand_computation() -> None:
    # pullback_strength_score maps directly from intraday_pullback_strength_score.
    # weight 1.0 on pullback, threshold 60 -> v2_selected iff pullback >= 60.
    artifact = _one_weight_artifact("pullback_strength_score", 60.0)
    records = [
        {"symbol": "AAA", "v1_selected": True,
         "score_components": {"intraday_pullback_strength_score": 80.0}},  # v2 pass -> agree
        {"symbol": "BBB", "v1_selected": True,
         "score_components": {"intraday_pullback_strength_score": 40.0}},  # v2 fail -> v1 only
        {"symbol": "CCC", "v1_selected": False,
         "score_components": {"intraday_pullback_strength_score": 90.0}},  # v2 pass -> v2 only
    ]
    diff = rescore_records(records, artifact=artifact)
    assert diff.total == 3
    assert diff.agree_count == 1
    assert diff.v1_only == ("BBB",)
    assert diff.v2_only == ("CCC",)


def test_rescore_is_read_only_on_inputs() -> None:
    artifact = _one_weight_artifact("pullback_strength_score", 60.0)
    records = [
        {"symbol": "AAA", "v1_selected": True,
         "score_components": {"intraday_pullback_strength_score": 80.0}},
        {"symbol": "BBB", "v1_selected": False,
         "score_components": {"intraday_pullback_strength_score": 20.0}},
    ]
    before = copy.deepcopy(records)
    rescore_records(records, artifact=artifact)
    # No write-back into the supplied records or their component mappings.
    assert records == before


def test_final_score_is_recorded_per_outcome() -> None:
    artifact = _one_weight_artifact("pullback_strength_score", 60.0)
    records = [
        {"symbol": "AAA", "v1_selected": True,
         "score_components": {"intraday_pullback_strength_score": 80.0}},
    ]
    diff = rescore_records(records, artifact=artifact)
    assert diff.outcomes[0].symbol == "AAA"
    assert diff.outcomes[0].v2_final_score == 80.0
    assert diff.outcomes[0].v2_selected is True
    assert diff.outcomes[0].agrees is True


def test_hostile_records_are_skipped_not_raised() -> None:
    artifact = _one_weight_artifact("pullback_strength_score", 60.0)
    records = [
        None,
        "not a mapping",
        {"v1_selected": True, "score_components": {"intraday_pullback_strength_score": 80.0}},  # no symbol
        {"symbol": "  ", "score_components": {}},  # blank symbol
        {"symbol": "DDD", "v1_selected": True, "score_components": "not a mapping"},
        {"symbol": "EEE", "v1_selected": True,
         "score_components": {"intraday_pullback_strength_score": 70.0}},  # the only valid one
    ]
    diff = rescore_records(records, artifact=artifact)
    assert diff.total == 1
    assert diff.outcomes[0].symbol == "EEE"


def test_missing_components_yield_zero_score_not_selected() -> None:
    # A record with empty components -> all v2 scores None -> final 0 -> not selected.
    artifact = _one_weight_artifact("pullback_strength_score", 60.0)
    records = [
        {"symbol": "AAA", "v1_selected": True, "score_components": {}},
    ]
    diff = rescore_records(records, artifact=artifact)
    assert diff.outcomes[0].v2_final_score == 0.0
    assert diff.outcomes[0].v2_selected is False
    assert diff.v1_only == ("AAA",)
