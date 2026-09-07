from dataclasses import dataclass

from app.market_data.schema import MarketSnapshot
from app.portfolio.schema import PortfolioPosition, PortfolioSnapshot


@dataclass(frozen=True)
class SellTestScenario:
    mode: str
    symbol: str
    holding_qty: int
    average_cost: int
    market_snapshot: MarketSnapshot
    description: str

    @property
    def portfolio_snapshot(self) -> PortfolioSnapshot:
        return PortfolioSnapshot(
            positions=(
                PortfolioPosition(
                    symbol=self.symbol,
                    name=None,
                    holding_qty=self.holding_qty,
                    average_cost=self.average_cost,
                    current_price=self.market_snapshot.current_price,
                    market_value=self.market_snapshot.current_price * self.holding_qty,
                    gross_pnl=(
                        (self.market_snapshot.current_price - self.average_cost)
                        * self.holding_qty
                    ),
                    gross_pnl_pct=(
                        ((self.market_snapshot.current_price / self.average_cost) - 1.0) * 100
                        if self.average_cost > 0
                        else 0.0
                    ),
                    has_position=self.holding_qty > 0,
                ),
            ),
            cash_total=10_000_000,
            cash_orderable=10_000_000,
            cash_next_day=10_000_000,
            total_evaluation_amount=self.market_snapshot.current_price * self.holding_qty,
        )


def build_sell_test_scenario(mode: str) -> SellTestScenario | None:
    normalized_mode = mode.strip().lower()
    if normalized_mode == "off":
        return None

    if normalized_mode == "take_profit":
        return SellTestScenario(
            mode=normalized_mode,
            symbol="005930",
            holding_qty=10,
            average_cost=100_000,
            market_snapshot=MarketSnapshot(
                symbol="005930",
                current_price=104_000,
                open_price=103_000,
                low_price=101_500,
                prev_day_change_pct=1.20,
            ),
            description="평균단가 대비 수익 구간이라 take_profit 검증용 시나리오입니다.",
        )

    if normalized_mode == "stop_loss":
        return SellTestScenario(
            mode=normalized_mode,
            symbol="005930",
            holding_qty=10,
            average_cost=100_000,
            market_snapshot=MarketSnapshot(
                symbol="005930",
                current_price=96_000,
                open_price=98_000,
                low_price=95_500,
                prev_day_change_pct=-2.80,
            ),
            description="평균단가 대비 손실 구간이라 stop_loss 검증용 시나리오입니다.",
        )

    if normalized_mode == "hold":
        return SellTestScenario(
            mode=normalized_mode,
            symbol="005930",
            holding_qty=10,
            average_cost=100_000,
            market_snapshot=MarketSnapshot(
                symbol="005930",
                current_price=101_000,
                open_price=100_500,
                low_price=99_800,
                prev_day_change_pct=0.40,
            ),
            description="손절/익절/트레일링 조건을 모두 피하는 hold 검증용 시나리오입니다.",
        )

    raise ValueError(f"지원하지 않는 SELL_TEST_MODE 입니다: {mode}")
