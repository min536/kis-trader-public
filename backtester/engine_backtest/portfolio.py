"""Backtester in-memory portfolio: positions, cash, trade ledger."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.execution.schema import ExecutionSnapshot
from app.portfolio.schema import PortfolioPosition, PortfolioSnapshot


@dataclass
class BacktestPosition:
    symbol: str
    qty: int
    avg_cost: int
    buy_date: date
    high_water_mark: int  # highest close seen since entry (for trailing stop context)


@dataclass
class ClosedTrade:
    symbol: str
    buy_date: date
    sell_date: date
    buy_price: int
    sell_price: int
    qty: int
    gross_pnl_krw: int
    net_pnl_krw: int
    gross_pnl_pct: float
    net_pnl_pct: float
    hold_days: int
    sell_trigger: str  # "stop_loss" | "take_profit" | "trailing_stop" | "eod"

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "buy_date": self.buy_date.isoformat(),
            "sell_date": self.sell_date.isoformat(),
            "buy_price": self.buy_price,
            "sell_price": self.sell_price,
            "qty": self.qty,
            "gross_pnl_krw": self.gross_pnl_krw,
            "net_pnl_krw": self.net_pnl_krw,
            "gross_pnl_pct": self.gross_pnl_pct,
            "net_pnl_pct": self.net_pnl_pct,
            "hold_days": self.hold_days,
            "sell_trigger": self.sell_trigger,
        }


class BacktestPortfolio:
    """Manages cash, open positions, and closed trade ledger."""

    def __init__(self, initial_cash: int) -> None:
        self.cash: int = initial_cash
        self._positions: dict[str, BacktestPosition] = {}
        self.trade_log: list[ClosedTrade] = []

    # ── execution ─────────────────────────────────────────────────────────

    def execute_buy(
        self,
        symbol: str,
        qty: int,
        price: int,
        trading_date: date,
        settings,
    ) -> None:
        buy_notional = price * qty
        buy_fee = _bps(buy_notional, settings.buy_fee_bps)
        buy_slip = _bps(buy_notional, settings.buy_slippage_bps)
        total_cost = buy_notional + buy_fee + buy_slip

        if self.cash < total_cost:
            # clamp to available — should not happen if position sizing is correct
            return

        self.cash -= total_cost

        if symbol in self._positions:
            pos = self._positions[symbol]
            merged_qty = pos.qty + qty
            merged_avg = (pos.avg_cost * pos.qty + price * qty) // merged_qty
            self._positions[symbol] = BacktestPosition(
                symbol=symbol,
                qty=merged_qty,
                avg_cost=merged_avg,
                buy_date=pos.buy_date,
                high_water_mark=max(pos.high_water_mark, price),
            )
        else:
            self._positions[symbol] = BacktestPosition(
                symbol=symbol,
                qty=qty,
                avg_cost=price,
                buy_date=trading_date,
                high_water_mark=price,
            )

    def seed_position(
        self,
        *,
        symbol: str,
        qty: int,
        avg_cost: int,
        buy_date: date,
        high_water_mark: int | None = None,
    ) -> None:
        """Seed a historical position for offline replay and parity checks."""
        if qty <= 0 or avg_cost <= 0:
            return
        self._positions[symbol] = BacktestPosition(
            symbol=symbol,
            qty=qty,
            avg_cost=avg_cost,
            buy_date=buy_date,
            high_water_mark=high_water_mark or avg_cost,
        )

    def execute_sell(
        self,
        symbol: str,
        qty: int,
        price: int,
        trading_date: date,
        sell_trigger: str,
        settings,
    ) -> ClosedTrade | None:
        pos = self._positions.get(symbol)
        if pos is None or pos.qty == 0:
            return None

        sell_qty = min(qty, pos.qty)
        sell_notional = price * sell_qty
        sell_fee = _bps(sell_notional, settings.sell_fee_bps)
        sell_tax = _bps(sell_notional, settings.sell_tax_bps)
        sell_slip = _bps(sell_notional, settings.sell_slippage_bps)

        buy_notional = pos.avg_cost * sell_qty
        buy_fee_alloc = _bps(buy_notional, settings.buy_fee_bps)
        buy_slip_alloc = _bps(buy_notional, settings.buy_slippage_bps)

        gross_pnl_krw = sell_notional - buy_notional
        total_costs = sell_fee + sell_tax + sell_slip + buy_fee_alloc + buy_slip_alloc
        net_pnl_krw = gross_pnl_krw - total_costs

        gross_pnl_pct = gross_pnl_krw / buy_notional * 100 if buy_notional > 0 else 0.0
        net_pnl_pct = net_pnl_krw / buy_notional * 100 if buy_notional > 0 else 0.0

        net_proceeds = sell_notional - sell_fee - sell_tax - sell_slip
        self.cash += net_proceeds

        trade = ClosedTrade(
            symbol=symbol,
            buy_date=pos.buy_date,
            sell_date=trading_date,
            buy_price=pos.avg_cost,
            sell_price=price,
            qty=sell_qty,
            gross_pnl_krw=gross_pnl_krw,
            net_pnl_krw=net_pnl_krw,
            gross_pnl_pct=round(gross_pnl_pct, 2),
            net_pnl_pct=round(net_pnl_pct, 2),
            hold_days=(trading_date - pos.buy_date).days,
            sell_trigger=sell_trigger,
        )
        self.trade_log.append(trade)

        remaining = pos.qty - sell_qty
        if remaining <= 0:
            del self._positions[symbol]
        else:
            self._positions[symbol] = BacktestPosition(
                symbol=symbol,
                qty=remaining,
                avg_cost=pos.avg_cost,
                buy_date=pos.buy_date,
                high_water_mark=pos.high_water_mark,
            )

        return trade

    # ── state queries ──────────────────────────────────────────────────────

    def update_high_water_marks(self, prices: dict[str, int]) -> None:
        for symbol, pos in list(self._positions.items()):
            price = prices.get(symbol)
            if price and price > pos.high_water_mark:
                self._positions[symbol] = BacktestPosition(
                    symbol=symbol,
                    qty=pos.qty,
                    avg_cost=pos.avg_cost,
                    buy_date=pos.buy_date,
                    high_water_mark=price,
                )

    def total_value(self, prices: dict[str, int]) -> int:
        equity = sum(
            prices.get(s, pos.avg_cost) * pos.qty
            for s, pos in self._positions.items()
        )
        return self.cash + equity

    @property
    def positions(self) -> dict[str, BacktestPosition]:
        return dict(self._positions)

    # ── snapshot builders ──────────────────────────────────────────────────

    def to_portfolio_snapshot(self, prices: dict[str, int]) -> PortfolioSnapshot:
        """Build PortfolioSnapshot compatible with engine sell/buy functions."""
        plist: list[PortfolioPosition] = []
        for symbol, pos in self._positions.items():
            price = prices.get(symbol, pos.avg_cost)
            market_value = price * pos.qty
            gross_pnl_krw = (price - pos.avg_cost) * pos.qty
            gross_pnl_pct = (
                gross_pnl_krw / (pos.avg_cost * pos.qty) * 100
                if pos.avg_cost > 0
                else 0.0
            )
            plist.append(
                PortfolioPosition(
                    symbol=symbol,
                    name=None,
                    holding_qty=pos.qty,
                    average_cost=pos.avg_cost,
                    current_price=price,
                    market_value=market_value,
                    gross_pnl=gross_pnl_krw,
                    gross_pnl_pct=round(gross_pnl_pct, 2),
                    has_position=True,
                )
            )
        total_mkt = sum(p.market_value for p in plist)
        return PortfolioSnapshot(
            positions=tuple(sorted(plist, key=lambda p: p.symbol)),
            cash_total=self.cash,
            cash_orderable=self.cash,
            cash_next_day=self.cash,
            total_evaluation_amount=self.cash + total_mkt,
        )

    def to_execution_snapshot(
        self, symbol: str, price: int, qty: int
    ) -> ExecutionSnapshot:
        """Build ExecutionSnapshot for position sizing."""
        return ExecutionSnapshot(
            symbol=symbol,
            orderable_cash=self.cash,
            orderable_qty=self.cash // price if price > 0 else 0,
            current_price=price,
            expected_notional_krw=price * qty,
        )


# ── helpers ────────────────────────────────────────────────────────────────

def _bps(notional: int, rate_bps: float) -> int:
    return int(round(notional * rate_bps / 10_000))
