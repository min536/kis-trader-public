from __future__ import annotations

from datetime import datetime, timedelta

API_TRANSIENT_BACKOFF_WINDOW_SECONDS = 600
API_TRANSIENT_BASE_BACKOFF_SECONDS = 60
API_TRANSIENT_MAX_BACKOFF_SECONDS = 300


def prune_recent_rate_limit_hits(
    api_budget_state: dict[str, object],
    *,
    now: datetime,
) -> list[datetime]:
    hits = [
        value
        for value in list(api_budget_state.get("recent_rate_limit_hit_times", []))
        if isinstance(value, datetime)
        and (now - value).total_seconds() <= 600
    ]
    api_budget_state["recent_rate_limit_hit_times"] = hits
    return hits


def prune_recent_transient_api_errors(
    api_budget_state: dict[str, object],
    *,
    now: datetime,
) -> list[datetime]:
    hits = [
        value
        for value in list(api_budget_state.get("recent_transient_error_times", []))
        if isinstance(value, datetime)
        and (now - value).total_seconds() <= API_TRANSIENT_BACKOFF_WINDOW_SECONDS
    ]
    api_budget_state["recent_transient_error_times"] = hits
    return hits


def summarize_api_budget_state(
    api_budget_state: dict[str, object],
    *,
    now: datetime,
) -> dict[str, object]:
    prune_api_budget_requests(api_budget_state, now=now)
    backoff_until = api_budget_state.get("backoff_until")
    backoff_remaining_seconds = 0
    if isinstance(backoff_until, datetime) and backoff_until > now:
        backoff_remaining_seconds = max(
            0,
            int((backoff_until - now).total_seconds()),
        )
    transient_until = api_budget_state.get("transient_error_until")
    transient_backoff_remaining_seconds = 0
    if isinstance(transient_until, datetime) and transient_until > now:
        transient_backoff_remaining_seconds = max(
            0,
            int((transient_until - now).total_seconds()),
        )
    return {
        "recent_request_count": len(api_budget_state.get("recent_requests", [])),
        "quotes_used_this_tick": int(api_budget_state.get("quotes_used_this_tick", 0)),
        "backoff_remaining_seconds": backoff_remaining_seconds,
        "transient_backoff_remaining_seconds": transient_backoff_remaining_seconds,
        "rate_limit_hits": int(api_budget_state.get("rate_limit_hits", 0) or 0),
        "last_rate_limit_source": (
            str(api_budget_state.get("last_rate_limit_source") or "").strip()
            or None
        ),
        "recent_rate_limit_hits_10m": len(
            prune_recent_rate_limit_hits(api_budget_state, now=now)
        ),
        "consecutive_backoff_cycles": int(
            api_budget_state.get("consecutive_backoff_cycles", 0) or 0
        ),
        "transient_error_hits": int(
            api_budget_state.get("transient_error_hits", 0) or 0
        ),
        "last_transient_error_source": (
            str(api_budget_state.get("last_transient_error_source") or "").strip()
            or None
        ),
        "recent_transient_error_hits_10m": len(
            prune_recent_transient_api_errors(api_budget_state, now=now)
        ),
    }


def api_budget_update_rate_limit_recovery_state(
    api_budget_state: dict[str, object],
    *,
    now: datetime,
    rate_limit_source: str | None,
) -> None:
    normalized_source = str(rate_limit_source or "").strip() or None
    if normalized_source:
        api_budget_state["last_rate_limit_source"] = normalized_source
        return
    if api_budget_backoff_active(api_budget_state, now=now):
        return
    api_budget_state["last_rate_limit_source"] = None
    api_budget_state["rate_limit_hits"] = 0


def api_budget_update_transient_recovery_state(
    api_budget_state: dict[str, object],
    *,
    now: datetime,
    transient_error_source: str | None,
) -> None:
    normalized_source = str(transient_error_source or "").strip() or None
    if normalized_source:
        api_budget_state["last_transient_error_source"] = normalized_source
        return
    if api_budget_transient_backoff_active(api_budget_state, now=now):
        return
    api_budget_state["last_transient_error_source"] = None
    api_budget_state["transient_error_hits"] = 0


def api_budget_backoff_active(
    api_budget_state: dict[str, object],
    *,
    now: datetime,
) -> bool:
    backoff_until = api_budget_state.get("backoff_until")
    return isinstance(backoff_until, datetime) and backoff_until > now


def api_budget_transient_backoff_active(
    api_budget_state: dict[str, object],
    *,
    now: datetime,
) -> bool:
    transient_until = api_budget_state.get("transient_error_until")
    return isinstance(transient_until, datetime) and transient_until > now


