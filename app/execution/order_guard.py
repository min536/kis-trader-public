from dataclasses import dataclass
from typing import Any

from app.core.time_utils import get_korean_now
from app.runtime_state import (
    SELL_EMERGENCY_TRIGGERS,
    is_runtime_state_trusted,
    parse_recent_order_time,
    pending_sell_intent_submitted_at,
)


@dataclass(frozen=True)
class OrderGuardResult:
    allowed: bool
    action: str | None
    reason: str
    is_cooldown: bool = False
    details: dict[str, object] | None = None


def build_buy_attempt_signature(*, symbol: str, qty: int) -> str:
    return f"BUY:{symbol}:{int(qty)}"


def build_sell_attempt_signature(*, symbol: str, trigger: str | None, qty: int) -> str:
    # qty-level audit/logging signature — not used for cooldown comparison.
    return f"SELL:{symbol}:{trigger or '-'}:{int(qty)}"


def build_sell_cooldown_key(*, symbol: str, trigger: str | None) -> str:
    # Intentionally excludes qty so that partial sell sizing changes (e.g. the
    # geometric 6→3→2→1 pattern from available_holding_qty // 2) do not bypass
    # the cooldown.  Used only for duplicate-sell detection, not for logging.
    return f"SELL:{symbol}:{trigger or '-'}"


def _runtime_state_block_result(state: dict, *, action: str) -> OrderGuardResult | None:
    if is_runtime_state_trusted(state):
        return None
    reason_code = str(state.get("runtime_state_safety_reason") or "").strip()
    return OrderGuardResult(
        allowed=False,
        action=action,
        reason="런타임 상태를 신뢰할 수 없어 주문 제출을 차단합니다.",
        details={"runtime_state_safety_reason": reason_code or "unknown"},
    )


def _within_cooldown(
    *,
    state: dict,
    side: str,
    symbol: str,
    qty: int,
    cooldown_minutes: int,
    require_qty_match: bool = True,
    now=None,
) -> bool:
    if cooldown_minutes <= 0:
        return False

    # ADR-4: an injected `now` (e.g. an exchange-local ET clock for overseas)
    # overrides the default Korean clock; None preserves domestic behavior.
    now = now if now is not None else get_korean_now()
    for order in reversed(state.get("recent_orders", [])):
        if order.get("date") != now.date().isoformat():
            continue
        if order.get("side") != side:
            continue
        if order.get("symbol") != symbol:
            continue
        if require_qty_match and int(order.get("qty", 0)) != int(qty):
            continue

        order_time = parse_recent_order_time(order)
        if order_time is None:
            continue

        elapsed_minutes = (now - order_time).total_seconds() / 60
        if elapsed_minutes <= cooldown_minutes:
            return True
        return False
    return False


def _matches_last_attempt_cooldown(
    *,
    state: dict,
    signature: str,
    last_signature_key: str,
    last_at_key: str,
    cooldown_minutes: int,
    now=None,
) -> bool:
    if cooldown_minutes <= 0:
        return False
    if str(state.get(last_signature_key) or "").strip() != signature:
        return False

    last_at_text = str(state.get(last_at_key) or "").strip()
    if not last_at_text:
        return False

    order = {"timestamp": last_at_text}
    order_time = parse_recent_order_time(order)
    if order_time is None:
        return False

    now = now if now is not None else get_korean_now()
    elapsed_minutes = (now - order_time).total_seconds() / 60
    return elapsed_minutes <= cooldown_minutes


def _count_symbol_orders_today(
    *,
    state: dict,
    side: str,
    symbol: str,
    actions: tuple[str, ...],
    now=None,
) -> int:
    now = now if now is not None else get_korean_now()
    count = 0
    for order in reversed(state.get("recent_orders", [])):
        if order.get("date") != now.date().isoformat():
            continue
        if order.get("side") != side:
            continue
        if order.get("symbol") != symbol:
            continue
        if str(order.get("action", "")).strip() not in actions:
            continue
        count += 1
    return count


def _pending_sell_intent_qty(state: dict, symbol: str) -> int:
    pending = state.get("pending_sell_intents_by_symbol")
    if not isinstance(pending, dict):
        return 0
    entry = pending.get(symbol)
    if isinstance(entry, dict):
        return max(0, int(entry.get("qty", 0) or 0))
    return max(0, int(entry or 0))


def _last_symbol_order_within_minutes(
    *,
    state: dict,
    side: str,
    symbol: str,
    minutes: int,
    actions: tuple[str, ...],
    now=None,
) -> bool:
    if minutes <= 0:
        return False

    now = now if now is not None else get_korean_now()
    for order in reversed(state.get("recent_orders", [])):
        if order.get("date") != now.date().isoformat():
            continue
        if order.get("side") != side:
            continue
        if order.get("symbol") != symbol:
            continue
        if str(order.get("action", "")).strip() not in actions:
            continue

        order_time = parse_recent_order_time(order)
        if order_time is None:
            continue
        elapsed_minutes = (now - order_time).total_seconds() / 60
        return elapsed_minutes <= minutes
    return False


