"""Offline risk diagnostics for backtest equity curves.

The contracts were reviewed against Goldman Sachs' Apache-2.0 GS Quant
implementation at commit ``ccbd4ae780f51be4e01ecbf834c7b93583fec57f``:

* ``gs_quant.timeseries.econometrics.max_drawdown``
* ``gs_quant.timeseries.econometrics.volatility``
* ``gs_quant.timeseries.statistics.winsorize``

This module is an independent implementation for kis-trader's existing equity
curve format.  It does not import GS Quant, call Marquee, or alter trading
decisions.  The adoption record is
``docs/gs_quant_adoption_roadmap_20260902.md``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Sequence


EquityPoint = tuple[date, int | float]
OptionalSeriesPoint = tuple[date, float | None]


@dataclass(frozen=True)
class WinsorizedReturns:
    """A non-mutating winsorization result for decimal return values."""

    values: tuple[float | None, ...]
    lower_bound: float | None
    upper_bound: float | None
    clipped_count: int


def _finite_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _daily_return_path(equity_curve: Sequence[EquityPoint]) -> list[OptionalSeriesPoint]:
    if len(equity_curve) < 2:
        return []

    path: list[OptionalSeriesPoint] = []
    previous_value = _finite_float(equity_curve[0][1])
    for day, raw_value in equity_curve[1:]:
        value = _finite_float(raw_value)
        if previous_value is None or previous_value <= 0 or value is None:
            daily_return = None
        else:
            daily_return = value / previous_value - 1.0
        path.append((day, daily_return))
        previous_value = value
    return path


def drawdown_path(equity_curve: Iterable[EquityPoint]) -> list[OptionalSeriesPoint]:
    """Return point-in-time drawdown percentages, using negative loss values.

    GS Quant's ``max_drawdown`` returns the worst drawdown observed within its
    window.  This companion path intentionally returns the current underwater
    value at every point; its minimum is comparable to the existing backtest
    ``max_drawdown_pct`` after changing sign.
    """
    peak: float | None = None
    path: list[OptionalSeriesPoint] = []

    for day, raw_value in equity_curve:
        value = _finite_float(raw_value)
        if value is None or value < 0:
            path.append((day, None))
            continue

        if peak is None:
            if value == 0:
                path.append((day, None))
                continue
            peak = value
            path.append((day, 0.0))
            continue

        if value > peak:
            peak = value
        path.append((day, (value / peak - 1.0) * 100.0))

    return path


def rolling_annualized_volatility(
    equity_curve: Iterable[EquityPoint],
    *,
    window: int = 20,
    periods_per_year: int = 252,
) -> list[OptionalSeriesPoint]:
    """Return rolling sample volatility of simple returns as annualized percent."""
    if window < 2:
        raise ValueError("window must be at least 2")
    if periods_per_year <= 0:
        raise ValueError("periods_per_year must be positive")

    returns = _daily_return_path(list(equity_curve))
    result: list[OptionalSeriesPoint] = []
    annualization = math.sqrt(periods_per_year) * 100.0

    for index, (day, _) in enumerate(returns):
        if index + 1 < window:
            result.append((day, None))
            continue
        sample = [value for _, value in returns[index + 1 - window : index + 1]]
        if any(value is None for value in sample):
            result.append((day, None))
            continue
        finite_sample = [float(value) for value in sample if value is not None]
        mean = sum(finite_sample) / len(finite_sample)
        variance = sum((value - mean) ** 2 for value in finite_sample) / (len(finite_sample) - 1)
        result.append((day, math.sqrt(variance) * annualization))

    return result


def winsorize_returns(
    returns: Sequence[float | None],
    *,
    z_limit: float = 2.5,
) -> WinsorizedReturns:
    """Clip finite returns to sample mean plus/minus ``z_limit`` sample std."""
    if not math.isfinite(z_limit) or z_limit <= 0:
        raise ValueError("z_limit must be positive")

    normalized = tuple(_finite_float(value) for value in returns)
    finite_values = [value for value in normalized if value is not None]
    if not finite_values:
        return WinsorizedReturns(normalized, None, None, 0)

    mean = sum(finite_values) / len(finite_values)
    if len(finite_values) == 1:
        sample_std = 0.0
    else:
        sample_std = math.sqrt(
            sum((value - mean) ** 2 for value in finite_values) / (len(finite_values) - 1)
        )
    lower_bound = mean - z_limit * sample_std
    upper_bound = mean + z_limit * sample_std

    clipped_count = 0
    clipped_values: list[float | None] = []
    for value in normalized:
        if value is None:
            clipped_values.append(None)
            continue
        clipped = min(max(value, lower_bound), upper_bound)
        if clipped != value:
            clipped_count += 1
        clipped_values.append(clipped)

    return WinsorizedReturns(
        values=tuple(clipped_values),
        lower_bound=lower_bound,
        upper_bound=upper_bound,
        clipped_count=clipped_count,
    )


def _rounded(value: float | None, digits: int = 8) -> float | None:
    return None if value is None else round(value, digits)


def _last_value(path: Sequence[OptionalSeriesPoint]) -> float | None:
    return path[-1][1] if path else None


def build_risk_diagnostics(
    equity_curve: Iterable[EquityPoint],
    *,
    volatility_window: int = 20,
    volatility_periods_per_year: int = 252,
    winsor_z_limit: float = 2.5,
) -> dict[str, object]:
    """Build a JSON-safe report payload without changing the source curve."""
    curve = list(equity_curve)
    drawdowns = drawdown_path(curve)
    daily_returns = _daily_return_path(curve)
    rolling_volatility = rolling_annualized_volatility(
        curve,
        window=volatility_window,
        periods_per_year=volatility_periods_per_year,
    )
    winsorized = winsorize_returns(
        [value for _, value in daily_returns],
        z_limit=winsor_z_limit,
    )

    return {
        "reference": {
            "project": "goldmansachs/gs-quant",
            "commit": "ccbd4ae780f51be4e01ecbf834c7b93583fec57f",
            "mode": "independent_offline_implementation",
        },
        "current_drawdown_pct": _rounded(_last_value(drawdowns)),
        "drawdown_path": [
            {"date": day.isoformat(), "drawdown_pct": _rounded(value)}
            for day, value in drawdowns
        ],
        "rolling_volatility": {
            "window": volatility_window,
            "periods_per_year": volatility_periods_per_year,
            "latest_annualized_pct": _rounded(_last_value(rolling_volatility)),
            "path": [
                {"date": day.isoformat(), "annualized_pct": _rounded(value)}
                for day, value in rolling_volatility
            ],
        },
        "winsorized_returns": {
            "z_limit": winsor_z_limit,
            "lower_bound_pct": _rounded(
                None if winsorized.lower_bound is None else winsorized.lower_bound * 100.0
            ),
            "upper_bound_pct": _rounded(
                None if winsorized.upper_bound is None else winsorized.upper_bound * 100.0
            ),
            "clipped_count": winsorized.clipped_count,
            "path": [
                {
                    "date": day.isoformat(),
                    "raw_pct": _rounded(None if raw is None else raw * 100.0),
                    "winsorized_pct": _rounded(
                        None if clipped is None else clipped * 100.0
                    ),
                    "clipped": raw is not None and clipped is not None and raw != clipped,
                }
                for (day, raw), clipped in zip(daily_returns, winsorized.values)
            ],
        },
    }
