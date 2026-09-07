"""M-D1 tests — KR→last-US-session calendar map (plan §2)."""

from __future__ import annotations

from datetime import date

import pytest

from app.research.us_regime.calendar_map import (  # noqa: F401
    build_kr_to_us_map,
    kr_trading_days,
    to_frame,
)


def test_friday_us_maps_to_monday_kr_gap_3():
    # US sessions Wed/Thu/Fri; KR trading days Thu/Fri/Mon.
    us_days = [date(2020, 1, 1), date(2020, 1, 2), date(2020, 1, 3)]  # Wed-Fri
    kr_days = [date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6)]  # Thu,Fri,Mon
    mapping = build_kr_to_us_map(kr_days, us_days)
    # KR Monday 2020-01-06 <- last US session strictly before it = Fri 2020-01-03.
    assert mapping[date(2020, 1, 6)] == date(2020, 1, 3)
    # gap_days = (Mon - Fri) = 3.
    frame = to_frame(mapping)
    row = frame[frame["kr_day"] == date(2020, 1, 6)].iloc[0]
    assert int(row["gap_days"]) == 3


def test_kr_holiday_next_day_points_to_last_completed_us_session():
    # US sessions Wed/Thu. KR would have Thu+Fri, but Thu is a KR holiday, so
    # the surviving KR days are Fri and Mon. The next KR day after the holiday
    # (Fri) still resolves to the last US session strictly before it (Thu).
    us_days = [date(2020, 1, 1), date(2020, 1, 2)]  # Wed, Thu
    kr_days = [date(2020, 1, 3), date(2020, 1, 6)]  # Fri (post-holiday), Mon
    mapping = build_kr_to_us_map(kr_days, us_days)
    assert mapping[date(2020, 1, 3)] == date(2020, 1, 2)  # Fri <- Thu, gap 1
    assert mapping[date(2020, 1, 6)] == date(2020, 1, 2)  # Mon <- Thu, gap 4


def test_gap_5_kept_gap_6_excluded():
    kr_mon = date(2020, 1, 6)
    # gap 5: US Thu+Fri holiday, last US session Wed 2020-01-01 -> KR Mon = gap 5.
    m5 = build_kr_to_us_map([kr_mon], [date(2020, 1, 1)])
    assert m5[kr_mon] == date(2020, 1, 1)
    assert (kr_mon - date(2020, 1, 1)).days == 5

    # gap 6: last US session Tue 2019-12-31 -> KR Mon = gap 6 -> excluded.
    m6 = build_kr_to_us_map([kr_mon], [date(2019, 12, 31)])
    assert kr_mon not in m6
    assert m6 == {}


def _make_partition(root, day: date) -> None:
    # Mirror the toss minute parquet layout: date=YYYY-MM-DD/part.parquet.
    import pandas as pd

    part_dir = root / f"date={day.isoformat()}"
    part_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"symbol": [], "datetime": []}).to_parquet(
        part_dir / "part.parquet", index=False
    )


def test_kr_trading_days_missing_root_raises(tmp_path):
    with pytest.raises(FileNotFoundError) as exc:
        kr_trading_days(tmp_path / "does_not_exist")
    assert "gate2_minute_backtest_plan D1 conversion" in str(exc.value)


def test_kr_trading_days_zero_partitions_raises(tmp_path):
    empty_root = tmp_path / "toss"
    empty_root.mkdir()
    with pytest.raises(FileNotFoundError) as exc:
        kr_trading_days(empty_root)
    assert "gate2_minute_backtest_plan D1 conversion" in str(exc.value)


def test_kr_trading_days_derived_from_partition_names(tmp_path):
    root = tmp_path / "toss"
    for day in (date(2020, 1, 6), date(2020, 1, 2), date(2020, 1, 3)):
        _make_partition(root, day)
    days = kr_trading_days(root)
    # Ascending, derived from directory names (not file content).
    assert days == (date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6))
