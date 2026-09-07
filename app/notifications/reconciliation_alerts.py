"""Operator alert for suspected external / manual broker trades.

When ``sync_reconciliation_state`` detects drift that looks like an *external
manual trade* (a ``suspected_manual_*`` classification — a position that appeared,
vanished, or shrank with no matching local order trace), the operator needs a
single, distinct Slack signal rather than another line buried in the cycle stream.

Design (gate C5a, ``docs/manual_trade_reconciliation_design_20260704.md`` §4): the
ENTIRE function body — message construction AND the notify call — is wrapped in one
``try/except Exception → return False``. The call site is inside the
account-snapshot phase's outer ``try`` boundary; if this helper raised, a normal
cycle would re-raise and a ``scan_only`` run would mis-fire its diagnostic
fallback. A malformed report (missing keys, non-dict events) is therefore a silent
no-op, never an exception.

No latch: drift events are naturally one-shot (the reconciliation baseline is
advanced to actual on every sync, so the same drift does not re-fire next cycle).

This module imports NO broker / order API (observation-only, gate C5).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.notifications.slack import RECONCILIATION_EVENT_TYPE

_MANUAL_TRADE_SUSPECT_CLASSES = {
    "suspected_manual_buy",
    "suspected_manual_sell",
    "suspected_manual_partial_sell",
}

_CLASSIFICATION_LABELS = {
    "suspected_manual_buy": "수동 매수 의심",
    "suspected_manual_sell": "수동 매도 의심",
    "suspected_manual_partial_sell": "수동 부분매도 의심",
}


def maybe_notify_manual_trade_suspects(
    report: Any,
    *,
    notify: Callable[..., Any],
) -> bool:
    """Emit one operator alert if the report holds suspected-manual-trade events.

    Returns ``True`` iff an alert was emitted. Never raises: a malformed report
    or a failing notifier both return ``False`` (gate C5a).
    """

    try:
        events = report.get("events") or []
        suspect_lines: list[str] = []
        suspect_count = 0
        for event in events:
            if not isinstance(event, dict):
                continue
            classification = str(event.get("classification") or "")
            if classification not in _MANUAL_TRADE_SUSPECT_CLASSES:
                continue
            symbol = str(event.get("symbol") or "-")
            expected_qty = int(event.get("expected_qty", 0) or 0)
            actual_qty = int(event.get("actual_qty", 0) or 0)
            label = _CLASSIFICATION_LABELS.get(classification, classification)
            suspect_lines.append(
                f"• {symbol} | {label} | {expected_qty}→{actual_qty}"
            )
            suspect_count += 1
        if not suspect_lines:
            return False
        message = (
            f"🔎 외부/수동 매매 의심 {suspect_count}건 감지\n"
            + "\n".join(suspect_lines)
            + "\n조치: 브로커 체결 내역과 대조해 수동 매매 여부를 확인하세요."
        )
        details: dict[str, object] = {
            "suspect_count": suspect_count,
            "alert_class": "reconciliation_drift",
        }
        notify(
            RECONCILIATION_EVENT_TYPE,
            message,
            symbol=None,
            details=details,
        )
        return True
    except Exception:
        return False
