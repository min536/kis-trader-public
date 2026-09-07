from dataclasses import dataclass
from datetime import datetime

from app.core.time_utils import KOREA_TZ, get_korean_now

REGULAR_OPEN_HHMM = 900
REGULAR_CLOSE_HHMM = 1530
REGULAR_ORDER_CUTOFF_HHMM = 1520


@dataclass(frozen=True)
class MarketSessionStatus:
    session: str
    order_allowed: bool
    reason: str
    buy_block_action: str | None
    sell_block_action: str | None


def get_korean_market_session(now: datetime | None = None) -> MarketSessionStatus:
    current = now.astimezone(KOREA_TZ) if now else get_korean_now()

    if current.weekday() >= 5:
        return MarketSessionStatus(
            session="CLOSED",
            order_allowed=False,
            reason="주말 또는 휴장 시간대로 자동매매 주문을 보내지 않습니다.",
            buy_block_action="blocked_holiday_or_closed",
            sell_block_action="blocked_sell_holiday_or_closed",
        )

    current_hhmm = current.hour * 100 + current.minute
    if current_hhmm < REGULAR_OPEN_HHMM:
        return MarketSessionStatus(
            session="PREMARKET",
            order_allowed=False,
            reason="한국 정규장 시작 전이라 자동매매 주문을 보내지 않습니다.",
            buy_block_action="blocked_premarket",
            sell_block_action="blocked_sell_premarket",
        )
    if REGULAR_OPEN_HHMM <= current_hhmm < REGULAR_ORDER_CUTOFF_HHMM:
        return MarketSessionStatus(
            session="REGULAR",
            order_allowed=True,
            reason="한국 정규장 시간입니다.",
            buy_block_action=None,
            sell_block_action=None,
        )
    if REGULAR_ORDER_CUTOFF_HHMM <= current_hhmm <= REGULAR_CLOSE_HHMM:
        return MarketSessionStatus(
            session="REGULAR",
            order_allowed=False,
            reason=(
                "정규장 마감 보호 구간(15:20 이후)이라 신규 자동매매 주문을 "
                "보내지 않습니다."
            ),
            buy_block_action="blocked_closing_buffer",
            sell_block_action="blocked_sell_closing_buffer",
        )
    if current_hhmm <= 1800:
        return MarketSessionStatus(
            session="AFTER_MARKET",
            order_allowed=False,
            reason="정규장이 종료되어 자동매매 주문을 보내지 않습니다.",
            buy_block_action="blocked_after_market",
            sell_block_action="blocked_sell_after_market",
        )

    return MarketSessionStatus(
        session="CLOSED",
        order_allowed=False,
        reason="정규장 및 시간외 거래 시간이 아니어서 자동매매 주문을 보내지 않습니다.",
        buy_block_action="blocked_holiday_or_closed",
        sell_block_action="blocked_sell_holiday_or_closed",
    )


def build_market_session_console_lines(status: MarketSessionStatus) -> list[str]:
    return [
        "=== 시장 상태 ===",
        f"현재 시장 세션: {status.session}",
        f"주문 가능 여부: {'YES' if status.order_allowed else 'NO'}",
        f"판정 근거: {status.reason}",
    ]
