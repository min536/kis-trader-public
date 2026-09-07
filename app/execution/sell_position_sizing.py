from dataclasses import dataclass

from app.core.costs import calc_round_trip_cost_estimate


@dataclass(frozen=True)
class SellPositionSizingResult:
    recommended_sell_qty: int
    sell_reason: str
    sell_trigger: str | None
    sell_fraction: float
    available_holding_qty: int
    recommended_notional_krw: int
    details: dict[str, object]


def calculate_sell_position_sizing(
    *,
    trigger: str | None,
    holding_qty: int,
    current_price: int,
    settings,
) -> SellPositionSizingResult:
    available_holding_qty = max(int(holding_qty), 0)
    price = max(int(current_price), 0)

    if available_holding_qty <= 0:
        return SellPositionSizingResult(
            recommended_sell_qty=0,
            sell_reason="보유 수량이 없어 매도 추천 수량을 계산하지 않습니다.",
            sell_trigger=trigger,
            sell_fraction=0.0,
            available_holding_qty=0,
            recommended_notional_krw=0,
            details={
                "trigger": trigger,
                "available_holding_qty": 0,
                "sell_fraction": 0.0,
                "recommended_sell_qty": 0,
                "recommended_notional_krw": 0,
                "estimated_sell_fee_krw": 0,
                "estimated_sell_tax_krw": 0,
                "estimated_sell_slippage_krw": 0,
                "estimated_net_proceeds_krw": 0,
            },
        )

    if trigger == "stop_loss":
        sell_fraction = 1.0
        recommended_sell_qty = available_holding_qty
        sell_reason = "stop_loss 신호라 전량 매도를 추천합니다."
    elif trigger == "take_profit":
        recommended_sell_qty = max(1, available_holding_qty // 2)
        sell_fraction = recommended_sell_qty / available_holding_qty
        sell_reason = (
            "take_profit 신호라 절반 매도를 추천합니다."
            if recommended_sell_qty < available_holding_qty
            else "take_profit 신호이나 보유 수량이 적어 전량 매도를 추천합니다."
        )
    elif trigger == "trailing_stop":
        recommended_sell_qty = max(1, available_holding_qty // 2)
        sell_fraction = recommended_sell_qty / available_holding_qty
        sell_reason = (
            "trailing_stop 신호라 보수적으로 절반 매도를 추천합니다."
            if recommended_sell_qty < available_holding_qty
            else "trailing_stop 신호이나 보유 수량이 적어 전량 매도를 추천합니다."
        )
    elif trigger in ("live_leadership_loss", "live_power_breakdown"):
        recommended_sell_qty = max(1, available_holding_qty // 2)
        sell_fraction = recommended_sell_qty / available_holding_qty
        sell_reason = (
            f"{trigger} 신호라 보수적으로 절반 매도를 추천합니다."
            if recommended_sell_qty < available_holding_qty
            else f"{trigger} 신호이나 보유 수량이 적어 전량 매도를 추천합니다."
        )
    elif trigger == "rebalance":
        sell_fraction = 1.0
        recommended_sell_qty = available_holding_qty
        sell_reason = "리밸런싱 매도라 전량 매도를 추천합니다."
    else:
        sell_fraction = 0.0
        recommended_sell_qty = 0
        sell_reason = "매도 트리거가 없어 추천 매도 수량이 0주입니다."

    recommended_notional_krw = recommended_sell_qty * price
    cost_estimate = calc_round_trip_cost_estimate(
        buy_notional_krw=recommended_notional_krw,
        sell_notional_krw=recommended_notional_krw,
        settings=settings,
    )
    estimated_sell_fee_krw = int(cost_estimate["estimated_sell_fee_krw"])
    estimated_sell_tax_krw = int(cost_estimate["estimated_sell_tax_krw"])
    estimated_sell_slippage_krw = int(cost_estimate["estimated_sell_slippage_krw"])
    estimated_net_proceeds_krw = (
        recommended_notional_krw
        - estimated_sell_fee_krw
        - estimated_sell_tax_krw
        - estimated_sell_slippage_krw
    )
    return SellPositionSizingResult(
        recommended_sell_qty=recommended_sell_qty,
        sell_reason=sell_reason,
        sell_trigger=trigger,
        sell_fraction=sell_fraction,
        available_holding_qty=available_holding_qty,
        recommended_notional_krw=recommended_notional_krw,
        details={
            "trigger": trigger,
            "available_holding_qty": available_holding_qty,
            "sell_fraction": sell_fraction,
            "recommended_sell_qty": recommended_sell_qty,
            "recommended_notional_krw": recommended_notional_krw,
            "estimated_sell_fee_krw": estimated_sell_fee_krw,
            "estimated_sell_tax_krw": estimated_sell_tax_krw,
            "estimated_sell_slippage_krw": estimated_sell_slippage_krw,
            "estimated_net_proceeds_krw": estimated_net_proceeds_krw,
        },
    )
