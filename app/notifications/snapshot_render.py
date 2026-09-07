"""Snapshot format helpers + positions read/render (R3-S4).

Relocated verbatim from app.notifications.runtime_status_snapshot.
runtime_status_snapshot keeps legacy bindings so existing import sites and
patch targets remain valid.
"""

from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app.core.time_utils import KOREA_TZ
from app.notifications.snapshot_shared import (
    RuntimeSnapshotReadResult,
    _parse_timestamp,
    _safe_int,
    _safe_price,
    _safe_text,
)
from app.scanner.symbol_names import get_symbol_name

DEFAULT_PERFORMANCE_SUMMARY_MAX_LINES = 200

LIMITED_STATUS_MISSING_TEXT = (
    "⚠️ *상태 확인 제한됨*\n\n"
    "아직 공유 runtime snapshot이 없습니다.\n"
    "trading session이 한 번 이상 진행된 뒤 다시 확인해 주세요."
)
LIMITED_STATUS_STALE_TEXT = (
    "⚠️ *상태 확인 제한됨*\n\n"
    "공유 runtime snapshot이 오래되었습니다.\n"
    "trading session 진행 상태를 확인한 뒤 다시 조회해 주세요."
)
LIMITED_STATUS_UNREADABLE_TEXT = (
    "❌ *상태 확인 실패*\n\n"
    "runtime snapshot을 읽을 수 없습니다.\n"
    "파일이 깨졌거나 쓰기 도중 문제가 있었을 수 있습니다."
)
LIMITED_BOTTLENECKS_MISSING_TEXT = (
    "⚠️ *병목 확인 제한됨*\n\n"
    "아직 공유 runtime snapshot이 없습니다.\n"
    "trading session이 한 번 이상 진행된 뒤 다시 확인해 주세요."
)
LIMITED_BOTTLENECKS_STALE_TEXT = (
    "⚠️ *병목 확인 제한됨*\n\n"
    "공유 runtime snapshot이 오래되었습니다.\n"
    "trading session 진행 상태를 확인한 뒤 다시 조회해 주세요."
)
LIMITED_BOTTLENECKS_UNREADABLE_TEXT = (
    "❌ *병목 확인 실패*\n\n"
    "runtime snapshot을 읽을 수 없습니다.\n"
    "파일이 깨졌거나 쓰기 도중 문제가 있었을 수 있습니다."
)
LIMITED_HEALTH_MISSING_TEXT = (
    "⚠️ *health 확인 제한됨*\n\n"
    "아직 공유 runtime snapshot이 없습니다.\n"
    "trading session이 한 번 이상 진행된 뒤 다시 확인해 주세요."
)
LIMITED_HEALTH_STALE_TEXT = (
    "⚠️ *health 확인 제한됨*\n\n"
    "공유 runtime snapshot이 오래되었습니다.\n"
    "trading session 진행 상태를 확인한 뒤 다시 조회해 주세요."
)
LIMITED_HEALTH_UNREADABLE_TEXT = (
    "❌ *health 확인 실패*\n\n"
    "runtime snapshot을 읽을 수 없습니다.\n"
    "파일이 깨졌거나 쓰기 도중 문제가 있었을 수 있습니다."
)
LIMITED_POSITIONS_MISSING_TEXT = (
    "⚠️ *보유 종목 확인 제한됨*\n\n"
    "아직 공유 runtime snapshot이 없습니다.\n"
    "trading session이 한 번 이상 진행된 뒤 다시 확인해 주세요."
)
LIMITED_POSITIONS_STALE_TEXT = (
    "⚠️ *보유 종목 확인 제한됨*\n\n"
    "공유 runtime snapshot이 오래되었습니다.\n"
    "trading session 진행 상태를 확인한 뒤 다시 조회해 주세요."
)
LIMITED_POSITIONS_UNREADABLE_TEXT = (
    "❌ *보유 종목 확인 실패*\n\n"
    "runtime snapshot을 읽을 수 없습니다.\n"
    "파일이 깨졌거나 쓰기 도중 문제가 있었을 수 있습니다."
)


