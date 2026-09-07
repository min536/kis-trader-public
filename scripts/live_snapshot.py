#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib import error, parse, request as urllib_request

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.auth.settings import LIVE_BASE_URL_DEFAULT
from app.auth.token import is_rate_limit_response, issue_access_token_for
from app.core.time_utils import get_korean_now
from app.market_data.live_snapshot_collect import (
    _build_headers,
    _build_runtime_pressure_defer_reason,
    _extract_rows,
    _extract_symbols,
    _first_env,
    _merge_ranked_lists,
    _parse_response_body,
    _positive_int,
)

SNAPSHOT_PATH = PROJECT_ROOT / "data" / "live_snapshot.json"
# LIVE_BASE_URL_DEFAULT is the single source of truth in app.auth.settings
# (re-exported here for the snapshot worker's _first_env default).
LIVE_ENV = "live"
REQUEST_TIMEOUT_SECONDS = 10.0
INTER_REQUEST_DELAY_SECONDS = 0.3
GET_RETRY_ATTEMPTS = 3
_RUNTIME_STATE_GLOB = "runtime_state_*.json"


class LiveSnapshotRateLimitError(RuntimeError):
    pass


def _load_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _load_existing_snapshot() -> dict[str, Any] | None:
    return _load_json_file(SNAPSHOT_PATH)


def _load_latest_runtime_state() -> dict[str, Any] | None:
    candidates = sorted(
        SNAPSHOT_PATH.parent.glob(_RUNTIME_STATE_GLOB),
        key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
        reverse=True,
    )
    for path in candidates:
        payload = _load_json_file(path)
        if payload:
            return payload
    return None


def _get_json(
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, str],
) -> dict[str, Any]:
    query = parse.urlencode(params)
    request_url = f"{url}?{query}" if query else url
    req = urllib_request.Request(request_url, headers=headers, method="GET")

    last_network_error: error.URLError | TimeoutError | None = None
    for attempt in range(1, GET_RETRY_ATTEMPTS + 1):
        try:
            with urllib_request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                body = response.read().decode("utf-8")
                data = json.loads(body)
            break
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            error_payload = _parse_response_body(body)
            if is_rate_limit_response(error_payload):
                raise LiveSnapshotRateLimitError(
                    f"KIS rate limit response: {error_payload}"
                ) from exc
            raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
        except (error.URLError, TimeoutError) as exc:
            last_network_error = exc
            if attempt >= GET_RETRY_ATTEMPTS:
                raise RuntimeError(f"네트워크 요청 실패: {exc}") from exc
            time.sleep(min(float(attempt), 3.0))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"JSON 파싱 실패: {exc}") from exc
    else:
        raise RuntimeError(f"네트워크 요청 실패: {last_network_error}")

    if not isinstance(data, dict):
        raise RuntimeError(f"예상하지 못한 응답 형식: {type(data).__name__}")
    if is_rate_limit_response(data):
        raise LiveSnapshotRateLimitError(f"KIS rate limit response: {data}")
    return data


def _fetch_volume_rank(
    *,
    base_url: str,
    token: str,
    app_key: str,
    app_secret: str,
) -> list[dict[str, Any]]:
    return _extract_rows(
        _get_json(
            f"{base_url}/uapi/domestic-stock/v1/quotations/volume-rank",
            headers=_build_headers(
                token=token,
                tr_id="FHPST01710000",
                app_key=app_key,
                app_secret=app_secret,
            ),
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_COND_SCR_DIV_CODE": "20171",
                "FID_INPUT_ISCD": "0000",
                "FID_DIV_CLS_CODE": "0",
                "FID_BLNG_CLS_CODE": "0",
                "FID_TRGT_CLS_CODE": "111111111",
                "FID_TRGT_EXLS_CLS_CODE": "0000000000",
                "FID_INPUT_PRICE_1": "0",
                "FID_INPUT_PRICE_2": "0",
                "FID_VOL_CNT": "0",
                "FID_INPUT_DATE_1": "",
            },
        )
    )