def evaluate_buy_order_guard(
    *,
    state: dict,
    symbol: str,
    qty: int,
    block_rebuy_symbols_bought_today: bool,
    allow_one_buy_per_symbol_per_day: bool,
    rebuy_cooldown_minutes: int,
    same_symbol_max_buys_per_day: int,
    order_cooldown_minutes: int,
    blocked_cooldown_minutes: int,
    reentry_decision: Any | None = None,
    now=None,
) -> OrderGuardResult:
    signature = build_buy_attempt_signature(symbol=symbol, qty=qty)
    _ = block_rebuy_symbols_bought_today
    _ = allow_one_buy_per_symbol_per_day
    # ADR-4: resolve the clock once and thread it into every time-keyed helper so
    # an overseas (ET) caller keys "today"/cooldowns off the exchange-local clock.
    now = now if now is not None else get_korean_now()

    runtime_state_block = _runtime_state_block_result(
        state,
        action="blocked_buy_runtime_state_untrusted",
    )
    if runtime_state_block is not None:
        return runtime_state_block

    if reentry_decision is not None and not bool(
        getattr(reentry_decision, "allowed", True)
    ):
        reentry_state = str(getattr(reentry_decision, "state", "") or "").strip()
        reentry_reason = str(
            getattr(reentry_decision, "reason", "") or "state-aware re-entry manager가 차단했습니다."
        ).strip()
        action = "blocked_buy_reentry_state"
        if reentry_state == "blocked_residual_position":
            action = "blocked_buy_residual_position"
        elif reentry_state == "blocked_same_day_stop_loss_reentry":
            action = "blocked_buy_same_day_stop_loss_reentry"
        elif reentry_state == "blocked_recent_stop_loss":
            action = "blocked_buy_recent_stop_loss"
        elif reentry_state == "blocked_daily_limit_fallback":
            action = "blocked_buy_same_symbol_daily_limit"
        elif reentry_state == "blocked_hard_guard":
            action = "blocked_buy_reentry_hard_guard"
        elif reentry_state == "blocked_churn_risk":
            action = "blocked_buy_churn_risk"
        return OrderGuardResult(
            allowed=False,
            action=action,
            reason=reentry_reason,
            is_cooldown=reentry_state in {
                "blocked_same_day_stop_loss_reentry",
                "blocked_recent_stop_loss",
                "blocked_churn_risk",
                "blocked_daily_limit_fallback",
            },
            details=(
                reentry_decision.to_log_payload()
                if hasattr(reentry_decision, "to_log_payload")
                else None
            ),
        )

    if _last_symbol_order_within_minutes(
        state=state,
        side="BUY",
        symbol=symbol,
        minutes=rebuy_cooldown_minutes,
        actions=("order_submitted", "order_succeeded"),
        now=now,
    ):
        return OrderGuardResult(
            allowed=False,
            action="blocked_buy_reentry_cooldown",
            reason="마지막 매수 후 재진입 cooldown 시간 안이라 이번 매수는 건너뜁니다.",
            is_cooldown=True,
        )

    if same_symbol_max_buys_per_day > 0 and _count_symbol_orders_today(
        state=state,
        side="BUY",
        symbol=symbol,
        actions=("order_submitted", "order_succeeded"),
        now=now,
    ) >= same_symbol_max_buys_per_day:
        return OrderGuardResult(
            allowed=False,
            action="blocked_buy_same_symbol_daily_limit",
            reason="같은 종목의 당일 매수 횟수 제한에 도달해 추가 재진입을 차단합니다.",
        )

    if _matches_last_attempt_cooldown(
        state=state,
        signature=signature,
        last_signature_key="last_buy_attempt_signature",
        last_at_key="last_buy_attempt_at",
        cooldown_minutes=blocked_cooldown_minutes,
        now=now,
    ):
        return OrderGuardResult(
            allowed=False,
            action="blocked_buy_cooldown",
            reason="동일 BUY 신호가 cooldown 시간 안에 반복되어 이번 사이클에서는 건너뜁니다.",
            is_cooldown=True,
        )

    if _within_cooldown(
        state=state,
        side="BUY",
        symbol=symbol,
        qty=qty,
        cooldown_minutes=order_cooldown_minutes,
        now=now,
    ):
        return OrderGuardResult(
            allowed=False,
            action="blocked_buy_duplicate_guard",
            reason="동일 BUY 주문이 cooldown 시간 안에 반복되어 차단했습니다.",
            is_cooldown=True,
        )

    return OrderGuardResult(allowed=True, action=None, reason="BUY 중복 주문 가드 통과")