def _mapping_from_snapshot(snapshot: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = snapshot.get(key)
    return value if isinstance(value, Mapping) else {}


def _format_counter_map(counter_map: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    return ", ".join(f"{key}={_safe_int(counter_map.get(key))}" for key in keys)


def _limited_text_for_result(result: RuntimeSnapshotReadResult, *, command: str) -> str:
    _LIMITED_BY_COMMAND = {
        "bottlenecks": (LIMITED_BOTTLENECKS_MISSING_TEXT, LIMITED_BOTTLENECKS_STALE_TEXT, LIMITED_BOTTLENECKS_UNREADABLE_TEXT),
        "health": (LIMITED_HEALTH_MISSING_TEXT, LIMITED_HEALTH_STALE_TEXT, LIMITED_HEALTH_UNREADABLE_TEXT),
        "positions": (LIMITED_POSITIONS_MISSING_TEXT, LIMITED_POSITIONS_STALE_TEXT, LIMITED_POSITIONS_UNREADABLE_TEXT),
    }
    missing, stale, unreadable = _LIMITED_BY_COMMAND.get(
        command,
        (LIMITED_STATUS_MISSING_TEXT, LIMITED_STATUS_STALE_TEXT, LIMITED_STATUS_UNREADABLE_TEXT),
    )
    if result.status == "stale":
        return stale
    if result.status == "unreadable":
        return unreadable
    return missing


def _format_snapshot_time(value: object) -> str:
    parsed = _parse_timestamp(value)
    if parsed is None:
        return _safe_text(value) or "-"
    return parsed.astimezone(KOREA_TZ).strftime("%Y-%m-%d %H:%M:%S KST")


def _format_krw_optional(value: object) -> str | None:
    amount = _safe_price(value)
    if amount is None:
        return None
    return f"{amount:,}원"


def _format_signed_krw_optional(value: object) -> str | None:
    if value is None:
        return None
    try:
        amount = int(value)
    except (TypeError, ValueError):
        return None
    sign = "+" if amount > 0 else ""
    return f"{sign}{amount:,}원"


def _first_int_value(mapping: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        if key not in mapping:
            continue
        try:
            return int(mapping.get(key) or 0)
        except (TypeError, ValueError):
            continue
    return None


def _normalize_performance_position(position: Mapping[str, Any]) -> dict[str, Any] | None:
    symbol = str(position.get("symbol") or "").strip()
    if not symbol:
        return None
    qty = _first_int_value(position, "holding_qty", "quantity", "qty")
    if qty is None or qty <= 0:
        return None

    name = (
        _safe_text(position.get("symbol_name"))
        or _safe_text(position.get("name"))
        or get_symbol_name(symbol)
    )
    current_price = _first_int_value(position, "current_price_krw", "current_price")
    avg_price = _first_int_value(position, "average_cost_krw", "average_price", "avg_price")
    market_value = _first_int_value(position, "evaluation_amount_krw", "market_value")
    pnl = _first_int_value(position, "net_pnl_krw", "net_pnl", "gross_pnl_krw", "gross_pnl")
    pnl_pct = position.get("net_pnl_pct", position.get("gross_pnl_pct"))
    try:
        pnl_pct = float(pnl_pct) if pnl_pct is not None else None
    except (TypeError, ValueError):
        pnl_pct = None

    return {
        "symbol": symbol,
        "symbol_name": name,
        "qty": qty,
        "current_price": current_price if current_price and current_price > 0 else None,
        "avg_price": avg_price if avg_price and avg_price > 0 else None,
        "market_value": market_value if market_value and market_value > 0 else None,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
    }


def _read_latest_performance_positions(
    *,
    path: Path | None,
    now: datetime | None,
    stale_after_sec: int,
    max_lines: int = DEFAULT_PERFORMANCE_SUMMARY_MAX_LINES,
) -> list[dict[str, Any]]:
    if path is None:
        try:
            from app.auth.account_scope import get_performance_summary_path
            path = get_performance_summary_path()
        except Exception:
            return []
    if not path.exists():
        return []

    raw_lines: deque[str] = deque(maxlen=max(1, int(max_lines or 1)))
    try:
        with path.open("r", encoding="utf-8") as log_file:
            for raw_line in log_file:
                raw_lines.append(raw_line)
    except OSError:
        return []

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    for raw_line in reversed(raw_lines):
        line = raw_line.strip()
        if not line:
            continue
        try:
            summary = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(summary, Mapping):
            continue

        generated_at = _parse_timestamp(summary.get("generated_at"))
        if generated_at is None:
            continue
        age_seconds = max(
            0.0,
            (current_time.astimezone(timezone.utc) - generated_at).total_seconds(),
        )
        if stale_after_sec > 0 and age_seconds > stale_after_sec:
            continue

        raw_positions = summary.get("positions")
        if not isinstance(raw_positions, list):
            continue
        positions = [
            normalized
            for raw_position in raw_positions
            if isinstance(raw_position, Mapping)
            for normalized in (_normalize_performance_position(raw_position),)
            if normalized is not None
        ]
        if positions:
            return sorted(positions, key=lambda item: str(item.get("symbol") or ""))
    return []


def _positions_freshness_line(
    result: RuntimeSnapshotReadResult,
    snapshot: Mapping[str, Any],
) -> str:
    age_sec = result.age_seconds
    if result.status == "stale":
        freshness = "stale"
        age_text = f"{int(age_sec or 0)}s old"
        reason = f"snapshot older than freshness window ({age_text})"
    elif result.status == "ok":
        freshness = "fresh"
        age_text = f"{int(age_sec or 0)}s old"
        reason = age_text
    else:
        freshness = "unavailable"
        reason = result.status

    parts = [
        f"freshness=`{freshness}`",
        f"snapshot=`{_format_snapshot_time(snapshot.get('timestamp'))}`",
    ]
    positions_synced_at = _safe_text(snapshot.get("positions_synced_at"))
    if positions_synced_at:
        parts.append(f"broker_sync=`{_format_snapshot_time(positions_synced_at)}`")
    account = _safe_text(snapshot.get("masked_account_display"))
    if account:
        parts.append(f"account=`{account}`")
    if reason:
        parts.append(f"reason=`{reason}`")
    return "_로컬 snapshot 기준 · " + " · ".join(parts) + "_"


def _render_position_line(pos: Mapping[str, Any]) -> str:
    symbol = str(pos.get("symbol") or "-")
    name = _safe_text(pos.get("symbol_name"))
    header = f"`{symbol}`" + (f" {name}" if name else "")
    qty = _safe_int(pos.get("qty"))

    details: list[str] = [f"`{qty}`주"]
    avg_price = _format_krw_optional(pos.get("avg_price"))
    current_price = _format_krw_optional(pos.get("current_price"))
    market_value = _format_krw_optional(pos.get("market_value"))
    pnl = _format_signed_krw_optional(pos.get("pnl"))
    pnl_pct = pos.get("pnl_pct")
    if current_price:
        details.append(f"현재 `{current_price}`")
    if avg_price:
        details.append(f"평균 `{avg_price}`")
    if market_value:
        details.append(f"평가 `{market_value}`")
    if pnl:
        pnl_text = f"손익 `{pnl}`"
        if isinstance(pnl_pct, (int, float)):
            pnl_text += f" ({pnl_pct:+.2f}%)"
        details.append(pnl_text)
    return header + "\n" + " · ".join(details)
