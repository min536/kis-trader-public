from collections import deque
import time
from typing import Any

from app.auth.settings import get_settings
from app.core.kis_rate_limits import (
    environment_from_base_url,
    resolve_rest_rate_limit,
)

_WINDOW_SECONDS = 1.0
_SAFETY_BUFFER_SECONDS = 0.05
_ADAPTIVE_PENALTY_TTL_SECONDS = 120.0
_ADAPTIVE_EXTRA_DELAY_SECONDS = 0.3
# KIS API rejects bursts faster than the nominal per-second budget in practice;
# keep a conservative default floor while allowing settings/env overrides.
_MIN_INTER_REQUEST_SECONDS = 0.8
_LAST_CATEGORY_TS: dict[str, float] = {
    "request": 0.0,
    "quote": 0.0,
}
_LAST_GLOBAL_TS = 0.0
_REQUEST_TIMESTAMPS: deque[float] = deque()
_QUOTE_TIMESTAMPS: deque[float] = deque()
_THROTTLE_METRICS: dict[str, Any] = {}
_ADAPTIVE_PENALTY_UNTIL: float = 0.0
_ADAPTIVE_PENALTY_EXTRA_DELAY_SECONDS: float = 0.0
_LAST_RATE_LIMIT_SOURCE: str | None = None


def _empty_metrics() -> dict[str, Any]:
    return {
        "total_sleep_ms": 0.0,
        "sleep_events": 0,
        "min_sleep_ms": None,
        "max_sleep_ms": 0.0,
        "immediate_pass_count": 0,
        "categories": {
            "request": {
                "calls": 0,
                "sleep_ms": 0.0,
                "sleep_events": 0,
                "immediate_pass_count": 0,
            },
            "quote": {
                "calls": 0,
                "sleep_ms": 0.0,
                "sleep_events": 0,
                "immediate_pass_count": 0,
            },
        },
    }


def reset_throttle_metrics() -> None:
    _THROTTLE_METRICS.clear()
    _THROTTLE_METRICS.update(_empty_metrics())


def reset_throttle_state() -> None:
    """Clear metrics *and* the rolling request ledger (test isolation)."""
    global _LAST_GLOBAL_TS
    reset_throttle_metrics()
    _REQUEST_TIMESTAMPS.clear()
    _QUOTE_TIMESTAMPS.clear()
    _LAST_CATEGORY_TS.clear()
    _LAST_GLOBAL_TS = 0.0


def _ensure_metrics() -> None:
    if not _THROTTLE_METRICS:
        reset_throttle_metrics()


def _prune_old_timestamps(now: float) -> None:
    threshold = now - _WINDOW_SECONDS
    while _REQUEST_TIMESTAMPS and _REQUEST_TIMESTAMPS[0] <= threshold:
        _REQUEST_TIMESTAMPS.popleft()
    while _QUOTE_TIMESTAMPS and _QUOTE_TIMESTAMPS[0] <= threshold:
        _QUOTE_TIMESTAMPS.popleft()


def _update_metrics(*, category: str, sleep_seconds: float) -> None:
    _ensure_metrics()
    category_metrics = (_THROTTLE_METRICS.get("categories") or {}).get(category)
    if category_metrics is None:
        return

    _THROTTLE_METRICS["total_sleep_ms"] = float(
        _THROTTLE_METRICS.get("total_sleep_ms", 0.0)
    ) + (sleep_seconds * 1000.0)
    category_metrics["calls"] = int(category_metrics.get("calls", 0)) + 1

    if sleep_seconds > 0:
        sleep_ms = sleep_seconds * 1000.0
        _THROTTLE_METRICS["sleep_events"] = int(
            _THROTTLE_METRICS.get("sleep_events", 0)
        ) + 1
        _THROTTLE_METRICS["max_sleep_ms"] = max(
            float(_THROTTLE_METRICS.get("max_sleep_ms", 0.0)),
            sleep_ms,
        )
        current_min = _THROTTLE_METRICS.get("min_sleep_ms")
        _THROTTLE_METRICS["min_sleep_ms"] = (
            sleep_ms if current_min is None else min(float(current_min), sleep_ms)
        )
        category_metrics["sleep_ms"] = float(category_metrics.get("sleep_ms", 0.0)) + sleep_ms
        category_metrics["sleep_events"] = int(category_metrics.get("sleep_events", 0)) + 1
    else:
        _THROTTLE_METRICS["immediate_pass_count"] = int(
            _THROTTLE_METRICS.get("immediate_pass_count", 0)
        ) + 1
        category_metrics["immediate_pass_count"] = int(
            category_metrics.get("immediate_pass_count", 0)
        ) + 1


