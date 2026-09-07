from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app.core.file_read_limits import (
    LocalReadLimitError,
    read_text_bounded,
)
from app.core.time_utils import get_korean_now
from app.runtime_state import parse_recent_order_time
from app.notifications.orders_today import (
    DEFAULT_ORDER_LOG_MAX_LINES,
    _ORDER_ACTION_STATUS,
    _compact_order_lifecycles,
    _first_present,
    _format_order_time,
    _nested_mapping,
    _order_compact_key,
    _order_failure_category,
    _order_price_and_notional,
    _order_qty,
    _order_record_time,
    _order_side_status,
    _pair_submission,
    _raw_response_mapping,
    _read_today_order_events,
    _render_order_lifecycle_line,
    _truncate_order_reason,
    render_orders_today_reply,
)
from app.notifications.snapshot_shared import (
    DEFAULT_SNAPSHOT_PATH,
    DEFAULT_STALE_AFTER_SEC,
    RuntimeSnapshotReadResult,
    _SIDE_LABELS,
    _parse_timestamp,
    _safe_int,
    _safe_price,
    _safe_text,
)
from app.scanner.symbol_names import get_symbol_name


# Limited-reply texts, format helpers, and positions read/render cluster:
# canonical implementations imported from app.notifications.snapshot_render
# (legacy names stay bound here).
from app.notifications.snapshot_render import (  # noqa: E402
    DEFAULT_PERFORMANCE_SUMMARY_MAX_LINES,
    LIMITED_BOTTLENECKS_MISSING_TEXT,
    LIMITED_BOTTLENECKS_STALE_TEXT,
    LIMITED_BOTTLENECKS_UNREADABLE_TEXT,
    LIMITED_HEALTH_MISSING_TEXT,
    LIMITED_HEALTH_STALE_TEXT,
    LIMITED_HEALTH_UNREADABLE_TEXT,
    LIMITED_POSITIONS_MISSING_TEXT,
    LIMITED_POSITIONS_STALE_TEXT,
    LIMITED_POSITIONS_UNREADABLE_TEXT,
    LIMITED_STATUS_MISSING_TEXT,
    LIMITED_STATUS_STALE_TEXT,
    LIMITED_STATUS_UNREADABLE_TEXT,
    _first_int_value,
    _format_counter_map,
    _format_krw_optional,
    _format_signed_krw_optional,
    _format_snapshot_time,
    _limited_text_for_result,
    _mapping_from_snapshot,
    _normalize_performance_position,
    _positions_freshness_line,
    _read_latest_performance_positions,
    _render_position_line,
)

