from dataclasses import dataclass
from typing import Any, Mapping

from app.scanner.symbol_names import get_symbol_name


@dataclass(frozen=True)
class PortfolioPosition:
    symbol: str
    name: str | None
    holding_qty: int
    average_cost: int
    current_price: int
    market_value: int
    gross_pnl: int
    gross_pnl_pct: float
    has_position: bool


@dataclass(frozen=True)
class PortfolioSnapshot:
    positions: tuple[PortfolioPosition, ...]
    cash_total: int
    cash_orderable: int
    cash_next_day: int
    total_evaluation_amount: int

    @property
    def cash_available(self) -> int:
        if self.cash_orderable > 0:
            return self.cash_orderable
        return self.cash_total

    @property
    def position_count(self) -> int:
        return len(self.positions)

    @property
    def held_positions(self) -> tuple[PortfolioPosition, ...]:
        return tuple(position for position in self.positions if position.has_position)

    def get_position(self, symbol: str) -> PortfolioPosition | None:
        normalized_symbol = str(symbol).strip()
        for position in self.positions:
            if position.symbol == normalized_symbol:
                return position
        return None

    def get_holding_qty(self, symbol: str) -> int:
        position = self.get_position(symbol)
        if position is None:
            return 0
        return position.holding_qty

    def has_position_for(self, symbol: str) -> bool:
        position = self.get_position(symbol)
        return position is not None and position.has_position


def _normalize_symbol(value: Any) -> str:
    return str(value or "").strip()


def _parse_int(value: Any, default: int = 0) -> int:
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        try:
            return int(float(text))
        except ValueError:
            return default


def _parse_float(value: Any, default: float = 0.0) -> float:
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _build_position(item: Mapping[str, Any]) -> PortfolioPosition | None:
    symbol = _normalize_symbol(item.get("pdno") or item.get("stck_shrn_iscd"))
    if not symbol:
        return None

    name = (
        _normalize_symbol(
            item.get("prdt_name")
            or item.get("hldg_name")
            or item.get("item_name")
            or item.get("hts_kor_isnm")
        )
        or get_symbol_name(symbol)
    )
    holding_qty = _parse_int(item.get("hldg_qty") or item.get("hold_qty"))
    average_cost = _parse_int(
        item.get("pchs_avg_pric")
        or item.get("pchs_avg_price")
        or item.get("avg_prvs")
        or item.get("pchs_unpr")
    )
    current_price = _parse_int(
        item.get("prpr")
        or item.get("stck_prpr")
    )
    market_value = _parse_int(
        item.get("evlu_amt")
        or item.get("evlu_amt_smtl")
        or item.get("evlu_amt1")
        or item.get("evlu_amt2")
    )
    gross_pnl = _parse_int(
        item.get("evlu_pfls_amt")
        or item.get("evlu_pfls_smtl_amt")
        or item.get("evlu_erng_amt")
    )
    gross_pnl_pct = _parse_float(
        item.get("evlu_pfls_rt")
        or item.get("evlu_erng_rt")
    )
    return PortfolioPosition(
        symbol=symbol,
        name=name or None,
        holding_qty=holding_qty,
        average_cost=average_cost,
        current_price=current_price,
        market_value=market_value,
        gross_pnl=gross_pnl,
        gross_pnl_pct=gross_pnl_pct,
        has_position=holding_qty > 0,
    )


def build_portfolio_snapshot(balance_data: Mapping[str, Any]) -> PortfolioSnapshot:
    raw_positions = balance_data.get("output1", [])
    raw_summary = balance_data.get("output2", [])

    position_map: dict[str, PortfolioPosition] = {}
    if isinstance(raw_positions, list):
        for item in raw_positions:
            if not isinstance(item, Mapping):
                continue
            position = _build_position(item)
            if position is None:
                continue

            existing = position_map.get(position.symbol)
            if existing is None:
                position_map[position.symbol] = position
                continue

            merged_qty = existing.holding_qty + position.holding_qty
            merged_average_cost = 0
            if merged_qty > 0:
                merged_average_cost = int(
                    (
                        existing.holding_qty * existing.average_cost
                        + position.holding_qty * position.average_cost
                    )
                    / merged_qty
                )
            position_map[position.symbol] = PortfolioPosition(
                symbol=position.symbol,
                name=existing.name or position.name,
                holding_qty=merged_qty,
                average_cost=merged_average_cost,
                current_price=position.current_price or existing.current_price,
                market_value=existing.market_value + position.market_value,
                gross_pnl=existing.gross_pnl + position.gross_pnl,
                gross_pnl_pct=position.gross_pnl_pct or existing.gross_pnl_pct,
                has_position=merged_qty > 0,
            )

    summary = raw_summary[0] if isinstance(raw_summary, list) and raw_summary else {}
    if not isinstance(summary, Mapping):
        summary = {}

    return PortfolioSnapshot(
        positions=tuple(
            sorted(position_map.values(), key=lambda position: position.symbol)
        ),
        cash_total=_parse_int(summary.get("dnca_tot_amt")),
        cash_orderable=_parse_int(
            summary.get("prvs_rcdl_excc_amt") or summary.get("dnca_tot_amt")
        ),
        cash_next_day=_parse_int(
            summary.get("nxdy_excc_amt") or summary.get("dnca_tot_amt")
        ),
        total_evaluation_amount=_parse_int(summary.get("tot_evlu_amt")),
    )
