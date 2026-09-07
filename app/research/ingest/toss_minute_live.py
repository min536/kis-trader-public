"""T1 — pure logic for the intraday Toss minute collector.

docs/toss_minute_live_collection_design_20260707.md §3.1. Session gate, per-
symbol sweep planning (watermark-based catch-up), and idempotent bar merge. No
network, no clock, no filesystem — the residency loop (T3) injects ``now`` and
supplies the fetched rows. KR session first; US (Phase 2) is probe-gated.

Holiday calendar is intentionally out of scope here (weekday + time window only);
the residency loop layers a holiday skip via the existing calendar assets.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

# KR regular session. 15:31 upper bound so the 15:30 closing minute's bar, which
# finalizes just after 15:30, is still collected on the tick after close.
_KR_OPEN = time(9, 0)
_KR_CLOSE = time(15, 31)

_DEFAULT_FETCH_COUNT = 2  # direct previous minute + one self-heal re-fetch


@dataclass(frozen=True)
class SweepItem:
    """One symbol's fetch plan for a single sweep."""

    symbol: str
    count: int


def is_kr_session_open(now: datetime) -> bool:
    """True when ``now`` (KST-aware) is within the KR regular weekday session."""
    if now.weekday() >= 5:  # Saturday/Sunday
        return False
    return _KR_OPEN <= now.time() <= _KR_CLOSE


def _parse_watermark_minute(value: str) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except (ValueError, AttributeError):
            continue
    return None


def plan_sweep(
    now: datetime,
    *,
    symbols: tuple[str, ...],
    watermarks: dict[str, str],
    max_catchup: int = 10,
) -> list[SweepItem]:
    """Plan the per-symbol fetch counts for the sweep at ``now``.

    Normal case: fetch ``_DEFAULT_FETCH_COUNT`` recent minutes (previous minute
    plus a self-heal re-fetch of the one before). When a symbol's watermark shows
    a gap of *g* minutes, request ``g + 1`` bars (capped at ``max_catchup``) to
    catch up after a stall/restart.
    """
    now_minute = now.replace(second=0, microsecond=0, tzinfo=None)
    plan: list[SweepItem] = []
    for symbol in symbols:
        count = _DEFAULT_FETCH_COUNT
        raw = watermarks.get(symbol)
        if raw:
            last = _parse_watermark_minute(raw)
            if last is not None:
                gap_minutes = int((now_minute - last).total_seconds() // 60)
                if gap_minutes > 1:
                    # The current minute's bar is not finalized yet, so missing
                    # finalized bars = gap_minutes - 1; add one self-heal re-fetch
                    # of the boundary bar -> gap_minutes total.
                    count = min(gap_minutes, max_catchup)
        plan.append(SweepItem(symbol=symbol, count=max(count, 1)))
    return plan


def merge_bars(
    existing_rows: list[dict],
    fetched_rows: list[dict],
) -> list[dict]:
    """Idempotent upsert of minute bars keyed by ``(symbol, datetime)``.

    Later (fetched) rows overwrite earlier ones for the same minute so a re-fetch
    of an unfinalized bar self-corrects. Output is sorted by ``(symbol,
    datetime)`` for deterministic CSV rewrites.
    """
    merged: dict[tuple[str, str], dict] = {}
    for row in existing_rows:
        key = (str(row.get("symbol", "")), str(row.get("datetime", "")))
        merged[key] = row
    for row in fetched_rows:
        key = (str(row.get("symbol", "")), str(row.get("datetime", "")))
        merged[key] = row
    return [merged[key] for key in sorted(merged.keys())]