_ORDER_COUNTER_KEYS = (
    "buy_submitted",
    "buy_succeeded",
    "buy_failed",
    "sell_submitted",
    "sell_succeeded",
    "sell_failed",
)
_BOTTLENECK_ACTION_KEYS = (
    "repeated_bottleneck",
    "kis_rate_limit",
    "cash_budget_shortage",
    "stale_snapshot",
    "fallback_spike",
    "rate_limit_detected_buy_scan",
    "rate_limit_detected_sell_watch",
    "skipped_buy_scan_budget_limited",
    "skipped_buy_scan_rate_limit",
    "skipped_api_transient_backoff",
    "buy_scan_partial_budget",
    "sell_watch_capped_for_buy_scan",
    "sell_watch_partial_budget",
    "sell_watch_partial_budget_protection",
    "sell_order_deferred_rate_limit_backoff",
    "blocked_buy_cash_insufficient",
    "blocked_buy_exposure_limited",
    "blocked_buy_trade_budget_limited",
    "blocked_daily_pnl_pause",
    "blocked_daily_pnl_hard_stop",
    # A stranded SELL intent silently pins a position for the rest of the session
    # — it must show up as a bottleneck, not just a line in the order log.
    "blocked_sell_no_sellable_residual",
    "blocked_sell_duplicate_pending_intent",
)
_BOTTLENECK_LABELS = {
    "repeated_bottleneck": "반복 병목",
    "kis_rate_limit": "KIS rate-limit",
    "cash_budget_shortage": "현금 예산 부족",
    "stale_snapshot": "stale snapshot",
    "fallback_spike": "fallback spike",
    "rate_limit_detected_buy_scan": "매수 스캔 rate-limit",
    "rate_limit_detected_sell_watch": "매도 감시 rate-limit",
    "skipped_buy_scan_budget_limited": "매수 스캔 예산 제한",
    "skipped_buy_scan_rate_limit": "매수 스캔 rate-limit 스킵",
    "skipped_api_transient_backoff": "API 일시 오류 backoff",
    "buy_scan_partial_budget": "매수 스캔 부분 예산",
    "sell_watch_capped_for_buy_scan": "매수 스캔 보호로 매도 감시 제한",
    "sell_watch_partial_budget": "매도 감시 부분 예산",
    "sell_watch_partial_budget_protection": "매도 감시 보호 cap 도달",
    "sell_order_deferred_rate_limit_backoff": "매도 주문 rate-limit 보류",
    "blocked_buy_cash_insufficient": "매수 현금 부족",
    "blocked_buy_exposure_limited": "매수 노출 제한",
    "blocked_buy_trade_budget_limited": "매수 거래 예산 제한",
    "blocked_daily_pnl_pause": "일일 손익 일시정지",
    "blocked_daily_pnl_hard_stop": "일일 손익 hard-stop",
    "blocked_sell_no_sellable_residual": "매도 잔량 없음 (intent 예약)",
    "blocked_sell_duplicate_pending_intent": "매도 중복 intent",
}
_RATE_LIMIT_SOURCE_LABELS = {
    "balance": "잔고 조회",
    "buy_scan": "매수 스캔",
    "sell_watch": "매도 감시",
    "buy_order": "매수 주문",
    "sell_order": "매도 주문",
}
_RATE_LIMIT_SOURCE_NEXT_ACTIONS = {
    "balance": "이번 사이클 스킵",
    "buy_scan": "매수 스캔 보류",
    "sell_watch": "보호된 매도 감시만 진행",
    "buy_order": "매수 주문 보류",
    "sell_order": "매도 주문 보류",
}
_ORDER_ACTION_LABELS = {
    "order_submitted": "제출",
    "order_succeeded": "접수",
    "order_failed": "실패",
    "sell_order_submitted": "제출",
    "sell_order_succeeded": "접수",
    "sell_order_failed": "실패",
}
# RuntimeSnapshotReadResult/_parse_timestamp/_safe_int/_safe_text/_safe_price:
# canonical implementations imported from app.notifications.snapshot_shared
# above (legacy names stay bound here).


def _build_positions_snapshot(runtime_state: Mapping[str, Any]) -> list[dict[str, Any]]:
    positions_by_symbol = runtime_state.get("broker_last_synced_positions_by_symbol")
    if not isinstance(positions_by_symbol, Mapping):
        return []

    recent_market_snapshots = runtime_state.get("recent_market_snapshots_by_symbol")
    if not isinstance(recent_market_snapshots, Mapping):
        recent_market_snapshots = {}

    positions_snapshot: list[dict[str, Any]] = []
    for raw_symbol, raw_qty in sorted(positions_by_symbol.items()):
        symbol = str(raw_symbol or "").strip()
        qty = _safe_int(raw_qty)
        if not symbol or qty <= 0:
            continue

        market_snapshot = recent_market_snapshots.get(symbol)
        if not isinstance(market_snapshot, Mapping):
            market_snapshot = {}

        current_price = _safe_price(market_snapshot.get("current_price"))
        market_value = qty * current_price if current_price is not None else None
        row: dict[str, Any] = {
            "symbol": symbol,
            "symbol_name": get_symbol_name(symbol),
            "qty": qty,
            "current_price": current_price,
            "avg_price": None,
            "market_value": market_value,
            "price_observed_at": _safe_text(market_snapshot.get("observed_at")),
        }
        positions_snapshot.append(row)

    return positions_snapshot


