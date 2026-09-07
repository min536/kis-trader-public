"""Reconciliation helpers: compare expected vs actual broker positions."""
from __future__ import annotations

from app.core.time_utils import get_korean_now
from app.runtime_state import (
    INTENT_ADJUSTMENTS_CAP,
    SELL_INTENT_EMERGENCY_TTL_MINUTES,
    SELL_INTENT_TTL_MINUTES,
    parse_recent_order_time,
    prune_stale_sell_intents,
    record_symbol_exit,
)


def build_positions_qty_map(portfolio_snapshot) -> dict[str, int]:
    if portfolio_snapshot is None:
        return {}
    return {
        position.symbol: int(position.holding_qty)
        for position in portfolio_snapshot.held_positions
        if str(position.symbol).strip()
    }


def _classify_event(event_type: str, *, expected_qty: int, actual_qty: int) -> str:
    """Map a drift event type to an external-manual-trade suspicion class (additive)."""
    if event_type == "unexpected_new_position":
        return "suspected_manual_buy"
    if event_type == "unexpected_missing_position":
        return "suspected_manual_sell"
    if event_type == "unexpected_quantity_difference":
        if actual_qty < expected_qty:
            return "suspected_manual_partial_sell"
        return "suspected_manual_buy"
    return "unexplained"


def build_reconciliation_report(*, state: dict, portfolio_snapshot) -> dict[str, object]:
    actual_positions = build_positions_qty_map(portfolio_snapshot)
    expected_positions = {
        str(symbol).strip(): int(qty or 0)
        for symbol, qty in (state.get("broker_last_synced_positions_by_symbol") or {}).items()
        if str(symbol).strip()
    }
    pending_intents_raw = state.get("pending_sell_intents_by_symbol") or {}
    pending_intents = {
        str(symbol).strip(): int(
            (entry.get("qty", 0) if isinstance(entry, dict) else entry) or 0
        )
        for symbol, entry in pending_intents_raw.items()
        if str(symbol).strip()
    }
    last_synced_at = str(state.get("broker_last_synced_at") or "").strip()
    since_time = parse_recent_order_time({"timestamp": last_synced_at}) if last_synced_at else None
    local_order_activity: set[str] = set()
    for order in state.get("recent_orders", []):
        order_time = parse_recent_order_time(order)
        if since_time is not None and order_time is not None and order_time <= since_time:
            continue
        action = str(order.get("action", "")).strip()
        if action not in {
            "order_submitted",
            "order_failed",
            "sell_order_submitted",
            "sell_order_failed",
            "sell_order_succeeded",
        }:
            continue
        symbol = str(order.get("symbol", "")).strip()
        if symbol:
            local_order_activity.add(symbol)

    events: list[dict[str, object]] = []
    if expected_positions:
        for symbol in sorted(set(expected_positions) | set(actual_positions)):
            expected_qty = int(expected_positions.get(symbol, 0) or 0)
            actual_qty = int(actual_positions.get(symbol, 0) or 0)
            if actual_qty == expected_qty:
                continue
            if symbol in local_order_activity:
                continue
            if expected_qty <= 0 < actual_qty:
                events.append(
                    {
                        "type": "unexpected_new_position",
                        "symbol": symbol,
                        "expected_qty": expected_qty,
                        "actual_qty": actual_qty,
                        "reason": "최근 로컬 주문 흔적 없이 신규 포지션이 나타났습니다.",
                    }
                )
            elif expected_qty > 0 and actual_qty <= 0:
                events.append(
                    {
                        "type": "unexpected_missing_position",
                        "symbol": symbol,
                        "expected_qty": expected_qty,
                        "actual_qty": actual_qty,
                        "reason": "최근 로컬 주문 흔적 없이 기존 포지션이 사라졌습니다.",
                    }
                )
            else:
                events.append(
                    {
                        "type": "unexpected_quantity_difference",
                        "symbol": symbol,
                        "expected_qty": expected_qty,
                        "actual_qty": actual_qty,
                        "reason": "최근 로컬 주문 흔적 없이 보유 수량 차이가 발생했습니다.",
                    }
                )

    for symbol, pending_qty in pending_intents.items():
        actual_qty = int(actual_positions.get(symbol, 0) or 0)
        if pending_qty > actual_qty:
            events.append(
                {
                    "type": "sell_intent_exceeds_actual_qty",
                    "symbol": symbol,
                    "expected_qty": pending_qty,
                    "actual_qty": actual_qty,
                    "reason": "내부 SELL intent 예약 수량이 실제 보유 수량보다 큽니다.",
                }
            )

    detected_at = get_korean_now().isoformat()
    for event in events:
        event_type = str(event.get("type") or "")
        event["classification"] = _classify_event(
            event_type,
            expected_qty=int(event.get("expected_qty", 0) or 0),
            actual_qty=int(event.get("actual_qty", 0) or 0),
        )
        event["detected_at"] = detected_at

    if not expected_positions:
        summary = "첫 broker sync라 reconciliation baseline만 초기화합니다."
    elif not events:
        summary = "expected state와 actual broker state 사이의 뚜렷한 drift가 없습니다."
    else:
        summary = f"reconciliation event {len(events)}건 감지"
    return {
        "expected_positions_by_symbol": expected_positions,
        "actual_positions_by_symbol": actual_positions,
        "pending_sell_intents_by_symbol": pending_intents,
        "events": events,
        "summary": summary,
        "event_count": len(events),
    }


