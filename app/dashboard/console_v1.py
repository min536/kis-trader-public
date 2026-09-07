"""Pure builder for Dashboard Console v1 — operator visibility layer."""
from __future__ import annotations

from typing import Any

_LATENCY_CAP = 30


def _as_list(value: Any) -> list:
    """A malformed (non-list) field must not be coerced into a char list."""
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _build_latency(cycles: list[dict[str, Any]]) -> dict[str, Any]:
    raw_ms_values: list[float] = []
    for cycle in reversed(cycles):  # oldest first
        if not isinstance(cycle, dict):
            continue
        raw_ms = cycle.get("cycle_elapsed_ms")
        if raw_ms is None:
            raw_sub = cycle.get("raw") or {}
            raw_ms = raw_sub.get("cycle_elapsed_ms") if isinstance(raw_sub, dict) else None
        if raw_ms is not None:
            try:
                raw_ms_values.append(float(raw_ms))
            except (TypeError, ValueError):
                pass
    recent_ms = raw_ms_values[-_LATENCY_CAP:]
    avg_ms: float | None = None
    max_ms: float | None = None
    if recent_ms:
        avg_ms = round(sum(recent_ms) / len(recent_ms), 2)
        max_ms = max(recent_ms)
    return {
        "recent_ms": recent_ms,
        "avg_ms": avg_ms,
        "max_ms": max_ms,
        "samples": len(recent_ms),
    }


def _count_submitted_orders_today(
    orders: list[dict[str, Any]],
    *,
    now_epoch: float | None = None,
) -> int:
    from datetime import datetime, timezone

    today = (
        datetime.fromtimestamp(now_epoch, tz=timezone.utc).date()
        if now_epoch is not None
        else datetime.now(tz=timezone.utc).date()
    )
    count = 0
    for order in orders:
        if not isinstance(order, dict):
            continue
        if str(order.get("action") or "") not in {
            "order_submitted",
            "sell_order_submitted",
        }:
            continue
        timestamp = str(order.get("timestamp") or "").strip()
        if not timestamp:
            continue
        try:
            dt = datetime.fromisoformat(timestamp)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt.astimezone(timezone.utc).date() == today:
            count += 1
    return count


