"""Slice R1 — gate2 ``EvaluationRecord`` runner over the replay scan driver.

Walks each trading day 09:01-15:30 in 1-minute ticks through the REAL scanner
(via ``app.research.replay.scan_driver.run_scan_at``) with an EMPTY sim (the
record stage is portfolio-stateless), and for every gate1-pass candidate emits a
per-horizon record. Each record carries the 15 v2 condition scores
(``condition_scores_from_v1``) and a forward-return label.

**Portfolio-stateless note (plan §7 R1 / 2nd-review F-3).** ``sim=None`` is passed
consistently through ``run_scan_at``. Two of the 15 condition scores
(``portfolio_diversification_score`` / ``volatility_risk_score``) are therefore
always neutral/None at the record stage — that is W2/Stage-A's concern (those
dims are frozen there); the records still CONTAIN all 15 columns.

**Label look-ahead (plan §7 R1 direction rule, extended by the coordinator for
missing bars — authorized).** Labels are labels, not features, so they are
allowed to look ahead:

- ``entry`` = open of the FIRST same-day bar with ``ts >= t + 1min``. If no such
  bar exists (no fill possible), the record is DROPPED.
- ``exit`` = open of the first same-day bar with ``ts >= t + 1min + h``. If none
  exists (session end), the last same-day bar's close is used and
  ``truncated=True``.
- ``forward_return_bps = (exit / entry - 1) * 10_000 - cost_roundtrip_bps``.

Future bars are read via ``provider.observable_snapshot(symbol, future_t)`` and
its ``bar_ts``: a bar with ``bar_ts == future_t`` is exactly that minute's bar
(use its ``.open``); otherwise the walk advances minute by minute within the
session to find the first bar with ``bar_ts >= target``, capped at session close.

Leaf research module: imports stdlib + gate2 pure-calculation siblings + the
replay scan driver. No ``get_settings`` / broker imports; no imports from
``tests/``.
"""

from __future__ import annotations

import contextlib
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from app.gate2.adapter import condition_scores_from_v1
from app.gate2.schema import CONDITION_SCORE_NAMES, default_artifact
from app.research.gate2.weight_search import EvaluationRecord
from app.research.replay.scan_driver import (
    ReplayHistoryWriter,
    ReplayLiveSnapshotWriter,
    run_scan_at,
)

_ONE_MINUTE = timedelta(minutes=1)


@dataclass(frozen=True)
class RecordRow:
    """Internal per-horizon row: an ``EvaluationRecord`` plus its ``truncated``
    flag (``EvaluationRecord`` has no truncated field). The parquet writer reads
    ``symbol``, ``ts``, the 15 score columns, ``forward_return_bps`` and
    ``truncated`` off this structure.
    """

    symbol: str
    ts: str
    scores: dict
    forward_return_bps: float
    truncated: bool

    @property
    def record(self) -> EvaluationRecord:
        return EvaluationRecord(
            symbol=self.symbol,
            ts=self.ts,
            scores=self.scores,
            forward_return_bps=self.forward_return_bps,
        )


def _hhmm_to_time(hhmm: str) -> time:
    h, m = (int(x) for x in hhmm.split(":")[:2])
    return time(h, m)


def _iter_tick_minutes(day: date, open_t: time, close_t: time):
    """Yield every 1-minute tick datetime in ``[open, close]`` for ``day``."""
    t = datetime.combine(day, open_t)
    end = datetime.combine(day, close_t)
    while t <= end:
        yield t
        t += _ONE_MINUTE


def _bar_open_at_or_after(provider, symbol: str, target, close_dt) -> float | None:
    """Open of the first same-day bar with ``bar_ts >= target`` (walking minute
    by minute, capped at ``close_dt``), or ``None`` if the session ends first.

    ``observable_snapshot`` returns the latest bar with ``bar_ts <= future_t``;
    when ``bar_ts == future_t`` that IS the bar at ``future_t``. Because the
    walk starts at ``target`` and steps forward, the first observation whose
    ``bar_ts >= target`` is the first bar at/after ``target``.
    """
    direct = getattr(provider, "bar_open_at_or_after", None)
    if callable(direct):
        return direct(symbol, target, close_dt)

    probe = target
    while probe <= close_dt:
        obs = provider.observable_snapshot(symbol, probe)
        if obs is not None and obs.bar_ts >= target:
            return float(obs.open)
        probe += _ONE_MINUTE
    return None


