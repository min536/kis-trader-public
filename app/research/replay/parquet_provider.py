"""Parquet-backed minute provider with look-ahead-safe daily rollups (D2).

Leaf research module under ``app/research/replay/``. Wraps the existing
``HistoricalMinuteDataProvider`` (see ``minute_provider.py``) for the observable
minute-bar surface and never reimplements its point-in-time logic.

Two asymmetric data residencies:

- **Minute bars are windowed.** ``load_window`` materialises only the date
  partitions in ``[start_date, end_date]`` (at most ~5 trading days resident,
  a memory budget), applies the intraday session filter, and builds an internal
  ``HistoricalMinuteDataProvider``. The observable methods
  (``observable_snapshot`` / ``universe_at`` / ``volume_rank_at``) delegate to
  it, and accessing a timestamp outside the loaded window raises ``ValueError``.
- **Daily rollups span the full period.** ``prev_close`` / ``daily_history`` are
  look-back scoring inputs that must resolve across the whole cache regardless
  of which minute window is loaded, so daily rollups are precomputed **once in
  ``__init__``** by streaming every date partition one date at a time (never
  holding more than a single date's minute frame — mirroring the memory
  discipline in ``app.research.coverage.quality.build_quality_report``) and
  reducing each symbol/day to daily scalars immediately. Because rollups are
  computed independently of ``load_window``, the ValueError window rule applies
  only to the minute-bar access methods, not to ``prev_close`` /
  ``daily_history``.

pandas/pyarrow are imported inside methods (leaf-module discipline). No imports
from ``app.*`` runtime modules; the only ``app`` import is the sibling research
provider.
"""

from __future__ import annotations

from datetime import date, datetime, time

from app.research.replay.minute_provider import (
    HistoricalMinuteDataProvider,
    MinuteBar,
)


def _hhmm_to_time(hhmm: str) -> time:
    h, m = (int(x) for x in hhmm.split(":")[:2])
    return time(h, m)


