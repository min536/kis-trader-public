"""One-shot operator alert for fatal account-level order rejections.

When KIS rejects an order because the *account itself* can no longer place
orders (e.g. ``40910000`` "모의투자 주문이 불가한 계좌입니다"), the operator
needs an immediate, distinct signal — not another generic order-rejected line
buried in the stream. This module emits exactly one ``account.blocked`` event
per ``(account_signature, "fatal_account")`` per process, then latches.

Design (gate condition C5): the latch is **process-local** — a module-level
dict, NOT persisted into runtime_state. A restart re-arms the alert by design,
so the operator is re-notified on the next session if the account is still
blocked. ``reset_fatal_account_latch()`` exists for test isolation.

The emitter swallows its own exceptions and never raises: a bug here can never
crash the order-failure branch it hangs off. See
``docs/eod_lane_account_incident_20260704.md`` §4 for the operator playbook.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


# Process-local one-shot latch keyed by (account_signature, alert_class).
# NOT persisted in runtime_state — a restart re-arms by design (C5).
_FATAL_ACCOUNT_ALERT_LATCH: set[tuple[str | None, str]] = set()

_ACCOUNT_BLOCKED_EVENT_TYPE = "account.blocked"

# Separate process-local latch for the order-log-integrity alert (§O1). Keyed by
# account_signature only — a persistent integrity failure blocks ALL buys for the
# whole session, so one alert per process is enough; a restart re-arms it.
_ORDER_LOG_INTEGRITY_ALERT_LATCH: set[str | None] = set()

_ORDER_LOG_UNTRUSTED_EVENT_TYPE = "order_log.untrusted"


def reset_fatal_account_latch() -> None:
    """Clear the process-local latch (test isolation)."""

    _FATAL_ACCOUNT_ALERT_LATCH.clear()


def reset_order_log_integrity_latch() -> None:
    """Clear the order-log-integrity latch (test isolation)."""

    _ORDER_LOG_INTEGRITY_ALERT_LATCH.clear()


def maybe_emit_order_log_integrity_alert(
    *,
    error_code: str | None,
    side: str,
    account_signature: str | None,
    notify: Callable[..., Any],
) -> bool:
    """Emit a one-shot ``order_log.untrusted`` operator alert.

    Fires when the order-log integrity guard fail-closed-blocks orders because the
    log can't be trusted (e.g. an oversize line). This is a distinct, session-
    deduped signal so the operator can act instead of the failure hiding inside a
    generic guard-blocked count. Returns ``True`` iff this call emitted the alert;
    ``False`` if already latched or the emit failed. Never raises.
    """

    try:
        if account_signature in _ORDER_LOG_INTEGRITY_ALERT_LATCH:
            return False
        message = _build_order_log_untrusted_message(
            side=side,
            error_code=error_code,
        )
        details: dict[str, object] = {
            "side": str(side or "").upper() or "-",
            "account_signature": account_signature or "-",
            "order_log_error_code": error_code or "unknown",
            "alert_class": "order_log_untrusted",
        }
        notify(
            _ORDER_LOG_UNTRUSTED_EVENT_TYPE,
            message,
            details=details,
        )
        # Latch only AFTER a successful emit so a transient notifier failure does
        # not permanently suppress the operator alert.
        _ORDER_LOG_INTEGRITY_ALERT_LATCH.add(account_signature)
        return True
    except Exception:
        return False


def _build_order_log_untrusted_message(
    *,
    side: str,
    error_code: str | None,
) -> str:
    side_label = str(side or "").upper() or "-"
    code_label = error_code or "unknown"
    return (
        f"🚨 주문 로그 무결성 실패 — {side_label} 주문이 fail-closed로 전면 차단 중 "
        f"(사유: {code_label})\n"
        "리스크 가드가 오늘 주문 이력을 신뢰할 수 없어 모든 신규 주문을 막고 있습니다.\n"
        "조치: 주문 로그 라인 비대/손상 확인 → 세션 재시작으로 판독 복구. "
        "(docs/order_log_line_limit_design_20260707.md §O1)"
    )


def maybe_emit_fatal_account_alert(
    *,
    reason: str | None,
    side: str,
    symbol: str | None,
    account_signature: str | None,
    notify: Callable[..., Any],
) -> bool:
    """Emit a one-shot ``account.blocked`` operator alert.

    Returns ``True`` iff this call emitted the alert (first time for this
    ``(account_signature, "fatal_account")`` key in this process); ``False`` if
    already latched or if the emit failed. Never raises.
    """

    try:
        latch_key = (account_signature, "fatal_account")
        if latch_key in _FATAL_ACCOUNT_ALERT_LATCH:
            return False
        message = _build_account_blocked_message(
            side=side,
            symbol=symbol,
            reason=reason,
        )
        details: dict[str, object] = {
            "side": str(side or "").upper() or "-",
            "symbol": symbol or "-",
            "account_signature": account_signature or "-",
            "reason": reason or "-",
            "alert_class": "fatal_account",
        }
        notify(
            _ACCOUNT_BLOCKED_EVENT_TYPE,
            message,
            symbol=symbol,
            details=details,
        )
        # Latch only AFTER a successful emit so a transient notifier failure
        # does not permanently suppress the operator alert.
        _FATAL_ACCOUNT_ALERT_LATCH.add(latch_key)
        return True
    except Exception:
        return False


def _build_account_blocked_message(
    *,
    side: str,
    symbol: str | None,
    reason: str | None,
) -> str:
    side_label = str(side or "").upper() or "-"
    symbol_label = symbol or "-"
    reason_label = reason or "-"
    return (
        f"🚫 계좌 주문 불가 감지 | {side_label} {symbol_label} | 사유: {reason_label}\n"
        "모의투자 계좌가 주문을 낼 수 없는 상태입니다 "
        "(mock account cannot place orders).\n"
        "조치: KIS 모의투자 참가 상태 확인/재신청 → 새 계좌 발급 시 자격증명 갱신 → "
        "세션 재시작. "
        "(docs/eod_lane_account_incident_20260704.md §4)"
    )
