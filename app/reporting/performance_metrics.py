"""Pure math/format/order-record/period helpers for performance reporting (R3-S6).

Relocated verbatim from app.reporting.performance. performance keeps legacy
bindings so existing import sites and patch targets remain valid.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from app.auth.account_scope import get_order_log_read_paths
from app.core.jsonl import read_jsonl_objects
from app.core.time_utils import KOREA_TZ, _timestamp_to_datetime


def _safe_ratio(numerator: float, denominator: float) -> float:
    if abs(denominator) < 1e-12:
        return 0.0
    return numerator / denominator


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = _mean(values)
    variance = sum((value - avg) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(max(variance, 0.0))


def _downside_std(values: list[float]) -> float:
    downside = [value for value in values if value < 0]
    if len(downside) < 2:
        return 0.0
    avg = _mean(downside)
    variance = sum((value - avg) ** 2 for value in downside) / (len(downside) - 1)
    return math.sqrt(max(variance, 0.0))


def _format_signed_krw(value: int) -> str:
    amount = int(value)
    if amount > 0:
        return f"+{amount:,}원"
    if amount < 0:
        return f"-{abs(amount):,}원"
    return "0원"


def _format_signed_pct(value: float) -> str:
    number = float(value)
    if number > 0:
        return f"+{number:.2f}%"
    if number < 0:
        return f"-{abs(number):.2f}%"
    return "0.00%"


def _format_pct(value: float) -> str:
    return f"{float(value):.2f}%"


def _format_optional_pct(value: float | None) -> str:
    if value is None:
        return "데이터 부족"
    return _format_signed_pct(value)


def _format_optional_ratio(value: float | None) -> str:
    if value is None:
        return "표본 부족"
    return f"{value:.4f}"


def _is_operating_record(record: dict[str, Any]) -> bool:
    if str(record.get("environment", "mock")).strip() != "mock":
        return False
    raw_response = record.get("raw_response")
    if isinstance(raw_response, dict):
        sell_test_mode = str(raw_response.get("sell_test_mode", "")).strip().lower()
        if sell_test_mode and sell_test_mode != "off":
            return False
    return True


def _iter_operating_order_records() -> list[dict[str, Any]]:
    order_log_files = [path for path in get_order_log_read_paths() if path.exists()]
    if not order_log_files:
        return []

    records: list[dict[str, Any]] = []
    for order_log_file in order_log_files:
        path_records, _errors = read_jsonl_objects(order_log_file)
        records.extend(path_records)
    return [record for record in records if _is_operating_record(record)]


def _same_day(dt: datetime, target: datetime) -> bool:
    return dt.date() == target.date()


def _same_week(dt: datetime, target: datetime) -> bool:
    return dt.isocalendar()[:2] == target.isocalendar()[:2]


def _same_month(dt: datetime, target: datetime) -> bool:
    return dt.year == target.year and dt.month == target.month


def _choose_period_start(
    snapshots: list[dict[str, Any]],
    *,
    target_dt: datetime,
    predicate,
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for snapshot in snapshots:
        dt = _timestamp_to_datetime(str(snapshot.get("timestamp", "")))
        if dt is None:
            continue
        if predicate(dt, target_dt):
            candidates.append(snapshot)
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: str(item.get("timestamp", "")))[0]
