"""F4 — dead-symbol auto-quarantine (E4).

Design: docs/daily_error_triage_design_20260707.md §5.

A symbol that returns a malformed quote (e.g. missing ``stck_shrn_iscd``) is
skipped every scan cycle, spamming a warning and burning a quote call each time.
Track consecutive missing-field skips per symbol; after
``DEAD_SYMBOL_MISS_THRESHOLD`` in a row, quarantine it for the rest of the day
(``runtime_state['dead_scan_symbols_today']``). The scan pre-filter then drops it
from the universe, so the warning stops. A symbol that couldn't return a valid
quote can't be scored/bought anyway, so quarantining it changes no order
behaviour — it only stops wasted calls and log spam. Pure and defensive: never
raises; counters reset on a clean scan and on a new trading day.
"""

from __future__ import annotations

from typing import Any, Iterable

DEAD_SYMBOL_MISS_THRESHOLD = 3


def _coerce_str_list(values: Iterable[Any] | None) -> list[str]:
    if not values:
        return []
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text:
            out.append(text)
    return out


def update_dead_symbol_tracking(
    state: dict[str, Any],
    *,
    parse_skipped_symbols: Iterable[str] | None,
    scanned_symbols: Iterable[str] | None,
    today: str,
    threshold: int = DEAD_SYMBOL_MISS_THRESHOLD,
) -> dict[str, list[str]]:
    """Update consecutive-miss tracking and promote to today's quarantine set.

    Resets when the stored day differs from ``today``. Increments the miss
    counter for each ``parse_skipped_symbols`` entry, resets it for symbols that
    were scanned OK (attempted but not skipped), and promotes symbols reaching
    ``threshold`` to ``dead_scan_symbols_today``. Returns the newly quarantined
    symbols (warn-once) and the full dead set. Never raises.
    """
    if not isinstance(state, dict):
        return {"newly_quarantined": [], "dead_symbols": []}

    tracking = state.get("dead_scan_tracking")
    if not isinstance(tracking, dict) or tracking.get("day") != today:
        tracking = {"day": today, "misses": {}, "dead": []}

    misses_raw = tracking.get("misses")
    misses: dict[str, int] = dict(misses_raw) if isinstance(misses_raw, dict) else {}
    dead: list[str] = _coerce_str_list(tracking.get("dead"))

    skipped = set(_coerce_str_list(parse_skipped_symbols))
    scanned = set(_coerce_str_list(scanned_symbols))

    # A symbol attempted and not skipped scanned OK → reset its streak.
    for symbol in scanned - skipped:
        if symbol in misses:
            misses[symbol] = 0

    newly: list[str] = []
    for symbol in skipped:
        try:
            current = int(misses.get(symbol, 0))
        except (TypeError, ValueError):
            current = 0
        current += 1
        misses[symbol] = current
        if current >= threshold and symbol not in dead:
            dead.append(symbol)
            newly.append(symbol)

    state["dead_scan_tracking"] = {"day": today, "misses": misses, "dead": dead}
    state["dead_scan_symbols_today"] = list(dead)
    return {"newly_quarantined": newly, "dead_symbols": list(dead)}


def filter_dead_symbols(
    symbols: Iterable[str],
    state: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Split ``symbols`` into (live, excluded) by today's quarantine set. Never raises."""
    dead_raw = state.get("dead_scan_symbols_today") if isinstance(state, dict) else None
    dead = set(_coerce_str_list(dead_raw)) if isinstance(dead_raw, list) else set()
    live: list[str] = []
    excluded: list[str] = []
    for symbol in symbols or []:
        if str(symbol) in dead:
            excluded.append(symbol)
        else:
            live.append(symbol)
    return live, excluded
