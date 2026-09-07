import json
import os
import time
from typing import Any
from urllib import error, parse, request

from app.auth.settings import get_settings, get_token_cache_path, validate_kis_base_url
from app.auth.token_cache_identity import (
    cache_payload_matches_app_key,
    stamp_app_key_fingerprint,
)
from app.core.throttle import note_rate_limit, throttle

TOKEN_TTL_SECONDS = 6 * 60 * 60
REQUEST_TIMEOUT_SECONDS = 10.0
TRANSIENT_REQUEST_RETRY_DELAYS_SECONDS = (0.5, 1.5, 3.0, 6.0)
DISABLE_CREDENTIAL_FILE_ACCESS_ENV = "KIS_TRADER_DISABLE_CREDENTIAL_FILES"
REQUEST_METRIC_CATEGORIES = (
    "quote",
    "balance",
    "orderable",
    "order",
    "token",
    "other",
)
_REQUEST_METRICS: dict[str, Any] = {}


class ApiHttpError(RuntimeError):
    def __init__(self, message: str, status_code: int, data: dict[str, Any]) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.data = data


def is_rate_limit_response(data: dict[str, Any]) -> bool:
    text = " ".join(
        str(data.get(key, ""))
        for key in ("msg1", "message", "msg_cd", "rt_cd", "raw_text")
    )
    return ("초당 거래건수" in text) or ("EGW00201" in text)


def _empty_request_metrics() -> dict[str, Any]:
    return {
        "total_requests": 0,
        "total_elapsed_ms": 0.0,
        "categories": {
            category: {"count": 0, "elapsed_ms": 0.0}
            for category in REQUEST_METRIC_CATEGORIES
        },
        "endpoints": {},
    }


def reset_request_metrics() -> None:
    _REQUEST_METRICS.clear()
    _REQUEST_METRICS.update(_empty_request_metrics())


def _normalize_endpoint_name(url: str) -> str:
    path = parse.urlparse(url).path.strip("/")
    return path or "root"


def _classify_request(*, error_label: str, url: str) -> str:
    label = error_label.strip()
    path = parse.urlparse(url).path

    if label == "현재가 조회" or "/quotations/" in path:
        return "quote"
    if label == "잔고 조회":
        return "balance"
    if label == "매수가능조회":
        return "orderable"
    if label in {"시장가 매수", "시장가 매도", "Hashkey 발급"} or "/trading/" in path:
        return "order"
    if label == "토큰 발급" or "/oauth2/" in path:
        return "token"
    return "other"


def _record_request_metric(
    *,
    error_label: str,
    url: str,
    elapsed_ms: float,
) -> None:
    if not _REQUEST_METRICS:
        reset_request_metrics()

    category = _classify_request(error_label=error_label, url=url)
    endpoint = _normalize_endpoint_name(url)

    _REQUEST_METRICS["total_requests"] = int(_REQUEST_METRICS.get("total_requests", 0)) + 1
    _REQUEST_METRICS["total_elapsed_ms"] = float(
        _REQUEST_METRICS.get("total_elapsed_ms", 0.0)
    ) + float(elapsed_ms)

    category_bucket = _REQUEST_METRICS.setdefault("categories", {}).setdefault(
        category,
        {"count": 0, "elapsed_ms": 0.0},
    )
    category_bucket["count"] = int(category_bucket.get("count", 0)) + 1
    category_bucket["elapsed_ms"] = float(category_bucket.get("elapsed_ms", 0.0)) + float(
        elapsed_ms
    )

    endpoint_bucket = _REQUEST_METRICS.setdefault("endpoints", {}).setdefault(
        endpoint,
        {"count": 0, "elapsed_ms": 0.0, "category": category},
    )
    endpoint_bucket["count"] = int(endpoint_bucket.get("count", 0)) + 1
    endpoint_bucket["elapsed_ms"] = float(endpoint_bucket.get("elapsed_ms", 0.0)) + float(
        elapsed_ms
    )


