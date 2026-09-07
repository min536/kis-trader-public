import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from app.auth.account_scope import (
    get_account_scope_context,
    get_legacy_account_signature,
    get_runtime_state_path,
    get_runtime_state_read_paths,
)
from app.core.file_read_limits import LocalReadLimitError, read_text_bounded
from app.core.time_utils import KOREA_TZ, get_korean_now

# Triggers whose SELL must not sit reserved for long — a stop_loss retry blocked
# behind a stale intent is the expensive failure mode (docs/todo_20260710.md §A-3).
# Defined here (not in app.execution.order_guard) because order_guard imports this
# module; keeping the constant upstream avoids an import cycle.
SELL_EMERGENCY_TRIGGERS: frozenset[str] = frozenset({"stop_loss"})


def _today_text() -> str:
    return get_korean_now().date().isoformat()


def _default_state(
    *,
    trusted: bool = True,
    safety_reason: str | None = None,
    previous_account_signature: str | None = None,
) -> dict[str, Any]:
    account_scope = get_account_scope_context()
    return {
        "account_signature": account_scope["account_signature"],
        "account_environment": account_scope["account_environment"],
        "masked_account_display": account_scope["masked_account_display"],
        "account_scope_changed": previous_account_signature is not None,
        "previous_account_signature": previous_account_signature,
        "runtime_state_trusted": trusted,
        "runtime_state_safety_status": "ok" if trusted else "blocked",
        "runtime_state_safety_reason": safety_reason,
        "order_submission_blocked_by_runtime_state": not trusted,
        "trading_date": _today_text(),
        "last_cycle_id": None,
        "last_cycle_started_at": None,
        "last_cycle_result": None,
        "last_cycle_elapsed_ms": None,
        "last_final_action": None,
        "last_final_reason": None,
        "last_warning_count": 0,
        "last_error_count": 0,
        "last_snapshot_write_ok": None,
        "last_performance_write_ok": None,
        "last_snapshot_write_failed_at": None,
        "last_performance_write_failed_at": None,
        "snapshot_write_failures_today": 0,
        "performance_write_failures_today": 0,
        "last_action": None,
        "last_buy_symbol": None,
        "last_buy_qty": 0,
        "last_sell_symbol": None,
        "last_sell_qty": 0,
        "last_decision_reason": None,
        "last_order_side": None,
        "last_order_date": None,
        "last_selected_symbol": None,
        "last_buy_attempt_signature": None,
        "last_buy_attempt_at": None,
        "last_sell_attempt_signature": None,
        "last_sell_attempt_at": None,
        "last_buy_quote_prefetch_deadline_hit": False,
        "last_buy_quote_prefetch_success_ratio": 0.0,
        "buy_quote_prefetch_request_timeout_seconds": None,
        "buy_quote_prefetch_max_attempts": None,
        "buy_quote_prefetch_timeout_count": 0,
        "buy_quote_prefetch_budget_skipped": 0,
        "buy_quote_prefetch_worker_detached": False,
        "buy_quote_prefetch_cleanup_nonblocking": False,
        "buy_quote_prefetch_future_done": True,
        "buy_scan_guard_released": False,
        "buy_scan_guard_release_reason": None,
        "lane_scheduler_enabled": False,
        "sell_lane_running": False,
        "buy_lane_running": False,
        "buy_lane_previous_scan_id": None,
        "buy_lane_previous_started_at": None,
        "order_gate_queue_depth": 0,
        "order_gate_processed_count": 0,
        "order_gate_last_decision": None,
        "order_gate_last_intent_type": None,
        "order_gate_last_symbol": None,
        "order_gate_last_skip_reason": None,
        "quote_age_max_ms": None,
        "quote_age_avg_ms": None,
        "suspected_shared_rate_bucket": False,
        "cycle_budget_exceeded": False,
        "cycle_budget_ms": None,
        "budget_exceeded_stage": None,
        "last_sell_check_at": None,
        "last_buy_scan_at": None,
        "buy_scan_profile_cursor": 0,
        "buy_scan_last_profile": None,
        "buy_scan_rotating_cursor": 0,
        "buy_scan_exploration_cursor": 0,
        "recent_market_snapshots_by_symbol": {},
        "sell_watch_next_start_index": 0,
        "sell_watch_retry_symbol": None,
        "last_sell_watch_cursor_before": 0,
        "last_sell_watch_cursor_after": 0,
        "last_sell_watch_total_holdings": 0,
        "last_sell_watch_partial": False,
        "last_sell_watch_partial_reason": None,
        "last_sell_watch_evaluated_symbols": [],
        "last_sell_watch_skipped_symbols": [],
        "last_market_session": None,
        "last_budget_status": None,
        "last_reentry_blocked_at": None,
        "last_reentry_blocked_at_by_symbol": {},
        "last_reentry_state_by_symbol": {},
        "last_reentry_reason_by_symbol": {},
        "pending_sell_intents_by_symbol": {},
        "last_exit_reason_by_symbol": {},
        "last_exit_at_by_symbol": {},
        "last_exit_price_by_symbol": {},
        "last_exit_qty_by_symbol": {},
        "last_exit_was_full_close_by_symbol": {},
        "last_exit_trigger_context_by_symbol": {},
        "broker_last_synced_positions_by_symbol": {},
        "broker_last_synced_at": None,
        "last_reconciliation_summary": None,
        "last_reconciliation_events": [],
        "intraday_pnl_baseline_krw": None,
        "intraday_pnl_pct": None,
        "current_brake_state": None,
        "current_regime": None,
        "regime_reason": None,
        "regime_multiplier": None,
        "daily_pnl_pause_state": None,
        "daily_pnl_pause_until": None,
        "daily_pnl_pause_reason": None,
        "symbols_bought_today": [],
        "symbols_sold_today": [],
        "buy_attempted_symbols_today": [],
        "buy_untradable_symbols_today": [],
        "sell_triggered_symbols_today": [],
        "blocked_buy_symbols_today": [],
        "blocked_sell_symbols_today": [],
        "buy_entries_by_symbol_today": {},
        "last_buy_entry_at_by_symbol": {},
        "buy_cooldown_blocked_symbols_today": [],
        "sell_cooldown_blocked_symbols_today": [],
        "buy_blocked_symbols_today": [],
        "sell_blocked_symbols_today": [],
        "rebalance_sell_submissions_today": 0,
        "recent_orders": [],
    }


