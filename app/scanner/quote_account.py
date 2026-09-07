from __future__ import annotations

from collections.abc import Callable
import os
import time
from dataclasses import dataclass, field
from typing import Any

from app.auth.settings import (
    Settings,
    classify_kis_base_url_env,
    resolve_kis_credential_profile,
)
from app.auth.token import issue_access_token_for
from app.auth.token import is_rate_limit_response
from app.core.kis_rate_limits import resolve_rest_rate_limit
from app.domestic_stock.quote import inquire_price, inquire_price_for_credentials

BUY_SCAN_QUOTE_KIS_ENV = "BUY_SCAN_QUOTE_KIS_ENV"
BUY_SCAN_QUOTE_MAX_REQUESTS_PER_SECOND = "BUY_SCAN_QUOTE_MAX_REQUESTS_PER_SECOND"
BUY_SCAN_QUOTE_MIN_INTER_REQUEST_SECONDS = "BUY_SCAN_QUOTE_MIN_INTER_REQUEST_SECONDS"
_DEFAULT_QUOTE_MAX_REQUESTS_PER_SECOND = 12
_DEFAULT_QUOTE_MIN_INTER_REQUEST_SECONDS = 0.09
_QUOTE_WINDOW_SECONDS = 1.0
_QUOTE_SAFETY_BUFFER_SECONDS = 0.005
_QUOTE_TIMESTAMPS_BY_ENV: dict[str, list[float]] = {}
_QUOTE_LAST_TS_BY_ENV: dict[str, float] = {}

READ_ONLY_QUOTE_ALLOWED_TR_IDS = frozenset({"FHKST01010100"})
READ_ONLY_QUOTE_ALLOWED_URL_SUFFIXES = (
    "/uapi/domestic-stock/v1/quotations/inquire-price",
)


def assert_read_only_quote_target(url: str, tr_id: str) -> None:
    """Fail-closed guard: the live read-only quote lane may only issue
    domestic quote requests. Any other URL path or TR id (e.g. an order
    endpoint / order TR) raises before the request is sent."""
    tr = str(tr_id or "")
    path = str(url or "")
    if tr not in READ_ONLY_QUOTE_ALLOWED_TR_IDS or not any(
        path.rstrip("?").endswith(suffix) or suffix in path
        for suffix in READ_ONLY_QUOTE_ALLOWED_URL_SUFFIXES
    ):
        raise RuntimeError(
            f"read-only quote lane blocked non-quote request: "
            f"tr_id={tr!r} url={path!r}"
        )


@dataclass(frozen=True)
class BuyScanQuoteContext:
    mode: str
    env: str
    token: str = field(repr=False)
    base_url: str = ""
    app_key: str = field(default="", repr=False)
    app_secret: str = field(default="", repr=False)

    @property
    def uses_execution_account(self) -> bool:
        return self.mode == "execution_account"


@dataclass(frozen=True)
class BuyScanQuotePrefetchResult:
    price_data_by_symbol: dict[str, dict[str, Any]]
    requested_symbols: tuple[str, ...]
    completed_symbols: tuple[str, ...]
    failed_symbols: tuple[str, ...]
    quote_account_mode: str
    quote_account_env: str
    elapsed_ms: float
    quote_response_ms: float
    throttle_sleep_ms: float
    skipped_deadline_symbols: tuple[str, ...] = ()
    budget_skipped_symbols: tuple[str, ...] = ()
    deadline_hit: bool = False
    deadline_seconds: float | None = None
    success_ratio: float = 0.0
    rate_limit_triggered: bool = False
    rate_limit_symbol: str | None = None
    rate_limit_message: str = ""
    request_timeout_seconds: float = 0.0
    max_attempts: int = 1
    timeout_count: int = 0
    # Monotonic clock() captured when each symbol's quote fetch completed
    # successfully — lets consumers compute true per-symbol quote age instead
    # of approximating with elapsed_ms. Empty for legacy/handmade results.
    collected_monotonic_by_symbol: dict[str, float] = field(default_factory=dict)