def get_request_metrics_summary() -> dict[str, Any]:
    if not _REQUEST_METRICS:
        reset_request_metrics()

    categories = {
        category: {
            "count": int((_REQUEST_METRICS.get("categories", {}).get(category) or {}).get("count", 0)),
            "elapsed_ms": round(
                float(
                    (_REQUEST_METRICS.get("categories", {}).get(category) or {}).get(
                        "elapsed_ms",
                        0.0,
                    )
                ),
                1,
            ),
        }
        for category in REQUEST_METRIC_CATEGORIES
    }
    endpoints = {
        endpoint: {
            "count": int(values.get("count", 0)),
            "elapsed_ms": round(float(values.get("elapsed_ms", 0.0)), 1),
            "category": str(values.get("category", "other")),
        }
        for endpoint, values in (_REQUEST_METRICS.get("endpoints", {}) or {}).items()
    }
    return {
        "total_requests": int(_REQUEST_METRICS.get("total_requests", 0)),
        "total_elapsed_ms": round(float(_REQUEST_METRICS.get("total_elapsed_ms", 0.0)), 1),
        "categories": categories,
        "endpoints": endpoints,
    }


def _load_token_cache(env: str = "mock") -> dict[str, Any]:
    if _credential_file_access_disabled():
        return {}
    cache_path = get_token_cache_path(env)
    if not cache_path.exists():
        return {}

    try:
        _harden_cache_file(cache_path)
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _credential_file_access_disabled() -> bool:
    return os.getenv(DISABLE_CREDENTIAL_FILE_ACCESS_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _harden_cache_file(cache_path) -> None:
    os.chmod(cache_path, 0o600)


def _prepare_cache_parent(cache_path) -> None:
    cache_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(cache_path.parent, 0o700)


def _write_secure_json(cache_path, data: dict[str, Any]) -> None:
    _prepare_cache_parent(cache_path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(cache_path, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as cache_file:
            json.dump(data, cache_file, ensure_ascii=False, indent=2)
            cache_file.write("\n")
    finally:
        os.chmod(cache_path, 0o600)


def _save_token_cache(access_token: str, env: str = "mock", *, app_key: str = "") -> None:
    if _credential_file_access_disabled():
        return
    cache_path = get_token_cache_path(env)
    data = {
        "access_token": access_token,
        "issued_at": time.time(),
    }
    _write_secure_json(cache_path, stamp_app_key_fingerprint(data, app_key))


def _parse_response_body(body: str) -> dict[str, Any]:
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    return {"raw_text": body}


def _is_retryable_http_method(method: str) -> bool:
    return method.upper() in {"GET", "HEAD"}


def _is_retryable_url_error_request(*, method: str, url: str) -> bool:
    """GET/HEAD are always safe to retry on transient URL errors.

    Some POSTs are also safe because they carry no trading side effects:
    token issuance (/oauth2/*) and hashkey (/uapi/hashkey). URL errors happen
    before the request reaches the server, but we still exclude trading POSTs
    to avoid any theoretical risk of a mid-flight connection reset double-submitting
    an order.
    """
    if _is_retryable_http_method(method):
        return True
    path = parse.urlparse(url).path
    if "/oauth2/" in path:
        return True
    if path.endswith("/uapi/hashkey") or "/uapi/hashkey" in path:
        return True
    return False


def _looks_like_transient_url_error(exc: BaseException) -> bool:
    text = f"{exc} {getattr(exc, 'reason', '')}".lower()
    transient_markers = (
        "timed out",
        "timeout",
        "temporary failure",
        "temporarily unavailable",
        "try again",
        "connection reset",
        "connection aborted",
        "connection refused",
        "connection closed",
        "connection broken",
        "network is unreachable",
        "name or service not known",
        "nodename nor servname provided, or not known",
        "server disconnected",
    )
    return any(marker in text for marker in transient_markers)


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    error_label: str,
    error_context: str | None = None,
    use_throttle: bool = True,
    record_metrics: bool = True,
    request_timeout_seconds: float | None = None,
    max_attempts: int | None = None,
    retry_delays_seconds: tuple[float, ...] | None = None,
) -> dict[str, Any]:
    request_category = _classify_request(error_label=error_label, url=url)
    if use_throttle:
        throttle(
            category=request_category,
        )
    query_string = parse.urlencode(params or {})
    request_url = f"{url}?{query_string}" if query_string else url
    request_body = None
    if payload is not None:
        request_body = json.dumps(payload).encode("utf-8")

    req = request.Request(
        url=request_url,
        data=request_body,
        headers=headers,
        method=method.upper(),
    )

    request_started_at = time.perf_counter()
    request_timeout = (
        REQUEST_TIMEOUT_SECONDS
        if request_timeout_seconds is None
        else max(0.001, float(request_timeout_seconds))
    )
    retry_delays = (
        TRANSIENT_REQUEST_RETRY_DELAYS_SECONDS
        if retry_delays_seconds is None
        and _is_retryable_url_error_request(method=method, url=url)
        else tuple(retry_delays_seconds or ())
    )
    if max_attempts is not None:
        retry_delays = retry_delays[: max(0, int(max_attempts) - 1)]
    url_error_attempts = 0
    while True:
        try:
            with request.urlopen(req, timeout=request_timeout) as response:
                status_code = response.status
                body = response.read().decode("utf-8")
            break
        except error.HTTPError as exc:
            status_code = exc.code
            body = exc.read().decode("utf-8", errors="replace")
            break
        except (error.URLError, TimeoutError) as exc:
            if (
                url_error_attempts < len(retry_delays)
                and _looks_like_transient_url_error(exc)
            ):
                time.sleep(float(retry_delays[url_error_attempts]))
                url_error_attempts += 1
                if use_throttle:
                    throttle(
                        category=request_category,
                    )
                continue

            elapsed_ms = (time.perf_counter() - request_started_at) * 1000
            if record_metrics:
                _record_request_metric(
                    error_label=error_label,
                    url=request_url,
                    elapsed_ms=elapsed_ms,
                )
            retry_note = (
                f" (일시 재시도 {url_error_attempts}회 후)"
                if url_error_attempts > 0
                else ""
            )
            raise RuntimeError(f"{error_label} 요청 실패{retry_note}: {exc}") from exc

    elapsed_ms = (time.perf_counter() - request_started_at) * 1000
    if record_metrics:
        _record_request_metric(
            error_label=error_label,
            url=request_url,
            elapsed_ms=elapsed_ms,
        )

    data = _parse_response_body(body)
    if is_rate_limit_response(data):
        note_rate_limit(source=request_category)
    if status_code >= 400:
        details = f" / {error_context}" if error_context else ""
        raise ApiHttpError(
            f"{error_label} HTTP {status_code}{details}: {data}",
            status_code=status_code,
            data=data,
        )

    return data


def _post_json_without_throttle(
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    error_label: str,
    request_timeout_seconds: float | None = None,
    max_attempts: int | None = None,
    retry_delays_seconds: tuple[float, ...] | None = None,
) -> dict[str, Any]:
    request_category = _classify_request(error_label=error_label, url=url)
    request_body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url=url,
        data=request_body,
        headers=headers,
        method="POST",
    )

    request_timeout = (
        REQUEST_TIMEOUT_SECONDS
        if request_timeout_seconds is None
        else max(0.001, float(request_timeout_seconds))
    )
    retry_delays = (
        TRANSIENT_REQUEST_RETRY_DELAYS_SECONDS
        if retry_delays_seconds is None
        and _is_retryable_url_error_request(method="POST", url=url)
        else tuple(retry_delays_seconds or ())
    )
    if max_attempts is not None:
        retry_delays = retry_delays[: max(0, int(max_attempts) - 1)]
    url_error_attempts = 0
    while True:
        try:
            with request.urlopen(req, timeout=request_timeout) as response:
                status_code = response.status
                body = response.read().decode("utf-8")
            break
        except error.HTTPError as exc:
            status_code = exc.code
            body = exc.read().decode("utf-8", errors="replace")
            break
        except (error.URLError, TimeoutError) as exc:
            if (
                url_error_attempts < len(retry_delays)
                and _looks_like_transient_url_error(exc)
            ):
                time.sleep(float(retry_delays[url_error_attempts]))
                url_error_attempts += 1
                continue
            retry_note = (
                f" (일시 재시도 {url_error_attempts}회 후)"
                if url_error_attempts > 0
                else ""
            )
            raise RuntimeError(f"{error_label} 요청 실패{retry_note}: {exc}") from exc

    data = _parse_response_body(body)
    if is_rate_limit_response(data):
        note_rate_limit(source=request_category)
    if status_code >= 400:
        raise ApiHttpError(
            f"{error_label} HTTP {status_code}: {data}",
            status_code=status_code,
            data=data,
        )
    return data


def issue_access_token(force_refresh: bool = False) -> str:
    from app.auth.account_scope import get_account_environment

    settings = get_settings()
    env = get_account_environment(settings)
    cache = _load_token_cache(env)
    cached_token = cache.get("access_token")
    issued_at = float(cache.get("issued_at", 0.0))

    if (
        not force_refresh
        and cached_token
        and cache_payload_matches_app_key(cache, settings.app_key)
        and (time.time() - issued_at) < TOKEN_TTL_SECONDS
    ):
        return str(cached_token)

    url = f"{settings.base_url}/oauth2/tokenP"
    headers = {
        "content-type": "application/json; charset=utf-8",
    }
    payload = {
        "grant_type": "client_credentials",
        "appkey": settings.app_key,
        "appsecret": settings.app_secret,
    }

    data = request_json(
        "POST",
        url,
        headers=headers,
        payload=payload,
        error_label="토큰 발급",
    )

    access_token = data.get("access_token")
    if not access_token:
        raise RuntimeError(f"토큰 발급 실패: {data}")

    _save_token_cache(str(access_token), env, app_key=settings.app_key)
    return str(access_token)


def issue_access_token_for(
    *,
    base_url: str,
    app_key: str,
    app_secret: str,
    env: str,
    force_refresh: bool = False,
    request_timeout_seconds: float | None = None,
    max_attempts: int | None = None,
) -> str:
    validated_base_url = validate_kis_base_url(base_url, expected_env=env)
    cache = _load_token_cache(env)
    cached_token = cache.get("access_token")
    issued_at = float(cache.get("issued_at", 0.0))

    if (
        not force_refresh
        and cached_token
        and cache_payload_matches_app_key(cache, app_key)
        and (time.time() - issued_at) < TOKEN_TTL_SECONDS
    ):
        return str(cached_token)

    url = f"{validated_base_url}/oauth2/tokenP"
    headers = {
        "content-type": "application/json; charset=utf-8",
    }
    payload = {
        "grant_type": "client_credentials",
        "appkey": app_key,
        "appsecret": app_secret,
    }

    data = _post_json_without_throttle(
        url,
        headers=headers,
        payload=payload,
        error_label="토큰 발급",
        request_timeout_seconds=request_timeout_seconds,
        max_attempts=max_attempts,
        retry_delays_seconds=() if max_attempts == 1 else None,
    )

    access_token = data.get("access_token")
    if not access_token:
        raise RuntimeError(f"토큰 발급 실패 (env={env}): {data}")

    _save_token_cache(str(access_token), env, app_key=app_key)
    return str(access_token)


def issue_hashkey(payload: dict[str, Any], token: str | None = None) -> str:
    settings = get_settings()
    token = token or issue_access_token()

    url = f"{settings.base_url}/uapi/hashkey"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": settings.app_key,
        "appsecret": settings.app_secret,
    }

    data = request_json(
        "POST",
        url,
        headers=headers,
        payload=payload,
        error_label="Hashkey 발급",
    )

    hashkey = data.get("HASH")
    if not hashkey:
        raise RuntimeError(f"Hashkey 발급 실패: {data}")

    return str(hashkey)


def build_auth_headers(
    token: str,
    tr_id: str,
    hashkey: str | None = None,
) -> dict[str, str]:
    settings = get_settings()
    return build_auth_headers_for(
        token=token,
        tr_id=tr_id,
        app_key=settings.app_key,
        app_secret=settings.app_secret,
        hashkey=hashkey,
    )


def build_auth_headers_for(
    *,
    token: str,
    tr_id: str,
    app_key: str,
    app_secret: str,
    hashkey: str | None = None,
) -> dict[str, str]:

    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": app_key,
        "appsecret": app_secret,
        "tr_id": tr_id,
        "custtype": "P",
    }

    if hashkey:
        headers["hashkey"] = hashkey

    return headers