def _unique_append(items: list[str], value: str | None) -> None:
    if not value:
        return
    if value not in items:
        items.append(value)


def is_runtime_state_trusted(state: dict[str, Any]) -> bool:
    if state.get("runtime_state_trusted") is False:
        return False
    if str(state.get("runtime_state_safety_status") or "").strip() == "blocked":
        return False
    return not bool(state.get("order_submission_blocked_by_runtime_state"))


def _blocked_default_state(
    reason: str,
    *,
    previous_account_signature: str | None = None,
) -> dict[str, Any]:
    return _default_state(
        trusted=False,
        safety_reason=reason,
        previous_account_signature=previous_account_signature,
    )


def _path_modified_today(path: Path) -> bool:
    try:
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=KOREA_TZ)
    except OSError:
        return True
    return modified_at.date() == get_korean_now().date()


def _normalize_state(
    state: dict[str, Any],
    *,
    source_path: Path | None = None,
    allow_legacy_account_signature: bool = False,
) -> dict[str, Any]:
    normalized = _default_state()
    normalized.update(state)
    current_scope = get_account_scope_context()
    previous_signature = str(normalized.get("account_signature") or "").strip()
    allowed_signatures = {"", current_scope["account_signature"]}
    if allow_legacy_account_signature:
        allowed_signatures.add(get_legacy_account_signature())
    if previous_signature not in allowed_signatures:
        return _blocked_default_state(
            "account_signature_mismatch",
            previous_account_signature=previous_signature,
        )
    normalized["account_signature"] = current_scope["account_signature"]
    normalized["account_environment"] = current_scope["account_environment"]
    normalized["masked_account_display"] = current_scope["masked_account_display"]
    if normalized.get("trading_date") != _today_text():
        if source_path is not None and _path_modified_today(source_path):
            return _blocked_default_state("trading_date_mismatch_modified_today")
        return _default_state()
    if not is_runtime_state_trusted(normalized):
        reason = (
            str(normalized.get("runtime_state_safety_reason") or "").strip()
            or "runtime_state_marked_untrusted"
        )
        return _blocked_default_state(reason)
    normalized["runtime_state_trusted"] = True
    normalized["runtime_state_safety_status"] = "ok"
    normalized["runtime_state_safety_reason"] = None
    normalized["order_submission_blocked_by_runtime_state"] = False
    if not normalized.get("blocked_buy_symbols_today") and normalized.get(
        "buy_blocked_symbols_today"
    ):
        normalized["blocked_buy_symbols_today"] = list(
            normalized.get("buy_blocked_symbols_today", [])
        )
    if not normalized.get("blocked_sell_symbols_today") and normalized.get(
        "sell_blocked_symbols_today"
    ):
        normalized["blocked_sell_symbols_today"] = list(
            normalized.get("sell_blocked_symbols_today", [])
        )
    if (
        normalized.get("blocked_sell_symbols_today")
        and not normalized.get("last_sell_attempt_signature")
        and not normalized.get("symbols_sold_today")
        and not normalized.get("sell_triggered_symbols_today")
    ):
        normalized["blocked_sell_symbols_today"] = []
        normalized["sell_blocked_symbols_today"] = []
    pending_sell_intents = normalized.get("pending_sell_intents_by_symbol")
    if not isinstance(pending_sell_intents, dict):
        normalized["pending_sell_intents_by_symbol"] = {}
    else:
        normalized["pending_sell_intents_by_symbol"] = {
            str(symbol).strip(): {
                "qty": int(
                    (raw_entry.get("qty", 0) if isinstance(raw_entry, dict) else raw_entry)
                    or 0
                ),
                "submitted_at": (
                    str(raw_entry.get("submitted_at") or "").strip()
                    if isinstance(raw_entry, dict)
                    else ""
                )
                or None,
            }
            for symbol, raw_entry in pending_sell_intents.items()
            if str(symbol).strip()
            and int(
                (raw_entry.get("qty", 0) if isinstance(raw_entry, dict) else raw_entry) or 0
            )
            > 0
        }
    for key in (
        "broker_last_synced_positions_by_symbol",
        "last_reconciliation_events",
        "last_reentry_state_by_symbol",
        "last_reentry_reason_by_symbol",
        "last_exit_reason_by_symbol",
        "last_exit_at_by_symbol",
        "last_exit_price_by_symbol",
        "last_exit_qty_by_symbol",
        "last_exit_was_full_close_by_symbol",
        "last_exit_trigger_context_by_symbol",
    ):
        if key == "broker_last_synced_positions_by_symbol":
            raw_map = normalized.get(key)
            normalized[key] = (
                {
                    str(symbol).strip(): int(qty or 0)
                    for symbol, qty in raw_map.items()
                    if str(symbol).strip()
                }
                if isinstance(raw_map, dict)
                else {}
            )
        elif key == "last_exit_was_full_close_by_symbol":
            raw_map = normalized.get(key)
            normalized[key] = (
                {
                    str(symbol).strip(): bool(value)
                    for symbol, value in raw_map.items()
                    if str(symbol).strip()
                }
                if isinstance(raw_map, dict)
                else {}
            )
        elif key == "last_exit_trigger_context_by_symbol":
            raw_map = normalized.get(key)
            normalized[key] = (
                {
                    str(symbol).strip(): value
                    for symbol, value in raw_map.items()
                    if str(symbol).strip()
                }
                if isinstance(raw_map, dict)
                else {}
            )
        elif key in {
            "last_exit_price_by_symbol",
            "last_exit_qty_by_symbol",
        }:
            raw_map = normalized.get(key)
            normalized[key] = (
                {
                    str(symbol).strip(): int(value or 0)
                    for symbol, value in raw_map.items()
                    if str(symbol).strip()
                }
                if isinstance(raw_map, dict)
                else {}
            )
        else:
            raw_value = normalized.get(key)
            if key == "last_reconciliation_events":
                normalized[key] = raw_value if isinstance(raw_value, list) else []
            else:
                normalized[key] = (
                    {
                        str(symbol).strip(): value
                        for symbol, value in raw_value.items()
                        if str(symbol).strip()
                    }
                    if isinstance(raw_value, dict)
                    else {}
                )
    recent_snapshots = normalized.get("recent_market_snapshots_by_symbol")
    if not isinstance(recent_snapshots, dict):
        normalized["recent_market_snapshots_by_symbol"] = {}
    else:
        normalized["recent_market_snapshots_by_symbol"] = {
            str(symbol).strip(): snapshot
            for symbol, snapshot in recent_snapshots.items()
            if str(symbol).strip() and isinstance(snapshot, dict)
        }
    return normalized


