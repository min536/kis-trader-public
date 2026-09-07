"""Current KIS REST gateway limits and conservative client-side pacing."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

KIS_MOCK_REST_MAX_REQUESTS_PER_SECOND = 1
KIS_LIVE_REST_MAX_REQUESTS_PER_SECOND = 18
KIS_MOCK_MIN_INTER_REQUEST_SECONDS = 1.05
KIS_LIVE_MIN_INTER_REQUEST_SECONDS = 0.1


@dataclass(frozen=True)
class RestRateLimit:
    max_requests_per_second: int
    min_inter_request_seconds: float


def environment_from_base_url(base_url: str) -> str | None:
    host = str(urlparse(str(base_url or "").strip()).hostname or "").lower()
    if host == "openapivts.koreainvestment.com":
        return "mock"
    if host == "openapi.koreainvestment.com":
        return "live"
    return None


def resolve_rest_rate_limit(
    *,
    environment: str | None,
    configured_max_requests_per_second: int,
    configured_min_inter_request_seconds: float,
) -> RestRateLimit:
    """Clamp operator settings to the KIS limits effective 2026-04-20.

    The runtime's soft request budget is a scheduling control and can remain
    higher than the gateway limit. This resolver is used at the final request
    boundary so hash/order pairs still serialize safely in mock trading.
    """
    configured_max = max(1, int(configured_max_requests_per_second))
    configured_floor = max(0.0, float(configured_min_inter_request_seconds))
    normalized_env = str(environment or "").strip().lower()

    if normalized_env == "mock":
        return RestRateLimit(
            max_requests_per_second=min(
                configured_max,
                KIS_MOCK_REST_MAX_REQUESTS_PER_SECOND,
            ),
            min_inter_request_seconds=max(
                configured_floor,
                KIS_MOCK_MIN_INTER_REQUEST_SECONDS,
            ),
        )
    if normalized_env == "live":
        return RestRateLimit(
            max_requests_per_second=min(
                configured_max,
                KIS_LIVE_REST_MAX_REQUESTS_PER_SECOND,
            ),
            min_inter_request_seconds=max(
                configured_floor,
                KIS_LIVE_MIN_INTER_REQUEST_SECONDS,
            ),
        )
    return RestRateLimit(
        max_requests_per_second=configured_max,
        min_inter_request_seconds=configured_floor,
    )
