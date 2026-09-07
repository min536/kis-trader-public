"""Historical minute-bar data provider with no-future-leakage guarantees (R1).

Leaf research module: imports only stdlib. No imports from app.*.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable


@dataclass(frozen=True)
class MinuteBar:
    """A single OHLCV minute bar for one symbol."""

    symbol: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class MinuteObservation:
    """Point-in-time observable state at time ``ts`` for one symbol."""

    symbol: str
    ts: datetime
    bar_ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    day_open: float
    day_high: float
    day_low: float
    day_cum_volume: int


class HistoricalMinuteDataProvider:
    """Stores minute bars per symbol, sorted by timestamp."""

    def __init__(self, bars: Iterable[MinuteBar]) -> None:
        by_symbol: dict[str, list[MinuteBar]] = {}
        for bar in bars:
            by_symbol.setdefault(bar.symbol, []).append(bar)
        self._bars: dict[str, list[MinuteBar]] = {}
        self._times: dict[str, list[datetime]] = {}
        self._day_open: dict[str, dict[date, float]] = {}
        self._day_high_prefix: dict[str, list[float]] = {}
        self._day_low_prefix: dict[str, list[float]] = {}
        self._day_volume_prefix: dict[str, list[int]] = {}
        for symbol, symbol_bars in by_symbol.items():
            ordered = sorted(symbol_bars, key=lambda b: b.ts)
            seen: set[datetime] = set()
            day_open_by_date: dict[date, float] = {}
            high_prefix: list[float] = []
            low_prefix: list[float] = []
            volume_prefix: list[int] = []
            current_day: date | None = None
            running_high = float("-inf")
            running_low = float("inf")
            running_volume = 0
            for bar in ordered:
                if bar.ts in seen:
                    raise ValueError(
                        f"duplicate timestamp {bar.ts!r} for symbol {symbol!r}"
                    )
                seen.add(bar.ts)
                bar_day = bar.ts.date()
                if bar_day != current_day:
                    current_day = bar_day
                    day_open_by_date[bar_day] = bar.open
                    running_high = bar.high
                    running_low = bar.low
                    running_volume = bar.volume
                else:
                    if bar.high > running_high:
                        running_high = bar.high
                    if bar.low < running_low:
                        running_low = bar.low
                    running_volume += bar.volume
                high_prefix.append(running_high)
                low_prefix.append(running_low)
                volume_prefix.append(running_volume)
            self._bars[symbol] = ordered
            self._times[symbol] = [bar.ts for bar in ordered]
            self._day_open[symbol] = day_open_by_date
            self._day_high_prefix[symbol] = high_prefix
            self._day_low_prefix[symbol] = low_prefix
            self._day_volume_prefix[symbol] = volume_prefix

    def observable_snapshot(
        self, symbol: str, t: datetime
    ) -> MinuteObservation | None:
        """Return the observable snapshot for ``symbol`` at time ``t``.

        Uses only same-day bars with ``ts <= t``. Returns ``None`` if no such
        bar exists. Bars after ``t`` cannot affect the result.
        """
        ordered = self._bars.get(symbol)
        if not ordered:
            return None
        times = self._times[symbol]
        index = bisect_right(times, t) - 1
        if index < 0:
            return None
        day = t.date()
        latest = ordered[index]
        if latest.ts.date() != day:
            return None

        return MinuteObservation(
            symbol=symbol,
            ts=t,
            bar_ts=latest.ts,
            open=latest.open,
            high=latest.high,
            low=latest.low,
            close=latest.close,
            volume=latest.volume,
            day_open=self._day_open[symbol].get(day, latest.open),
            day_high=self._day_high_prefix[symbol][index],
            day_low=self._day_low_prefix[symbol][index],
            day_cum_volume=self._day_volume_prefix[symbol][index],
        )

    def universe_at(self, t: datetime) -> tuple[str, ...]:
        """Return sorted symbols that have a same-day bar with ``ts <= t``."""
        day = t.date()
        result: list[str] = []
        for symbol, ordered in self._bars.items():
            times = self._times[symbol]
            index = bisect_right(times, t) - 1
            if index >= 0 and ordered[index].ts.date() == day:
                result.append(symbol)
        return tuple(sorted(result))

    def volume_rank_at(self, t: datetime, top_n: int) -> tuple[str, ...]:
        """Return up to ``top_n`` symbols by same-day cumulative volume at ``t``.

        Descending cumulative volume; ties broken by ascending symbol. Live
        volume-rank proxy using only data observable at time ``t``.
        """
        scored: list[tuple[int, str]] = []
        for symbol in self.universe_at(t):
            obs = self.observable_snapshot(symbol, t)
            if obs is None:
                continue
            scored.append((obs.day_cum_volume, symbol))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return tuple(symbol for _, symbol in scored[:top_n])

    def bar_open_at_or_after(
        self,
        symbol: str,
        target: datetime,
        close_dt: datetime,
    ) -> float | None:
        """Open of the first same-day bar with ``ts >= target``."""
        ordered = self._bars.get(symbol)
        if not ordered:
            return None
        times = self._times[symbol]
        index = bisect_left(times, target)
        if index >= len(ordered):
            return None
        bar = ordered[index]
        if bar.ts.date() != target.date() or bar.ts > close_dt:
            return None
        return float(bar.open)

    def last_close_on_day(self, symbol: str, close_dt: datetime) -> float | None:
        """Close of the last same-day bar at/before ``close_dt``."""
        ordered = self._bars.get(symbol)
        if not ordered:
            return None
        times = self._times[symbol]
        index = bisect_right(times, close_dt) - 1
        if index < 0:
            return None
        bar = ordered[index]
        if bar.ts.date() != close_dt.date():
            return None
        return float(bar.close)
