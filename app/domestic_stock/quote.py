from collections.abc import Callable
from typing import Any

from app.auth.settings import get_settings
from app.auth.token import (
    build_auth_headers_for,
    issue_access_token,
    request_json,
)


def inquire_price(symbol: str, token: str | None = None) -> dict[str, Any]:
    settings = get_settings()
    token = token or issue_access_token()

    return inquire_price_for_credentials(
        symbol,
        base_url=settings.base_url,
        token=token,
        app_key=settings.app_key,
        app_secret=settings.app_secret,
    )


def inquire_price_for_credentials(
    symbol: str,
    *,
    base_url: str,
    token: str,
    app_key: str,
    app_secret: str,
    use_global_throttle: bool = True,
    record_metrics: bool = True,
    request_timeout_seconds: float | None = None,
    max_attempts: int | None = None,
    request_guard: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    url = f"{str(base_url).rstrip('/')}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = build_auth_headers_for(
        token=token,
        tr_id="FHKST01010100",
        app_key=app_key,
        app_secret=app_secret,
    )
    params = {
        "fid_cond_mrkt_div_code": "J",
        "fid_input_iscd": symbol,
    }

    if request_guard is not None:
        request_guard(url, "FHKST01010100")

    return request_json(
        "GET",
        url,
        headers=headers,
        params=params,
        error_label="현재가 조회",
        use_throttle=use_global_throttle,
        record_metrics=record_metrics,
        request_timeout_seconds=request_timeout_seconds,
        max_attempts=max_attempts,
        retry_delays_seconds=() if max_attempts == 1 else None,
    )
