"""Pure stdlib-only collection helpers for the live KIS snapshot script.

Relocated verbatim from ``scripts/live_snapshot.py`` so the script keeps the
network/CLI seam while parsing, header construction, and ranking helpers live
in a stdlib-only, unit-testable module under ``app/market_data``.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
import re
from typing import Any

_SELL_WATCH_PARTIAL_DEFER_SECONDS = 120
_VALID_SYMBOL_PATTERN = re.compile(r"^\d{6}$")


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def _positive_int(value: int, *, name: str) -> int:
    if value <= 0:
        raise ValueError(f"{name} 는 1 이상의 정수여야 합니다.")
    return value


def _parse_response_body(body: str) -> dict[str, Any]:
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    return {"raw_text": body}


def _parse_runtime_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _build_runtime_pressure_defer_reason(
    *,
    existing_snapshot: dict[str, Any] | None,
    runtime_state: dict[str, Any] | None,
    now: datetime,
    ttl_seconds: int,
    refresh_interval_seconds: int,
) -> str | None:
    if not isinstance(existing_snapshot, dict) or not isinstance(runtime_state, dict):
        return None

    updated_at = _parse_runtime_timestamp(existing_snapshot.get("updated_at"))
    if updated_at is None:
        return None
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=now.tzinfo)
    snapshot_age_seconds = max(0.0, (now - updated_at).total_seconds())
    if snapshot_age_seconds > float(ttl_seconds):
        return None

    cycle_started_at = _parse_runtime_timestamp(runtime_state.get("last_cycle_started_at"))
    if cycle_started_at is None:
        return None
    if cycle_started_at.tzinfo is None:
        cycle_started_at = cycle_started_at.replace(tzinfo=now.tzinfo)
    cycle_age_seconds = max(0.0, (now - cycle_started_at).total_seconds())

    rate_limit_source = str(runtime_state.get("rate_limit_source") or "").strip()
    backoff_applied_seconds = max(0, int(runtime_state.get("backoff_applied_seconds", 0) or 0))
    if rate_limit_source and backoff_applied_seconds > 0:
        runtime_cooldown_seconds = max(
            int(refresh_interval_seconds or 0),
            backoff_applied_seconds,
        )
        if cycle_age_seconds <= float(runtime_cooldown_seconds):
            return (
                f"runtime {rate_limit_source} backoff {runtime_cooldown_seconds}s 중이라 "
                f"fresh snapshot(age={int(snapshot_age_seconds)}s)을 재사용합니다."
            )

    if bool(runtime_state.get("last_sell_watch_partial")):
        partial_cooldown_seconds = max(
            int(refresh_interval_seconds or 0),
            _SELL_WATCH_PARTIAL_DEFER_SECONDS,
        )
        if cycle_age_seconds <= float(partial_cooldown_seconds):
            return (
                "최근 sell_watch partial pressure가 있어 "
                f"fresh snapshot(age={int(snapshot_age_seconds)}s)을 재사용합니다."
            )

    return None


def _build_headers(
    *,
    token: str,
    tr_id: str,
    app_key: str,
    app_secret: str,
) -> dict[str, str]:
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": app_key,
        "appsecret": app_secret,
        "tr_id": tr_id,
        "custtype": "P",
    }


def _extract_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    rows = data.get("output")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    return []


def _extract_symbols(rows: list[dict[str, Any]], *, top_n: int) -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()
    for row in rows:
        symbol = str(
            row.get("mksc_shrn_iscd")
            or row.get("stck_shrn_iscd")
            or row.get("pdno")
            or ""
        ).strip()
        if not _VALID_SYMBOL_PATTERN.fullmatch(symbol):
            continue
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        symbols.append(symbol)
        if len(symbols) >= top_n:
            break
    return symbols


def _merge_ranked_lists(*groups: list[str], top_n: int) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    max_len = max((len(group) for group in groups), default=0)

    for index in range(max_len):
        for group in groups:
            if index >= len(group):
                continue
            symbol = group[index]
            if symbol in seen:
                continue
            seen.add(symbol)
            merged.append(symbol)
            if len(merged) >= top_n:
                return merged
    return merged