def sync_reconciliation_state(*, state: dict, portfolio_snapshot) -> dict[str, object]:
    report = build_reconciliation_report(state=state, portfolio_snapshot=portfolio_snapshot)
    actual_positions = dict(report.get("actual_positions_by_symbol") or {})
    previous_positions = dict(report.get("expected_positions_by_symbol") or {})
    pending = state.setdefault("pending_sell_intents_by_symbol", {})
    adjustments: list[dict[str, object]] = []
    now = get_korean_now()
    adjust_detected_at = now.isoformat()
    for symbol in list(pending.keys()):
        entry = pending.get(symbol)
        pending_qty = int((entry.get("qty", 0) if isinstance(entry, dict) else entry) or 0)
        current_qty = int(actual_positions.get(symbol, 0) or 0)
        previous_qty = int(previous_positions.get(symbol, current_qty) or 0)
        before_qty = pending_qty  # snapshot for audit; arithmetic below unchanged
        if current_qty <= 0:
            pending.pop(symbol, None)
            _append_intent_adjustment(
                adjustments, symbol=symbol, before_qty=before_qty,
                after_qty=0, cause="position_gone", detected_at=adjust_detected_at,
            )
            continue
        if previous_qty > current_qty:
            pending_qty = max(0, pending_qty - (previous_qty - current_qty))
            _append_intent_adjustment(
                adjustments, symbol=symbol, before_qty=before_qty,
                after_qty=pending_qty, cause="position_shrunk",
                detected_at=adjust_detected_at,
            )
        pre_clamp_qty = pending_qty
        pending_qty = min(pending_qty, current_qty)
        if pending_qty != pre_clamp_qty:
            _append_intent_adjustment(
                adjustments, symbol=symbol, before_qty=pre_clamp_qty,
                after_qty=pending_qty, cause="clamped_to_actual",
                detected_at=adjust_detected_at,
            )
        if pending_qty <= 0:
            pending.pop(symbol, None)
            _append_intent_adjustment(
                adjustments, symbol=symbol, before_qty=pre_clamp_qty,
                after_qty=0, cause="drained", detected_at=adjust_detected_at,
            )
            continue
        submitted_at = (
            str(entry.get("submitted_at") or "").strip() if isinstance(entry, dict) else ""
        ) or None
        rewritten: dict = {"qty": pending_qty, "submitted_at": submitted_at}
        # Carry the trigger across the rewrite — dropping it downgrades an
        # emergency (stop_loss) intent to the slow default TTL next cycle.
        trigger = (
            str(entry.get("trigger") or "").strip() if isinstance(entry, dict) else ""
        )
        if trigger:
            rewritten["trigger"] = trigger
        pending[symbol] = rewritten

    # Anything still reserved after the broker-truth pass above is unexplained by
    # any position change. Age it out so a failed submit cannot pin the position
    # for the rest of the session (docs/todo_20260710.md §A-3).
    adjustments.extend(
        {
            "symbol": released["symbol"],
            "before_qty": released["qty"],
            "after_qty": 0,
            "cause": released["cause"],
            "detected_at": released["detected_at"],
            "age_minutes": released["age_minutes"],
            "ttl_minutes": released["ttl_minutes"],
        }
        for released in prune_stale_sell_intents(
            state,
            ttl_minutes=SELL_INTENT_TTL_MINUTES,
            emergency_ttl_minutes=SELL_INTENT_EMERGENCY_TTL_MINUTES,
            now=now,
        )
    )

    for event in list(report.get("events") or []):
        if not isinstance(event, dict):
            continue
        symbol = str(event.get("symbol") or "").strip()
        if not symbol:
            continue
        event_type = str(event.get("type") or "").strip()
        expected_qty = int(event.get("expected_qty", 0) or 0)
        actual_qty = int(event.get("actual_qty", 0) or 0)
        if event_type not in {
            "unexpected_missing_position",
            "unexpected_quantity_difference",
        }:
            continue
        if actual_qty >= expected_qty:
            continue
        record_symbol_exit(
            state,
            symbol=symbol,
            qty=max(expected_qty - actual_qty, 0),
            exit_reason="manual_or_reconciled",
            exit_price=None,
            was_full_close=actual_qty <= 0,
            trigger_context={
                "reconciliation_event": event_type,
                "expected_qty": expected_qty,
                "actual_qty": actual_qty,
                "reason": event.get("reason"),
            },
        )

    _update_manual_trade_suspects(state=state, report=report)

    report["intent_adjustments"] = adjustments
    if adjustments:
        stored = state.get("last_intent_adjustments")
        if not isinstance(stored, list):
            stored = []
        stored.extend(adjustments)
        state["last_intent_adjustments"] = stored[-_INTENT_ADJUSTMENTS_CAP:]

    state["broker_last_synced_positions_by_symbol"] = actual_positions
    state["broker_last_synced_at"] = get_korean_now().isoformat()
    state["last_reconciliation_summary"] = report.get("summary")
    state["last_reconciliation_events"] = list(report.get("events") or [])
    return report


