from dataclasses import dataclass
from typing import Any

from app.runtime_state import parse_recent_order_time


@dataclass(frozen=True)
class ReEntryEligibilityResult:
    allowed: bool
    state: str
    reason: str
    cooldown_minutes_effective: int | None
    requires_fresh_setup: bool
    exit_reason: str | None
    residual_position_present: bool
    diagnostics: tuple[str, ...]

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "state": self.state,
            "reason": self.reason,
            "cooldown_minutes_effective": self.cooldown_minutes_effective,
            "requires_fresh_setup": self.requires_fresh_setup,
            "exit_reason": self.exit_reason,
            "residual_position_present": self.residual_position_present,
            "diagnostics": list(self.diagnostics),
        }


def normalize_exit_reason(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "unknown"
    if "stop_loss" in text or "손절" in text:
        return "stop_loss"
    if "take_profit" in text or "익절" in text:
        return "take_profit"
    if "trailing_stop" in text or "트레일링" in text:
        return "trailing_stop"
    if "rebalance" in text:
        return "rebalance_sell"
    if "daily_pnl" in text or "브레이크" in text:
        return "daily_pnl_brake"
    if "manual" in text or "reconcil" in text:
        return "manual_or_reconciled"
    return "unknown"


def _minutes_since_iso(iso_text: str | None, *, now) -> float | None:
    text = str(iso_text or "").strip()
    if not text:
        return None
    parsed = parse_recent_order_time({"timestamp": text})
    if parsed is None:
        return None
    return max(0.0, (now - parsed).total_seconds() / 60.0)


def _is_same_korean_day(iso_text: str | None, *, now) -> bool:
    text = str(iso_text or "").strip()
    if not text:
        return False
    parsed = parse_recent_order_time({"timestamp": text})
    if parsed is None:
        return False
    return parsed.date() == now.date()


def _current_snapshot_value(current_snapshot: object, key: str) -> float | None:
    if isinstance(current_snapshot, dict):
        value = current_snapshot.get(key)
    else:
        value = getattr(current_snapshot, key, None)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _build_fresh_setup_proxy(
    *,
    candidate: object | None,
    current_snapshot: object | None,
    last_exit_price: int | None,
) -> tuple[bool, list[str]]:
    diagnostics: list[str] = []
    score = 0

    current_price = _current_snapshot_value(current_snapshot, "current_price")
    open_price = _current_snapshot_value(current_snapshot, "open_price")
    prev_day_change_pct = _current_snapshot_value(current_snapshot, "prev_day_change_pct")

    if candidate is not None:
        candidate_flag = bool(getattr(candidate, "candidate", False))
        passed_count = int(getattr(candidate, "passed_count", 0) or 0)
        enabled_count = int(getattr(candidate, "enabled_count", 0) or 0)
        score_value = float(getattr(candidate, "score", 0.0) or 0.0)
        cost_block_reason = str(getattr(candidate, "cost_block_reason", "") or "").strip()
        if candidate_flag:
            score += 1
            diagnostics.append("candidate=true")
        if enabled_count > 0 and passed_count >= max(1, int(round(enabled_count * 0.6))):
            score += 1
            diagnostics.append("passed_count strong")
        if score_value >= 1.0:
            score += 1
            diagnostics.append("deep score >= 1.0")
        if not cost_block_reason:
            score += 1
            diagnostics.append("no cost veto")

    if current_price is not None and open_price is not None and current_price >= open_price:
        score += 1
        diagnostics.append("price above open")
    if prev_day_change_pct is not None and prev_day_change_pct >= -1.0:
        score += 1
        diagnostics.append("prev_day_change acceptable")
    if (
        last_exit_price is not None
        and current_price is not None
        and last_exit_price > 0
        and current_price >= last_exit_price * 1.003
    ):
        score += 1
        diagnostics.append("price recovered above exit")

    threshold = 3 if candidate is not None else 2
    return score >= threshold, diagnostics


def evaluate_reentry_eligibility(
    *,
    symbol: str,
    runtime_state: dict[str, Any],
    settings,
    regime_state: dict[str, Any] | None,
    daily_pnl_brake_state: dict[str, Any] | None,
    current_snapshot: object | None,
    residual_position_qty: int,
    candidate: object | None,
    now,
) -> ReEntryEligibilityResult:
    normalized_symbol = str(symbol or "").strip()
    residual_qty = max(0, int(residual_position_qty or 0))
    exit_reason_raw = (
        (runtime_state.get("last_exit_reason_by_symbol") or {}).get(normalized_symbol)
    )
    exit_reason = normalize_exit_reason(exit_reason_raw) if exit_reason_raw else None
    last_exit_at = (runtime_state.get("last_exit_at_by_symbol") or {}).get(normalized_symbol)
    last_exit_price_raw = (runtime_state.get("last_exit_price_by_symbol") or {}).get(normalized_symbol)
    try:
        last_exit_price = int(last_exit_price_raw) if last_exit_price_raw is not None else None
    except (TypeError, ValueError):
        last_exit_price = None
    minutes_since_exit = _minutes_since_iso(last_exit_at, now=now)
    entry_count_today = int(
        ((runtime_state.get("buy_entries_by_symbol_today") or {}).get(normalized_symbol, 0) or 0)
    )
    regime = str((regime_state or {}).get("current_regime") or runtime_state.get("current_regime") or "").strip()
    buy_paused = bool((daily_pnl_brake_state or {}).get("buy_paused"))

    fresh_setup, setup_diagnostics = _build_fresh_setup_proxy(
        candidate=candidate,
        current_snapshot=current_snapshot,
        last_exit_price=last_exit_price,
    )
    diagnostics = list(setup_diagnostics)
    if exit_reason:
        diagnostics.append(f"exit_reason={exit_reason}")
    if minutes_since_exit is not None:
        diagnostics.append(f"minutes_since_exit={minutes_since_exit:.1f}")

    if residual_qty > 0:
        return ReEntryEligibilityResult(
            allowed=False,
            state="blocked_residual_position",
            reason="잔여 포지션이 남아 있어 이번 BUY를 일반 재진입으로 허용하지 않습니다.",
            cooldown_minutes_effective=None,
            requires_fresh_setup=False,
            exit_reason=exit_reason,
            residual_position_present=True,
            diagnostics=tuple(diagnostics + [f"residual_qty={residual_qty}"]),
        )

    if exit_reason is None:
        if settings.same_symbol_max_buys_per_day > 0 and entry_count_today >= settings.same_symbol_max_buys_per_day:
            return ReEntryEligibilityResult(
                allowed=False,
                state="blocked_daily_limit_fallback",
                reason="동일 종목 일일 진입 횟수 제한으로 추가 진입을 차단합니다.",
                cooldown_minutes_effective=settings.rebuy_cooldown_minutes,
                requires_fresh_setup=False,
                exit_reason=None,
                residual_position_present=False,
                diagnostics=tuple(diagnostics + [f"entry_count_today={entry_count_today}"]),
            )
        return ReEntryEligibilityResult(
            allowed=True,
            state="allowed_no_recent_exit",
            reason="최근 exit 기록이 없어 일반 BUY 후보로 취급합니다.",
            cooldown_minutes_effective=None,
            requires_fresh_setup=False,
            exit_reason=None,
            residual_position_present=False,
            diagnostics=tuple(diagnostics),
        )

    base_cooldown = max(int(settings.rebuy_cooldown_minutes or 0), 0)
    effective_cooldown = base_cooldown
    requires_fresh_setup = False

    if exit_reason == "stop_loss":
        effective_cooldown = max(base_cooldown * 2, 30)
        requires_fresh_setup = True
        same_day_floor = max(
            int(getattr(settings, "stop_loss_same_day_reentry_min_minutes", 0) or 0),
            0,
        )
        if _is_same_korean_day(last_exit_at, now=now) and (
            minutes_since_exit is None or minutes_since_exit < same_day_floor
        ):
            return ReEntryEligibilityResult(
                allowed=False,
                state="blocked_same_day_stop_loss_reentry",
                reason=(
                    "당일 stop_loss 이후에는 same-day 최소 재진입 cooldown이 지나기 전까지 "
                    "같은 종목 재진입을 차단합니다."
                ),
                cooldown_minutes_effective=max(effective_cooldown, same_day_floor),
                requires_fresh_setup=True,
                exit_reason=exit_reason,
                residual_position_present=False,
                diagnostics=tuple(
                    diagnostics
                    + [
                        f"same_day_stop_loss_floor={same_day_floor}",
                        "fresh_setup_override=disabled",
                    ]
                ),
            )
        if regime == "RISK_OFF":
            return ReEntryEligibilityResult(
                allowed=False,
                state="blocked_hard_guard",
                reason="최근 stop_loss 이후 현재 regime가 RISK_OFF라 재진입을 보수적으로 차단합니다.",
                cooldown_minutes_effective=effective_cooldown,
                requires_fresh_setup=True,
                exit_reason=exit_reason,
                residual_position_present=False,
                diagnostics=tuple(diagnostics + ["regime=RISK_OFF"]),
            )
        if minutes_since_exit is None or (
            minutes_since_exit < effective_cooldown and not fresh_setup
        ):
            return ReEntryEligibilityResult(
                allowed=False,
                state="blocked_recent_stop_loss",
                reason="최근 stop_loss 이후 아직 fresh setup이 확인되지 않아 재진입을 차단합니다.",
                cooldown_minutes_effective=effective_cooldown,
                requires_fresh_setup=True,
                exit_reason=exit_reason,
                residual_position_present=False,
                diagnostics=tuple(diagnostics + ["fresh_setup=no"]),
            )
        if fresh_setup and minutes_since_exit is not None and minutes_since_exit >= max(base_cooldown // 2, 10):
            allowed_state = "allowed_fresh_setup_after_stop"
            allowed_reason = "최근 stop_loss였지만 fresh setup이 다시 확인되어 제한적으로 재진입을 허용합니다."
        else:
            allowed_state = "allowed_after_time_decay"
            allowed_reason = "stop_loss 이후 시간이 충분히 지나 재진입을 허용합니다."
    elif exit_reason in {"take_profit", "trailing_stop"}:
        effective_cooldown = max(base_cooldown // 2, 5)
        requires_fresh_setup = True
        if fresh_setup and (minutes_since_exit is None or minutes_since_exit >= effective_cooldown):
            allowed_state = "allowed_after_profit_exit"
            allowed_reason = "profit exit 이후 fresh setup이 다시 확인되어 재진입을 허용합니다."
        elif minutes_since_exit is not None and minutes_since_exit >= max(base_cooldown, effective_cooldown):
            allowed_state = "allowed_after_time_decay"
            allowed_reason = "profit exit 이후 시간이 지나 재진입을 허용합니다."
        else:
            return ReEntryEligibilityResult(
                allowed=False,
                state="blocked_churn_risk",
                reason="profit exit 직후 setup이 충분히 새로워 보이지 않아 재진입을 차단합니다.",
                cooldown_minutes_effective=effective_cooldown,
                requires_fresh_setup=True,
                exit_reason=exit_reason,
                residual_position_present=False,
                diagnostics=tuple(diagnostics + ["fresh_setup=no"]),
            )
    elif exit_reason == "rebalance_sell":
        effective_cooldown = max(base_cooldown, 15)
        requires_fresh_setup = True
        if fresh_setup and minutes_since_exit is not None and minutes_since_exit >= max(base_cooldown // 2, 10):
            allowed_state = "allowed_after_time_decay"
            allowed_reason = "rebalance exit 이후 setup이 다시 확인되어 재진입을 허용합니다."
        elif minutes_since_exit is not None and minutes_since_exit >= effective_cooldown:
            allowed_state = "allowed_after_time_decay"
            allowed_reason = "rebalance exit 이후 시간이 지나 재진입을 허용합니다."
        else:
            return ReEntryEligibilityResult(
                allowed=False,
                state="blocked_churn_risk",
                reason="rebalance exit 직후라 churn risk를 피하기 위해 재진입을 차단합니다.",
                cooldown_minutes_effective=effective_cooldown,
                requires_fresh_setup=True,
                exit_reason=exit_reason,
                residual_position_present=False,
                diagnostics=tuple(diagnostics),
            )
    elif exit_reason == "daily_pnl_brake":
        effective_cooldown = max(base_cooldown * 2, 30)
        requires_fresh_setup = True
        if buy_paused or regime == "RISK_OFF":
            return ReEntryEligibilityResult(
                allowed=False,
                state="blocked_hard_guard",
                reason="daily pnl brake 관련 exit 이후 아직 brake/regime가 보수적이라 재진입을 차단합니다.",
                cooldown_minutes_effective=effective_cooldown,
                requires_fresh_setup=True,
                exit_reason=exit_reason,
                residual_position_present=False,
                diagnostics=tuple(diagnostics + [f"buy_paused={buy_paused}", f"regime={regime or '-'}"]),
            )
        if minutes_since_exit is None or minutes_since_exit < effective_cooldown or not fresh_setup:
            return ReEntryEligibilityResult(
                allowed=False,
                state="blocked_hard_guard",
                reason="daily pnl brake exit 이후 충분한 시간 경과나 fresh setup이 없어 재진입을 차단합니다.",
                cooldown_minutes_effective=effective_cooldown,
                requires_fresh_setup=True,
                exit_reason=exit_reason,
                residual_position_present=False,
                diagnostics=tuple(diagnostics),
            )
        allowed_state = "allowed_after_time_decay"
        allowed_reason = "daily pnl brake 관련 exit 이후 시간이 지나고 setup이 회복되어 재진입을 허용합니다."
    else:
        effective_cooldown = base_cooldown
        if minutes_since_exit is None or minutes_since_exit < effective_cooldown:
            return ReEntryEligibilityResult(
                allowed=False,
                state="blocked_churn_risk",
                reason="최근 exit 이후 fallback cooldown 구간이라 재진입을 차단합니다.",
                cooldown_minutes_effective=effective_cooldown,
                requires_fresh_setup=False,
                exit_reason=exit_reason,
                residual_position_present=False,
                diagnostics=tuple(diagnostics),
            )
        allowed_state = "allowed_after_time_decay"
        allowed_reason = "최근 exit 이후 fallback cooldown이 지나 재진입을 허용합니다."

    if settings.same_symbol_max_buys_per_day > 0 and entry_count_today >= settings.same_symbol_max_buys_per_day:
        return ReEntryEligibilityResult(
            allowed=False,
            state="blocked_daily_limit_fallback",
            reason="같은 종목의 당일 매수 횟수 제한에 도달해 추가 재진입을 차단합니다.",
            cooldown_minutes_effective=effective_cooldown,
            requires_fresh_setup=requires_fresh_setup,
            exit_reason=exit_reason,
            residual_position_present=False,
            diagnostics=tuple(diagnostics + [f"entry_count_today={entry_count_today}"]),
        )

    return ReEntryEligibilityResult(
        allowed=True,
        state=allowed_state,
        reason=allowed_reason,
        cooldown_minutes_effective=effective_cooldown,
        requires_fresh_setup=requires_fresh_setup,
        exit_reason=exit_reason,
        residual_position_present=False,
        diagnostics=tuple(diagnostics + [f"fresh_setup={'yes' if fresh_setup else 'no'}"]),
    )