def _configured_quote_env() -> str:
    text = os.getenv(BUY_SCAN_QUOTE_KIS_ENV, "").strip().lower()
    if text in {"", "current", "execution", "execution_account"}:
        return ""
    if text not in {"mock", "live"}:
        raise ValueError(
            f"{BUY_SCAN_QUOTE_KIS_ENV} must be empty/current, mock, or live."
        )
    return text


def buy_scan_uses_separate_quote_account(settings: Settings) -> bool:
    configured_env = _configured_quote_env()
    if not configured_env:
        return False
    execution_env = classify_kis_base_url_env(
        str(getattr(settings, "base_url", "") or "")
    ) or ""
    return configured_env != execution_env


def _quote_lane_limits(env: str) -> tuple[int, float]:
    max_requests = int(
        os.getenv(
            BUY_SCAN_QUOTE_MAX_REQUESTS_PER_SECOND,
            str(_DEFAULT_QUOTE_MAX_REQUESTS_PER_SECOND),
        )
    )
    min_interval = float(
        os.getenv(
            BUY_SCAN_QUOTE_MIN_INTER_REQUEST_SECONDS,
            str(_DEFAULT_QUOTE_MIN_INTER_REQUEST_SECONDS),
        )
    )
    if max_requests <= 0:
        raise ValueError(f"{BUY_SCAN_QUOTE_MAX_REQUESTS_PER_SECOND} must be >= 1.")
    if min_interval < 0:
        raise ValueError(f"{BUY_SCAN_QUOTE_MIN_INTER_REQUEST_SECONDS} must be >= 0.")
    rate_limit = resolve_rest_rate_limit(
        environment=env,
        configured_max_requests_per_second=max_requests,
        configured_min_inter_request_seconds=min_interval,
    )
    return (
        rate_limit.max_requests_per_second,
        rate_limit.min_inter_request_seconds,
    )


def _throttle_read_only_quote_lane(env: str) -> float:
    max_requests, min_interval = _quote_lane_limits(env)
    key = str(env or "unknown").strip().lower() or "unknown"
    now = time.perf_counter()
    timestamps = [
        ts
        for ts in _QUOTE_TIMESTAMPS_BY_ENV.get(key, [])
        if now - ts < _QUOTE_WINDOW_SECONDS
    ]
    _QUOTE_TIMESTAMPS_BY_ENV[key] = timestamps

    sleep_seconds = 0.0
    last_ts = float(_QUOTE_LAST_TS_BY_ENV.get(key, 0.0) or 0.0)
    if min_interval > 0 and last_ts > 0:
        elapsed = now - last_ts
        if elapsed < min_interval:
            sleep_seconds = max(sleep_seconds, min_interval - elapsed)
    if len(timestamps) >= max_requests:
        sleep_seconds = max(
            sleep_seconds,
            (timestamps[0] + _QUOTE_WINDOW_SECONDS) - now + _QUOTE_SAFETY_BUFFER_SECONDS,
        )
    if sleep_seconds > 0:
        time.sleep(max(0.0, sleep_seconds))
        now = time.perf_counter()
        timestamps = [
            ts
            for ts in _QUOTE_TIMESTAMPS_BY_ENV.get(key, [])
            if now - ts < _QUOTE_WINDOW_SECONDS
        ]
        _QUOTE_TIMESTAMPS_BY_ENV[key] = timestamps

    _QUOTE_TIMESTAMPS_BY_ENV.setdefault(key, []).append(now)
    _QUOTE_LAST_TS_BY_ENV[key] = now
    return max(0.0, sleep_seconds)


def build_buy_scan_quote_context(
    *,
    settings: Settings,
    execution_token: str,
) -> BuyScanQuoteContext:
    configured_env = _configured_quote_env()
    execution_env = classify_kis_base_url_env(
        str(getattr(settings, "base_url", "") or "")
    ) or ""
    if not configured_env or configured_env == execution_env:
        return BuyScanQuoteContext(
            mode="execution_account",
            env=execution_env or "unknown",
            token=execution_token,
        )

    profile = resolve_kis_credential_profile(
        configured_env,
        allow_generic_fallback=False,
    )
    request_timeout_seconds = _quote_request_timeout_seconds(settings)
    max_attempts = _quote_max_attempts(settings)
    token = issue_access_token_for(
        base_url=profile.base_url,
        app_key=profile.app_key,
        app_secret=profile.app_secret,
        env=profile.env,
        request_timeout_seconds=request_timeout_seconds,
        max_attempts=max_attempts,
    )
    return BuyScanQuoteContext(
        mode="read_only_quote_account",
        env=profile.env,
        token=token,
        base_url=profile.base_url,
        app_key=profile.app_key,
        app_secret=profile.app_secret,
    )


