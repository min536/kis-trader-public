import json
from datetime import datetime, timedelta
from pathlib import Path

from app.auth.account_scope import get_account_scope_context
from app.core.file_read_limits import LocalReadLimitError, read_text_bounded
from app.core.formatters import format_krw, format_signed_krw, format_signed_pct
from app.core.time_utils import get_korean_now
from app.portfolio.equity_state import build_daily_pnl_state
from app.notifications.runtime_alerts import get_slack_notifier
from app.notifications.slack import RISK_BRAKE_EVENT_TYPE
from app.runtime_state import get_runtime_state_path, parse_recent_order_time


def _notify_brake_escalation(message: str) -> None:
    """Best-effort operator Slack alert for brake escalation; never raises.

    Replaces the retired telegram path (2026-07-19) — telegram was a silent
    no-op with no token configured, leaving escalations with no live channel.
    """
    try:
        get_slack_notifier().notify(RISK_BRAKE_EVENT_TYPE, message, symbol=None, details=None)
    except Exception as exc:
        print(f"[warn] pnl_brake_slack_notify_failed: {exc}")


def daily_pnl_brake_display_status(brake_state: dict[str, object] | None) -> str:
    status = str((brake_state or {}).get("status") or "DATA_INSUFFICIENT")
    if status == "NORMAL":
        return "OK"
    if status == "HARD_STOP":
        return "HARD_STOP_READY"
    return status


def clear_daily_pnl_pause_if_expired(state: dict) -> None:
    pause_until = str(state.get("daily_pnl_pause_until") or "").strip()
    if not pause_until:
        return
    pause_dt = parse_recent_order_time({"timestamp": pause_until})
    if pause_dt is None:
        state["daily_pnl_pause_until"] = None
        state["daily_pnl_pause_state"] = None
        state["daily_pnl_pause_reason"] = None
        return
    if get_korean_now() >= pause_dt:
        state["daily_pnl_pause_until"] = None
        state["daily_pnl_pause_state"] = None
        state["daily_pnl_pause_reason"] = None


def manual_buy_pause_override_path() -> Path:
    runtime_state_path = get_runtime_state_path()
    override_name = runtime_state_path.name.replace(
        "runtime_state_",
        "manual_buy_pause_override_",
        1,
    )
    if override_name == runtime_state_path.name:
        override_name = "manual_buy_pause_override.json"
    return runtime_state_path.with_name(override_name)


