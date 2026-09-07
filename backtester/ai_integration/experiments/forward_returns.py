"""Forward-return lookup built from an already-loaded OHLCV provider.

The lookup maps ``date_iso → ticker → pct_return`` where ``pct_return``
is the **percent** return from close(d) to close(d + K trading days),
measured on the symbol's own trading calendar (skipping non-trading
days). K defaults to 5 trading days.

Used by:

* :class:`PerfectAIProvider` — fed as ``future_returns_cache`` for its
  look-ahead baseline signals.
* :class:`AIEvaluator` — ground-truth for veto precision / recall,
  NDCG@3, and KS score separation.

This module reads the already-loaded :class:`BacktestDataProvider`;
it does not touch the filesystem, re-fetch data, or modify the engine.
"""
from __future__ import annotations

from datetime import date
from typing import Iterable

from backtester.engine_backtest.data_provider import BacktestDataProvider


__all__ = ["build_forward_returns"]


def build_forward_returns(
    data_provider: BacktestDataProvider,
    *,
    horizon_trading_days: int = 5,
    symbols: Iterable[str] | None = None,
) -> dict[str, dict[str, float]]:
    """Return ``{date_iso: {ticker: pct_return_over_horizon}}``.

    * ``horizon_trading_days`` — positive integer; K in "T+K close".
    * ``symbols`` — restrict the universe; defaults to the provider's
      full symbol list.

    Days at the tail of each symbol's calendar that lack a T+K close
    are omitted (no padding, no look-ahead bias outside the horizon).
    """
    if horizon_trading_days <= 0:
        raise ValueError(
            f"horizon_trading_days must be >= 1, got {horizon_trading_days}"
        )

    universe = list(symbols) if symbols is not None else data_provider.symbols()
    out: dict[str, dict[str, float]] = {}

    for symbol in universe:
        dates = data_provider.trading_dates(symbol)
        if len(dates) <= horizon_trading_days:
            continue
        for i, d in enumerate(dates[:-horizon_trading_days]):
            base = data_provider.get_row(symbol, d)
            future = data_provider.get_row(
                symbol, dates[i + horizon_trading_days]
            )
            if base is None or future is None:
                continue
            if base.close_price <= 0:
                continue
            pct = (future.close_price - base.close_price) / base.close_price * 100.0
            out.setdefault(d.isoformat(), {})[symbol] = round(pct, 6)

    return out
