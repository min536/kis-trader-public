"""Free-form operator warnings for lane-pipeline operational stalls.

The order-event Slack path (``_send_order_slack_notification``) is strictly
order-shaped, so operational warnings — e.g. the OrderGate refusing intents
while a detached (timed-out) order handler is still running — need their own
sender. Delivery failures are swallowed: a Slack outage must never break the
trading cycle.
"""

from __future__ import annotations

from typing import Any

ORDER_GATE_BLOCKED_EVENT_TYPE = "order_gate_blocked"


def send_detached_handler_warning(
    *,
    reason: str,
    blocked_count: int,
    notifier: Any | None = None,
) -> bool:
    """Warn the operator that this cycle's order intents were blocked.

    ``reason`` is the gate decision reason naming the in-flight handler
    (e.g. ``detached order handler still running: SELL:000660``).
    Returns True only when the notifier reports the message as sent.
    """
    try:
        if notifier is None:
            from app.notifications.runtime_alerts import get_slack_notifier

            notifier = get_slack_notifier()
        result = notifier.send(
            ORDER_GATE_BLOCKED_EVENT_TYPE,
            (
                "OrderGate가 이번 사이클 주문 인텐트를 보류했습니다 — "
                f"{reason} (blocked_count={int(blocked_count)}). "
                "이전 사이클에서 타임아웃된 주문 핸들러가 아직 실행 중입니다."
            ),
            details={
                "blocked_count": int(blocked_count),
                "reason": str(reason),
            },
        )
        return str(getattr(result, "status", "") or "") == "sent"
    except Exception:
        return False
