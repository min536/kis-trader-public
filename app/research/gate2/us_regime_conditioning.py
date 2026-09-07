"""US regime-conditioning KR-day selector (BP-1 path A / E3 S1).

Pure, read-only research leaf. Given a US-day->regime map and KR<->US day
pairs, select the KR trading days whose mapped US session is in a target regime,
**excluding holdout US days** (leak prevention) and reporting per-regime
coverage. This wraps the intent of ``us_regime.regimes.conditional_kr_days``
with the holdout-exclusion + coverage guard the walk-forward search needs, over
plain structures (no pandas) so it is cheaply unit-testable with synthetic
fixtures. S2 bridges the ``calendar_map`` pandas frame into this selector.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping


@dataclass(frozen=True)
class ConditionedSelection:
    selected_kr_days: tuple[str, ...]
    regime_coverage: dict[str, int]
    excluded_holdout_count: int
    meets_minimum: bool


def select_conditioned_kr_days(
    regime_by_us_day: Mapping[str, str],
    kr_us_pairs: Iterable[object],
    *,
    target_regime: str,
    holdout_us_days: Iterable[str] = (),
    min_kr_days: int = 0,
) -> ConditionedSelection:
    """Select KR days conditioned on ``target_regime``, excluding holdout US days.

    ``kr_us_pairs`` is an iterable of ``(kr_day, us_day)`` pairs. Malformed pairs
    (non-2-tuple, blank days) are skipped rather than raising. A US day absent
    from ``regime_by_us_day`` contributes no regime and is skipped. Any pair
    whose US day is in ``holdout_us_days`` is excluded from the selection (and
    counted) so holdout leakage cannot enter the conditioned train set.

    Leak prevention is **per KR day, not per pair**: a KR day that touches a
    holdout US day through ANY mapping is excluded even if another (non-holdout)
    mapping would select it. Real ``calendar_map`` frames are 1:1 per kr_day,
    but S2 bridges arbitrary frames, so the selector stays safe under
    multi-mapping (2026-07-05 adversarial review probe).
    """
    holdout = {str(d) for d in holdout_us_days}
    regime_coverage: dict[str, int] = {}
    seen_us_for_coverage: set[str] = set()
    selected: set[str] = set()
    holdout_touched_kr_days: set[str] = set()
    excluded_holdout_count = 0

    for pair in kr_us_pairs:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            continue
        kr_day = str(pair[0]).strip()
        us_day = str(pair[1]).strip()
        if not kr_day or not us_day:
            continue
        regime = regime_by_us_day.get(us_day)
        if regime is None:
            continue
        regime = str(regime)

        # Coverage counts each US day once per regime (independent of holdout,
        # so the regime landscape is reported truthfully).
        if us_day not in seen_us_for_coverage:
            seen_us_for_coverage.add(us_day)
            regime_coverage[regime] = regime_coverage.get(regime, 0) + 1

        if regime != target_regime:
            continue
        if us_day in holdout:
            excluded_holdout_count += 1
            holdout_touched_kr_days.add(kr_day)
            continue
        selected.add(kr_day)

    # Per-KR-day exclusion: any holdout touch disqualifies the KR day entirely,
    # even when another non-holdout mapping selected it above.
    selected -= holdout_touched_kr_days

    selected_kr_days = tuple(sorted(selected))
    return ConditionedSelection(
        selected_kr_days=selected_kr_days,
        regime_coverage=regime_coverage,
        excluded_holdout_count=excluded_holdout_count,
        meets_minimum=len(selected_kr_days) >= max(0, int(min_kr_days)),
    )