def api_budget_backoff_remaining_seconds(
    api_budget_state: dict[str, object],
    *,
    now: datetime,
) -> float:
    """Return how many seconds of backoff remain (0.0 if none active)."""
    backoff_until = api_budget_state.get("backoff_until")
    if not isinstance(backoff_until, datetime):
        return 0.0
    return max(0.0, (backoff_until - now).total_seconds())


def api_budget_transient_backoff_remaining_seconds(
    api_budget_state: dict[str, object],
    *,
    now: datetime,
) -> float:
    transient_until = api_budget_state.get("transient_error_until")
    if not isinstance(transient_until, datetime):
        return 0.0
    return max(0.0, (transient_until - now).total_seconds())


def api_budget_can_quote(
    api_budget_state: dict[str, object],
    *,
    quote_cost: int = 1,
) -> bool:
    used_quotes = int(api_budget_state.get("quotes_used_this_tick", 0))
    soft_max_quotes = int(api_budget_state.get("soft_max_quotes_per_tick", 1))
    return used_quotes + max(0, quote_cost) <= soft_max_quotes


def api_budget_remaining_quotes(api_budget_state: dict[str, object]) -> int:
    return max(
        0,
        int(api_budget_state.get("soft_max_quotes_per_tick", 1))
        - int(api_budget_state.get("quotes_used_this_tick", 0)),
    )


def prune_api_budget_requests(api_budget_state: dict[str, object], *, now: datetime) -> None:
    recent_requests = [
        request_time
        for request_time in api_budget_state.get("recent_requests", [])
        if isinstance(request_time, datetime)
        and (now - request_time).total_seconds() < 1.0
    ]
    api_budget_state["recent_requests"] = recent_requests


def api_budget_can_request(
    api_budget_state: dict[str, object],
    *,
    now: datetime,
    request_cost: int = 1,
    allow_during_backoff: bool = False,
) -> bool:
    prune_api_budget_requests(api_budget_state, now=now)
    if (
        api_budget_backoff_active(api_budget_state, now=now)
        and not allow_during_backoff
    ):
        return False
    if (
        api_budget_transient_backoff_active(api_budget_state, now=now)
        and not allow_during_backoff
    ):
        return False
    return (
        len(api_budget_state.get("recent_requests", [])) + request_cost
        <= int(api_budget_state.get("soft_max_requests_per_second", 1))
    )


def api_budget_remaining_requests(
    api_budget_state: dict[str, object],
    *,
    now: datetime,
    allow_during_backoff: bool = False,
) -> int:
    prune_api_budget_requests(api_budget_state, now=now)
    if (
        api_budget_backoff_active(api_budget_state, now=now)
        and not allow_during_backoff
    ):
        return 0
    if (
        api_budget_transient_backoff_active(api_budget_state, now=now)
        and not allow_during_backoff
    ):
        return 0
    return max(
        0,
        int(api_budget_state.get("soft_max_requests_per_second", 1))
        - len(api_budget_state.get("recent_requests", [])),
    )


def api_budget_request_window_size(
    api_budget_state: dict[str, object],
    *,
    now: datetime,
) -> int:
    prune_api_budget_requests(api_budget_state, now=now)
    return len(api_budget_state.get("recent_requests", []))


def api_budget_preserves_buy_scan_reserve(
    api_budget_state: dict[str, object],
    *,
    now: datetime,
    request_cost: int,
    quote_cost: int,
    request_reserve: int,
    quote_reserve: int,
) -> bool:
    remaining_requests = api_budget_remaining_requests(api_budget_state, now=now)
    remaining_quotes = api_budget_remaining_quotes(api_budget_state)
    return (
        remaining_requests - max(0, request_cost) >= max(0, request_reserve)
        and remaining_quotes - max(0, quote_cost) >= max(0, quote_reserve)
    )


def build_api_budget_state(settings) -> dict[str, object]:
    return {
        "recent_requests": [],
        "quotes_used_this_tick": 0,
        "backoff_until": None,
        "rate_limit_hits": 0,
        "last_rate_limit_source": None,
        "recent_rate_limit_hit_times": [],
        "transient_error_until": None,
        "transient_error_hits": 0,
        "last_transient_error_source": None,
        "recent_transient_error_times": [],
        "consecutive_backoff_cycles": 0,
        "degraded_mode_until": None,
        "degraded_mode_reason": None,
        "soft_max_requests_per_second": settings.api_soft_max_requests_per_second,
        "soft_max_quotes_per_tick": settings.api_soft_max_quotes_per_tick,
        "backoff_seconds_on_rate_limit": settings.api_backoff_seconds_on_rate_limit,
    }


def api_budget_register_request(
    api_budget_state: dict[str, object],
    *,
    now: datetime,
    quote_cost: int = 0,
) -> None:
    prune_api_budget_requests(api_budget_state, now=now)
    api_budget_state.setdefault("recent_requests", []).append(now)
    api_budget_state["quotes_used_this_tick"] = int(
        api_budget_state.get("quotes_used_this_tick", 0)
    ) + max(0, quote_cost)