def evaluate_sell_order_guard(
    *,
    state: dict,
    symbol: str,
    holding_qty: int,
    qty: int,
    block_resell_symbols_sold_today: bool,
    allow_one_sell_trigger_per_symbol_per_day: bool,
    order_cooldown_minutes: int,
    blocked_cooldown_minutes: int,
    trigger: str | None,
    emergency_triggers: frozenset[str] = SELL_EMERGENCY_TRIGGERS,
    now=None,
) -> OrderGuardResult:
    # ADR-4: thread the (optionally injected, e.g. ET) clock into the time-keyed
    # cooldown helpers; None preserves domestic Korean-clock behavior.
    now = now if now is not None else get_korean_now()
    signature = build_sell_attempt_signature(symbol=symbol, trigger=trigger, qty=qty)
    # qty-free key used for cooldown comparison — prevents geometric partial-sell
    # sequences (e.g. 6→3→2→1 from available_holding_qty // 2) from bypassing
    # the cooldown simply because qty changes each cycle.
    cooldown_key = build_sell_cooldown_key(symbol=symbol, trigger=trigger)
    is_emergency = bool(trigger) and trigger in emergency_triggers
    runtime_state_block = _runtime_state_block_result(
        state,
        action="blocked_sell_runtime_state_untrusted",
    )
    if runtime_state_block is not None:
        return runtime_state_block

    pending_intent_qty = _pending_sell_intent_qty(state, symbol)
    sellable_residual_qty = max(0, int(holding_qty) - pending_intent_qty)
    details = {
        "holding_qty": int(holding_qty),
        "pending_intent_qty": pending_intent_qty,
        "sellable_residual_qty": sellable_residual_qty,
        # Without the submit stamp a post-hoc reader cannot tell a legitimate
        # in-flight reservation from one stranded hours ago.
        "pending_intent_submitted_at": pending_sell_intent_submitted_at(state, symbol),
    }

    if int(holding_qty) <= 0:
        return OrderGuardResult(
            allowed=False,
            action="blocked_sell_already_fully_exited",
            reason="이미 전량 청산된 종목이라 추가 SELL을 차단합니다.",
            details=details,
        )

    if sellable_residual_qty <= 0:
        return OrderGuardResult(
            allowed=False,
            action="blocked_sell_no_sellable_residual",
            reason=(
                "이미 제출된 SELL intent가 현재 보유 수량을 모두 예약 중이라 "
                "추가로 매도 가능한 잔량이 없습니다."
            ),
            details=details,
        )

    if int(qty) > sellable_residual_qty:
        return OrderGuardResult(
            allowed=False,
            action="blocked_sell_duplicate_pending_intent",
            reason=(
                f"이미 제출된 SELL intent {pending_intent_qty}주를 제외하면 "
                f"잔여 매도 가능 수량은 {sellable_residual_qty}주라 "
                f"이번 SELL {int(qty)}주는 중복 intent로 차단합니다."
            ),
            details=details,
        )

    if (
        block_resell_symbols_sold_today
        and symbol in state.get("symbols_sold_today", [])
    ):
        return OrderGuardResult(
            allowed=False,
            action="blocked_resell_symbol_sold_today",
            reason="오늘 이미 매도한 종목이라 재매도를 차단합니다.",
            details=details,
        )

    if (
        allow_one_sell_trigger_per_symbol_per_day
        and symbol in state.get("sell_triggered_symbols_today", [])
        and pending_intent_qty > 0
    ):
        return OrderGuardResult(
            allowed=False,
            action="blocked_sell_duplicate_pending_intent",
            reason="이미 제출된 SELL intent가 남아 있어 동일 종목의 중복 SELL 신호를 차단합니다.",
            details=details,
        )

    if not is_emergency and _matches_last_attempt_cooldown(
        state=state,
        signature=cooldown_key,
        last_signature_key="last_sell_attempt_signature",
        last_at_key="last_sell_attempt_at",
        cooldown_minutes=blocked_cooldown_minutes,
        now=now,
    ):
        return OrderGuardResult(
            allowed=False,
            action="blocked_sell_cooldown",
            reason="동일 SELL 신호가 cooldown 시간 안에 반복되어 이번 사이클에서는 건너뜁니다.",
            is_cooldown=True,
            details=details,
        )

    if not is_emergency and _within_cooldown(
        state=state,
        side="SELL",
        symbol=symbol,
        qty=qty,
        cooldown_minutes=order_cooldown_minutes,
        now=now,
    ):
        return OrderGuardResult(
            allowed=False,
            action="blocked_sell_duplicate_guard",
            reason="동일 SELL 주문이 cooldown 시간 안에 반복되어 차단했습니다.",
            is_cooldown=True,
            details=details,
        )

    return OrderGuardResult(
        allowed=True,
        action=None,
        reason="SELL 중복 주문 가드 통과",
        details=details,
    )


def evaluate_rebalance_sell_guard(
    *,
    state: dict,
    max_submissions_per_day: int,
) -> OrderGuardResult:
    runtime_state_block = _runtime_state_block_result(
        state,
        action="blocked_rebalance_runtime_state_untrusted",
    )
    if runtime_state_block is not None:
        return runtime_state_block

    used = int(state.get("rebalance_sell_submissions_today", 0))
    if max_submissions_per_day >= 0 and used >= max_submissions_per_day:
        return OrderGuardResult(
            allowed=False,
            action="blocked_rebalance_sell_limit",
            reason="오늘 리밸런싱 매도 한도를 이미 사용해 추가 리밸런싱을 차단합니다.",
        )

    return OrderGuardResult(
        allowed=True,
        action=None,
        reason="리밸런싱 매도 한도를 통과했습니다.",
    )
