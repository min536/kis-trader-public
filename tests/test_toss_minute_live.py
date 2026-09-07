"""T1 (docs/toss_minute_live_collection_design_20260707.md §3.1): pure logic for
the intraday Toss minute collector — session gate, per-symbol sweep planning
(watermark-based catch-up), and idempotent bar merge. No network, no clock."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.research.ingest import toss_minute_live as tml

_KST = ZoneInfo("Asia/Seoul")


def _kst(y, mo, d, h, mi) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=_KST)


# -- session gate ---------------------------------------------------------


def test_kr_session_open_during_regular_hours() -> None:
    assert tml.is_kr_session_open(_kst(2026, 7, 7, 10, 30)) is True  # Tue 10:30


def test_kr_session_closed_before_open_and_after_close() -> None:
    assert tml.is_kr_session_open(_kst(2026, 7, 7, 8, 59)) is False
    assert tml.is_kr_session_open(_kst(2026, 7, 7, 15, 40)) is False


def test_kr_session_closed_on_weekend() -> None:
    assert tml.is_kr_session_open(_kst(2026, 7, 4, 10, 30)) is False  # Saturday


# -- sweep planning -------------------------------------------------------


def test_plan_sweep_default_count_when_no_watermark() -> None:
    now = _kst(2026, 7, 7, 10, 5)
    plan = tml.plan_sweep(now, symbols=("012450", "047810"), watermarks={})
    counts = {item.symbol: item.count for item in plan}
    # No prior state -> minimal self-healing fetch of 2 recent minutes.
    assert counts == {"012450": 2, "047810": 2}


def test_plan_sweep_catches_up_on_gap() -> None:
    now = _kst(2026, 7, 7, 10, 5)
    # last saved bar 10:00 -> minutes 10:01..10:04 missing (gap 4) + current.
    plan = tml.plan_sweep(
        now,
        symbols=("012450",),
        watermarks={"012450": "2026-07-07 10:00"},
        max_catchup=10,
    )
    item = plan[0]
    assert item.symbol == "012450"
    assert item.count == 5  # 4 missing + 1 self-heal


def test_plan_sweep_caps_catchup() -> None:
    now = _kst(2026, 7, 7, 14, 0)
    plan = tml.plan_sweep(
        now,
        symbols=("012450",),
        watermarks={"012450": "2026-07-07 09:00"},
        max_catchup=30,
    )
    assert plan[0].count == 30  # capped, not 300+


# -- idempotent merge -----------------------------------------------------


def test_merge_bars_upserts_by_minute_later_wins() -> None:
    existing = [
        {"symbol": "012450", "datetime": "2026-07-07 10:00", "close": 100},
        {"symbol": "012450", "datetime": "2026-07-07 10:01", "close": 101},
    ]
    fetched = [
        {"symbol": "012450", "datetime": "2026-07-07 10:01", "close": 999},  # revised
        {"symbol": "012450", "datetime": "2026-07-07 10:02", "close": 102},  # new
    ]
    merged = tml.merge_bars(existing, fetched)
    by_dt = {row["datetime"]: row["close"] for row in merged}
    assert by_dt == {
        "2026-07-07 10:00": 100,
        "2026-07-07 10:01": 999,  # fetched (later) overwrote the existing bar
        "2026-07-07 10:02": 102,
    }
    # merged rows stay sorted by datetime
    assert [row["datetime"] for row in merged] == [
        "2026-07-07 10:00",
        "2026-07-07 10:01",
        "2026-07-07 10:02",
    ]


def test_merge_bars_keeps_distinct_symbols_separate() -> None:
    existing = [{"symbol": "A", "datetime": "2026-07-07 10:00", "close": 1}]
    fetched = [{"symbol": "B", "datetime": "2026-07-07 10:00", "close": 2}]
    merged = tml.merge_bars(existing, fetched)
    keys = {(row["symbol"], row["datetime"]) for row in merged}
    assert keys == {("A", "2026-07-07 10:00"), ("B", "2026-07-07 10:00")}