def _last_close_on_day(provider, symbol: str, close_dt) -> float | None:
    """Close of the last same-day bar at/before session close (or ``None``)."""
    direct = getattr(provider, "last_close_on_day", None)
    if callable(direct):
        return direct(symbol, close_dt)

    obs = provider.observable_snapshot(symbol, close_dt)
    if obs is None:
        return None
    return float(obs.close)


def _forward_label(
    provider,
    symbol: str,
    t: datetime,
    horizon_min: int,
    close_dt: datetime,
    cost_roundtrip_bps: float,
) -> tuple[float, bool] | None:
    """Return ``(forward_return_bps, truncated)`` for one (symbol, t, horizon),
    or ``None`` when there is no entry fill (drop the record)."""
    entry_target = t + _ONE_MINUTE
    entry = _bar_open_at_or_after(provider, symbol, entry_target, close_dt)
    if entry is None or entry == 0:
        return None

    exit_target = t + _ONE_MINUTE + timedelta(minutes=horizon_min)
    exit_open = _bar_open_at_or_after(provider, symbol, exit_target, close_dt)
    if exit_open is not None:
        exit_price = exit_open
        truncated = False
    else:
        last_close = _last_close_on_day(provider, symbol, close_dt)
        if last_close is None:
            return None
        exit_price = last_close
        truncated = True

    forward_return_bps = (exit_price / entry - 1.0) * 10_000.0 - cost_roundtrip_bps
    return forward_return_bps, truncated


def build_records(
    *,
    provider,
    dates: list[date],
    horizons_min=(10, 30, 60),
    cost_roundtrip_bps: float,
    settings,
    session_open: str = "09:01",
    session_close: str = "15:30",
    history_writer: ReplayHistoryWriter | None = None,
    live_writer: ReplayLiveSnapshotWriter | None = None,
    reseed_before_first_day: bool = False,
) -> dict[int, list[RecordRow]]:
    """Build per-horizon gate2 evaluation records over ``dates``.

    Returns ``{horizon_min: [RecordRow, ...]}``. Each ``RecordRow`` exposes the
    ``EvaluationRecord`` view via ``.record`` plus its ``truncated`` flag for the
    parquet writer.

    Day-loop mechanics (plan §7 R1): one ``ReplayLiveSnapshotWriter`` and one
    ``ReplayHistoryWriter`` are reused across ALL days (the history buffer lives
    on the writer instance — a fresh writer per day would wipe prior history);
    ``reseed_for_new_day`` is called at each day boundary AFTER the first day.
    ``provider.load_window(day, day)`` loads each day's minute bars.

    Writer injection (cross-chunk continuity): by default this function OWNS the
    two store writers — it creates them in a run-scoped tmp dir (never repo
    ``data/``) and they die with the call. A chunked caller (e.g. the monthly
    CLI) must instead pass ONE shared ``history_writer``/``live_writer`` pair
    into every chunk and set ``reseed_before_first_day=True`` from the second
    chunk on: that triggers ``reseed_for_new_day()`` before this chunk's first
    day, carrying the previous chunk's history tail across the boundary exactly
    like an intra-chunk day boundary. Without this, every chunk would restart
    with EMPTY history and the first ~8 trading days per chunk (3000-record
    technical window ≈ 7.7 days) would see systematically thinner
    trend/macd/momentum context than runtime — a calendar-correlated bias.
    When writers are injected, no tmp dir is created for them and their
    lifecycle stays with the caller.
    """
    open_t = _hhmm_to_time(session_open)
    close_t = _hhmm_to_time(session_close)
    caps = default_artifact().normalization_caps
    per_horizon: dict[int, list[RecordRow]] = {int(h): [] for h in horizons_min}

    with contextlib.ExitStack() as stack:
        if history_writer is None or live_writer is None:
            tmp_path = Path(
                stack.enter_context(
                    tempfile.TemporaryDirectory(prefix="gate2_records_")
                )
            )
            if live_writer is None:
                live_dir = tmp_path / "live"
                live_dir.mkdir()
                live_writer = ReplayLiveSnapshotWriter(live_dir)
            if history_writer is None:
                history_writer = ReplayHistoryWriter(
                    tmp_path / "cycle_snapshots.jsonl"
                )

        # Chunk-boundary continuity: the caller signals that this call continues
        # a previous chunk on the SAME shared writers — treat the boundary like
        # a normal day boundary (keep the tail, don't wipe).
        if reseed_before_first_day:
            history_writer.reseed_for_new_day()

        # Both store seams (seam A live snapshot + seam B history JSONL) are
        # redirected to these writers' tmp paths INSIDE ``run_scan_at`` for the
        # duration of each scan (§10-① pre-approved seams). ``build_records``
        # never reads the stores outside ``run_scan_at``, so it delegates all
        # seam management there and only owns the writers' lifecycles here
        # (and only when not injected by the caller).
        for day_index, day in enumerate(dates):
            provider.load_window(day, day)
            if day_index > 0:
                history_writer.reseed_for_new_day()
            close_dt = datetime.combine(day, close_t)
            for t in _iter_tick_minutes(day, open_t, close_t):
                results = run_scan_at(
                    t=t,
                    provider=provider,
                    sim=None,
                    settings=settings,
                    history_writer=history_writer,
                    live_writer=live_writer,
                )
                _emit_tick_records(
                    results=results,
                    provider=provider,
                    t=t,
                    close_dt=close_dt,
                    horizons_min=horizons_min,
                    cost_roundtrip_bps=cost_roundtrip_bps,
                    caps=caps,
                    per_horizon=per_horizon,
                )
    return per_horizon


