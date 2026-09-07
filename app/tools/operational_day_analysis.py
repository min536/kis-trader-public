"""Offline operational diagnostics built from existing order logs."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from app.auth.settings import PROJECT_ROOT, get_settings
from app.core.time_utils import KOREA_TZ

_BUY_NOTIONAL_THRESHOLDS: tuple[int, ...] = (80, 90, 95, 100)


def _orders_path(account: str) -> Path:
    account_path = PROJECT_ROOT / "logs" / f"orders_{account}.jsonl"
    if account_path.exists():
        return account_path
    return PROJECT_ROOT / "logs" / "orders.jsonl"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=KOREA_TZ)
    return parsed.astimezone(KOREA_TZ)


def _same_target_date(row: dict[str, Any], target_date: str) -> bool:
    timestamp = str(row.get("timestamp") or row.get("ts") or "").strip()
    return timestamp[:10].replace("-", "") == target_date


def _extract_notional_krw(record: dict[str, Any]) -> int:
    raw_response = record.get("raw_response")
    if not isinstance(raw_response, dict):
        return 0

    direct_value = raw_response.get("notional_krw")
    if direct_value is not None:
        try:
            return int(float(str(direct_value).strip()))
        except ValueError:
            return 0

    for field in ("order_plan", "sell_plan"):
        plan = raw_response.get(field)
        if not isinstance(plan, dict):
            continue
        value = plan.get("notional_krw")
        if value is None:
            continue
        try:
            return int(float(str(value).strip()))
        except ValueError:
            return 0

    return 0


def summarize_buy_notional_rows(
    rows: list[dict[str, Any]],
    *,
    daily_limit_krw: int,
    late_day_hour: int = 14,
) -> dict[str, Any]:
    buy_submitted_rows = sorted(
        [
            row
            for row in rows
            if str(row.get("order_type") or "").strip() == "market_buy"
            and str(row.get("action") or "").strip() == "order_submitted"
            and str(row.get("result") or "").strip() == "success"
        ],
        key=lambda row: str(row.get("timestamp") or ""),
    )
    buy_success_rows = [
        row
        for row in rows
        if str(row.get("order_type") or "").strip() == "market_buy"
        and str(row.get("action") or "").strip() == "order_succeeded"
        and str(row.get("result") or "").strip() == "success"
    ]
    blocked_rows = [
        row
        for row in rows
        if str(row.get("action") or "").strip() == "blocked_buy_daily_notional_limit"
    ]

    submitted_notional_krw = 0
    successful_notional_krw = 0
    submitted_notional_by_hour: Counter[str] = Counter()
    blocked_count_by_hour: Counter[str] = Counter()
    threshold_crossed_at: dict[str, str] = {}
    last_submitted_at: str | None = None

    for row in buy_submitted_rows:
        notional = _extract_notional_krw(row)
        timestamp = str(row.get("timestamp") or "").strip()
        parsed_ts = _parse_timestamp(timestamp)
        hour_bucket = f"{parsed_ts.hour:02d}:00" if parsed_ts is not None else "unknown"
        submitted_notional_krw += notional
        submitted_notional_by_hour[hour_bucket] += notional
        last_submitted_at = timestamp or last_submitted_at
        if daily_limit_krw > 0:
            utilization_pct = (submitted_notional_krw / daily_limit_krw) * 100.0
            for threshold in _BUY_NOTIONAL_THRESHOLDS:
                key = f"{threshold}%"
                if utilization_pct >= threshold and key not in threshold_crossed_at:
                    threshold_crossed_at[key] = timestamp

    for row in buy_success_rows:
        successful_notional_krw += _extract_notional_krw(row)

    late_day_blocked_symbols: set[str] = set()
    for row in blocked_rows:
        timestamp = str(row.get("timestamp") or "").strip()
        parsed_ts = _parse_timestamp(timestamp)
        hour_bucket = f"{parsed_ts.hour:02d}:00" if parsed_ts is not None else "unknown"
        blocked_count_by_hour[hour_bucket] += 1
        if parsed_ts is not None and parsed_ts.hour >= late_day_hour:
            symbol = str(row.get("symbol") or "").strip()
            if symbol:
                late_day_blocked_symbols.add(symbol)

    remaining_notional_krw = max(daily_limit_krw - submitted_notional_krw, 0)
    utilization_pct = (
        round((submitted_notional_krw / daily_limit_krw) * 100.0, 2)
        if daily_limit_krw > 0
        else 0.0
    )
    stop_light = (
        "critical"
        if utilization_pct >= 95.0
        else "warning"
        if utilization_pct >= 90.0
        else "normal"
    )

    return {
        "daily_limit_krw": int(daily_limit_krw),
        "submitted_notional_krw": int(submitted_notional_krw),
        "successful_notional_krw": int(successful_notional_krw),
        "remaining_notional_krw": int(remaining_notional_krw),
        "utilization_pct": utilization_pct,
        "status": stop_light,
        "submitted_order_count": len(buy_submitted_rows),
        "successful_order_count": len(buy_success_rows),
        "blocked_daily_notional_limit_count": len(blocked_rows),
        "blocked_count_by_hour": dict(sorted(blocked_count_by_hour.items())),
        "submitted_notional_by_hour": dict(sorted(submitted_notional_by_hour.items())),
        "late_day_start_hour": late_day_hour,
        "late_day_blocked_count": sum(
            count
            for bucket, count in blocked_count_by_hour.items()
            if bucket != "unknown" and int(bucket[:2]) >= late_day_hour
        ),
        "late_day_blocked_symbols": sorted(late_day_blocked_symbols),
        "threshold_crossed_at": threshold_crossed_at,
        "last_submitted_at": last_submitted_at,
    }


def summarize_sell_reason_rows(
    rows: list[dict[str, Any]],
    *,
    min_sell_count_for_defensive_day: int = 3,
    stop_loss_share_threshold_pct: float = 60.0,
) -> dict[str, Any]:
    sell_success_rows = [
        row
        for row in rows
        if str(row.get("order_type") or "").strip() == "market_sell"
        and str(row.get("action") or "").strip() == "sell_order_succeeded"
        and str(row.get("result") or "").strip() == "success"
    ]

    reason_counts: Counter[str] = Counter()
    for row in sell_success_rows:
        raw_response = row.get("raw_response")
        reason = ""
        if isinstance(raw_response, dict):
            reason = str(
                ((raw_response.get("sell_strategy_details") or {}).get("triggered_rule_name"))
                or ((raw_response.get("sell_plan") or {}).get("sell_trigger"))
                or ""
            ).strip()
        if not reason:
            reason = "unknown"
        reason_counts[reason] += 1

    sell_success_count = len(sell_success_rows)
    dominant_reason = ""
    dominant_reason_count = 0
    if reason_counts:
        dominant_reason, dominant_reason_count = sorted(
            reason_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[0]
    dominant_reason_share_pct = (
        round((dominant_reason_count / sell_success_count) * 100.0, 2)
        if sell_success_count > 0
        else 0.0
    )
    stop_loss_count = int(reason_counts.get("stop_loss", 0))
    stop_loss_share_pct = (
        round((stop_loss_count / sell_success_count) * 100.0, 2)
        if sell_success_count > 0
        else 0.0
    )
    defensive_day = (
        sell_success_count >= min_sell_count_for_defensive_day
        and stop_loss_share_pct >= stop_loss_share_threshold_pct
    )
    operator_note = (
        "stop_loss 비중이 높아 방어적 매도 우세일 가능성이 큽니다."
        if defensive_day
        else "sell reason 분산이 비교적 정상 범위입니다."
        if sell_success_count > 0
        else "체결된 SELL 주문이 없어 stop-loss dominance를 판단하기 어렵습니다."
    )

    return {
        "sell_success_count": sell_success_count,
        "sell_reason_counts": dict(sorted(reason_counts.items())),
        "dominant_reason": dominant_reason or None,
        "dominant_reason_share_pct": dominant_reason_share_pct,
        "stop_loss_count": stop_loss_count,
        "stop_loss_share_pct": stop_loss_share_pct,
        "defensive_day": defensive_day,
        "operator_note": operator_note,
    }


def detect_same_day_stop_loss_reentries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stop_loss_exits: dict[str, list[datetime]] = {}
    buy_entries: dict[str, list[datetime]] = {}

    for row in rows:
        parsed_ts = _parse_timestamp(row.get("timestamp"))
        if parsed_ts is None:
            continue
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue

        action = str(row.get("action") or "").strip()
        order_type = str(row.get("order_type") or "").strip()
        if order_type == "market_sell" and action == "sell_order_succeeded":
            raw_response = row.get("raw_response")
            trigger = ""
            if isinstance(raw_response, dict):
                trigger = str(
                    ((raw_response.get("sell_strategy_details") or {}).get("triggered_rule_name"))
                    or ((raw_response.get("sell_plan") or {}).get("sell_trigger"))
                    or ""
                ).strip()
            if trigger == "stop_loss":
                stop_loss_exits.setdefault(symbol, []).append(parsed_ts)
        elif order_type == "market_buy" and action == "order_succeeded":
            buy_entries.setdefault(symbol, []).append(parsed_ts)

    reentries: list[dict[str, Any]] = []
    for symbol, exit_times in stop_loss_exits.items():
        later_buys = sorted(buy_entries.get(symbol, []))
        for exit_time in sorted(exit_times):
            matched_buy = next(
                (
                    buy_time
                    for buy_time in later_buys
                    if buy_time.date() == exit_time.date() and buy_time > exit_time
                ),
                None,
            )
            if matched_buy is None:
                continue
            reentries.append(
                {
                    "symbol": symbol,
                    "stop_loss_exit_at": exit_time.isoformat(),
                    "reentry_buy_at": matched_buy.isoformat(),
                    "minutes_between": round((matched_buy - exit_time).total_seconds() / 60.0, 1),
                }
            )
    return reentries


def build_operational_day_diagnostics(
    *,
    account: str,
    date: str,
    late_day_hour: int = 14,
) -> dict[str, Any]:
    rows = [
        row
        for row in _read_jsonl(_orders_path(account))
        if _same_target_date(row, date)
    ]
    settings = get_settings()
    return {
        "buy_notional": summarize_buy_notional_rows(
            rows,
            daily_limit_krw=int(settings.buy_daily_max_notional_krw or 0),
            late_day_hour=late_day_hour,
        ),
        "sell_reason": summarize_sell_reason_rows(rows),
        "same_day_stop_loss_reentries": detect_same_day_stop_loss_reentries(rows),
        "order_log_rows": len(rows),
    }