class ParquetMinuteProvider:
    """Windowed minute provider over a ``date=YYYY-MM-DD/part.parquet`` cache."""

    def __init__(
        self,
        parquet_root,
        *,
        symbols: tuple[str, ...] | None = None,
        session_open: str = "09:00",
        session_close: str = "15:30",
        min_date: date | None = None,
        max_date: date | None = None,
    ) -> None:
        from pathlib import Path

        self._root = Path(parquet_root)
        self._symbols = tuple(symbols) if symbols is not None else None
        self._session_open = _hhmm_to_time(session_open)
        self._session_close = _hhmm_to_time(session_close)
        self._min_date = min_date
        self._max_date = max_date
        self._partitions = self._discover_date_partitions()

        self._window_start: date | None = None
        self._window_end: date | None = None
        self._inner: HistoricalMinuteDataProvider | None = None

        # Full-period daily rollups (precomputed once, streaming per date).
        # symbol -> ordered list of {date, open, high, low, close, volume}.
        self._daily: dict[str, list[dict]] = {}
        self._precompute_daily_rollups()

    # -- rollup precompute (full period, streaming) -----------------------

    def _discover_date_partitions(self):
        """Return ``(date_obj, part_path)`` pairs for date partitions, ascending."""
        from pathlib import Path

        partitions = []
        for date_dir in sorted(
            p for p in self._root.glob("date=*") if p.is_dir()
        ):
            date_str = date_dir.name[len("date=") :]
            part = date_dir / "part.parquet"
            if not part.exists():
                continue
            try:
                day = date.fromisoformat(date_str)
            except ValueError:
                continue
            if self._min_date is not None and day < self._min_date:
                continue
            if self._max_date is not None and day > self._max_date:
                continue
            partitions.append((day, Path(part)))
        return tuple(partitions)

    def _iter_date_partitions(self):
        """Yield ``(date_obj, part_path)`` for each date partition, ascending."""
        yield from self._partitions

    def _precompute_daily_rollups(self) -> None:
        import pandas as pd

        wanted = set(self._symbols) if self._symbols is not None else None
        for day, part in self._iter_date_partitions():
            frame = pd.read_parquet(part)
            if "symbol" not in frame.columns:
                continue
            if wanted is not None:
                frame = frame[frame["symbol"].isin(wanted)]
            if frame.empty:
                continue
            frame = frame.copy()
            frame["_dt"] = pd.to_datetime(frame["datetime"])
            # Session filter so the rollup ignores pre-market rows.
            times = frame["_dt"].dt.time
            frame = frame[
                (times >= self._session_open) & (times <= self._session_close)
            ]
            if frame.empty:
                continue
            for symbol, sframe in frame.groupby("symbol"):
                sframe = sframe.sort_values("_dt", kind="stable")
                rollup = {
                    "date": day.isoformat(),
                    "open": float(sframe["open"].iloc[0]),
                    "high": float(sframe["high"].max()),
                    "low": float(sframe["low"].min()),
                    "close": float(sframe["close"].iloc[-1]),
                    "volume": int(sframe["volume"].sum()),
                }
                self._daily.setdefault(str(symbol), []).append(rollup)
            # frame/sframe rebind next iteration -> this date is released.

    # -- windowed minute-bar surface --------------------------------------

    def load_window(self, start_date: date, end_date: date) -> None:
        """Load only the date partitions in ``[start_date, end_date]``.

        Applies the intraday session filter, then builds an internal
        ``HistoricalMinuteDataProvider`` over the surviving minute bars. At most
        ~5 trading days should be requested at a time (memory budget). After
        this call, minute-bar access outside ``[start_date, end_date]`` raises
        ``ValueError``.
        """
        import pandas as pd

        wanted = set(self._symbols) if self._symbols is not None else None
        bars: list[MinuteBar] = []
        for day, part in self._iter_date_partitions():
            if day < start_date or day > end_date:
                continue
            frame = pd.read_parquet(part)
            if "symbol" not in frame.columns:
                continue
            if wanted is not None:
                frame = frame[frame["symbol"].isin(wanted)]
            if frame.empty:
                continue
            frame = frame.copy()
            frame["dt"] = pd.to_datetime(frame["datetime"])
            times = frame["dt"].dt.time
            frame = frame[
                (times >= self._session_open) & (times <= self._session_close)
            ]
            for row in frame.itertuples(index=False):
                bars.append(
                    MinuteBar(
                        symbol=str(row.symbol),
                        ts=row.dt.to_pydatetime(),
                        open=float(row.open),
                        high=float(row.high),
                        low=float(row.low),
                        close=float(row.close),
                        volume=int(row.volume),
                    )
                )
            # frame rebinds next iteration -> this date is released.

        self._inner = HistoricalMinuteDataProvider(bars)
        self._window_start = start_date
        self._window_end = end_date

    def _require_window(self, t: datetime) -> HistoricalMinuteDataProvider:
        if self._inner is None:
            raise ValueError("load_window must be called before minute access")
        day = t.date()
        if (
            self._window_start is None
            or self._window_end is None
            or day < self._window_start
            or day > self._window_end
        ):
            raise ValueError(
                f"timestamp {t!r} is outside the loaded window "
                f"[{self._window_start}, {self._window_end}]"
            )
        return self._inner

    def observable_snapshot(self, symbol: str, t: datetime):
        """Delegate to the internal provider (window-guarded)."""
        return self._require_window(t).observable_snapshot(symbol, t)

    def universe_at(self, t: datetime) -> tuple[str, ...]:
        """Delegate to the internal provider (window-guarded)."""
        return self._require_window(t).universe_at(t)

    def volume_rank_at(self, t: datetime, top_n: int) -> tuple[str, ...]:
        """Delegate to the internal provider (window-guarded)."""
        return self._require_window(t).volume_rank_at(t, top_n)

    def bar_open_at_or_after(
        self,
        symbol: str,
        target: datetime,
        close_dt: datetime,
    ) -> float | None:
        """Delegate to the internal provider (window-guarded)."""
        return self._require_window(target).bar_open_at_or_after(
            symbol,
            target,
            close_dt,
        )

    def last_close_on_day(self, symbol: str, close_dt: datetime) -> float | None:
        """Delegate to the internal provider (window-guarded)."""
        return self._require_window(close_dt).last_close_on_day(symbol, close_dt)

    # -- daily look-back queries (full period) ----------------------------

    def prev_close(self, symbol: str, day: date) -> float | None:
        """Close of the last trading day strictly BEFORE ``day`` (or None)."""
        target = day.isoformat()
        rows = self._daily.get(symbol)
        if not rows:
            return None
        prev: float | None = None
        for row in rows:
            if row["date"] >= target:
                break
            prev = row["close"]
        return prev

    def daily_history(self, symbol: str, day: date, n: int) -> list[dict]:
        """Daily rollups for the ``n`` trading days strictly BEFORE ``day``.

        Returns up to ``n`` ``{date, open, high, low, close, volume}`` dicts in
        ascending date order. The query ``day`` itself is never included
        (look-ahead blocking); the earliest day yields ``[]``.
        """
        target = day.isoformat()
        rows = self._daily.get(symbol)
        if not rows:
            return []
        prior = [dict(row) for row in rows if row["date"] < target]
        if n <= 0:
            return []
        return prior[-n:]
