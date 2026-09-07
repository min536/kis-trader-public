"""Offline operational diagnostics for a single trading day.

This module reads existing order logs only. It does not call the broker API or
change live behavior.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from app.auth.settings import PROJECT_ROOT, get_settings
from app.core.time_utils import KOREA_TZ


def _orders_path(account: str) -> Path:
    account_path = PROJECT_ROOT / "logs" / f"orders_{account}.jsonl"
    if account_path.exists():
        return account_path
    return PROJECT_ROOT / "logs" / "orders.jsonl"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
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


def _parse_ts(value: Any) -> datetime | None:
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


def _matches_date(row: dict[str, Any], date_text: str) -> bool:
    parsed = _parse_ts(row.get("timestamp"))
    if parsed is None:
        return False
    return parsed.strftime("%Y%m%d") == date_text


def _extract_notional_krw(row: dict[str, Any]) -> int:
    raw_response = row.get("raw_response")
    if not isinstance(raw_response, dict):
        return 0

    for key in ("notional_krw",):
        value = raw_response.get(key)
        if value is not None:
            try:
                return int(float(str(value).strip()))
            except (TypeError, ValueError):
                return 0

    for nested_key in ("order_plan", "sell_plan"):
        nested = raw_response.get(nested_key)
        if not isinstance(nested, dict):
            continue
        value = nested.get("notional_krw")
        if value is None:
            continue
        try:
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            return 0

    return 0


def _extract_sell_reason(row: dict[str, Any]) -> str:
    raw_response = row.get("raw_response")
    if not isinstance(raw_response, dict):
        return "unknown"
    strategy = raw_response.get("sell_strategy_details")
    if not isinstance(strategy, dict):
        return "unknown"
    reason = str(
        strategy.get("triggered_rule_name")
        or strategy.get("triggered_rule")
        or strategy.get("reason")
        or ""
    ).strip()
    return reason or "unknown"


def summarize_buy_notional_rows(
    rows: list[dict[str, Any]],
    *,
    daily_limit_krw: int,
    late_day_start_hour: int = 14,
) -> dict[str, Any]:
    buy_submissions = sorted(
        (
            row for row in rows
            if str(row.get("action") or "").strip() == "order_submitted"
            and str(row.get("order_type") or "").strip() == "market_buy"
        ),
        key=lambda row: str(row.get("timestamp") or ""),
    )
    buy_successes = [
        row for row in rows
        if str(row.get("action") or "").strip() == "order_succeeded"
        and str(row.get("order_type") or "").strip() == "market_buy"
    ]
    blocked_rows = [
        row for row in rows
        if str(row.get("action") or "").strip() == "blocked_buy_daily_notional_limit"
    ]

    used_notional_krw = sum(_extract_notional_krw(row) for row in buy_submissions)
    successful_notional_krw = sum(_extract_notional_krw(row) for row in buy_successes)
    remaining_notional_krw = max(int(daily_limit_krw or 0) - used_notional_krw, 0)
    utilization_pct = (
        round(used_notional_krw / daily_limit_krw * 100.0, 2)
        if daily_limit_krw > 0
        else None
    )

    threshold_crossed_at: dict[str, str | None] = {}
    if daily_limit_krw > 0:
        cumulative = 0
        thresholds = (
            ("80%", 0.80),
            ("90%", 0.90),
            ("95%", 0.95),
            ("99%", 0.99),
        )
        crossed: dict[str, str | None] = {label: None for label, _ in thresholds}
        for row in buy_submissions:
            cumulative += _extract_notional_krw(row)
            ts = str(row.get("timestamp") or "").strip() or None
            for label, ratio in thresholds:
                if crossed[label] is None and cumulative >= daily_limit_krw * ratio:
                    crossed[label] = ts
        threshold_crossed_at = crossed

    blocked_by_hour: Counter[str] = Counter()
    late_day_blocked_symbols: Counter[str] = Counter()
    late_day_blocked_count = 0
    for row in blocked_rows:
        ts = _parse_ts(row.get("timestamp"))
        if ts is None:
            continue
        blocked_by_hour[f"{ts.hour:02d}:00"] += 1
        if ts.hour >= late_day_start_hour:
            late_day_blocked_count += 1
            symbol = str(row.get("symbol") or "").strip()
            if symbol:
                late_day_blocked_symbols[symbol] += 1

    pressure_level = "normal"
    if utilization_pct is not None and utilization_pct >= 99.0:
        pressure_level = "critical"
    elif utilization_pct is not None and utilization_pct >= 95.0:
        pressure_level = "high"
    elif utilization_pct is not None and utilization_pct >= 90.0:
        pressure_level = "elevated"

    return {
        "buy_order_submitted_count": len(buy_submissions),
        "buy_order_success_count": len(buy_successes),
        "buy_notional_limit_krw": int(daily_limit_krw or 0),
        "buy_notional_used_krw": used_notional_krw,
        "buy_notional_success_krw": successful_notional_krw,
        "buy_notional_remaining_krw": remaining_notional_krw,
        "buy_notional_utilization_pct": utilization_pct,
        "buy_notional_pressure_level": pressure_level,
        "buy_notional_threshold_crossed_at": threshold_crossed_at,
        "blocked_buy_daily_notional_limit_count": len(blocked_rows),
        "blocked_buy_daily_notional_limit_by_hour": dict(blocked_by_hour),
        "late_day_buy_notional_blocked_count": late_day_blocked_count,
        "late_day_buy_notional_blocked_symbols": dict(late_day_blocked_symbols),
    }


def summarize_sell_reason_rows(
    rows: list[dict[str, Any]],
    *,
    stop_loss_dominance_threshold_pct: float = 60.0,
    min_sell_count_for_defensive_day: int = 3,
) -> dict[str, Any]:
    sell_successes = [
        row for row in rows
        if str(row.get("action") or "").strip() == "sell_order_succeeded"
        and str(row.get("order_type") or "").strip() == "market_sell"
    ]
    sell_reason_counts: Counter[str] = Counter(_extract_sell_reason(row) for row in sell_successes)
    sell_success_count = len(sell_successes)
    stop_loss_count = int(sell_reason_counts.get("stop_loss", 0) or 0)
    dominant_reason = None
    dominant_reason_share_pct = None
    if sell_reason_counts and sell_success_count > 0:
        dominant_reason, dominant_count = sell_reason_counts.most_common(1)[0]
        dominant_reason_share_pct = round(dominant_count / sell_success_count * 100.0, 2)
    stop_loss_ratio_pct = (
        round(stop_loss_count / sell_success_count * 100.0, 2)
        if sell_success_count > 0
        else None
    )
    defensive_day = bool(
        sell_success_count >= min_sell_count_for_defensive_day
        and stop_loss_ratio_pct is not None
        and stop_loss_ratio_pct >= stop_loss_dominance_threshold_pct
    )
    operator_note = None
    if defensive_day:
        operator_note = (
            "Stop-loss exits dominated successful sells, so the day looks unusually defensive."
        )

    return {
        "sell_success_count": sell_success_count,
        "sell_reason_distribution": dict(sell_reason_counts),
        "stop_loss_ratio_pct": stop_loss_ratio_pct,
        "dominant_sell_reason": dominant_reason,
        "dominant_sell_reason_share_pct": dominant_reason_share_pct,
        "defensive_day": defensive_day,
        "operator_note": operator_note,
    }


def detect_same_day_stop_loss_reentries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sell_rows = sorted(
        (
            row for row in rows
            if str(row.get("action") or "").strip() == "sell_order_succeeded"
            and str(row.get("order_type") or "").strip() == "market_sell"
            and _extract_sell_reason(row) == "stop_loss"
        ),
        key=lambda row: str(row.get("timestamp") or ""),
    )
    buy_rows_by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if (
            str(row.get("action") or "").strip() == "order_succeeded"
            and str(row.get("order_type") or "").strip() == "market_buy"
        ):
            symbol = str(row.get("symbol") or "").strip()
            if symbol:
                buy_rows_by_symbol[symbol].append(row)
    for buy_rows in buy_rows_by_symbol.values():
        buy_rows.sort(key=lambda row: str(row.get("timestamp") or ""))

    detections: list[dict[str, Any]] = []
    for sell_row in sell_rows:
        symbol = str(sell_row.get("symbol") or "").strip()
        sell_ts = _parse_ts(sell_row.get("timestamp"))
        if not symbol or sell_ts is None:
            continue
        for buy_row in buy_rows_by_symbol.get(symbol, []):
            buy_ts = _parse_ts(buy_row.get("timestamp"))
            if buy_ts is None or buy_ts <= sell_ts:
                continue
            detections.append(
                {
                    "symbol": symbol,
                    "sell_ts": sell_ts.isoformat(),
                    "rebuy_ts": buy_ts.isoformat(),
                    "minutes_gap": round((buy_ts - sell_ts).total_seconds() / 60.0, 1),
                    "sell_cycle_id": sell_row.get("cycle_id"),
                    "rebuy_cycle_id": buy_row.get("cycle_id"),
                }
            )
            break
    return detections


def build_operational_day_diagnostics(
    *,
    account: str,
    date: str,
    daily_buy_notional_limit_krw: int | None = None,
) -> dict[str, Any]:
    order_log_path = _orders_path(account)
    rows = [row for row in _load_jsonl(order_log_path) if _matches_date(row, date)]

    resolved_limit = daily_buy_notional_limit_krw
    if resolved_limit is None:
        try:
            resolved_limit = int(get_settings().buy_daily_max_notional_krw or 0)
        except Exception:
            resolved_limit = 0

    buy_notional = summarize_buy_notional_rows(
        rows,
        daily_limit_krw=max(int(resolved_limit or 0), 0),
    )
    sell_reasons = summarize_sell_reason_rows(rows)
    same_day_reentries = detect_same_day_stop_loss_reentries(rows)

    warnings: list[str] = []
    if float(buy_notional.get("buy_notional_utilization_pct") or 0.0) >= 95.0:
        warnings.append("daily_buy_notional_nearly_exhausted")
    if int(buy_notional.get("late_day_buy_notional_blocked_count", 0) or 0) > 0:
        warnings.append("late_day_daily_notional_blocks_present")
    if same_day_reentries:
        warnings.append("same_day_stop_loss_reentry_detected")
    if sell_reasons.get("defensive_day"):
        warnings.append("stop_loss_dominant_sell_day")

    return {
        "order_log_path": str(order_log_path),
        "date": date,
        "buy_notional": buy_notional,
        "sell_reasons": sell_reasons,
        "same_day_stop_loss_reentries": same_day_reentries,
        "warnings": warnings,
    }