def fetch_buy_scan_price(
    symbol: str,
    *,
    context: BuyScanQuoteContext,
    record_metrics: bool = True,
    request_timeout_seconds: float | None = None,
    max_attempts: int | None = None,
) -> dict[str, Any]:
    if context.uses_execution_account:
        return inquire_price(symbol, token=context.token)
    _throttle_read_only_quote_lane(context.env)
    return inquire_price_for_credentials(
        symbol,
        base_url=context.base_url,
        token=context.token,
        app_key=context.app_key,
        app_secret=context.app_secret,
        use_global_throttle=False,
        record_metrics=record_metrics,
        request_timeout_seconds=request_timeout_seconds,
        max_attempts=max_attempts,
        request_guard=assert_read_only_quote_target,
    )


def _prefetch_deadline_seconds(settings: Settings) -> float | None:
    if not bool(getattr(settings, "buy_scan_prefetch_deadline_enabled", True)):
        return None
    seconds = float(
        getattr(settings, "buy_scan_quote_prefetch_deadline_seconds", 18.0) or 0.0
    )
    return seconds if seconds > 0 else None


def _quote_request_timeout_seconds(settings: Settings) -> float:
    seconds = float(
        getattr(settings, "buy_scan_quote_request_timeout_seconds", 2.0) or 2.0
    )
    return max(0.001, seconds)


def _quote_max_attempts(settings: Settings) -> int:
    attempts = int(getattr(settings, "buy_scan_quote_max_attempts", 1) or 1)
    return max(1, attempts)


def _success_ratio(*, completed_count: int, requested_count: int) -> float:
    if requested_count <= 0:
        return 0.0
    return round(completed_count / requested_count, 4)


