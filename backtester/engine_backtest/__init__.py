"""Full-engine backtester — calls real strategy/execution code, no KIS API.

Quick start
-----------
from backtester.engine_backtest import run_backtest, BacktestDataProvider, print_summary
from backtester.engine_backtest.settings_factory import make_settings

provider = BacktestDataProvider.from_csv("data/prices.csv")
result = run_backtest(provider, make_settings(), initial_cash=10_000_000)
print_summary(result)
"""
from backtester.engine_backtest.data_provider import BacktestDataProvider, DailyOHLCV
from backtester.engine_backtest.portfolio import BacktestPortfolio, ClosedTrade
from backtester.engine_backtest.runner import run_backtest, BacktestResult
from backtester.engine_backtest.metrics import compute_metrics
from backtester.engine_backtest.parity import build_parity_report
from backtester.engine_backtest.report import print_summary, write_report
from backtester.engine_backtest.settings_factory import make_settings

__all__ = [
    "BacktestDataProvider",
    "DailyOHLCV",
    "BacktestPortfolio",
    "ClosedTrade",
    "run_backtest",
    "BacktestResult",
    "compute_metrics",
    "build_parity_report",
    "print_summary",
    "write_report",
    "make_settings",
]