def _action_counts_from_daily_summary(daily_summary: object | None) -> Mapping[str, int]:
    raw_counts = getattr(daily_summary, "action_counts", None)
    if not isinstance(raw_counts, Mapping):
        return {}
    return {str(key): _safe_int(value) for key, value in raw_counts.items()}


def _build_pending_sell_intents(runtime_state: Mapping[str, Any]) -> dict[str, Any]:
    """Summarize reserved-but-unresolved SELL intents.

    The oldest age is the operator's tell: a reservation minutes old is a normal
    in-flight fill, one tens of minutes old means a position is pinned
    (docs/todo_20260710.md §A-3).
    """
    pending = runtime_state.get("pending_sell_intents_by_symbol")
    empty: dict[str, Any] = {
        "count": 0,
        "total_qty": 0,
        "oldest_symbol": "",
        "oldest_age_minutes": None,
    }
    if not isinstance(pending, Mapping) or not pending:
        return empty

    now = get_korean_now()
    total_qty = 0
    oldest_symbol = ""
    oldest_age: float | None = None
    for symbol, entry in pending.items():
        if isinstance(entry, Mapping):
            total_qty += _safe_int(entry.get("qty"))
            submitted_at = parse_recent_order_time(
                {"timestamp": _safe_text(entry.get("submitted_at"))}
            )
        else:
            total_qty += _safe_int(entry)
            submitted_at = None
        if submitted_at is None:
            continue
        age_minutes = (now - submitted_at).total_seconds() / 60.0
        if oldest_age is None or age_minutes > oldest_age:
            oldest_age = age_minutes
            oldest_symbol = _safe_text(symbol)

    return {
        "count": len(pending),
        "total_qty": total_qty,
        "oldest_symbol": oldest_symbol,
        "oldest_age_minutes": round(oldest_age, 1) if oldest_age is not None else None,
    }


def render_pending_intent_line(snapshot: Mapping[str, Any]) -> str:
    """One Slack line for stranded SELL intents ("" when there is nothing to say)."""
    intents = snapshot.get("pending_sell_intents")
    if not isinstance(intents, Mapping) or _safe_int(intents.get("count")) <= 0:
        return ""
    count = _safe_int(intents.get("count"))
    total_qty = _safe_int(intents.get("total_qty"))
    oldest_symbol = _safe_text(intents.get("oldest_symbol"))
    oldest_age = intents.get("oldest_age_minutes")
    line = f"• 대기 SELL intent: `{count}`건 / `{total_qty}`주"
    if oldest_symbol and oldest_age is not None:
        line += f" (최고령 `{oldest_symbol}` `{oldest_age}`분)"
    return line


def _last_order_event(runtime_state: Mapping[str, Any]) -> dict[str, Any] | None:
    recent_orders = runtime_state.get("recent_orders")
    if not isinstance(recent_orders, list) or not recent_orders:
        return None
    raw_order = recent_orders[-1]
    if not isinstance(raw_order, Mapping):
        return None
    return {
        "timestamp": _safe_text(raw_order.get("timestamp")),
        "side": _safe_text(raw_order.get("side")),
        "symbol": _safe_text(raw_order.get("symbol")),
        "quantity": _safe_int(raw_order.get("qty")),
        "action": _safe_text(raw_order.get("action")),
    }


