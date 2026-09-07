"""US/Eastern clock and session utilities for overseas (US equity) trading.

NOTE: DEFAULT_US_HOLIDAYS is a provisional 2026 US equity-market holiday list.
      This list requires annual maintenance — verify against the NYSE holiday
      calendar each year before use.
"""
from datetime import datetime, time
from zoneinfo import ZoneInfo

US_EASTERN = ZoneInfo("America/New_York")
RTH_OPEN = time(9, 30)
RTH_CLOSE = time(16, 0)

# Provisional 2026 US equity-market holidays (maintenance required; note this).
DEFAULT_US_HOLIDAYS: frozenset[str] = frozenset(
    {
        "2026-01-01",
        "2026-01-19",
        "2026-02-16",
        "2026-04-03",
        "2026-05-25",
        "2026-06-19",
        "2026-07-03",
        "2026-09-07",
        "2026-11-26",
        "2026-12-25",
    }
)


def get_us_eastern_now() -> datetime:
    """Return the current datetime in the US/Eastern timezone."""
    return datetime.now(US_EASTERN)


def us_session_date(now: datetime | None = None) -> str:
    """Return the current US/Eastern date as an ISO-8601 string (YYYY-MM-DD)."""
    dt = (now or get_us_eastern_now()).astimezone(US_EASTERN)
    return dt.date().isoformat()


def to_eastern_date(ts: str) -> str | None:
    """Parse an ISO-8601 timestamp and return its US/Eastern date.

    If the timestamp is naive, it is assumed to be UTC.
    Returns None on any parse failure (never raises).
    """
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        return dt.astimezone(US_EASTERN).date().isoformat()
    except Exception:
        return None


def is_us_regular_session(
    now: datetime | None = None,
    *,
    holidays: frozenset[str] = DEFAULT_US_HOLIDAYS,
) -> bool:
    """Return True iff the given moment falls within US regular trading hours.

    Regular trading hours (RTH): Mon–Fri, 09:30–16:00 ET, excluding holidays.
    """
    dt = (now or get_us_eastern_now()).astimezone(US_EASTERN)
    if dt.weekday() >= 5:
        return False
    if dt.date().isoformat() in holidays:
        return False
    t = dt.time().replace(tzinfo=None)
    return RTH_OPEN <= t < RTH_CLOSE
