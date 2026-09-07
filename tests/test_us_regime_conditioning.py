"""US regime-conditioning selector tests (BP-1 path A / E3 S1).

Pure selector over synthetic fixtures: given a US-day->regime map and KR<->US
day pairs, select the KR days conditioned on a target regime, EXCLUDING holdout
US days (leak prevention). No pandas, no I/O. See
docs/us_regime_conditioning_plan_20260705.md.
"""

from __future__ import annotations

from app.research.gate2.us_regime_conditioning import select_conditioned_kr_days


def test_selects_kr_days_for_target_regime() -> None:
    regime_by_us_day = {
        "2025-01-02": "A",
        "2025-01-03": "A",
        "2025-01-06": "B",
    }
    kr_us_pairs = [
        ("2025-01-02", "2025-01-02"),
        ("2025-01-03", "2025-01-03"),
        ("2025-01-06", "2025-01-06"),
    ]
    sel = select_conditioned_kr_days(
        regime_by_us_day, kr_us_pairs, target_regime="A"
    )
    assert sel.selected_kr_days == ("2025-01-02", "2025-01-03")
    assert sel.regime_coverage == {"A": 2, "B": 1}


def test_holdout_us_days_are_excluded_from_selection() -> None:
    # Leak prevention: a target-regime KR day mapped to a holdout US day must NOT
    # be selected, but coverage still reports the regime landscape truthfully.
    regime_by_us_day = {"2025-06-02": "A", "2025-06-03": "A", "2025-06-04": "A"}
    kr_us_pairs = [
        ("2025-06-02", "2025-06-02"),
        ("2025-06-03", "2025-06-03"),  # holdout -> excluded
        ("2025-06-04", "2025-06-04"),  # holdout -> excluded
    ]
    sel = select_conditioned_kr_days(
        regime_by_us_day,
        kr_us_pairs,
        target_regime="A",
        holdout_us_days=("2025-06-03", "2025-06-04"),
    )
    assert sel.selected_kr_days == ("2025-06-02",)
    assert sel.excluded_holdout_count == 2
    # Coverage counts all three A-regime US days regardless of holdout.
    assert sel.regime_coverage == {"A": 3}


def test_empty_target_regime_degrades_safely() -> None:
    regime_by_us_day = {"2025-01-02": "B"}
    kr_us_pairs = [("2025-01-02", "2025-01-02")]
    sel = select_conditioned_kr_days(
        regime_by_us_day, kr_us_pairs, target_regime="A", min_kr_days=1
    )
    assert sel.selected_kr_days == ()
    assert sel.meets_minimum is False


def test_min_kr_days_gate() -> None:
    regime_by_us_day = {"d1": "A", "d2": "A"}
    kr_us_pairs = [("k1", "d1"), ("k2", "d2")]
    sel = select_conditioned_kr_days(
        regime_by_us_day, kr_us_pairs, target_regime="A", min_kr_days=3
    )
    assert len(sel.selected_kr_days) == 2
    assert sel.meets_minimum is False
    sel2 = select_conditioned_kr_days(
        regime_by_us_day, kr_us_pairs, target_regime="A", min_kr_days=2
    )
    assert sel2.meets_minimum is True


def test_malformed_pairs_and_unknown_us_days_are_skipped() -> None:
    regime_by_us_day = {"d1": "A"}
    kr_us_pairs = [
        None,
        ("only-one",),
        ("k", "d", "extra"),
        ("", "d1"),        # blank kr
        ("k0", ""),        # blank us
        ("k1", "unknown"),  # us day not in regime map
        ("k2", "d1"),       # the one valid A-regime pair
    ]
    sel = select_conditioned_kr_days(
        regime_by_us_day, kr_us_pairs, target_regime="A"
    )
    assert sel.selected_kr_days == ("k2",)


def test_kr_day_touching_holdout_via_any_mapping_is_excluded() -> None:
    # Adversarial (2026-07-05 review probe): a KR day mapped to BOTH a holdout
    # US day and a non-holdout US day must NOT be selected via the non-holdout
    # path — leak prevention is per KR day, not per pair. Real calendar_map
    # frames are 1:1 per kr_day, but S2 bridges arbitrary frames, so the
    # selector must be safe under multi-mapping.
    regime_by_us_day = {"h": "A", "ok": "A", "ok2": "A"}
    kr_us_pairs = [
        ("k1", "h"),    # holdout touch
        ("k1", "ok"),   # non-holdout path for the SAME kr day
        ("k2", "ok2"),  # clean kr day
    ]
    sel = select_conditioned_kr_days(
        regime_by_us_day, kr_us_pairs, target_regime="A", holdout_us_days=("h",)
    )
    assert sel.selected_kr_days == ("k2",)
    assert sel.excluded_holdout_count == 1


def test_duplicate_kr_days_deduplicated() -> None:
    regime_by_us_day = {"d1": "A"}
    kr_us_pairs = [("k1", "d1"), ("k1", "d1")]
    sel = select_conditioned_kr_days(
        regime_by_us_day, kr_us_pairs, target_regime="A"
    )
    assert sel.selected_kr_days == ("k1",)
    # Coverage counts the US day once even when the pair repeats.
    assert sel.regime_coverage == {"A": 1}