def build_slack_status_snapshot(
    *,
    runtime_state: Mapping[str, Any],
    daily_summary: object | None = None,
    session_status: str | None = None,
    timestamp: str | None = None,
    daily_summary_sent: bool | None = None,
) -> dict[str, Any]:
    action_counts = _action_counts_from_daily_summary(daily_summary)
    now_text = timestamp or get_korean_now().isoformat()
    order_counters = {
        "buy_submitted": _safe_int(action_counts.get("order_submitted")),
        "buy_succeeded": _safe_int(action_counts.get("order_succeeded")),
        "buy_failed": _safe_int(action_counts.get("order_failed")),
        "sell_submitted": _safe_int(action_counts.get("sell_order_submitted")),
        "sell_succeeded": _safe_int(action_counts.get("sell_order_succeeded")),
        "sell_failed": _safe_int(action_counts.get("sell_order_failed")),
    }
    bottleneck_counters = {
        key: _safe_int(action_counts.get(key)) for key in _BOTTLENECK_ACTION_KEYS
    }
    exception_count = max(
        _safe_int(runtime_state.get("last_error_count")),
        _safe_int(action_counts.get("cycle_error")),
    )

    positions_synced_at = _safe_text(runtime_state.get("broker_last_synced_at"))

    health_data: dict[str, Any] = {
        "rate_limit_hits": _safe_int(runtime_state.get("rate_limit_hits")),
        "last_rate_limit_source": _safe_text(runtime_state.get("last_rate_limit_source")),
        "daily_pnl_pause_state": _safe_text(runtime_state.get("daily_pnl_pause_state")),
        "daily_pnl_pause_reason": _safe_text(runtime_state.get("daily_pnl_pause_reason")),
        "last_cycle_elapsed_ms": runtime_state.get("last_cycle_elapsed_ms"),
        "consecutive_backoff_cycles": _safe_int(
            runtime_state.get("consecutive_backoff_cycles")
        ),
    }

    return {
        "timestamp": now_text,
        "pid": os.getpid(),
        "account_signature": _safe_text(runtime_state.get("account_signature")),
        "masked_account_display": _safe_text(runtime_state.get("masked_account_display")),
        "session_status": (
            _safe_text(session_status)
            or _safe_text(runtime_state.get("last_market_session"))
            or "unknown"
        ),
        "last_heartbeat": now_text,
        "order_counters_today": order_counters,
        "bottleneck_counters_today": bottleneck_counters,
        "last_order_event": _last_order_event(runtime_state),
        "exception_count": exception_count,
        "daily_summary_sent": daily_summary_sent,
        "positions_snapshot": _build_positions_snapshot(runtime_state),
        "positions_snapshot_source": "runtime_state.broker_last_synced_positions_by_symbol",
        "positions_synced_at": positions_synced_at,
        "pending_sell_intents": _build_pending_sell_intents(runtime_state),
        "health": health_data,
    }