def is_manual_buy_pause_override_active() -> bool:
    path = manual_buy_pause_override_path()
    if not path.exists():
        return False
    try:
        payload = json.loads(read_text_bounded(path, encoding="utf-8"))
    except (LocalReadLimitError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    if not bool(payload.get("enabled", True)):
        return False
    expires_at = str(payload.get("expires_at") or "").strip()
    if not expires_at:
        return True
    try:
        expiry = datetime.fromisoformat(expires_at)
    except ValueError:
        return False
    return get_korean_now() < expiry


def build_daily_pnl_brake_state(
    *,
    state: dict,
    settings,
    current_equity_krw: int,
    operating_equity_krw: int,
) -> dict[str, object]:
    if not settings.enable_daily_pnl_brake:
        state["current_brake_state"] = "OFF"
        return {
            "evaluated": False,
            "status": "OFF",
            "daily_pnl_pct": None,
            "baseline_equity_krw": None,
            "reason": "ENABLE_DAILY_PNL_BRAKE=false 라서 일중 손실 브레이크를 평가하지 않습니다.",
            "buy_paused": False,
            "pause_state": None,
            "pause_reason": None,
            "pause_until": None,
            "remaining_minutes": 0,
            "action": None,
        }

    clear_daily_pnl_pause_if_expired(state)
    pnl_state = build_daily_pnl_state(
        current_equity_krw=current_equity_krw,
        warning_pct=settings.daily_pnl_warning_pct,
        buy_pause_pct=settings.daily_pnl_buy_pause_pct,
        hard_stop_pct=settings.daily_pnl_hard_stop_pct,
    )

    if (
        is_manual_buy_pause_override_active()
        and str(pnl_state.get("status") or "").strip().upper() == "BUY_PAUSE"
    ):
        pnl_state = {
            **pnl_state,
            "status": "NORMAL",
            "manual_buy_pause_override": True,
            "daily_pnl_pct_raw": pnl_state.get("daily_pnl_pct"),
            "reason": (
                "수동 오버라이드가 활성화되어 BUY_PAUSE를 해제했습니다. "
                "일중 손실률 관찰은 계속 유지합니다."
            ),
        }
        state["daily_pnl_pause_until"] = None
        state["daily_pnl_pause_state"] = None
        state["daily_pnl_pause_reason"] = None

    # Cross-validate: if deployment_invariant shows HARD_STOP/BUY_PAUSE but
    # operating_equity (cash_orderable + holdings) doesn't confirm the same loss,
    # the signal is a cash-settlement timing artifact. This happens when the session
    # baseline was captured right after a sell that temporarily inflated cash_orderable,
    # so subsequent cycles show a lower deployment_invariant value even though the
    # actual account has no real loss. Use the operating-equity reading instead.
    deploy_status = str(pnl_state.get("status") or "DATA_INSUFFICIENT")
    brake_suppressed_by_cross_validation = False
    baseline_abnormal_warning = None
    baseline = int(pnl_state.get("baseline_equity_krw") or 0)
    if baseline > 0 and operating_equity_krw > 0:
        baseline_vs_operating_gap_pct = ((baseline / operating_equity_krw) - 1.0) * 100
        if baseline_vs_operating_gap_pct > 5.0:
            baseline_abnormal_warning = (
                "daily_pnl baseline이 운영 equity보다 "
                f"{baseline_vs_operating_gap_pct:.2f}% 높습니다."
            )
            if not state.get("daily_pnl_baseline_abnormal_warned"):
                print(
                    "[brake] baseline anomaly warning: "
                    f"baseline={baseline:,} operating_equity={operating_equity_krw:,} "
                    f"gap={baseline_vs_operating_gap_pct:.2f}%"
                )
                state["daily_pnl_baseline_abnormal_warned"] = True
            pnl_state = {
                **pnl_state,
                "baseline_abnormal_warning": baseline_abnormal_warning,
                "baseline_vs_operating_gap_pct": round(baseline_vs_operating_gap_pct, 2),
            }
    if deploy_status in {"HARD_STOP", "BUY_PAUSE"} and pnl_state.get("baseline_equity_krw"):
        baseline = int(pnl_state["baseline_equity_krw"])
        op_pnl_pct = ((operating_equity_krw / baseline) - 1.0) * 100
        if op_pnl_pct <= settings.daily_pnl_hard_stop_pct:
            op_status = "HARD_STOP"
        elif op_pnl_pct <= settings.daily_pnl_buy_pause_pct:
            op_status = "BUY_PAUSE"
        elif op_pnl_pct <= settings.daily_pnl_warning_pct:
            op_status = "WARNING"
        else:
            op_status = "NORMAL"
        if op_status not in {"HARD_STOP", "BUY_PAUSE"}:
            print(
                f"[brake] cross-validate suppressed {deploy_status}: "
                f"deploy_invariant_pnl={pnl_state.get('daily_pnl_pct')}% "
                f"operating_pnl={op_pnl_pct:.2f}%"
            )
            brake_suppressed_by_cross_validation = True
            pnl_state = {
                **pnl_state,
                "status": op_status,
                "daily_pnl_pct": round(op_pnl_pct, 2),
                "operating_equity_pnl_pct": round(op_pnl_pct, 2),
                "deployment_invariant_pnl_pct": pnl_state.get("daily_pnl_pct"),
                "brake_suppressed_by_cross_validation": True,
                "reason": (
                    f"운영 equity({operating_equity_krw:,}원) 기준 일중 손실률 "
                    f"{op_pnl_pct:.2f}%로 brake 미해당 — "
                    f"배포불변 equity의 {deploy_status} 신호는 현금 결제 시차 오인으로 억제."
                ),
            }

    now = get_korean_now()
    pause_until_text = str(state.get("daily_pnl_pause_until") or "").strip()
    pause_until = parse_recent_order_time({"timestamp": pause_until_text}) if pause_until_text else None
    active_pause = pause_until is not None and now < pause_until
    pause_state = str(state.get("daily_pnl_pause_state") or "").strip() or None
    pause_reason = str(state.get("daily_pnl_pause_reason") or "").strip() or None
    if brake_suppressed_by_cross_validation and pause_state in {"BUY_PAUSE", "HARD_STOP"}:
        state["daily_pnl_pause_until"] = None
        state["daily_pnl_pause_state"] = None
        state["daily_pnl_pause_reason"] = None
        active_pause = False
        pause_until = None
        pause_state = None
        pause_reason = None

    status = str(pnl_state.get("status") or "DATA_INSUFFICIENT")
    display_status = (
        "OK"
        if status == "NORMAL"
        else ("HARD_STOP_READY" if status == "HARD_STOP" else status)
    )
    state["intraday_pnl_baseline_krw"] = pnl_state.get("baseline_equity_krw")
    state["intraday_pnl_current_equity_krw"] = pnl_state.get("current_equity_krw")
    state["intraday_pnl_pct"] = pnl_state.get("daily_pnl_pct")
    state["current_brake_state"] = display_status
    if status in {"BUY_PAUSE", "HARD_STOP"}:
        pause_until = now + timedelta(minutes=settings.daily_pnl_cooldown_minutes)
        state["daily_pnl_pause_until"] = pause_until.isoformat()
        state["daily_pnl_pause_state"] = status
        state["daily_pnl_pause_reason"] = str(pnl_state.get("reason") or "")
        active_pause = True
        pause_state = status
        pause_reason = str(pnl_state.get("reason") or "")

    remaining_minutes = 0
    if active_pause and pause_until is not None:
        remaining_minutes = max(
            0,
            int((pause_until - now).total_seconds() // 60),
        )

    last_notified_pnl_state = state.get("last_notified_pnl_state")
    if status in {"WARNING", "BUY_PAUSE", "HARD_STOP"}:
        status_rank = {"OFF": 0, "DATA_INSUFFICIENT": 0, "NORMAL": 1, "WARNING": 2, "BUY_PAUSE": 3, "HARD_STOP": 4}
        current_rank = status_rank.get(status, 0)
        last_rank = status_rank.get(str(last_notified_pnl_state), 0)

        if current_rank > last_rank:
            state["last_notified_pnl_state"] = status
            prefix = get_account_scope_context().get("account_signature", "unknown_account")
            pnl_pct_text = f"{float(pnl_state.get('daily_pnl_pct') or 0.0):.2f}%"
            _notify_brake_escalation(f"🛑 [{prefix}] Daily PNL Brake escalated to {status} | Current PNL: {pnl_pct_text}")
    elif status in {"NORMAL", "OFF"}:
        state["last_notified_pnl_state"] = status

    return {
        **pnl_state,
        "display_status": display_status,
        "buy_paused": active_pause,
        "pause_state": pause_state,
        "pause_reason": pause_reason,
        "pause_until": pause_until.isoformat() if pause_until is not None else None,
        "remaining_minutes": remaining_minutes,
        "action": (
            "blocked_daily_pnl_hard_stop"
            if pause_state == "HARD_STOP"
            else ("blocked_daily_pnl_pause" if active_pause else None)
        ),
    }


def print_daily_pnl_brake_state(
    *,
    brake_state: dict[str, object],
    current_equity_krw: int,
    settings,
) -> None:
    print("=== 일중 손실 브레이크 상태 ===")
    print(
        "equity | "
        f"baseline={format_krw(int(brake_state.get('baseline_equity_krw') or 0)) if brake_state.get('baseline_equity_krw') else '데이터 부족'} | "
        f"current={format_krw(int(brake_state.get('current_equity_krw') or current_equity_krw))}"
    )
    print(
        "pnl | "
        f"realized={format_signed_krw(int(brake_state.get('realized_pnl_krw', 0) or 0))} | "
        f"unrealized={format_signed_krw(int(brake_state.get('unrealized_pnl_krw', 0) or 0))} | "
        f"daily={format_signed_pct(float(brake_state.get('daily_pnl_pct') or 0.0)) if brake_state.get('daily_pnl_pct') is not None else '데이터 부족'}"
    )
    print(
        "composition | "
        f"cash={format_krw(int(brake_state.get('cash_krw', 0) or 0))} | "
        f"holdings={format_krw(int(brake_state.get('holdings_value_krw', 0) or 0))}"
    )
    print(
        "equity basis: "
        f"{brake_state.get('equity_basis') or 'unknown'} "
        f"(accounting-consistent={'YES' if brake_state.get('current_equity_accounting_consistent') else 'NO'}, "
        f"cash-deployment invariant={'YES' if brake_state.get('current_equity_cash_deployment_invariant') else 'NO'})"
    )
    if brake_state.get("snapshot_warning"):
        print(f"[warn] brake snapshot degraded: {brake_state.get('snapshot_warning')}")
    print(f"daily pnl brake: {daily_pnl_brake_display_status(brake_state)}")
    print(f"신규 BUY 가능 여부: {'NO' if brake_state.get('buy_paused') else 'YES'}")
    if brake_state.get("buy_paused") and brake_state.get("remaining_minutes") is not None:
        print(f"남은 cooldown: {int(brake_state.get('remaining_minutes', 0))}분")
    thresholds = []
    if brake_state.get("status") not in {"OFF", "DATA_INSUFFICIENT"}:
        thresholds.append(f"warning {settings.daily_pnl_warning_pct}%")
        thresholds.append(f"pause {settings.daily_pnl_buy_pause_pct}%")
        thresholds.append(f"hard stop {settings.daily_pnl_hard_stop_pct}%")
    if thresholds:
        print("threshold: " + " / ".join(thresholds))
    print(f"이유: {brake_state.get('reason') or brake_state.get('pause_reason') or '데이터 부족'}")
    print()


def build_daily_pnl_brake_observability(
    *,
    brake_state: dict[str, object],
    account_state_payload: dict[str, int],
    realized_summary: dict[str, object],
    portfolio_snapshot,
) -> dict[str, object]:
    current_equity_krw = int(account_state_payload.get("deployment_invariant_equity_krw", 0) or 0)
    cash_krw = int(account_state_payload.get("deployment_invariant_cash_leg_krw", 0) or 0)
    holdings_value_krw = int(account_state_payload.get("holdings_market_value_krw", 0) or 0)
    realized_pnl_krw = int(realized_summary.get("realized_net_pnl_krw", 0) or 0)
    unrealized_pnl_krw = int(account_state_payload.get("total_unrealized_net_pnl_krw", 0) or 0)

    warnings: list[str] = []
    if portfolio_snapshot is None:
        warnings.append("잔고 snapshot이 없어 브레이크 계좌 상태를 검증하지 못했습니다.")
    else:
        if int(portfolio_snapshot.total_evaluation_amount or 0) <= 0:
            warnings.append("잔고 raw 총평가금액이 0원 이하라 snapshot이 불완전할 수 있습니다.")
        if portfolio_snapshot.position_count > 0 and holdings_value_krw <= 0:
            warnings.append("보유 종목이 있는데 holdings_value가 0원이라 snapshot이 degraded 상태입니다.")
        if current_equity_krw <= 0:
            warnings.append("current_equity가 0원 이하라 브레이크 계산 근거가 약합니다.")
    if cash_krw + holdings_value_krw != current_equity_krw:
        warnings.append("current_equity와 cash/holdings 합이 일치하지 않습니다.")

    accounting_consistent = not warnings and current_equity_krw > 0
    return {
        "baseline_equity_krw": brake_state.get("baseline_equity_krw"),
        "current_equity_krw": current_equity_krw,
        "realized_pnl_krw": realized_pnl_krw,
        "unrealized_pnl_krw": unrealized_pnl_krw,
        "cash_krw": cash_krw,
        "holdings_value_krw": holdings_value_krw,
        "daily_pnl_pct": brake_state.get("daily_pnl_pct"),
        "brake_state": brake_state.get("display_status") or brake_state.get("status"),
        "reason": brake_state.get("reason") or brake_state.get("pause_reason"),
        "current_equity_accounting_consistent": accounting_consistent,
        "current_equity_cash_deployment_invariant": bool(
            brake_state.get("cash_deployment_invariant", True)
        ),
        "snapshot_warning": " | ".join(warnings) if warnings else None,
        "snapshot_degraded": bool(warnings),
    }