def api_budget_register_requests(
    api_budget_state: dict[str, object],
    *,
    now: datetime,
    request_count: int = 1,
    quote_cost: int = 0,
) -> None:
    for index in range(max(0, int(request_count or 0))):
        api_budget_register_request(
            api_budget_state,
            now=now,
            quote_cost=quote_cost if index == 0 else 0,
        )


def api_budget_register_measured_extra_requests(
    api_budget_state: dict[str, object],
    *,
    request_delta: dict[str, object],
    category: str,
    already_registered_count: int,
    now: datetime,
) -> int:
    categories = request_delta.get("categories") if isinstance(request_delta, dict) else {}
    category_bucket = (
        (categories or {}).get(category) if isinstance(categories, dict) else {}
    )
    measured_count = int((category_bucket or {}).get("count", 0) or 0)
    extra_count = max(0, measured_count - max(0, int(already_registered_count or 0)))
    if extra_count > 0:
        api_budget_register_requests(
            api_budget_state,
            now=now,
            request_count=extra_count,
        )
    return extra_count


def api_budget_note_transient_api_error(
    api_budget_state: dict[str, object],
    *,
    now: datetime,
    source: str | None = None,
) -> None:
    recent_hits = prune_recent_transient_api_errors(api_budget_state, now=now)
    recent_hits.append(now)
    api_budget_state["recent_transient_error_times"] = recent_hits
    api_budget_state["last_transient_error_source"] = (
        str(source or "").strip() or None
    )
    hits = len(recent_hits)
    api_budget_state["transient_error_hits"] = hits
    backoff_seconds = min(
        API_TRANSIENT_BASE_BACKOFF_SECONDS * (2 ** min(hits - 1, 3)),
        API_TRANSIENT_MAX_BACKOFF_SECONDS,
    )
    api_budget_state["transient_error_until"] = now + timedelta(
        seconds=backoff_seconds
    )
    print(
        "[info] transient API backoff scheduled"
        f" | source={api_budget_state['last_transient_error_source'] or 'unknown'}"
        f" | hits_10m={hits} | backoff={backoff_seconds}s"
    )


def api_budget_min_wait_for_request_slot(
    api_budget_state: dict[str, object],
    *,
    now: datetime,
    request_cost: int = 1,
    request_reserve: int = 0,
) -> float:
    prune_api_budget_requests(api_budget_state, now=now)
    recent_requests = list(api_budget_state.get("recent_requests", []))
    limit = max(1, int(api_budget_state.get("soft_max_requests_per_second", 1)))
    needed_total = len(recent_requests) + max(0, request_cost) + max(0, request_reserve)
    overflow = needed_total - limit
    if overflow <= 0 or not recent_requests:
        return 0.0
    pivot_index = min(len(recent_requests) - 1, overflow - 1)
    pivot_time = recent_requests[pivot_index]
    return max(0.0, 1.0 - (now - pivot_time).total_seconds()) + 0.05


def request_metrics_delta(
    before: dict[str, object] | None,
    after: dict[str, object] | None,
) -> dict[str, object]:
    before_categories = (before or {}).get("categories") or {}
    after_categories = (after or {}).get("categories") or {}
    categories: dict[str, dict[str, float | int]] = {}
    for category in ("quote", "balance", "orderable", "order", "token", "other"):
        before_bucket = before_categories.get(category) or {}
        after_bucket = after_categories.get(category) or {}
        categories[category] = {
            "count": max(
                0,
                int(after_bucket.get("count", 0)) - int(before_bucket.get("count", 0)),
            ),
            "elapsed_ms": round(
                max(
                    0.0,
                    float(after_bucket.get("elapsed_ms", 0.0))
                    - float(before_bucket.get("elapsed_ms", 0.0)),
                ),
                1,
            ),
        }
    return {
        "total_requests": max(
            0,
            int((after or {}).get("total_requests", 0))
            - int((before or {}).get("total_requests", 0)),
        ),
        "total_elapsed_ms": round(
            max(
                0.0,
                float((after or {}).get("total_elapsed_ms", 0.0))
                - float((before or {}).get("total_elapsed_ms", 0.0)),
            ),
            1,
        ),
        "categories": categories,
    }


def buy_scan_reserve_active(
    *,
    settings,
    session_status,
    buy_scan_due: bool,
) -> bool:
    if not buy_scan_due or session_status is None:
        return False
    if getattr(session_status, "session", "") != "REGULAR":
        return False
    return (
        settings.api_buy_scan_min_request_reserve > 0
        or settings.api_buy_scan_min_quote_reserve > 0
    )
