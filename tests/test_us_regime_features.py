"""M-F1 tests — session features + leak-free similarity (plan §3)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.research.us_regime.features import (
    build_feature_frame,
    expanding_zscore,
)
from app.research.us_regime.similarity import find_similar_us_days


def _daily(rows):
    """rows: list of (date, open, high, low, close, volume) -> DataFrame."""
    return pd.DataFrame(
        rows, columns=["date", "open", "high", "low", "close", "volume"]
    )


def test_feature_hand_verification():
    # 7 sessions; close chosen so day_ret is easy to reason about. The last row
    # (2020-01-09) is the first with a full 5-session rv5 window behind it.
    rows = [
        (date(2020, 1, 1), 100.0, 101.0, 99.0, 100.0, 0),
        (date(2020, 1, 2), 100.0, 102.0, 100.0, 101.0, 0),
        (date(2020, 1, 3), 101.0, 103.0, 100.0, 102.0, 0),
        (date(2020, 1, 6), 102.0, 104.0, 101.0, 103.0, 0),
        (date(2020, 1, 7), 103.0, 105.0, 102.0, 104.0, 0),
        (date(2020, 1, 8), 104.0, 106.0, 103.0, 105.0, 0),
        (date(2020, 1, 9), 105.0, 110.0, 104.0, 108.0, 0),
    ]
    frame = build_feature_frame({"SPX": _daily(rows)}, ("SPX",))

    # index is the us_day; warmup rows (no prev_close / short rv5 window) dropped.
    last = frame.loc[date(2020, 1, 9)]
    prev_close = 105.0  # close of 2020-01-08
    assert last["SPX_gap_pct"] == pytest.approx(105.0 / prev_close - 1)  # 0.0
    assert last["SPX_intraday_ret"] == pytest.approx(108.0 / 105.0 - 1)
    assert last["SPX_day_ret"] == pytest.approx(108.0 / prev_close - 1)
    assert last["SPX_close_pos"] == pytest.approx((108.0 - 104.0) / (110.0 - 104.0))
    assert last["SPX_range_pct"] == pytest.approx((110.0 - 104.0) / prev_close)

    # rv5 = population... use sample stdev of the prior 5 day_ret values.
    closes = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 108.0]
    day_rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    prior5 = day_rets[-6:-1]  # the 5 sessions strictly before 2020-01-09
    assert last["SPX_rv5"] == pytest.approx(np.std(prior5, ddof=1))


def test_expanding_zscore_excludes_current_row_from_its_own_stats():
    # A single column of known values; min_obs small for the synthetic test.
    idx = pd.Index([date(2020, 1, i) for i in range(1, 8)], name="us_day")
    vals = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 100.0]  # last is an outlier
    frame = pd.DataFrame({"x": vals}, index=idx)

    z = expanding_zscore(frame, min_obs=3)

    # min_obs=3 -> the first row with >=3 PRIOR observations is index position 3
    # (value 4.0), computed from prior [1,2,3]. Rows before that are dropped.
    assert z.index[0] == date(2020, 1, 4)

    # Hand-check the first surviving row: prior = [1,2,3].
    prior = np.array([1.0, 2.0, 3.0])
    expected = (4.0 - prior.mean()) / prior.std(ddof=1)
    assert z["x"].iloc[0] == pytest.approx(expected)

    # LEAK GATE: the outlier last row (100.0) must be normalized against ONLY the
    # prior [1..6], NOT including itself. If 100 were in its own stats, the mean
    # would be pulled up massively and the z-score would be far smaller.
    prior_all = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    expected_last = (100.0 - prior_all.mean()) / prior_all.std(ddof=1)
    assert z["x"].loc[date(2020, 1, 7)] == pytest.approx(expected_last)
    # The self-inclusive z-score would be a completely different (smaller) value.
    self_incl = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 100.0])
    self_incl_z = (100.0 - self_incl.mean()) / self_incl.std(ddof=1)
    assert abs(z["x"].loc[date(2020, 1, 7)] - self_incl_z) > 1.0


def _z_frame(pairs):
    """pairs: list of (date, [feature values]) -> z DataFrame."""
    idx = pd.Index([p[0] for p in pairs], name="us_day")
    data = np.array([p[1] for p in pairs], dtype=float)
    cols = [f"f{i}" for i in range(data.shape[1])]
    return pd.DataFrame(data, index=idx, columns=cols)


def test_similar_days_never_include_future():
    # target sits in the middle; there are rows both before and after it.
    frame = _z_frame(
        [
            (date(2020, 1, 1), [0.0, 0.0]),
            (date(2020, 1, 2), [0.1, 0.1]),  # closest to target
            (date(2020, 1, 3), [5.0, 5.0]),  # far
            (date(2020, 1, 6), [0.0, 0.0]),  # TARGET
            (date(2020, 1, 7), [0.05, 0.05]),  # future, would be closest
            (date(2020, 1, 8), [0.0, 0.0]),  # future, identical to target
        ]
    )
    target = date(2020, 1, 6)
    out = find_similar_us_days(frame, target, k=10, max_age_years=None)

    returned_days = [d for d, _ in out]
    # NO future dates (>= target) may appear.
    assert all(d < target for d in returned_days)
    assert date(2020, 1, 7) not in returned_days
    assert date(2020, 1, 8) not in returned_days
    assert target not in returned_days

    # Euclidean distance ascending: 2020-01-01 ([0,0], distance 0 from target)
    # first, the far row (2020-01-03) last.
    assert returned_days[0] == date(2020, 1, 1)
    assert returned_days[-1] == date(2020, 1, 3)
    dists = [dist for _, dist in out]
    assert dists == sorted(dists)
    # 2020-01-01 is exactly at distance 0 from target [0,0].
    assert dict(out)[date(2020, 1, 1)] == pytest.approx(0.0)


def test_max_age_years_excludes_old_days():
    frame = _z_frame(
        [
            (date(2010, 1, 4), [0.0, 0.0]),  # ~10y before target -> excluded
            (date(2019, 6, 3), [1.0, 1.0]),  # within 1y -> kept
            (date(2020, 1, 6), [0.0, 0.0]),  # TARGET
        ]
    )
    out = find_similar_us_days(frame, date(2020, 1, 6), k=10, max_age_years=1.0)
    returned = [d for d, _ in out]
    assert date(2010, 1, 4) not in returned
    assert date(2019, 6, 3) in returned


def test_close_pos_high_equals_low_is_half():
    # A flat session (high == low == open == close). close_pos must be 0.5, not
    # a divide-by-zero. Build enough sessions that the flat one survives warmup.
    rows = [
        (date(2020, 1, 1), 100.0, 101.0, 99.0, 100.0, 0),
        (date(2020, 1, 2), 100.0, 102.0, 100.0, 101.0, 0),
        (date(2020, 1, 3), 101.0, 103.0, 100.0, 102.0, 0),
        (date(2020, 1, 6), 102.0, 104.0, 101.0, 103.0, 0),
        (date(2020, 1, 7), 103.0, 105.0, 102.0, 104.0, 0),
        (date(2020, 1, 8), 104.0, 106.0, 103.0, 105.0, 0),
        (date(2020, 1, 9), 105.0, 105.0, 105.0, 105.0, 0),  # flat: high == low
    ]
    frame = build_feature_frame({"SPX": _daily(rows)}, ("SPX",))
    assert frame.loc[date(2020, 1, 9), "SPX_close_pos"] == pytest.approx(0.5)
    # range_pct for a flat bar is 0 (span 0), still finite.
    assert frame.loc[date(2020, 1, 9), "SPX_range_pct"] == pytest.approx(0.0)