def _emit_tick_records(
    *,
    results,
    provider,
    t: datetime,
    close_dt: datetime,
    horizons_min,
    cost_roundtrip_bps: float,
    caps,
    per_horizon: dict[int, list[RecordRow]],
) -> None:
    """For each gate1-pass candidate at ``t``, emit a per-horizon record."""
    ts_iso = t.isoformat()
    for result in results:
        if float(getattr(result, "passed_count", 0) or 0) < 1.0:
            continue
        scores = condition_scores_from_v1(result.score_components, caps)
        for horizon in horizons_min:
            label = _forward_label(
                provider,
                result.symbol,
                t,
                int(horizon),
                close_dt,
                cost_roundtrip_bps,
            )
            if label is None:
                continue
            forward_return_bps, truncated = label
            per_horizon[int(horizon)].append(
                RecordRow(
                    symbol=result.symbol,
                    ts=ts_iso,
                    scores=dict(scores),
                    forward_return_bps=forward_return_bps,
                    truncated=truncated,
                )
            )


# Column order for the parquet writer: symbol, ts, the 15 score columns,
# forward_return_bps, truncated (plan §7 R1 output contract).
PARQUET_COLUMNS: tuple[str, ...] = (
    "symbol",
    "ts",
    *CONDITION_SCORE_NAMES,
    "forward_return_bps",
    "truncated",
)


def _rows_to_records(rows) -> dict[str, list]:
    """Column-oriented dict for the parquet frame (score None -> NaN via pandas)."""
    columns: dict[str, list] = {col: [] for col in PARQUET_COLUMNS}
    for row in rows:
        columns["symbol"].append(row.symbol)
        columns["ts"].append(row.ts)
        for name in CONDITION_SCORE_NAMES:
            columns[name].append(row.scores.get(name))
        columns["forward_return_bps"].append(row.forward_return_bps)
        columns["truncated"].append(bool(row.truncated))
    return columns


def write_records_parquet(rows, path) -> None:
    """Write ``RecordRow``s to a parquet file with the R1 column contract.

    Columns (in order): ``symbol``, ``ts``, the 15 condition-score columns,
    ``forward_return_bps``, ``truncated``. A ``None`` score column round-trips as
    NaN. The parent directory is created if missing.
    """
    import pandas as pd

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(_rows_to_records(rows), columns=list(PARQUET_COLUMNS))
    frame.to_parquet(path, index=False)