def _fetch_fluctuation_rank(
    *,
    base_url: str,
    token: str,
    app_key: str,
    app_secret: str,
) -> list[dict[str, Any]]:
    return _extract_rows(
        _get_json(
            f"{base_url}/uapi/domestic-stock/v1/ranking/fluctuation",
            headers=_build_headers(
                token=token,
                tr_id="FHPST01700000",
                app_key=app_key,
                app_secret=app_secret,
            ),
            params={
                "fid_cond_mrkt_div_code": "J",
                "fid_cond_scr_div_code": "20170",
                "fid_input_iscd": "0000",
                "fid_rank_sort_cls_code": "0",
                "fid_input_cnt_1": "0",
                "fid_prc_cls_code": "1",
                "fid_input_price_1": "0",
                "fid_input_price_2": "0",
                "fid_vol_cnt": "0",
                "fid_trgt_cls_code": "0",
                "fid_trgt_exls_cls_code": "0",
                "fid_div_cls_code": "0",
                "fid_rsfl_rate1": "",
                "fid_rsfl_rate2": "",
            },
        )
    )


def _fetch_volume_power_rank(
    *,
    base_url: str,
    token: str,
    app_key: str,
    app_secret: str,
) -> list[dict[str, Any]]:
    return _extract_rows(
        _get_json(
            f"{base_url}/uapi/domestic-stock/v1/ranking/volume-power",
            headers=_build_headers(
                token=token,
                tr_id="FHPST01680000",
                app_key=app_key,
                app_secret=app_secret,
            ),
            params={
                "fid_cond_mrkt_div_code": "J",
                "fid_cond_scr_div_code": "20168",
                "fid_input_iscd": "0000",
                "fid_rank_sort_cls_code": "0",
                "fid_input_cnt_1": "0",
                "fid_prc_cls_code": "0",
                "fid_input_price_1": "0",
                "fid_input_price_2": "0",
                "fid_vol_cnt": "0",
                "fid_trgt_cls_code": "0",
                "fid_trgt_exls_cls_code": "0",
                "fid_div_cls_code": "0",
            },
        )
    )


