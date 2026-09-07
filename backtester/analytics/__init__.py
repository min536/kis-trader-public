"""Offline analytics for backtest reports."""

from backtester.analytics.robust_risk import (
    WinsorizedReturns,
    build_risk_diagnostics,
    drawdown_path,
    rolling_annualized_volatility,
    winsorize_returns,
)

__all__ = [
    "WinsorizedReturns",
    "build_risk_diagnostics",
    "drawdown_path",
    "rolling_annualized_volatility",
    "winsorize_returns",
]