def build_console_overview(
    data: dict[str, Any],
    *,
    report: dict[str, Any] | None = None,
    account_signature: str | None = None,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    import time
    from datetime import datetime
    try:
        from app.core.time_utils import KOREA_TZ
    except Exception:
        import datetime as _dt
        KOREA_TZ = _dt.timezone(_dt.timedelta(hours=9))

    cycles: list[dict[str, Any]] = _as_list(data.get("cycles"))
    engine_state_view: dict[str, Any] = _as_dict(data.get("engine_state_view"))

    # derive market_session
    market_session = str(engine_state_view.get("market_session") or "").upper()

    # derive recent_cycle_at from cycles
    recent_cycle_at: str | None = None
    if cycles and isinstance(cycles[0], dict):
        recent_cycle_at = str(cycles[0].get("timestamp") or "").strip() or None

    # compute heartbeat_seconds
    heartbeat_s: int | None = None
    if recent_cycle_at:
        try:
            dt = datetime.fromisoformat(recent_cycle_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=KOREA_TZ)
            epoch_now = now_epoch if now_epoch is not None else time.time()
            heartbeat_s = max(0, int(epoch_now - dt.timestamp()))
        except Exception:
            heartbeat_s = None

    # derive status
    if heartbeat_s is None:
        status = "UNKNOWN"
    elif heartbeat_s >= 180:
        status = "STALE"
    elif market_session == "REGULAR":
        status = "RUNNING"
    else:
        status = "IDLE"

    # derive heartbeat_label
    if heartbeat_s is None:
        heartbeat_label = "시각 없음"
    elif heartbeat_s < 60:
        heartbeat_label = "fresh"
    else:
        heartbeat_label = f"{heartbeat_s // 60}m stale"

    # ── api_health ────────────────────────────────────────────────────────
    budget_status: dict[str, Any] = {}
    raw_budget = engine_state_view.get("budget_status")
    if isinstance(raw_budget, dict):
        budget_status = raw_budget

    request_used: int | None = None
    raw_used = budget_status.get("recent_request_count")
    if isinstance(raw_used, (int, float)):
        try:
            request_used = int(raw_used)
        except (ValueError, TypeError):
            pass

    request_limit: int | None = None
    try:
        from app.auth.settings import get_settings
        request_limit = int(get_settings().api_soft_max_requests_per_second)
    except Exception:
        pass

    request_remaining: int | None = None
    if request_used is not None and request_limit is not None:
        request_remaining = max(0, request_limit - request_used)

    # ── main_pid ──────────────────────────────────────────────────────────
    runtime_state: dict[str, Any] = _as_dict(data.get("runtime_state"))
    main_pid: int | None = None
    raw_pid = runtime_state.get("main_pid")
    if isinstance(raw_pid, (int, float)):
        try:
            main_pid = int(raw_pid)
        except (ValueError, TypeError):
            pass

    # ── account_signature ─────────────────────────────────────────────────
    if account_signature is None:
        raw_sig = runtime_state.get("account_signature")
        if isinstance(raw_sig, str) and raw_sig.strip():
            account_signature = raw_sig.strip()

    # ── tags ──────────────────────────────────────────────────────────────
    positions: list[dict[str, Any]] = _as_list(data.get("positions"))
    tags: dict[str, list[str]] = {}
    if positions:
        try:
            from app.core.symbol_tags import load_symbol_tags
            registry = load_symbol_tags()
            for position in positions:
                if not isinstance(position, dict):
                    continue
                sym = str(position.get("symbol") or "")
                if sym:
                    sym_tags = list(registry.get_tags(sym))
                    if sym_tags:
                        tags[sym] = sym_tags
        except Exception:
            tags = {}

    # ── orders_today ──────────────────────────────────────────────────────
    orders: list[dict[str, Any]] = _as_list(data.get("orders"))

    # count today's submitted orders directly
    try:
        from app.dashboard.metrics import build_top_summary as _bts
        _summary = _bts(data)
        today_count = int(_summary.get("today_order_count") or 0)
    except Exception:
        today_count = 0
    today_count = max(
        today_count,
        _count_submitted_orders_today(orders, now_epoch=now_epoch),
    )

    order_rows: list[dict[str, Any]] = []
    for order in orders[:8]:
        if not isinstance(order, dict):
            continue
        order_rows.append({
            "ts": str(order.get("timestamp") or ""),
            "action": str(order.get("action") or ""),
            "symbol": str(order.get("symbol") or ""),
            "symbol_name": str(order.get("symbol_name") or ""),
            "result": str(order.get("result") or ""),
        })

    backoff_count = 0
    raw_backoff = budget_status.get("rate_limit_hits")
    if isinstance(raw_backoff, (int, float)):
        try:
            backoff_count = int(raw_backoff)
        except (ValueError, TypeError):
            pass

    if backoff_count > 0:
        api_tone = "danger"
    elif request_remaining is None or request_limit is None or request_limit == 0:
        api_tone = "warning"
    elif request_remaining / request_limit < 0.2:
        api_tone = "warning"
    else:
        api_tone = "positive"

    return {
        "session": {
            "status": status,
            "heartbeat_seconds": heartbeat_s,
            "heartbeat_label": heartbeat_label,
            "market_session": market_session,
            "brake_state": str(engine_state_view.get("current_brake_state") or ""),
            "main_pid": main_pid,
        },
        "api_health": {
            "request_used": request_used,
            "request_limit": request_limit,
            "request_remaining": request_remaining,
            "backoff_count": backoff_count,
            "tone": api_tone,
        },
        "latency": _build_latency(cycles),
        "orders_today": {
            "count": today_count,
            "rows": order_rows,
        },
        "account_signature": account_signature,
        "tags": tags,
    }
