from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class SellWatchBudgetPlan:
    max_evaluations: int
    pressure_level: str
    reason: str


def compute_effective_sell_check_interval_seconds(
    *,
    base_interval_seconds: int,
    total_holdings: int,
    recent_partial: bool,
    last_rate_limit_source: str | None,
    recent_partial_streak: int = 0,
) -> int:
    base_interval = max(1, int(base_interval_seconds or 1))
    holdings = max(0, int(total_holdings or 0))
    rate_limit_source = str(last_rate_limit_source or "").strip().lower()
    partial_streak = max(0, int(recent_partial_streak or 0))
    if holdings <= 0:
        return base_interval

    multiplier = 1
    if recent_partial:
        if holdings >= 4 and partial_streak >= 2:
            multiplier = 4
        elif holdings >= 4:
            multiplier = 3
        else:
            multiplier = 2
    if rate_limit_source == "sell_watch":
        # When sell_watch itself recently hit the KIS per-second ceiling,
        # spread follow-up checks out more aggressively so the next cycle
        # does not re-hit the same session-level ban immediately.
        multiplier = 6 if holdings >= 4 else 4
    return max(1, base_interval * multiplier)


def should_update_sell_watch_partial_memory(
    *,
    sell_check_due: bool,
    market_open: bool,
    holding_count: int,
) -> bool:
    return bool(sell_check_due and market_open and int(holding_count or 0) > 0)


def build_sell_watch_budget_plan(
    *,
    total_holdings: int,
    remaining_requests: int,
    remaining_quotes: int,
    request_window_size: int,
    soft_request_limit: int,
    buy_scan_due: bool,
    buy_scan_request_reserve: int,
    buy_scan_quote_reserve: int,
    execution_request_reserve: int = 0,
    recent_partial: bool,
    last_rate_limit_source: str | None,
    rate_limit_hits: int,
    recent_partial_streak: int = 0,
) -> SellWatchBudgetPlan:
    holdings = max(0, int(total_holdings or 0))
    partial_streak = max(0, int(recent_partial_streak or 0))
    if holdings <= 0:
        return SellWatchBudgetPlan(
            max_evaluations=0,
            pressure_level="empty",
            reason="보유 종목이 없어 SELL watch 보호 계획이 필요하지 않습니다.",
        )

    buy_request_reserve = max(0, int(buy_scan_request_reserve or 0))
    buy_quote_reserve = max(0, int(buy_scan_quote_reserve or 0))
    execution_reserve = max(0, int(execution_request_reserve or 0))
    remaining_request_count = max(0, int(remaining_requests or 0))
    remaining_quote_count = max(0, int(remaining_quotes or 0))
    request_headroom = max(
        0,
        remaining_request_count - buy_request_reserve - execution_reserve,
    )
    quote_headroom = max(
        0,
        remaining_quote_count - buy_quote_reserve,
    )
    budget_cap = min(holdings, request_headroom, quote_headroom)

    pressure_level = "normal"
    soft_cap = holdings
    rate_limit_source = str(last_rate_limit_source or "").strip().lower()

    if budget_cap <= 0:
        request_headroom_before_execution_reserve = max(
            0,
            remaining_request_count - buy_request_reserve,
        )
        if (
            execution_reserve > 0
            and buy_request_reserve <= 0
            and buy_quote_reserve <= 0
            and request_headroom_before_execution_reserve > 0
            and quote_headroom > 0
        ):
            return SellWatchBudgetPlan(
                max_evaluations=1,
                pressure_level="execution_reserve_relaxed",
                reason=(
                    "매도 주문 reserve가 빠듯하지만 보유 보호를 위해 "
                    "SELL watch 최소 1개 종목을 평가합니다."
                ),
            )
        if (
            execution_reserve > 0
            and remaining_request_count - buy_request_reserve <= execution_reserve
        ):
            return SellWatchBudgetPlan(
                max_evaluations=0,
                pressure_level="execution_reserve_exhausted",
                reason="매도 주문 제출용 request reserve를 남기기 위해 이번 tick의 SELL watch를 건너뜁니다.",
            )
        return SellWatchBudgetPlan(
            max_evaluations=0,
            pressure_level="buy_scan_reserve_exhausted",
            reason="BUY scan reserve를 남기기 위해 이번 tick의 SELL watch를 건너뜁니다.",
        )

    if rate_limit_source == "sell_watch":
        pressure_level = "rate_limit_recent"
        soft_cap = 1
    elif int(rate_limit_hits or 0) > 0:
        pressure_level = "rate_limit_recent"
        soft_cap = 1 if holdings <= 3 else 2
    elif recent_partial:
        pressure_level = "partial_recent"
        soft_cap = (
            max(1, math.ceil(holdings / 3))
            if partial_streak >= 2 and holdings >= 4
            else max(1, math.ceil(holdings / 2))
        )
    elif request_window_size >= max(1, int(soft_request_limit or 1) - 1):
        pressure_level = "request_window_hot"
        soft_cap = 1 if holdings <= 3 else 2
    elif buy_scan_due and (
        int(buy_scan_request_reserve or 0) > 0 or int(buy_scan_quote_reserve or 0) > 0
    ):
        pressure_level = "buy_scan_reserve"
        soft_cap = (
            max(1, math.ceil(holdings / 3))
            if holdings >= 8
            else max(1, math.ceil(holdings / 2))
        )

    max_evaluations = max(0, min(budget_cap, soft_cap))
    if max_evaluations >= holdings:
        return SellWatchBudgetPlan(
            max_evaluations=max_evaluations,
            pressure_level=pressure_level,
            reason="이번 tick에서는 SELL watch 전 종목 평가가 가능합니다.",
        )

    if pressure_level == "rate_limit_recent":
        reason = "직전 SELL watch rate limit 여파로 이번 tick은 보수적으로 일부 보유 종목만 평가합니다."
    elif pressure_level == "partial_recent":
        reason = (
            "SELL watch partial 이 반복되어 이번 tick은 1/3 이하만 평가해 API 예산을 보호합니다."
            if partial_streak >= 2 and holdings >= 4
            else "직전 SELL watch가 partial 이었으므로 이번 tick은 절반 이하만 평가해 buy_scan 예산을 보호합니다."
        )
    elif pressure_level == "request_window_hot":
        reason = "request window가 이미 뜨거워 이번 tick은 일부 보유 종목만 평가합니다."
    elif pressure_level == "buy_scan_reserve":
        reason = (
            "BUY scan due 이므로 reserve를 남기기 위해 이번 tick은 일부 보유 종목만 "
            "더 보수적으로 평가합니다."
        )
    else:
        reason = "현재 API 예산 범위 안에서 일부 보유 종목만 평가합니다."

    return SellWatchBudgetPlan(
        max_evaluations=max_evaluations,
        pressure_level=pressure_level,
        reason=reason,
    )
