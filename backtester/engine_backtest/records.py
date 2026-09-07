"""Result dataclasses for backtester engine (R7-A1 extraction)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from backtester.engine_backtest.portfolio import ClosedTrade


@dataclass
class DayRecord:
    """What happened on a single trading day."""
    date: date
    portfolio_value: int
    cash: int
    buy_symbol: str | None = None
    buy_price: int | None = None
    buy_qty: int | None = None
    buy_reason: str | None = None
    sell_symbol: str | None = None
    sell_price: int | None = None
    sell_qty: int | None = None
    sell_trigger: str | None = None
    buy_signal_count: int = 0
    buy_scored_candidate_count: int = 0
    buy_candidate_symbols: list[str] = field(default_factory=list)
    buy_selected_score: float | None = None
    sell_evaluated_count: int = 0
    sell_triggered_count: int = 0
    sell_triggered_symbols: list[str] = field(default_factory=list)
    skipped_symbols: list[str] = field(default_factory=list)
    no_data_symbols: list[str] = field(default_factory=list)
    buy_rule_names: list[str] = field(default_factory=list)
    buy_rule_enabled_counts: dict[str, int] = field(default_factory=dict)
    buy_rule_pass_counts: dict[str, int] = field(default_factory=dict)
    buy_rejection_reason_counts: dict[str, int] = field(default_factory=dict)
    buy_funnel: dict[str, int] = field(default_factory=dict)
    buy_capacity: dict[str, object] = field(default_factory=dict)
    buy_score_stats: dict[str, object] = field(default_factory=dict)
    buy_sizing: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "date": self.date.isoformat(),
            "portfolio_value": self.portfolio_value,
            "cash": self.cash,
            "buy_symbol": self.buy_symbol,
            "buy_price": self.buy_price,
            "buy_qty": self.buy_qty,
            "buy_reason": self.buy_reason,
            "sell_symbol": self.sell_symbol,
            "sell_price": self.sell_price,
            "sell_qty": self.sell_qty,
            "sell_trigger": self.sell_trigger,
            "buy_signal_count": self.buy_signal_count,
            "buy_scored_candidate_count": self.buy_scored_candidate_count,
            "buy_candidate_symbols": list(self.buy_candidate_symbols),
            "buy_selected_score": self.buy_selected_score,
            "sell_evaluated_count": self.sell_evaluated_count,
            "sell_triggered_count": self.sell_triggered_count,
            "sell_triggered_symbols": list(self.sell_triggered_symbols),
            "skipped_count": len(self.skipped_symbols),
            "no_data_count": len(self.no_data_symbols),
            "buy_rule_names": list(self.buy_rule_names),
            "buy_rule_enabled_counts": dict(self.buy_rule_enabled_counts),
            "buy_rule_pass_counts": dict(self.buy_rule_pass_counts),
            "buy_rejection_reason_counts": dict(self.buy_rejection_reason_counts),
            "buy_funnel": dict(self.buy_funnel),
            "buy_capacity": dict(self.buy_capacity),
            "buy_score_stats": dict(self.buy_score_stats),
            "buy_sizing": dict(self.buy_sizing),
        }


@dataclass
class BacktestResult:
    initial_cash: int
    final_value: int
    trade_log: list[ClosedTrade]
    daily_records: list[DayRecord]
    equity_curve: list[tuple[date, int]]  # (date, portfolio_value)

    @property
    def total_return_pct(self) -> float:
        if self.initial_cash == 0:
            return 0.0
        return (self.final_value - self.initial_cash) / self.initial_cash * 100

    @property
    def n_trades(self) -> int:
        return len(self.trade_log)