def get_throttle_metrics_summary() -> dict[str, Any]:
    _ensure_metrics()
    categories = _THROTTLE_METRICS.get("categories") or {}
    request_bucket = categories.get("request") or {}
    quote_bucket = categories.get("quote") or {}
    return {
        "total_sleep_ms": round(float(_THROTTLE_METRICS.get("total_sleep_ms", 0.0)), 1),
        "sleep_events": int(_THROTTLE_METRICS.get("sleep_events", 0)),
        "min_sleep_ms": (
            None
            if _THROTTLE_METRICS.get("min_sleep_ms") is None
            else round(float(_THROTTLE_METRICS.get("min_sleep_ms", 0.0)), 1)
        ),
        "max_sleep_ms": round(float(_THROTTLE_METRICS.get("max_sleep_ms", 0.0)), 1),
        "immediate_pass_count": int(_THROTTLE_METRICS.get("immediate_pass_count", 0)),
        "request_calls": int(request_bucket.get("calls", 0)),
        "quote_calls": int(quote_bucket.get("calls", 0)),
        "quote_sleep_ms": round(float(quote_bucket.get("sleep_ms", 0.0)), 1),
        "quote_sleep_events": int(quote_bucket.get("sleep_events", 0)),
        "quote_immediate_pass_count": int(quote_bucket.get("immediate_pass_count", 0)),
        "quote_average_sleep_ms": round(
            (
                float(quote_bucket.get("sleep_ms", 0.0))
                / int(quote_bucket.get("sleep_events", 0))
            ),
            1,
        )
        if int(quote_bucket.get("sleep_events", 0)) > 0
        else 0.0,
        "request_window_count": len(_REQUEST_TIMESTAMPS),
        "quote_window_count": len(_QUOTE_TIMESTAMPS),
    }


def note_rate_limit(
    *,
    extra_delay_ms: float | None = None,
    ttl_seconds: float | None = None,
    source: str | None = None,
) -> None:
    global _ADAPTIVE_PENALTY_UNTIL
    global _ADAPTIVE_PENALTY_EXTRA_DELAY_SECONDS
    global _LAST_RATE_LIMIT_SOURCE
    now = time.perf_counter()
    normalized_source = str(source or "").strip() or None
    _LAST_RATE_LIMIT_SOURCE = normalized_source
    _ADAPTIVE_PENALTY_EXTRA_DELAY_SECONDS = max(
        0.0,
        float(extra_delay_ms / 1000.0)
        if extra_delay_ms is not None
        else _ADAPTIVE_EXTRA_DELAY_SECONDS,
    )
    _ADAPTIVE_PENALTY_UNTIL = now + max(
        0.0,
        float(ttl_seconds if ttl_seconds is not None else _ADAPTIVE_PENALTY_TTL_SECONDS),
    )


def get_adaptive_pacing_summary() -> dict[str, float | bool | str]:
    now = time.perf_counter()
    remaining_seconds = max(0.0, _ADAPTIVE_PENALTY_UNTIL - now)
    active = remaining_seconds > 0 and _ADAPTIVE_PENALTY_EXTRA_DELAY_SECONDS > 0
    return {
        "active": active,
        "extra_delay_ms": round(
            (_ADAPTIVE_PENALTY_EXTRA_DELAY_SECONDS * 1000.0) if active else 0.0,
            1,
        ),
        "remaining_ms": round(remaining_seconds * 1000.0, 1),
        "last_rate_limit_source": _LAST_RATE_LIMIT_SOURCE or "",
    }


