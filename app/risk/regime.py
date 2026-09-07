import math

from app.core.formatters import format_krw, format_qty


def _format_signed_pct(value: float) -> str:
    number = float(value)
    if number > 0:
        return f"+{number:.2f}%"
    if number < 0:
        return f"-{abs(number):.2f}%"
    return "0.00%"


def build_regime_state(
    *,
    settings,
    daily_pnl_brake_state: dict[str, object] | None,
    drawdown_state: dict[str, object] | None,
) -> dict[str, object]:
    brake_state = str((daily_pnl_brake_state or {}).get("display_status") or "").strip().upper()
    current_drawdown_pct = (
        None
        if not isinstance(drawdown_state, dict)
        else drawdown_state.get("current_drawdown_pct")
    )

    regime = "NORMAL"
    multiplier = float(settings.regime_normal_multiplier)
    reasons: list[str] = []

    if brake_state in {"BUY_PAUSE", "HARD_STOP_READY"}:
        regime = "RISK_OFF"
        multiplier = float(settings.regime_risk_off_multiplier)
        reasons.append(f"daily pnl brake {brake_state.lower()}")
    elif (
        current_drawdown_pct is not None
        and float(current_drawdown_pct) <= float(settings.regime_risk_off_drawdown_pct)
    ):
        regime = "RISK_OFF"
        multiplier = float(settings.regime_risk_off_multiplier)
        reasons.append("drawdown elevated")
    elif brake_state == "WARNING":
        regime = "CAUTION"
        multiplier = float(settings.regime_caution_multiplier)
        reasons.append("daily pnl brake warning")
    elif (
        current_drawdown_pct is not None
        and float(current_drawdown_pct) <= float(settings.regime_caution_drawdown_pct)
    ):
        regime = "CAUTION"
        multiplier = float(settings.regime_caution_multiplier)
        reasons.append("drawdown elevated")

    if not reasons:
        reasons.append("daily pnl brake 정상 + drawdown 안정")

    base_symbol_limit = max(int(settings.same_symbol_max_buys_per_day), 0)
    base_buy_limit = max(int(settings.buy_daily_max_order_submissions), 0)
    cooldown_multiplier = 1
    if regime == "CAUTION":
        cooldown_multiplier = 2
    elif regime == "RISK_OFF":
        cooldown_multiplier = 4

    effective_same_symbol_limit = base_symbol_limit
    if base_symbol_limit > 0:
        if regime == "CAUTION":
            effective_same_symbol_limit = max(1, min(base_symbol_limit, base_symbol_limit - 1))
        elif regime == "RISK_OFF":
            effective_same_symbol_limit = 1

    effective_buy_daily_limit = base_buy_limit
    if base_buy_limit > 0:
        if regime == "CAUTION":
            effective_buy_daily_limit = max(1, min(base_buy_limit, 15))
        elif regime == "RISK_OFF":
            # RISK_OFF already cuts notional, exposure, quantity, and re-entry
            # cadence.  Keep scanning pressure unchanged, but allow a small
            # amount of replenishment when SELLs free cash and candidates are
            # already available.
            effective_buy_daily_limit = max(1, min(base_buy_limit, 10))

    effective_budget_krw = max(
        1,
        int(round(settings.buy_max_budget_per_trade_krw * multiplier)),
    )
    effective_exposure_pct = max(
        0.1,
        round(settings.buy_max_account_exposure_pct * multiplier, 2),
    )
    effective_qty = max(
        1,
        int(
            math.floor(
                settings.buy_max_qty_per_trade
                * max(multiplier, 0.5 if regime == "RISK_OFF" else 0.34)
            )
        ),
    )
    effective_rebuy_cooldown = max(
        0,
        int(round(settings.rebuy_cooldown_minutes * cooldown_multiplier)),
    )

    return {
        "current_regime": regime,
        "regime_reason": " + ".join(reasons),
        "regime_multiplier": round(multiplier, 2),
        "current_drawdown_pct": current_drawdown_pct,
        "effective_buy_max_budget_per_trade_krw": effective_budget_krw,
        "effective_buy_max_account_exposure_pct": effective_exposure_pct,
        "effective_buy_max_qty_per_trade": effective_qty,
        "effective_rebuy_cooldown_minutes": effective_rebuy_cooldown,
        "effective_same_symbol_max_buys_per_day": effective_same_symbol_limit,
        "effective_buy_daily_max_order_submissions": effective_buy_daily_limit,
    }


def print_regime_state(*, regime_state: dict[str, object]) -> None:
    print("=== regime 상태 ===")
    print(f"current regime: {regime_state.get('current_regime') or '데이터 부족'}")
    print(f"reason: {regime_state.get('regime_reason') or '데이터 부족'}")
    print(
        "buy sizing multiplier="
        f"{float(regime_state.get('regime_multiplier', 1.0) or 1.0):.2f}"
    )
    if regime_state.get("current_drawdown_pct") is None:
        print("current drawdown: 데이터 부족")
    else:
        print(
            "current drawdown: "
            f"{_format_signed_pct(float(regime_state.get('current_drawdown_pct') or 0.0))}"
        )
    print(
        "effective BUY budget="
        f"{format_krw(int(regime_state.get('effective_buy_max_budget_per_trade_krw', 0) or 0))}"
    )
    print(
        "effective BUY exposure="
        f"{float(regime_state.get('effective_buy_max_account_exposure_pct', 0.0) or 0.0):.2f}%"
    )
    print(
        "effective BUY qty="
        f"{format_qty(int(regime_state.get('effective_buy_max_qty_per_trade', 0) or 0))}"
    )
    print(
        "effective reentry cooldown="
        f"{int(regime_state.get('effective_rebuy_cooldown_minutes', 0) or 0)}분"
    )
    print(
        "effective same-symbol limit="
        f"{int(regime_state.get('effective_same_symbol_max_buys_per_day', 0) or 0)}회"
    )
    print(
        "effective daily BUY cap="
        f"{int(regime_state.get('effective_buy_daily_max_order_submissions', 0) or 0)}회"
    )
    print()


def print_premarket_wait_notice(session_status) -> None:
    print("=== 장전 자동 대기 ===")
    print("동작 모드: 경량 대기")
    if session_status.session == "PREMARKET":
        print("사유: 정규장 시작 전이므로 주문 없이 대기합니다.")
        print("정규장 시작 전 대기 중입니다. 09:00 이후 자동으로 운영을 시작합니다.")
    elif session_status.session == "AFTER_MARKET":
        print("사유: 장후 세션에서는 자동매매 주문을 보내지 않습니다.")
        print("정규장이 종료되어 주문 없이 경량 대기 중입니다. 다음 정규장에 자동으로 운영을 재개합니다.")
    else:
        print("사유: 정규장 및 장후 세션이 아니므로 주문 없이 대기합니다.")
        print("현재 휴장/비거래 시간대라 경량 대기 중입니다. 다음 정규장에 자동으로 운영을 재개합니다.")
    print()


def should_run_light_session_cycle(*, settings, session_status) -> bool:
    if settings.run_mode != "trade":
        return False
    if session_status.session == "PREMARKET":
        return settings.enable_premarket_wait
    return session_status.session in {"AFTER_MARKET", "CLOSED"}
