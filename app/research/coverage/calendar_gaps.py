"""Calendar-level gap auditing for partitioned research data caches.

The module inspects directory names only. It never opens market-data payloads
and it never calls a broker or data-provider API.
"""

from __future__ import annotations

import calendar
import shlex
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Sequence


@dataclass(frozen=True)
class CalendarGapReport:
    """Date availability and missing-period classifications."""

    roots: tuple[str, ...]
    expected_start: str
    expected_end: str
    available_dates: tuple[str, ...]
    missing_years: tuple[int, ...]
    fully_missing_months: tuple[str, ...]
    confirmed_missing_trading_dates: tuple[str, ...]
    weekday_absence_candidates: tuple[str, ...]
    latest_available_date: str | None
    freshness_tail_start: str | None
    stale_calendar_days: int


@dataclass(frozen=True)
class FetchWindow:
    """A resumable calendar window prepared for a plan-only fetch command."""

    start: str
    end: str
    reason: str = "freshness_tail"


def scan_partition_dates(roots: Iterable[str | Path]) -> tuple[str, ...]:
    """Return valid ``date=YYYY-MM-DD`` directory values across ``roots``."""
    found: set[date] = set()
    for root in roots:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        for child in root_path.iterdir():
            if not child.is_dir() or not child.name.startswith("date="):
                continue
            try:
                found.add(date.fromisoformat(child.name.removeprefix("date=")))
            except ValueError:
                continue
    return tuple(day.isoformat() for day in sorted(found))


def build_calendar_gap_report(
    roots: Iterable[str | Path],
    *,
    expected_start: date | None = None,
    expected_end: date | None = None,
    expected_trading_dates: Iterable[str | date] = (),
) -> CalendarGapReport:
    """Audit year, month, trading-day, weekday-candidate, and freshness gaps.

    ``expected_trading_dates`` is optional and should come from an authoritative
    exchange calendar or another trusted trading-day dataset. Without it,
    absent weekdays remain candidates because Korean market holidays cannot be
    inferred safely from weekdays alone.
    """
    root_values = tuple(str(Path(root)) for root in roots)
    scanned = tuple(date.fromisoformat(value) for value in scan_partition_dates(root_values))

    if expected_start is None:
        if not scanned:
            raise ValueError("expected_start is required when no partitions exist")
        expected_start = min(scanned)
    if expected_end is None:
        if not scanned:
            raise ValueError("expected_end is required when no partitions exist")
        expected_end = max(scanned)
    if expected_start > expected_end:
        raise ValueError("expected_start must be on or before expected_end")

    available = tuple(
        day for day in scanned if expected_start <= day <= expected_end
    )
    available_set = set(available)

    missing_years = tuple(
        year
        for year in range(expected_start.year, expected_end.year + 1)
        if _whole_year_is_in_range(year, expected_start, expected_end)
        and not any(day.year == year for day in available)
    )
    fully_missing_months = tuple(
        f"{year:04d}-{month:02d}"
        for year, month in _iter_months(expected_start, expected_end)
        if _whole_month_is_in_range(year, month, expected_start, expected_end)
        and not any(day.year == year and day.month == month for day in available)
    )

    weekday_candidates: list[str] = []
    cursor = expected_start
    while cursor <= expected_end:
        if cursor.weekday() < 5 and cursor not in available_set:
            weekday_candidates.append(cursor.isoformat())
        cursor += timedelta(days=1)

    authoritative_dates = {
        _coerce_date(value)
        for value in expected_trading_dates
    }
    confirmed_missing = tuple(
        day.isoformat()
        for day in sorted(authoritative_dates)
        if expected_start <= day <= expected_end and day not in available_set
    )

    latest = max(available) if available else None
    if latest is not None and latest < expected_end:
        tail_start = latest + timedelta(days=1)
        stale_days = (expected_end - latest).days
    else:
        tail_start = None
        stale_days = 0

    return CalendarGapReport(
        roots=root_values,
        expected_start=expected_start.isoformat(),
        expected_end=expected_end.isoformat(),
        available_dates=tuple(day.isoformat() for day in available),
        missing_years=missing_years,
        fully_missing_months=fully_missing_months,
        confirmed_missing_trading_dates=confirmed_missing,
        weekday_absence_candidates=tuple(weekday_candidates),
        latest_available_date=latest.isoformat() if latest else None,
        freshness_tail_start=tail_start.isoformat() if tail_start else None,
        stale_calendar_days=stale_days,
    )


def build_monthly_fetch_windows(report: CalendarGapReport) -> tuple[FetchWindow, ...]:
    """Split the report's stale tail into restartable calendar-month windows."""
    if report.freshness_tail_start is None:
        return ()
    cursor = date.fromisoformat(report.freshness_tail_start)
    expected_end = date.fromisoformat(report.expected_end)
    windows: list[FetchWindow] = []
    while cursor <= expected_end:
        month_last = date(
            cursor.year,
            cursor.month,
            calendar.monthrange(cursor.year, cursor.month)[1],
        )
        window_end = min(month_last, expected_end)
        windows.append(FetchWindow(start=cursor.isoformat(), end=window_end.isoformat()))
        cursor = window_end + timedelta(days=1)
    return tuple(windows)


def build_plan_only_commands(
    windows: Sequence[FetchWindow],
    *,
    symbols_file: str,
    raw_dir: str,
) -> tuple[str, ...]:
    """Build KIS fetch dry-run commands; these commands cannot call the API."""
    commands: list[str] = []
    for window in windows:
        parts = [
            ".venv/bin/python",
            "-m",
            "app.tools.fetch_kis_minute_bars",
            "--symbols-file",
            symbols_file,
            "--start",
            window.start,
            "--end",
            window.end,
            "--raw-dir",
            raw_dir,
            "--plan-only",
        ]
        commands.append(shlex.join(parts))
    return tuple(commands)


def calendar_gap_report_to_dict(report: CalendarGapReport) -> dict:
    """Return a compact JSON-serializable representation."""
    return {
        "roots": list(report.roots),
        "expected_start": report.expected_start,
        "expected_end": report.expected_end,
        "available_date_count": len(report.available_dates),
        "first_available_date": report.available_dates[0] if report.available_dates else None,
        "latest_available_date": report.latest_available_date,
        "missing_years": list(report.missing_years),
        "fully_missing_months": list(report.fully_missing_months),
        "confirmed_missing_trading_dates": list(
            report.confirmed_missing_trading_dates
        ),
        "weekday_absence_candidates": list(report.weekday_absence_candidates),
        "freshness": {
            "tail_start": report.freshness_tail_start,
            "stale_calendar_days": report.stale_calendar_days,
        },
    }


def _coerce_date(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def _iter_months(start: date, end: date):
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1


def _whole_year_is_in_range(year: int, start: date, end: date) -> bool:
    return start <= date(year, 1, 1) and date(year, 12, 31) <= end


def _whole_month_is_in_range(
    year: int, month: int, start: date, end: date
) -> bool:
    month_start = date(year, month, 1)
    month_end = date(year, month, calendar.monthrange(year, month)[1])
    return start <= month_start and month_end <= end
