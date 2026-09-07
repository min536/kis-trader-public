"""M-F1 — leak-free k-NN similar US-day search (plan §3).

Leaf research module. numpy imported inside functions.
"""

from __future__ import annotations

from datetime import date, timedelta

_DAYS_PER_YEAR = 365.25


def find_similar_us_days(z, target_day, k: int = 20, max_age_years=8.0):
    """Top-``k`` past US days most similar to ``target_day`` (plan §3).

    Euclidean distance in z-space between the ``target_day`` row and each
    candidate row, ascending. Only rows **strictly before** ``target_day`` are
    candidates — a future date in the result is a leak (test-asserted). When
    ``max_age_years`` is not ``None``, rows older than that many years before
    ``target_day`` are excluded. Returns ``[(day, distance), ...]``.
    """
    import numpy as np

    if target_day not in z.index:
        raise KeyError(f"target_day {target_day!r} not in z frame index")

    target_vec = z.loc[target_day].to_numpy(dtype=float)

    min_day = None
    if max_age_years is not None:
        min_day = target_day - timedelta(days=max_age_years * _DAYS_PER_YEAR)

    scored: list[tuple[date, float]] = []
    for day, row in z.iterrows():
        if day >= target_day:  # leak gate: never look at the present or future
            continue
        if min_day is not None and day < min_day:
            continue
        diff = row.to_numpy(dtype=float) - target_vec
        dist = float(np.sqrt(np.dot(diff, diff)))
        scored.append((day, dist))

    scored.sort(key=lambda item: (item[1], item[0]))
    return scored[:k]
