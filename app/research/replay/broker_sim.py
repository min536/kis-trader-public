"""Broker fill simulator with an explicit bps cost model (R2).

Leaf research module: imports only stdlib. No imports from app.*.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping


@dataclass(frozen=True)
class SimCostParams:
    """Explicit cost parameters in basis points (no defaults — inject)."""

    buy_fee_bps: float
    sell_fee_bps: float
    sell_tax_bps: float
    buy_slippage_bps: float
    sell_slippage_bps: float


@dataclass(frozen=True)
class SimFill:
    """A single simulated fill."""

    symbol: str
    side: str
    qty: int
    ref_price: float
    fill_price: float
    fee: float
    tax: float
    ts: datetime
    cash_after: float


@dataclass
class SimPosition:
    """A held position: quantity and average entry price."""

    qty: int
    avg_price: float


class BrokerSimulator:
    """Simulates buys/sells against a cash balance with a bps cost model."""

    def __init__(self, initial_cash: float, costs: SimCostParams) -> None:
        self.cash: float = initial_cash
        self._costs = costs
        self.positions: dict[str, SimPosition] = {}
        self.realized_pnl: float = 0.0
        self.fills: list[SimFill] = []
        self.equity_curve: list[tuple[datetime, float]] = []

    def buy(
        self, symbol: str, qty: int, ref_price: float, ts: datetime
    ) -> "SimFill | None":
        """Simulate a buy. Returns ``None`` if cash is insufficient.

        ``fill_price = ref_price * (1 + buy_slippage_bps / 1e4)``; the total
        cost is ``fill_price * qty + fee`` where the fee is
        ``fill_price * qty * buy_fee_bps / 1e4``. A buy that would drive cash
        negative is hard-rejected (no negative cash).
        """
        fill_price = ref_price * (1 + self._costs.buy_slippage_bps / 1e4)
        gross = fill_price * qty
        fee = gross * self._costs.buy_fee_bps / 1e4
        total = gross + fee
        if total > self.cash:
            return None
        self.cash -= total
        existing = self.positions.get(symbol)
        if existing is None:
            self.positions[symbol] = SimPosition(qty=qty, avg_price=fill_price)
        else:
            new_qty = existing.qty + qty
            existing.avg_price = (
                existing.avg_price * existing.qty + fill_price * qty
            ) / new_qty
            existing.qty = new_qty
        fill = SimFill(
            symbol=symbol,
            side="BUY",
            qty=qty,
            ref_price=ref_price,
            fill_price=fill_price,
            fee=fee,
            tax=0.0,
            ts=ts,
            cash_after=self.cash,
        )
        self.fills.append(fill)
        return fill

    def equity(self, mark_prices: "Mapping[str, float]") -> float:
        """Total equity: cash plus marked-to-market position value.

        Positions without a mark price contribute zero.
        """
        total = self.cash
        for symbol, position in self.positions.items():
            mark = mark_prices.get(symbol, 0.0)
            total += position.qty * mark
        return total

    def record_equity(
        self, ts: datetime, mark_prices: "Mapping[str, float]"
    ) -> None:
        """Append ``(ts, equity)`` to the equity curve."""
        self.equity_curve.append((ts, self.equity(mark_prices)))

    def sell(
        self, symbol: str, qty: int, ref_price: float, ts: datetime
    ) -> "SimFill | None":
        """Simulate a sell. Returns ``None`` if holdings are insufficient.

        ``fill_price = ref_price * (1 - sell_slippage_bps / 1e4)``; proceeds are
        ``fill_price * qty - fee - tax`` where ``fee`` and ``tax`` use
        ``sell_fee_bps`` and ``sell_tax_bps``. Realized PnL is credited as
        ``proceeds - cost_basis`` where ``cost_basis = avg_price * qty``.
        """
        position = self.positions.get(symbol)
        if position is None or position.qty < qty:
            return None
        fill_price = ref_price * (1 - self._costs.sell_slippage_bps / 1e4)
        gross = fill_price * qty
        fee = gross * self._costs.sell_fee_bps / 1e4
        tax = gross * self._costs.sell_tax_bps / 1e4
        proceeds = gross - fee - tax
        cost_basis = position.avg_price * qty
        self.cash += proceeds
        self.realized_pnl += proceeds - cost_basis
        remaining = position.qty - qty
        if remaining == 0:
            del self.positions[symbol]
        else:
            position.qty = remaining
        fill = SimFill(
            symbol=symbol,
            side="SELL",
            qty=qty,
            ref_price=ref_price,
            fill_price=fill_price,
            fee=fee,
            tax=tax,
            ts=ts,
            cash_after=self.cash,
        )
        self.fills.append(fill)
        return fill