def collect(*, top_n: int = 30, ttl_seconds: int = 420) -> dict[str, Any]:
    _positive_int(top_n, name="top_n")
    _positive_int(ttl_seconds, name="ttl_seconds")
    refresh_interval_seconds = _positive_int(
        int(_first_env("LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS", default="180")),
        name="refresh_interval_seconds",
    )

    existing_snapshot = _load_existing_snapshot()
    runtime_state = _load_latest_runtime_state()
    now = get_korean_now()
    defer_reason = _build_runtime_pressure_defer_reason(
        existing_snapshot=existing_snapshot,
        runtime_state=runtime_state,
        now=now,
        ttl_seconds=ttl_seconds,
        refresh_interval_seconds=refresh_interval_seconds,
    )
    if defer_reason and isinstance(existing_snapshot, dict):
        print(f"[live_snapshot] refresh deferred: {defer_reason}")
        return {
            **existing_snapshot,
            "refresh_deferred": True,
            "refresh_deferred_reason": defer_reason,
            "refresh_deferred_at": now.isoformat(timespec="seconds"),
        }

    app_key = _first_env("KIS_LIVE_APP_KEY", "KIS_APP_LIVE_KEY", "KIS_APP_live_KEY")
    app_secret = _first_env(
        "KIS_LIVE_APP_SECRET",
        "KIS_APP_LIVE_SECRET",
        "KIS_APP_live_SECRET",
    )
    base_url = _first_env(
        "KIS_LIVE_BASE_URL",
        "KIS_BASE_LIVE_URL",
        "KIS_BASE_live_URL",
        default=LIVE_BASE_URL_DEFAULT,
    ).rstrip("/")

    if not app_key or not app_secret:
        raise EnvironmentError(
            "실전 snapshot 수집에는 KIS_LIVE_APP_KEY/KIS_APP_LIVE_KEY/KIS_APP_live_KEY 와 "
            "KIS_LIVE_APP_SECRET/KIS_APP_LIVE_SECRET/KIS_APP_live_SECRET 이 필요합니다."
        )

    token = issue_access_token_for(
        base_url=base_url,
        app_key=app_key,
        app_secret=app_secret,
        env=LIVE_ENV,
    )

    errors: list[str] = []
    source_rows: dict[str, list[dict[str, Any]]] = {}
    fetchers = (
        ("volume_rank", "거래량순위", _fetch_volume_rank),
        ("fluctuation_rank", "등락률순위", _fetch_fluctuation_rank),
        ("volume_power_rank", "체결강도 상위", _fetch_volume_power_rank),
    )

    for index, (key, label, fetcher) in enumerate(fetchers):
        try:
            rows = fetcher(
                base_url=base_url,
                token=token,
                app_key=app_key,
                app_secret=app_secret,
            )
            source_rows[key] = rows
            print(f"[live_snapshot] {label}: {len(rows)}건")
        except LiveSnapshotRateLimitError as exc:
            source_rows[key] = []
            errors.append(f"{label} rate limit: {exc}")
            print(f"[live_snapshot][warn] {label} rate limit: {exc}", file=sys.stderr)
            break
        except Exception as exc:
            source_rows[key] = []
            errors.append(f"{label} 실패: {exc}")
            print(f"[live_snapshot][warn] {label} 실패: {exc}", file=sys.stderr)
        if index < len(fetchers) - 1:
            time.sleep(INTER_REQUEST_DELAY_SECONDS)

    volume_symbols = _extract_symbols(source_rows.get("volume_rank", []), top_n=top_n)
    fluctuation_symbols = _extract_symbols(
        source_rows.get("fluctuation_rank", []),
        top_n=top_n,
    )
    volume_power_symbols = _extract_symbols(
        source_rows.get("volume_power_rank", []),
        top_n=top_n,
    )
    top_symbols = _merge_ranked_lists(
        volume_symbols,
        fluctuation_symbols,
        volume_power_symbols,
        top_n=top_n,
    )

    snapshot = {
        "updated_at": now.isoformat(timespec="seconds"),
        "ttl_seconds": ttl_seconds,
        "refresh_interval_seconds": refresh_interval_seconds,
        "top_n": top_n,
        "top_symbols": top_symbols,
        "source_counts": {
            "volume_rank": len(volume_symbols),
            "fluctuation_rank": len(fluctuation_symbols),
            "volume_power_rank": len(volume_power_symbols),
        },
        "source_symbols": {
            "volume_rank": volume_symbols,
            "fluctuation_rank": fluctuation_symbols,
            "volume_power_rank": volume_power_symbols,
        },
        "errors": errors,
    }

    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"[live_snapshot] refresh success: updated_at={snapshot['updated_at']}"
        f" | age=0s"
        f" | ttl={ttl_seconds}s"
        f" | refresh={snapshot['refresh_interval_seconds']}s"
    )
    print(
        f"[live_snapshot] 저장 완료: {SNAPSHOT_PATH}"
        f" | symbols={len(top_symbols)}"
        f" | ttl={ttl_seconds}s"
        f" | refresh={snapshot['refresh_interval_seconds']}s"
    )
    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser(description="KIS live read-only snapshot 수집기")
    parser.add_argument("--top-n", type=int, default=30, help="상위 심볼 최대 개수")
    parser.add_argument("--ttl", type=int, default=420, help="snapshot TTL(초)")
    args = parser.parse_args()
    collect(top_n=args.top_n, ttl_seconds=args.ttl)


if __name__ == "__main__":
    main()