def prefetch_buy_scan_prices(
    *,
    symbols: tuple[str, ...],
    settings: Settings,
    execution_token: str,
    deadline_at: float | None = None,
    clock: Callable[[], float] = time.perf_counter,
    fetch_price_for_credentials: Callable[..., dict[str, Any]] | None = None,
    throttle_quote_lane: Callable[[str], float] = _throttle_read_only_quote_lane,
    lane_budget: Any | None = None,
) -> BuyScanQuotePrefetchResult:
    fetch_price_for_credentials = fetch_price_for_credentials or inquire_price_for_credentials
    started_at = clock()
    configured_deadline_seconds = _prefetch_deadline_seconds(settings)
    if deadline_at is None and configured_deadline_seconds is not None:
        deadline_at = started_at + configured_deadline_seconds
    if lane_budget is not None:
        deadline_at = (
            min(deadline_at, lane_budget.deadline_at)
            if deadline_at is not None
            else lane_budget.deadline_at
        )
        lane_budget.checkpoint("buy_quote_prefetch_start")
    request_timeout_seconds = _quote_request_timeout_seconds(settings)
    max_attempts = _quote_max_attempts(settings)
    unique_symbols = tuple(
        dict.fromkeys(
            str(symbol).strip()
            for symbol in symbols
            if str(symbol).strip()
        )
    )
    context = build_buy_scan_quote_context(
        settings=settings,
        execution_token=execution_token,
    )
    if context.uses_execution_account:
        return BuyScanQuotePrefetchResult(
            price_data_by_symbol={},
            requested_symbols=unique_symbols,
            completed_symbols=(),
            failed_symbols=(),
            quote_account_mode=context.mode,
            quote_account_env=context.env,
            elapsed_ms=round((clock() - started_at) * 1000, 1),
            quote_response_ms=0.0,
            throttle_sleep_ms=0.0,
            deadline_seconds=configured_deadline_seconds,
            success_ratio=0.0,
            request_timeout_seconds=request_timeout_seconds,
            max_attempts=max_attempts,
        )

    price_data_by_symbol: dict[str, dict[str, Any]] = {}
    completed_symbols: list[str] = []
    failed_symbols: list[str] = []
    skipped_deadline_symbols: list[str] = []
    budget_skipped_symbols: list[str] = []
    quote_response_ms = 0.0
    throttle_sleep_ms = 0.0
    rate_limit_triggered = False
    rate_limit_symbol: str | None = None
    rate_limit_message = ""
    deadline_hit = False
    timeout_count = 0
    collected_monotonic_by_symbol: dict[str, float] = {}

    for index, symbol in enumerate(unique_symbols):
        now = clock()
        if deadline_at is not None and now >= deadline_at:
            deadline_hit = True
            skipped_deadline_symbols.extend(unique_symbols[index:])
            budget_skipped_symbols.extend(unique_symbols[index:])
            break
        request_budget_seconds = request_timeout_seconds
        if deadline_at is not None:
            remaining_seconds = max(0.0, deadline_at - now)
            if remaining_seconds <= 0.01:
                deadline_hit = True
                skipped_deadline_symbols.extend(unique_symbols[index:])
                budget_skipped_symbols.extend(unique_symbols[index:])
                break
            request_budget_seconds = min(request_timeout_seconds, remaining_seconds)
        throttle_sleep_ms += throttle_quote_lane(context.env) * 1000.0
        now = clock()
        if deadline_at is not None and now >= deadline_at:
            deadline_hit = True
            skipped_deadline_symbols.extend(unique_symbols[index:])
            budget_skipped_symbols.extend(unique_symbols[index:])
            break
        if deadline_at is not None:
            remaining_seconds = max(0.0, deadline_at - now)
            if remaining_seconds <= 0.01:
                deadline_hit = True
                skipped_deadline_symbols.extend(unique_symbols[index:])
                budget_skipped_symbols.extend(unique_symbols[index:])
                break
            request_budget_seconds = min(request_timeout_seconds, remaining_seconds)
        request_started_at = clock()
        try:
            price_data = fetch_price_for_credentials(
                symbol,
                base_url=context.base_url,
                token=context.token,
                app_key=context.app_key,
                app_secret=context.app_secret,
                use_global_throttle=False,
                record_metrics=False,
                request_timeout_seconds=request_budget_seconds,
                max_attempts=max_attempts,
                request_guard=assert_read_only_quote_target,
            )
        except Exception as exc:
            failed_symbols.append(symbol)
            if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower():
                timeout_count += 1
            if "EGW00201" in str(exc) or "초당 거래건수" in str(exc):
                rate_limit_triggered = True
                rate_limit_symbol = symbol
                rate_limit_message = str(exc)
                break
            continue
        finally:
            quote_response_ms += (clock() - request_started_at) * 1000.0

        if is_rate_limit_response(price_data):
            failed_symbols.append(symbol)
            rate_limit_triggered = True
            rate_limit_symbol = symbol
            rate_limit_message = str(price_data)
            break
        price_data_by_symbol[symbol] = price_data
        completed_symbols.append(symbol)
        collected_monotonic_by_symbol[symbol] = clock()

    return BuyScanQuotePrefetchResult(
        price_data_by_symbol=price_data_by_symbol,
        requested_symbols=unique_symbols,
        completed_symbols=tuple(completed_symbols),
        failed_symbols=tuple(failed_symbols),
        quote_account_mode=context.mode,
        quote_account_env=context.env,
        elapsed_ms=round((clock() - started_at) * 1000, 1),
        quote_response_ms=round(quote_response_ms, 1),
        throttle_sleep_ms=round(throttle_sleep_ms, 1),
        skipped_deadline_symbols=tuple(skipped_deadline_symbols),
        budget_skipped_symbols=tuple(budget_skipped_symbols),
        deadline_hit=deadline_hit,
        deadline_seconds=configured_deadline_seconds,
        success_ratio=_success_ratio(
            completed_count=len(completed_symbols),
            requested_count=len(unique_symbols),
        ),
        rate_limit_triggered=rate_limit_triggered,
        rate_limit_symbol=rate_limit_symbol,
        rate_limit_message=rate_limit_message,
        request_timeout_seconds=request_timeout_seconds,
        max_attempts=max_attempts,
        timeout_count=timeout_count,
        collected_monotonic_by_symbol=collected_monotonic_by_symbol,
    )