def throttle(
    minimum_interval: float | None = None,
    *,
    category: str = "request",
) -> dict[str, float | int | None | str]:
    global _LAST_GLOBAL_TS
    category_name = "quote" if category == "quote" else "request"
    settings = get_settings()
    quote_limit = max(1, int(settings.api_soft_max_quotes_per_tick))
    rate_limit = resolve_rest_rate_limit(
        environment=environment_from_base_url(
            str(getattr(settings, "base_url", "") or "")
        ),
        configured_max_requests_per_second=int(
            settings.api_soft_max_requests_per_second
        ),
        configured_min_inter_request_seconds=float(
            getattr(
                settings,
                "api_min_inter_request_seconds",
                _MIN_INTER_REQUEST_SECONDS,
            )
            or 0.0
        ),
    )
    request_limit = rate_limit.max_requests_per_second
    configured_floor = rate_limit.min_inter_request_seconds

    # Auto-derive minimum inter-call spacing from rate limit when not specified.
    # Without this, throttle allows burst calls (e.g. 3 calls in 0ms) which
    # triggers KIS EGW00201 even though the per-second count is within limit.
    # Evenly distributing calls across the 1-second window prevents bursts.
    if minimum_interval is None:
        minimum_interval = max(1.0 / request_limit, configured_floor)

    now = time.perf_counter()
    _prune_old_timestamps(now)
    adaptive_summary = get_adaptive_pacing_summary()
    adaptive_extra_seconds = (
        float(adaptive_summary.get("extra_delay_ms", 0.0) or 0.0) / 1000.0
    )

    sleep_seconds = 0.0
    if minimum_interval > 0:
        # KIS applies the per-second ceiling at the session level, not at our
        # local request category boundary.  A balance/quote/orderable sequence
        # can therefore trip EGW00201 even when each category is individually
        # spaced.  Keep the category metric, but enforce the floor globally.
        category_elapsed = now - float(_LAST_CATEGORY_TS.get(category_name, 0.0))
        global_elapsed = now - float(_LAST_GLOBAL_TS)
        if category_elapsed < minimum_interval:
            sleep_seconds = max(sleep_seconds, minimum_interval - category_elapsed)
        if global_elapsed < minimum_interval:
            sleep_seconds = max(sleep_seconds, minimum_interval - global_elapsed)

    if len(_REQUEST_TIMESTAMPS) >= request_limit:
        sleep_seconds = max(
            sleep_seconds,
            (_REQUEST_TIMESTAMPS[0] + _WINDOW_SECONDS) - now + _SAFETY_BUFFER_SECONDS,
        )

    if category_name == "quote" and len(_QUOTE_TIMESTAMPS) >= quote_limit:
        sleep_seconds = max(
            sleep_seconds,
            (_QUOTE_TIMESTAMPS[0] + _WINDOW_SECONDS) - now + _SAFETY_BUFFER_SECONDS,
        )

    if adaptive_extra_seconds > 0:
        sleep_seconds += adaptive_extra_seconds

    if sleep_seconds > 0:
        time.sleep(max(0.0, sleep_seconds))
        now = time.perf_counter()
        _prune_old_timestamps(now)

    _REQUEST_TIMESTAMPS.append(now)
    if category_name == "quote":
        _QUOTE_TIMESTAMPS.append(now)
    _LAST_CATEGORY_TS[category_name] = now
    _LAST_GLOBAL_TS = now
    _update_metrics(category=category_name, sleep_seconds=max(0.0, sleep_seconds))

    return {
        "category": category_name,
        "sleep_ms": round(max(0.0, sleep_seconds) * 1000.0, 1),
        "request_window_count": len(_REQUEST_TIMESTAMPS),
        "quote_window_count": len(_QUOTE_TIMESTAMPS),
    }


def wait_for_request_slots(count: int) -> float:
    """Block until the rolling window can absorb ``count`` more requests.

    An order submission is *always* a hashkey POST followed by the order POST —
    two requests KIS counts inside one per-key second. Callers previously guarded
    that pair with a fixed ``time.sleep(1.0)``, which is measured from code time
    rather than from this ledger: a quote sent 0.9s earlier still shared the
    gateway second and the order POST (the last request) took the EGW00201.

    Reserves headroom only — it records nothing. The two real requests append
    their own timestamps when they pass through :func:`throttle`, so this stays
    correct whether or not the hashkey call is itself throttled.

    Returns the seconds actually slept.
    """
    settings = get_settings()
    rate_limit = resolve_rest_rate_limit(
        environment=environment_from_base_url(
            str(getattr(settings, "base_url", "") or "")
        ),
        configured_max_requests_per_second=int(
            settings.api_soft_max_requests_per_second
        ),
        configured_min_inter_request_seconds=float(
            getattr(settings, "api_min_inter_request_seconds", _MIN_INTER_REQUEST_SECONDS)
            or 0.0
        ),
    )
    request_limit = rate_limit.max_requests_per_second
    # A limit smaller than the pair degrades to sequential spacing (the pre-T4
    # sleep behavior) — reserving must never become a hard failure on the order
    # path, whatever the operator configured.
    reserve = min(max(1, int(count)), request_limit)

    configured_floor = rate_limit.min_inter_request_seconds
    minimum_interval = max(1.0 / request_limit, configured_floor)

    now = time.perf_counter()
    _prune_old_timestamps(now)

    sleep_seconds = 0.0
    global_elapsed = now - float(_LAST_GLOBAL_TS)
    if _LAST_GLOBAL_TS and global_elapsed < minimum_interval:
        sleep_seconds = minimum_interval - global_elapsed

    # Room for `reserve` more means the window may hold at most limit - reserve now.
    overflow = len(_REQUEST_TIMESTAMPS) - (request_limit - reserve)
    if overflow > 0:
        # _REQUEST_TIMESTAMPS[overflow - 1] is the oldest entry that must age out.
        expiry = _REQUEST_TIMESTAMPS[overflow - 1] + _WINDOW_SECONDS
        sleep_seconds = max(sleep_seconds, expiry - now + _SAFETY_BUFFER_SECONDS)

    if sleep_seconds > 0:
        time.sleep(sleep_seconds)
        _prune_old_timestamps(time.perf_counter())
        return sleep_seconds
    return 0.0
