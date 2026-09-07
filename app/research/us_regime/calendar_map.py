"""M-D1 — KR trading day ← last completed US session mapping (plan §2).

Leaf research module. pandas imported inside functions.
"""

from __future__ import annotations

from datetime import date


_D1_HINT = "run docs/gate2_minute_backtest_plan D1 conversion first"


def kr_trading_days(parquet_root) -> tuple[date, ...]:
    """KR trading-day canon = the toss minute parquet's ``date=`` partitions.

    Days are derived from the ``date=YYYY-MM-DD`` partition *directory names*
    (not file content), sorted ascending. If ``parquet_root`` is missing or has
    zero valid date partitions, raise ``FileNotFoundError`` (no silent empty
    calendar — plan §2).
    """
    from pathlib import Path

    root = Path(parquet_root)
    days: list[date] = []
    if root.is_dir():
        for part_dir in root.glob("date=*"):
            if not part_dir.is_dir():
                continue
            date_str = part_dir.name[len("date=") :]
            try:
                days.append(date.fromisoformat(date_str))
            except ValueError:
                continue
    if not days:
        raise FileNotFoundError(_D1_HINT)
    return tuple(sorted(set(days)))


GAP_DAYS_MAX = 5
"""Exclusion threshold (plan §2, review #12): keep ``gap_days <= 5`` (a US
Thu/Fri holiday + weekend → KR Monday is gap 5 and stays valid); exclude ``> 5``
as low-confidence."""


def build_kr_to_us_map(kr_days, us_days) -> dict:
    """Map each KR trading day ``K`` to the last completed US session before it.

    Conservative date-only rule (plan §2, timezone calc deliberately coarse):
    a US session on eastern date ``D`` completes overnight in KST (≈ ``D+1``
    dawn), so ``map[K] = max{D in us_days : D < K}`` (strict calendar-day
    comparison). US holidays that push ``D`` back are kept as-is but recorded via
    ``gap_days = (K - D).days``.

    Days whose nearest prior US session is ``gap_days > GAP_DAYS_MAX`` are
    **excluded** (low-confidence); so are KR days with no prior US session.
    Returns ``{kr_day: us_day}`` (``dict[date, date]``); ``gap_days`` is a pure
    function of the pair and is recomputed in ``to_frame``.
    """
    ordered_us = sorted(set(us_days))
    mapping: dict = {}
    excluded = 0
    for k in sorted(set(kr_days)):
        prior = [d for d in ordered_us if d < k]
        if not prior:
            excluded += 1
            continue
        d = prior[-1]
        gap = (k - d).days
        if gap > GAP_DAYS_MAX:
            excluded += 1
            continue
        mapping[k] = d
    # Build-log line (plan §2: "제외된 KR 일수는 매핑 빌드 로그에 카운트로 출력").
    print(
        f"[us_regime.calendar_map] kr_to_us_map: mapped={len(mapping)} "
        f"excluded={excluded} (gap>{GAP_DAYS_MAX} or no prior US session)"
    )
    return mapping


def to_frame(mapping):
    """``{kr_day: us_day}`` → DataFrame ``(kr_day, us_day, gap_days)``.

    ``gap_days = (kr_day - us_day).days``. Rows ascending by ``kr_day``.
    Persisting to ``results/gate2_backtest/kr_us_calendar.parquet`` is the CLI's
    job.
    """
    import pandas as pd

    records = [
        {"kr_day": k, "us_day": d, "gap_days": (k - d).days}
        for k, d in sorted(mapping.items())
    ]
    return pd.DataFrame(records, columns=["kr_day", "us_day", "gap_days"])