def write_slack_status_snapshot(
    snapshot: Mapping[str, Any],
    *,
    path: Path = DEFAULT_SNAPSHOT_PATH,
) -> bool:
    tmp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(
            json.dumps(dict(snapshot), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        os.replace(tmp_path, path)
        return True
    except (OSError, TypeError, ValueError):
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def read_slack_status_snapshot(
    *,
    path: Path = DEFAULT_SNAPSHOT_PATH,
    now: datetime | None = None,
    stale_after_sec: int = DEFAULT_STALE_AFTER_SEC,
) -> RuntimeSnapshotReadResult:
    if not path.exists():
        return RuntimeSnapshotReadResult(status="missing")
    try:
        payload = json.loads(read_text_bounded(path, encoding="utf-8"))
    except (LocalReadLimitError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return RuntimeSnapshotReadResult(status="unreadable")
    if not isinstance(payload, dict):
        return RuntimeSnapshotReadResult(status="unreadable")

    timestamp = _parse_timestamp(payload.get("timestamp"))
    if timestamp is None:
        return RuntimeSnapshotReadResult(status="unreadable")
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    age_seconds = max(
        0.0,
        (current_time.astimezone(timezone.utc) - timestamp).total_seconds(),
    )
    if stale_after_sec > 0 and age_seconds > stale_after_sec:
        return RuntimeSnapshotReadResult(
            status="stale",
            snapshot=payload,
            age_seconds=age_seconds,
        )
    return RuntimeSnapshotReadResult(
        status="ok",
        snapshot=payload,
        age_seconds=age_seconds,
    )


# Format helpers + positions read/render cluster: canonical implementations
# live in app.notifications.snapshot_render; bind the legacy module
# attributes here (import seam preserved for tests and slack_bot).
from app.notifications.snapshot_render import (  # noqa: E402
    _first_int_value,
    _format_counter_map,
    _format_krw_optional,
    _format_signed_krw_optional,
    _format_snapshot_time,
    _limited_text_for_result,
    _mapping_from_snapshot,
    _normalize_performance_position,
    _positions_freshness_line,
    _read_latest_performance_positions,
    _render_position_line,
)


def _daily_summary_text(value: object) -> str:
    if value is None:
        return "알 수 없음"
    return "전송" if bool(value) else "미전송"


def _last_order_text(last_order: Mapping[str, Any]) -> str:
    if not last_order:
        return "최근 주문이 없습니다."
    side = str(_safe_text(last_order.get("side")) or "").upper()
    side_label = _SIDE_LABELS.get(side, side or "-")
    symbol = _safe_text(last_order.get("symbol")) or "-"
    qty = _safe_int(last_order.get("quantity"))
    action = _safe_text(last_order.get("action")) or "-"
    action_label = _ORDER_ACTION_LABELS.get(action, action)
    return f"{side_label} `{symbol}` 수량 `{qty}` — `{action_label}`"


def render_status_snapshot_reply(
    *,
    path: Path = DEFAULT_SNAPSHOT_PATH,
    now: datetime | None = None,
    stale_after_sec: int = DEFAULT_STALE_AFTER_SEC,
) -> str:
    result = read_slack_status_snapshot(
        path=path,
        now=now,
        stale_after_sec=stale_after_sec,
    )
    if result.status != "ok" or result.snapshot is None:
        return _limited_text_for_result(result, command="status")

    snapshot = result.snapshot
    orders = _mapping_from_snapshot(snapshot, "order_counters_today")
    last_order = _mapping_from_snapshot(snapshot, "last_order_event")
    health_lines = [
        f"• 예외: `{_safe_int(snapshot.get('exception_count'))}`",
        f"• 일일 요약: `{_daily_summary_text(snapshot.get('daily_summary_sent'))}`",
    ]
    pending_intent_line = render_pending_intent_line(snapshot)
    if pending_intent_line:
        health_lines.append(pending_intent_line)
    return "\n".join(
        (
            "✅ *PROJECT-SIGNALOR 상태*",
            "",
            f"세션: `{_safe_text(snapshot.get('session_status')) or 'unknown'}`",
            f"마지막 갱신: `{_format_snapshot_time(snapshot.get('last_heartbeat'))}`",
            "",
            "📌 *오늘 주문*",
            "• 매수: "
            f"제출 `{_safe_int(orders.get('buy_submitted'))}` / "
            f"접수 `{_safe_int(orders.get('buy_succeeded'))}` / "
            f"실패 `{_safe_int(orders.get('buy_failed'))}`",
            "• 매도: "
            f"제출 `{_safe_int(orders.get('sell_submitted'))}` / "
            f"접수 `{_safe_int(orders.get('sell_succeeded'))}` / "
            f"실패 `{_safe_int(orders.get('sell_failed'))}`",
            "",
            "🩺 *운영 상태*",
            *health_lines,
            "",
            "🧾 *최근 주문*",
            _last_order_text(last_order),
        )
    )


def render_bottlenecks_snapshot_reply(
    *,
    path: Path = DEFAULT_SNAPSHOT_PATH,
    now: datetime | None = None,
    stale_after_sec: int = DEFAULT_STALE_AFTER_SEC,
) -> str:
    result = read_slack_status_snapshot(
        path=path,
        now=now,
        stale_after_sec=stale_after_sec,
    )
    if result.status != "ok" or result.snapshot is None:
        return _limited_text_for_result(result, command="bottlenecks")

    counters = _mapping_from_snapshot(result.snapshot, "bottleneck_counters_today")
    nonzero_keys = tuple(
        key for key in _BOTTLENECK_ACTION_KEYS if _safe_int(counters.get(key))
    )
    if not nonzero_keys:
        return "✅ *오늘 병목 현황*\n\n현재 보고된 병목이 없습니다."
    lines = ["⚠️ *오늘 병목 현황*", ""]
    lines.extend(
        f"• {_BOTTLENECK_LABELS.get(key, key)}: `{_safe_int(counters.get(key))}`"
        for key in nonzero_keys
    )
    health = _mapping_from_snapshot(result.snapshot, "health")
    if _safe_int(counters.get("kis_rate_limit")) or _safe_int(health.get("rate_limit_hits")):
        detail_parts: list[str] = []
        source_detail = _rate_limit_source_detail(health.get("last_rate_limit_source"))
        if source_detail:
            detail_parts.append(source_detail)
        backoff_cycles = _safe_int(health.get("consecutive_backoff_cycles"))
        if backoff_cycles:
            detail_parts.append(f"backoff `{backoff_cycles}` cycles")
        if detail_parts:
            lines.append(f"• Rate-limit: {' · '.join(detail_parts)}")
    return "\n".join(lines)


def _health_check_line(label: str, ok: bool, detail: str = "") -> str:
    icon = "✅" if ok else "⚠️"
    suffix = f" — {detail}" if detail else ""
    return f"{icon} {label}{suffix}"


def _rate_limit_source_detail(source: object) -> str:
    source_text = _safe_text(source)
    if not source_text:
        return ""
    label = _RATE_LIMIT_SOURCE_LABELS.get(source_text)
    if not label:
        return f"source `{source_text}`"
    return f"source `{source_text}` {label}"


def _rate_limit_next_action(source: object) -> str:
    source_text = _safe_text(source)
    return _RATE_LIMIT_SOURCE_NEXT_ACTIONS.get(source_text, "")


def render_health_snapshot_reply(
    *,
    path: Path = DEFAULT_SNAPSHOT_PATH,
    now: datetime | None = None,
    stale_after_sec: int = DEFAULT_STALE_AFTER_SEC,
) -> str:
    result = read_slack_status_snapshot(
        path=path,
        now=now,
        stale_after_sec=stale_after_sec,
    )
    if result.status != "ok" or result.snapshot is None:
        return _limited_text_for_result(result, command="health")

    snapshot = result.snapshot
    health = _mapping_from_snapshot(snapshot, "health")
    checks: list[str] = []

    age_sec = result.age_seconds or 0.0
    heartbeat_ok = age_sec < 120
    checks.append(_health_check_line(
        "Heartbeat",
        heartbeat_ok,
        f"`{int(age_sec)}s` ago" if heartbeat_ok else f"`{int(age_sec)}s` ago (stale)",
    ))

    snapshot_ok = result.status == "ok"
    checks.append(_health_check_line("Snapshot readable", snapshot_ok))

    rate_limit_hits = _safe_int(health.get("rate_limit_hits"))
    rl_ok = rate_limit_hits == 0
    rl_detail = "none" if rl_ok else f"`{rate_limit_hits}` hits"
    rl_source = health.get("last_rate_limit_source")
    source_detail = _rate_limit_source_detail(rl_source)
    if source_detail:
        rl_detail += f" · {source_detail}"
    next_action = _rate_limit_next_action(rl_source)
    if next_action:
        rl_detail += f" · action {next_action}"
    checks.append(_health_check_line("Rate-limit", rl_ok, rl_detail))

    pnl_state = health.get("daily_pnl_pause_state")
    pnl_ok = not pnl_state
    pnl_detail = "정상" if pnl_ok else f"`{pnl_state}`"
    pnl_reason = health.get("daily_pnl_pause_reason")
    if pnl_reason:
        pnl_detail += f" — {pnl_reason}"
    checks.append(_health_check_line("Daily PnL", pnl_ok, pnl_detail))

    exc_count = _safe_int(snapshot.get("exception_count"))
    exc_ok = exc_count == 0
    checks.append(_health_check_line(
        "예외",
        exc_ok,
        "none" if exc_ok else f"`{exc_count}`건",
    ))

    backoff_cycles = _safe_int(health.get("consecutive_backoff_cycles"))
    backoff_ok = backoff_cycles <= 1
    if backoff_cycles > 0:
        checks.append(_health_check_line(
            "연속 backoff",
            backoff_ok,
            f"`{backoff_cycles}` cycles",
        ))

    cycle_ms = health.get("last_cycle_elapsed_ms")
    if cycle_ms is not None:
        try:
            ms = round(float(cycle_ms))
            checks.append(f"⏱️ 마지막 cycle: `{ms}ms`")
        except (TypeError, ValueError):
            pass

    all_ok = all(
        line.startswith("✅") or line.startswith("⏱️")
        for line in checks
    )
    header = "✅ *시스템 건강 상태*" if all_ok else "⚠️ *시스템 건강 상태*"
    return "\n".join([header, ""] + checks)


def render_positions_snapshot_reply(
    *,
    path: Path = DEFAULT_SNAPSHOT_PATH,
    performance_summary_path: Path | None = None,
    now: datetime | None = None,
    stale_after_sec: int = DEFAULT_STALE_AFTER_SEC,
) -> str:
    result = read_slack_status_snapshot(
        path=path,
        now=now,
        stale_after_sec=stale_after_sec,
    )
    if result.snapshot is None:
        return _limited_text_for_result(result, command="positions")

    snapshot = result.snapshot
    if result.status == "unreadable":
        return _limited_text_for_result(result, command="positions")

    positions = []
    if performance_summary_path is not None or path == DEFAULT_SNAPSHOT_PATH:
        positions = _read_latest_performance_positions(
            path=performance_summary_path,
            now=now,
            stale_after_sec=stale_after_sec,
        )
    if not positions:
        raw_positions = snapshot.get("positions_snapshot")
        positions = raw_positions if isinstance(raw_positions, list) else []
    if not isinstance(positions, list) or not positions:
        return "\n".join(
            (
                "📂 *보유 종목*",
                "",
                "No positions in latest local snapshot.",
                "",
                _positions_freshness_line(result, snapshot),
            )
        )

    lines = ["📂 *보유 종목*", ""]
    if result.status == "stale":
        lines.extend((
            "⚠️ Last known positions are stale.",
            "",
        ))
    for pos in positions:
        if not isinstance(pos, Mapping):
            continue
        lines.append(_render_position_line(pos))
    lines.append("")
    lines.append(f"총 `{len(positions)}`종목")
    lines.append(_positions_freshness_line(result, snapshot))
    return "\n".join(lines)


# Orders-today lifecycle cluster: canonical implementations live in
# app.notifications.orders_today and are bound by the module-top import
# (legacy attribute seam preserved there).


def build_smoke_test_snapshot(*, timestamp: str | None = None) -> dict[str, Any]:
    now_text = timestamp or get_korean_now().isoformat()
    return {
        "timestamp": now_text,
        "session_status": "SMOKE_TEST",
        "last_heartbeat": now_text,
        "order_counters_today": {
            "buy_submitted": 1,
            "buy_succeeded": 1,
            "buy_failed": 0,
            "sell_submitted": 1,
            "sell_succeeded": 0,
            "sell_failed": 0,
        },
        "bottleneck_counters_today": {
            "repeated_bottleneck": 1,
            "kis_rate_limit": 0,
            "cash_budget_shortage": 0,
            "stale_snapshot": 0,
            "fallback_spike": 0,
        },
        "last_order_event": {
            "timestamp": now_text,
            "side": "BUY",
            "symbol": "SMOKE",
            "quantity": 1,
            "action": "order_succeeded",
        },
        "exception_count": 0,
        "daily_summary_sent": False,
    }


def run_smoke_test(*, path: Path = DEFAULT_SNAPSHOT_PATH) -> bool:
    return write_slack_status_snapshot(build_smoke_test_snapshot(), path=path)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write or inspect the Slack runtime status snapshot."
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Write a fake safe runtime status snapshot using atomic file replacement.",
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=DEFAULT_SNAPSHOT_PATH,
        help="Snapshot path. Defaults to data/runtime/slack_status_snapshot.json.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    if not args.smoke_test:
        parser.print_help()
        return 2
    if not run_smoke_test(path=args.path):
        print(f"failed to write Slack runtime status snapshot: {args.path}")
        return 1
    print(f"wrote Slack runtime status smoke snapshot: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