def load_runtime_state() -> dict[str, Any]:
    primary_path = get_runtime_state_path()
    read_paths = [primary_path]
    try:
        candidates = list(get_runtime_state_read_paths())
        if primary_path in candidates:
            read_paths = candidates
    except Exception:
        pass
    runtime_state_file = next(
        (path for path in read_paths if path.exists()),
        None,
    )
    if runtime_state_file is None:
        return _default_state()

    try:
        state = json.loads(read_text_bounded(runtime_state_file, encoding="utf-8"))
    except (LocalReadLimitError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return _blocked_default_state("malformed_or_unreadable_runtime_state")

    if not isinstance(state, dict):
        return _blocked_default_state("unexpected_runtime_state_shape")
    return _normalize_state(
        state,
        source_path=runtime_state_file,
        allow_legacy_account_signature=runtime_state_file != primary_path,
    )


def save_runtime_state(state: dict[str, Any]) -> bool:
    try:
        runtime_state_file = get_runtime_state_path()
        state["account_signature"] = get_account_scope_context()["account_signature"]
        state["account_environment"] = get_account_scope_context()["account_environment"]
        state["masked_account_display"] = get_account_scope_context()["masked_account_display"]
        state["main_pid"] = os.getpid()
        runtime_state_file.parent.mkdir(parents=True, exist_ok=True)
        runtime_state_file.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return True
    except OSError:
        return False


def start_cycle(
    state: dict[str, Any],
    *,
    started_at: str,
    cycle_id: str | None = None,
) -> None:
    state["trading_date"] = _today_text()
    state["last_cycle_id"] = cycle_id
    state["last_cycle_started_at"] = started_at


def set_last_decision(
    state: dict[str, Any],
    *,
    action: str,
    reason: str,
    order_side: str | None = None,
    symbol: str | None = None,
    qty: int = 0,
    selected_symbol: str | None = None,
) -> None:
    state["last_action"] = action
    state["last_final_action"] = action
    state["last_decision_reason"] = reason
    state["last_final_reason"] = reason
    state["last_order_side"] = order_side
    state["last_order_date"] = _today_text()
    state["last_selected_symbol"] = selected_symbol or symbol
    if order_side == "BUY":
        state["last_buy_symbol"] = symbol
        state["last_buy_qty"] = qty
    elif order_side == "SELL":
        state["last_sell_symbol"] = symbol
        state["last_sell_qty"] = qty


def add_recent_order(
    state: dict[str, Any],
    *,
    side: str,
    symbol: str,
    qty: int,
    action: str,
    timestamp: str | None = None,
    cycle_id: str | None = None,
) -> None:
    recent_orders = state.setdefault("recent_orders", [])
    now_text = timestamp or get_korean_now().isoformat()
    recent_orders.append(
        {
            "timestamp": now_text,
            "date": _today_text(),
            "cycle_id": cycle_id or state.get("last_cycle_id"),
            "side": side,
            "symbol": symbol,
            "qty": qty,
            "action": action,
            "pid": os.getpid(),
        }
    )
    state["recent_orders"] = recent_orders[-50:]
    if side == "BUY" and action == "order_submitted":
        buy_entries = state.setdefault("buy_entries_by_symbol_today", {})
        buy_entries[symbol] = int(buy_entries.get(symbol, 0) or 0) + 1
        state.setdefault("last_buy_entry_at_by_symbol", {})[symbol] = now_text


def record_buy_attempt_signature(
    state: dict[str, Any],
    *,
    signature: str,
    attempted_at: str | None = None,
) -> None:
    state["last_buy_attempt_signature"] = signature
    state["last_buy_attempt_at"] = attempted_at or get_korean_now().isoformat()


def record_sell_attempt_signature(
    state: dict[str, Any],
    *,
    signature: str,
    attempted_at: str | None = None,
) -> None:
    state["last_sell_attempt_signature"] = signature
    state["last_sell_attempt_at"] = attempted_at or get_korean_now().isoformat()


def mark_buy_attempt(
    state: dict[str, Any],
    symbol: str,
    *,
    signature: str | None = None,
    attempted_at: str | None = None,
    record_symbol: bool = True,
) -> None:
    if record_symbol:
        _unique_append(state["buy_attempted_symbols_today"], symbol)
    if signature:
        record_buy_attempt_signature(
            state,
            signature=signature,
            attempted_at=attempted_at,
        )


def mark_sell_trigger(
    state: dict[str, Any],
    symbol: str,
    *,
    signature: str | None = None,
    attempted_at: str | None = None,
    record_symbol: bool = True,
) -> None:
    if record_symbol:
        _unique_append(state["sell_triggered_symbols_today"], symbol)
    if signature:
        record_sell_attempt_signature(
            state,
            signature=signature,
            attempted_at=attempted_at,
        )


def mark_bought_symbol(state: dict[str, Any], symbol: str) -> None:
    _unique_append(state["symbols_bought_today"], symbol)


def mark_buy_untradable_symbol(state: dict[str, Any], symbol: str) -> None:
    _unique_append(state.setdefault("buy_untradable_symbols_today", []), symbol)
    mark_buy_blocked(state, symbol)


def mark_sold_symbol(state: dict[str, Any], symbol: str) -> None:
    _unique_append(state["symbols_sold_today"], symbol)


def mark_buy_blocked(state: dict[str, Any], symbol: str) -> None:
    _unique_append(state["buy_blocked_symbols_today"], symbol)
    _unique_append(state["blocked_buy_symbols_today"], symbol)


def mark_sell_blocked(state: dict[str, Any], symbol: str) -> None:
    _unique_append(state["sell_blocked_symbols_today"], symbol)
    _unique_append(state["blocked_sell_symbols_today"], symbol)


def mark_buy_cooldown_blocked(state: dict[str, Any], symbol: str) -> None:
    _unique_append(state["buy_cooldown_blocked_symbols_today"], symbol)
    mark_buy_blocked(state, symbol)


def mark_reentry_blocked(state: dict[str, Any], symbol: str) -> None:
    timestamp = get_korean_now().isoformat()
    state["last_reentry_blocked_at"] = timestamp
    state.setdefault("last_reentry_blocked_at_by_symbol", {})[symbol] = timestamp


def record_reentry_decision(
    state: dict[str, Any],
    *,
    symbol: str,
    reentry_state: str | None,
    reason: str | None,
) -> None:
    normalized_symbol = str(symbol).strip()
    if not normalized_symbol:
        return
    if reentry_state:
        state.setdefault("last_reentry_state_by_symbol", {})[normalized_symbol] = str(
            reentry_state
        ).strip()
    if reason:
        state.setdefault("last_reentry_reason_by_symbol", {})[normalized_symbol] = str(
            reason
        ).strip()


def mark_sell_cooldown_blocked(state: dict[str, Any], symbol: str) -> None:
    _unique_append(state["sell_cooldown_blocked_symbols_today"], symbol)
    mark_sell_blocked(state, symbol)


def mark_sell_intent_submitted(
    state: dict[str, Any],
    symbol: str,
    qty: int,
    *,
    submitted_at: str | None = None,
    trigger: str | None = None,
) -> None:
    normalized_symbol = str(symbol).strip()
    normalized_qty = max(0, int(qty or 0))
    if not normalized_symbol or normalized_qty <= 0:
        return
    pending = state.setdefault("pending_sell_intents_by_symbol", {})
    existing = pending.get(normalized_symbol)
    existing_qty = int(
        (existing.get("qty", 0) if isinstance(existing, dict) else existing) or 0
    )
    entry: dict[str, Any] = {
        "qty": existing_qty + normalized_qty,
        "submitted_at": submitted_at or get_korean_now().isoformat(),
    }
    normalized_trigger = str(trigger or "").strip()
    if not normalized_trigger and isinstance(existing, dict):
        normalized_trigger = str(existing.get("trigger") or "").strip()
    if normalized_trigger:
        entry["trigger"] = normalized_trigger
    pending[normalized_symbol] = entry


def pending_sell_intent_submitted_at(state: dict[str, Any], symbol: str) -> str:
    """Best-effort ISO submit stamp for a pending intent ("" when unknown)."""
    pending = state.get("pending_sell_intents_by_symbol")
    if not isinstance(pending, dict):
        return ""
    entry = pending.get(str(symbol).strip())
    if not isinstance(entry, dict):
        return ""
    return str(entry.get("submitted_at") or "").strip()


_SELL_ORDER_OUTCOME_ACTIONS = frozenset(
    {"sell_order_submitted", "sell_order_failed", "sell_order_succeeded"}
)


def latest_sell_order_action(state: dict[str, Any], symbol: str) -> str:
    """Most recent SELL order outcome recorded for ``symbol`` ("" when none)."""
    normalized_symbol = str(symbol).strip()
    latest_action = ""
    latest_time: datetime | None = None
    for order in state.get("recent_orders", []) or []:
        if not isinstance(order, dict):
            continue
        if str(order.get("symbol", "")).strip() != normalized_symbol:
            continue
        action = str(order.get("action", "")).strip()
        if action not in _SELL_ORDER_OUTCOME_ACTIONS:
            continue
        order_time = parse_recent_order_time(order)
        if order_time is None:
            continue
        if latest_time is None or order_time >= latest_time:
            latest_time = order_time
            latest_action = action
    return latest_action


# Safety-net ceilings for reservations that outlive their submit (a market-order
# fill resolves in seconds; see docs/todo_20260710.md §A-3). Defined here — not in
# app.core.reconciliation, which imports this module — so both the reconciliation
# pass and the finalize-phase fallback share one source of truth.
SELL_INTENT_TTL_MINUTES = 10.0
SELL_INTENT_EMERGENCY_TTL_MINUTES = 3.0
INTENT_ADJUSTMENTS_CAP = 50


def prune_stale_sell_intents(
    state: dict[str, Any],
    *,
    ttl_minutes: float,
    emergency_ttl_minutes: float | None = None,
    emergency_triggers: frozenset[str] = SELL_EMERGENCY_TRIGGERS,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Release pending SELL intents whose reservation has outlived its TTL.

    A pending intent only exists to stop the *same* cycle-to-cycle SELL from
    being submitted twice while the broker fill is still in flight. When the
    broker *rejects* the submit (EGW00201), nothing is in flight, yet the
    reservation can pin the position for the rest of the session — the
    2026-07-09 023530/082740 lockouts.

    Only reservations whose last recorded outcome is a **rejection** (or that
    have no order trace at all) are aged out. An accepted-but-unfilled order
    keeps its reservation: `stop_loss` bypasses the order cooldown, so the
    reservation is the only thing preventing a duplicate sell. Positions whose
    fill did land are cleared upstream by reconciliation against broker truth.

    Returns one audit record per released intent (empty list when nothing aged out).
    """
    pending = state.get("pending_sell_intents_by_symbol")
    if not isinstance(pending, dict) or not pending:
        return []

    now = now or get_korean_now()
    ttl_minutes = max(0.0, float(ttl_minutes or 0.0))
    if ttl_minutes <= 0:
        return []
    emergency_ttl = (
        max(0.0, float(emergency_ttl_minutes))
        if emergency_ttl_minutes is not None
        else ttl_minutes
    )

    released: list[dict[str, Any]] = []
    for symbol in list(pending.keys()):
        entry = pending.get(symbol)
        if not isinstance(entry, dict):
            # Legacy scalar form carries no timestamp — upgrade it in place so the
            # next pass can age it out rather than releasing it blind right now.
            pending[symbol] = {
                "qty": max(0, int(entry or 0)),
                "submitted_at": now.isoformat(),
            }
            continue

        submitted_at = parse_recent_order_time(
            {"timestamp": str(entry.get("submitted_at") or "").strip()}
        )
        if submitted_at is None:
            entry["submitted_at"] = now.isoformat()
            continue

        trigger = str(entry.get("trigger") or "").strip()
        applicable_ttl = emergency_ttl if trigger in emergency_triggers else ttl_minutes
        age_minutes = (now - submitted_at).total_seconds() / 60.0
        if age_minutes < applicable_ttl:
            continue

        last_action = latest_sell_order_action(state, symbol)
        if last_action in {"sell_order_submitted", "sell_order_succeeded"}:
            # The broker may hold an unfilled order for this reservation.
            continue

        released.append(
            {
                "symbol": symbol,
                "qty": max(0, int(entry.get("qty", 0) or 0)),
                "trigger": trigger,
                "submitted_at": str(entry.get("submitted_at") or "").strip(),
                "age_minutes": round(age_minutes, 2),
                "ttl_minutes": applicable_ttl,
                "cause": "intent_ttl_expired",
                "last_order_action": last_action,
                "detected_at": now.isoformat(),
            }
        )
        pending.pop(symbol, None)

    return released


def prune_stale_sell_intents_with_audit(
    state: dict[str, Any],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Default-TTL prune that also writes the audit trail into the state itself.

    The reconciliation pass covers the legacy cycle path; the lane-scheduler path
    skips account_snapshot entirely, so finalize calls this as the convergence
    point for both. On the legacy path reconciliation has already pruned and this
    is a no-op, so audit records are never duplicated.
    """
    released = prune_stale_sell_intents(
        state,
        ttl_minutes=SELL_INTENT_TTL_MINUTES,
        emergency_ttl_minutes=SELL_INTENT_EMERGENCY_TTL_MINUTES,
        now=now,
    )
    if not released:
        return released
    stored = state.get("last_intent_adjustments")
    if not isinstance(stored, list):
        stored = []
    stored.extend(
        {
            "symbol": entry["symbol"],
            "before_qty": entry["qty"],
            "after_qty": 0,
            "cause": entry["cause"],
            "detected_at": entry["detected_at"],
            "age_minutes": entry["age_minutes"],
            "ttl_minutes": entry["ttl_minutes"],
            "last_order_action": entry["last_order_action"],
        }
        for entry in released
    )
    state["last_intent_adjustments"] = stored[-INTENT_ADJUSTMENTS_CAP:]
    return released


def resolve_sell_intent(
    state: dict[str, Any],
    symbol: str,
    qty: int,
    *,
    clear: bool = False,
) -> None:
    normalized_symbol = str(symbol).strip()
    if not normalized_symbol:
        return
    pending = state.setdefault("pending_sell_intents_by_symbol", {})
    existing = pending.get(normalized_symbol)
    if existing is None:
        return
    if clear:
        pending.pop(normalized_symbol, None)
        return
    existing_qty = int(
        (existing.get("qty", 0) if isinstance(existing, dict) else existing) or 0
    )
    remaining_qty = max(0, existing_qty - max(0, int(qty or 0)))
    if remaining_qty <= 0:
        pending.pop(normalized_symbol, None)
        return
    pending[normalized_symbol] = {
        "qty": remaining_qty,
        "submitted_at": (
            str(existing.get("submitted_at") or "").strip()
            if isinstance(existing, dict)
            else None
        )
        or get_korean_now().isoformat(),
    }


def record_symbol_exit(
    state: dict[str, Any],
    *,
    symbol: str,
    qty: int,
    exit_reason: str,
    exit_price: int | None,
    was_full_close: bool,
    exited_at: str | None = None,
    trigger_context: dict[str, Any] | str | None = None,
) -> None:
    normalized_symbol = str(symbol).strip()
    if not normalized_symbol:
        return
    timestamp = exited_at or get_korean_now().isoformat()
    state.setdefault("last_exit_reason_by_symbol", {})[normalized_symbol] = str(
        exit_reason or "unknown"
    ).strip() or "unknown"
    state.setdefault("last_exit_at_by_symbol", {})[normalized_symbol] = timestamp
    state.setdefault("last_exit_price_by_symbol", {})[normalized_symbol] = (
        int(exit_price or 0) if exit_price is not None else None
    )
    state.setdefault("last_exit_qty_by_symbol", {})[normalized_symbol] = max(
        0,
        int(qty or 0),
    )
    state.setdefault("last_exit_was_full_close_by_symbol", {})[normalized_symbol] = bool(
        was_full_close
    )
    if trigger_context is not None:
        state.setdefault("last_exit_trigger_context_by_symbol", {})[normalized_symbol] = (
            trigger_context
        )


def runtime_state_summary(state: dict[str, Any]) -> dict[str, int]:
    return {
        "buy_completed_count": len(state.get("symbols_bought_today", [])),
        "sell_completed_count": len(state.get("symbols_sold_today", [])),
        "buy_blocked_count": len(
            state.get("blocked_buy_symbols_today", state.get("buy_blocked_symbols_today", []))
        ),
        "sell_blocked_count": len(
            state.get("blocked_sell_symbols_today", state.get("sell_blocked_symbols_today", []))
        ),
        "buy_cooldown_blocked_count": len(
            state.get("buy_cooldown_blocked_symbols_today", [])
        ),
        "buy_untradable_count": len(
            state.get("buy_untradable_symbols_today", [])
        ),
        "sell_cooldown_blocked_count": len(
            state.get("sell_cooldown_blocked_symbols_today", [])
        ),
        "rebalance_sell_submissions_count": int(
            state.get("rebalance_sell_submissions_today", 0)
        ),
    }


def mark_rebalance_sell_submission(state: dict[str, Any]) -> None:
    state["rebalance_sell_submissions_today"] = int(
        state.get("rebalance_sell_submissions_today", 0)
    ) + 1


def parse_recent_order_time(order: dict[str, Any]) -> datetime | None:
    timestamp = str(order.get("timestamp", "")).strip()
    if not timestamp:
        return None
    try:
        order_time = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if order_time.tzinfo is None:
        return order_time.replace(tzinfo=KOREA_TZ)
    return order_time.astimezone(KOREA_TZ)
