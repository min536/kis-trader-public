from dataclasses import dataclass

from app.core.costs import calc_round_trip_cost_estimate
from app.execution.schema import ExecutionSnapshot
from app.portfolio.schema import PortfolioSnapshot


@dataclass(frozen=True)
class PositionSizingResult:
    recommended_qty: int
    recommended_notional_krw: int
    max_affordable_qty: int
    budget_limited_qty: int
    exposure_limited_qty: int
    max_qty_limited_qty: int
    reason: str
    details: dict[str, object]


def calculate_position_sizing(
    *,
    execution_snapshot: ExecutionSnapshot,
    portfolio_snapshot: PortfolioSnapshot,
    max_budget_per_trade_krw: int,
    max_account_exposure_pct: float,
    max_qty_per_trade: int,
    settings,
    allow_min_one_share_budget_rescue: bool = False,
) -> PositionSizingResult:
    def estimated_entry_cost_for_qty(qty: int) -> int:
        notional_krw = qty * current_price
        cost_estimate = calc_round_trip_cost_estimate(
            buy_notional_krw=notional_krw,
            sell_notional_krw=notional_krw,
            settings=settings,
        )
        return int(cost_estimate["estimated_entry_cost_krw"])

    def max_qty_with_entry_limit(limit_krw: int, upper_bound: int) -> int:
        if limit_krw <= 0 or upper_bound <= 0:
            return 0

        low = 0
        high = upper_bound
        best = 0
        while low <= high:
            mid = (low + high) // 2
            if estimated_entry_cost_for_qty(mid) <= limit_krw:
                best = mid
                low = mid + 1
            else:
                high = mid - 1
        return best

    current_price = execution_snapshot.current_price
    if current_price <= 0:
        return PositionSizingResult(
            recommended_qty=0,
            recommended_notional_krw=0,
            max_affordable_qty=0,
            budget_limited_qty=0,
            exposure_limited_qty=0,
            max_qty_limited_qty=0,
            reason="현재가가 유효하지 않아 추천 매수 수량을 계산하지 못했습니다.",
            details={
                "current_price_krw": current_price,
                "block_reason_code": "qty_or_price_limited",
                "block_reason_label": "수량/가격 제약",
            },
        )

    account_basis_krw = portfolio_snapshot.total_evaluation_amount
    if account_basis_krw <= 0:
        account_basis_krw = portfolio_snapshot.cash_available

    exposure_budget_krw = int(account_basis_krw * (max_account_exposure_pct / 100))
    upper_bound = min(execution_snapshot.orderable_qty, max_qty_per_trade)
    max_affordable_qty = max_qty_with_entry_limit(
        execution_snapshot.orderable_cash,
        upper_bound,
    )
    budget_limited_qty = max_qty_with_entry_limit(
        max_budget_per_trade_krw,
        upper_bound,
    )
    exposure_limited_qty = max_qty_with_entry_limit(exposure_budget_krw, upper_bound)
    max_qty_limited_qty = max_qty_per_trade

    recommended_qty = min(
        max_affordable_qty,
        budget_limited_qty,
        exposure_limited_qty,
        max_qty_limited_qty,
    )
    recommended_notional_krw = recommended_qty * current_price
    round_trip_cost_estimate = calc_round_trip_cost_estimate(
        buy_notional_krw=recommended_notional_krw,
        sell_notional_krw=recommended_notional_krw,
        settings=settings,
    )

    details = {
        "current_price_krw": current_price,
        "orderable_cash_krw": execution_snapshot.orderable_cash,
        "orderable_qty": execution_snapshot.orderable_qty,
        "account_basis_krw": account_basis_krw,
        "max_budget_per_trade_krw": max_budget_per_trade_krw,
        "max_account_exposure_pct": max_account_exposure_pct,
        "exposure_budget_krw": exposure_budget_krw,
        "max_qty_per_trade": max_qty_per_trade,
        "max_affordable_qty": max_affordable_qty,
        "budget_limited_qty": budget_limited_qty,
        "exposure_limited_qty": exposure_limited_qty,
        "max_qty_limited_qty": max_qty_limited_qty,
        "recommended_qty": recommended_qty,
        "recommended_notional_krw": recommended_notional_krw,
        "estimated_buy_fee_krw": round_trip_cost_estimate["estimated_buy_fee_krw"],
        "estimated_buy_slippage_krw": round_trip_cost_estimate[
            "estimated_buy_slippage_krw"
        ],
        "estimated_entry_cost_krw": round_trip_cost_estimate[
            "estimated_entry_cost_krw"
        ],
        "estimated_round_trip_cost_krw": round_trip_cost_estimate[
            "estimated_round_trip_cost_krw"
        ],
        "estimated_break_even_bps": round_trip_cost_estimate[
            "estimated_break_even_bps"
        ],
        "budget_rescue_enabled": bool(allow_min_one_share_budget_rescue),
        "budget_rescue_applied": False,
        "budget_rescue_qty": 0,
        "budget_rescue_reason": None,
    }

    if recommended_qty <= 0:
        block_reason_code = "qty_or_price_limited"
        block_reason_label = "수량/가격 제약"
        reason = (
            "주문 가능 수량, 가격 또는 호가 제약으로 추천 매수 수량이 0주입니다."
        )

        if max_affordable_qty <= 0:
            block_reason_code = "cash_insufficient"
            block_reason_label = "현금 부족"
            reason = "BUY 불가: 주문가능현금이 부족해 추천 매수 수량이 0주입니다."
        elif exposure_limited_qty <= 0:
            block_reason_code = "exposure_limited"
            block_reason_label = "account exposure 한도 부족"
            reason = "BUY 불가: account exposure 한도 부족으로 추천 매수 수량이 0주입니다."
        elif budget_limited_qty <= 0:
            block_reason_code = "trade_budget_limited"
            block_reason_label = "1회 매수 예산 부족"
            reason = "BUY 불가: 1회 매수 예산 부족으로 추천 매수 수량이 0주입니다."
        elif execution_snapshot.orderable_qty <= 0 or max_qty_limited_qty <= 0:
            block_reason_code = "qty_or_price_limited"
            block_reason_label = "수량/가격 제약"
            reason = "BUY 불가: 주문 가능 수량 또는 가격 제약으로 추천 매수 수량이 0주입니다."

        rescue_allowed = (
            allow_min_one_share_budget_rescue
            and budget_limited_qty <= 0
            and max_affordable_qty >= 1
            and exposure_limited_qty >= 1
            and execution_snapshot.orderable_qty >= 1
            and max_qty_limited_qty >= 1
        )
        if rescue_allowed:
            rescue_qty = 1
            rescue_notional_krw = rescue_qty * current_price
            rescue_cost_estimate = calc_round_trip_cost_estimate(
                buy_notional_krw=rescue_notional_krw,
                sell_notional_krw=rescue_notional_krw,
                settings=settings,
            )
            rescue_reason = (
                "BUY 가능: 1회 매수 예산으로는 1주를 담지 못하지만 현금/노출/주문가능 수량이"
                " 모두 허용되어 최소 1주 rescue를 적용했습니다."
            )
            details.update(
                {
                    "recommended_qty": rescue_qty,
                    "recommended_notional_krw": rescue_notional_krw,
                    "estimated_buy_fee_krw": rescue_cost_estimate["estimated_buy_fee_krw"],
                    "estimated_buy_slippage_krw": rescue_cost_estimate[
                        "estimated_buy_slippage_krw"
                    ],
                    "estimated_entry_cost_krw": rescue_cost_estimate[
                        "estimated_entry_cost_krw"
                    ],
                    "estimated_round_trip_cost_krw": rescue_cost_estimate[
                        "estimated_round_trip_cost_krw"
                    ],
                    "estimated_break_even_bps": rescue_cost_estimate[
                        "estimated_break_even_bps"
                    ],
                    "budget_rescue_applied": True,
                    "budget_rescue_qty": rescue_qty,
                    "budget_rescue_reason": rescue_reason,
                    "budget_rescue_triggered_from_block_reason": "trade_budget_limited",
                }
            )
            return PositionSizingResult(
                recommended_qty=rescue_qty,
                recommended_notional_krw=rescue_notional_krw,
                max_affordable_qty=max_affordable_qty,
                budget_limited_qty=budget_limited_qty,
                exposure_limited_qty=exposure_limited_qty,
                max_qty_limited_qty=max_qty_limited_qty,
                reason=rescue_reason,
                details=details,
            )

        details["block_reason_code"] = block_reason_code
        details["block_reason_label"] = block_reason_label
        return PositionSizingResult(
            recommended_qty=0,
            recommended_notional_krw=0,
            max_affordable_qty=max_affordable_qty,
            budget_limited_qty=budget_limited_qty,
            exposure_limited_qty=exposure_limited_qty,
            max_qty_limited_qty=max_qty_limited_qty,
            reason=reason,
            details=details,
        )

    return PositionSizingResult(
        recommended_qty=recommended_qty,
        recommended_notional_krw=recommended_notional_krw,
        max_affordable_qty=max_affordable_qty,
        budget_limited_qty=budget_limited_qty,
        exposure_limited_qty=exposure_limited_qty,
        max_qty_limited_qty=max_qty_limited_qty,
        reason="주문 가능 수량, 예산 한도, 계좌 비중 한도, 최대 수량 한도를 모두 반영해 추천 매수 수량을 계산했습니다.",
        details=details,
    )