_INTENT_ADJUSTMENTS_CAP = INTENT_ADJUSTMENTS_CAP


def _append_intent_adjustment(
    adjustments: list[dict[str, object]],
    *,
    symbol: str,
    before_qty: int,
    after_qty: int,
    cause: str,
    detected_at: str,
) -> None:
    adjustments.append(
        {
            "symbol": symbol,
            "before_qty": int(before_qty),
            "after_qty": int(after_qty),
            "cause": cause,
            "detected_at": detected_at,
        }
    )


_MANUAL_TRADE_SUSPECT_CLASSES = {
    "suspected_manual_buy",
    "suspected_manual_sell",
    "suspected_manual_partial_sell",
}
_MANUAL_TRADE_SUSPECTS_CAP = 30


def _update_manual_trade_suspects(*, state: dict, report: dict[str, object]) -> None:
    """Upsert the bounded manual-trade suspects ledger (additive; §2).

    Only ``suspected_manual_*`` classifications create/refresh an entry. A symbol
    that had a live suspect but no drift event in this sync is marked
    ``resolved_at`` (entry retained). The ledger is capped at 30 entries FIFO by
    ``first_detected_at`` ascending.
    """
    suspects = state.setdefault("manual_trade_suspects_by_symbol", {})
    now_iso = get_korean_now().isoformat()
    active_symbols: set[str] = set()
    for event in list(report.get("events") or []):
        if not isinstance(event, dict):
            continue
        classification = str(event.get("classification") or "")
        if classification not in _MANUAL_TRADE_SUSPECT_CLASSES:
            continue
        symbol = str(event.get("symbol") or "").strip()
        if not symbol:
            continue
        active_symbols.add(symbol)
        detected_at = str(event.get("detected_at") or now_iso)
        existing = suspects.get(symbol)
        if isinstance(existing, dict) and existing.get("resolved_at") is None:
            existing["last_detected_at"] = detected_at
            existing["last_event_type"] = str(event.get("type") or "")
            existing["classification"] = classification
            existing["expected_qty"] = int(event.get("expected_qty", 0) or 0)
            existing["actual_qty"] = int(event.get("actual_qty", 0) or 0)
            existing["occurrences"] = int(existing.get("occurrences", 0) or 0) + 1
        else:
            suspects[symbol] = {
                "first_detected_at": detected_at,
                "last_detected_at": detected_at,
                "last_event_type": str(event.get("type") or ""),
                "classification": classification,
                "expected_qty": int(event.get("expected_qty", 0) or 0),
                "actual_qty": int(event.get("actual_qty", 0) or 0),
                "occurrences": 1,
            }

    for symbol, entry in suspects.items():
        if (
            isinstance(entry, dict)
            and symbol not in active_symbols
            and entry.get("resolved_at") is None
        ):
            entry["resolved_at"] = now_iso

    if len(suspects) > _MANUAL_TRADE_SUSPECTS_CAP:
        ordered = sorted(
            suspects.items(),
            key=lambda item: str(
                (item[1] or {}).get("first_detected_at") or ""
            )
            if isinstance(item[1], dict)
            else "",
        )
        for symbol, _entry in ordered[: len(suspects) - _MANUAL_TRADE_SUSPECTS_CAP]:
            suspects.pop(symbol, None)


def print_reconciliation_report(report: dict[str, object]) -> None:
    print("=== 상태 정합성 점검 ===")
    print(str(report.get("summary") or "reconciliation 정보 없음"))
    events = list(report.get("events") or [])
    if events:
        for event in events[:5]:
            print(
                f"{event.get('symbol')} | {event.get('type')} | "
                f"expected={int(event.get('expected_qty', 0) or 0)} | "
                f"actual={int(event.get('actual_qty', 0) or 0)} | "
                f"{event.get('reason') or '-'}"
            )
        if len(events) > 5:
            print(f"... 외 {len(events) - 5}건")
    adjustments = list(report.get("intent_adjustments") or [])
    if adjustments:
        for adjustment in adjustments[:5]:
            print(
                f"{adjustment.get('symbol')} | intent {adjustment.get('cause')} | "
                f"{int(adjustment.get('before_qty', 0) or 0)}"
                f"→{int(adjustment.get('after_qty', 0) or 0)}"
            )
        if len(adjustments) > 5:
            print(f"... intent 조정 외 {len(adjustments) - 5}건")
    print()
