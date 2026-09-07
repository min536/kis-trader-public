from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OrderSlackNotificationPayload:
    message: str
    symbol: str | None
    details: dict[str, object]


def slack_event_type_for_order_action(action: str) -> str | None:
    normalized = str(action or "").strip()
    if normalized in {"order_submitted", "sell_order_submitted"}:
        return "order_submitted"
    if normalized in {"order_succeeded", "sell_order_succeeded"}:
        return "order_accepted"
    if normalized in {"order_failed", "sell_order_failed"}:
        return "order_rejected"
    return None


def build_order_slack_notification_payload(
    *,
    side: str,
    action: str,
    symbol: str,
    qty: int,
    status: str,
    reason: str | None = None,
    submitted_price_krw: int | None = None,
) -> OrderSlackNotificationPayload:
    side_text = str(side or "").strip().upper() or "ORDER"
    status_text = str(status or "").strip() or str(action or "").strip()
    details: dict[str, object] = {
        "side": side_text,
        "quantity": int(qty or 0),
        "status": status_text,
    }
    if submitted_price_krw is not None:
        details["submitted_price_krw"] = int(submitted_price_krw)
    reason_text = str(reason or "").strip()
    if reason_text:
        details["reason"] = reason_text

    return OrderSlackNotificationPayload(
        message=f"{side_text} order {status_text}",
        symbol=str(symbol or "").strip() or None,
        details=details,
    )
