"""Pure replay record view helpers."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

def _match(record: dict, *, action: str, session: str) -> bool:
    if action and action.lower() not in str(record.get("final_action", "")).lower():
        return False
    market_session = record.get("market_session") or {}
    session_value = str(market_session.get("session", "")).lower()
    if session and session.lower() != session_value:
        return False
    return True


def _parse_clock(value: str) -> time | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"잘못된 시간 형식입니다: {text} (예: 09:00 또는 09:00:45)")


def _record_time(record: dict[str, Any]) -> time | None:
    timestamp = str(record.get("timestamp") or "").strip()
    if not timestamp:
        return None
    try:
        return datetime.fromisoformat(timestamp).timetz().replace(tzinfo=None)
    except ValueError:
        return None


def _time_match(record: dict[str, Any], *, from_time: time | None, to_time: time | None) -> bool:
    if from_time is None and to_time is None:
        return True
    record_clock = _record_time(record)
    if record_clock is None:
        return False
    if from_time is not None and record_clock < from_time:
        return False
    if to_time is not None and record_clock > to_time:
        return False
    return True


def _record_symbol_set(record: dict[str, Any]) -> set[str]:
    symbols: set[str] = set()

    def _add(value: Any) -> None:
        symbol = str(value or "").strip()
        if symbol:
            symbols.add(symbol)

    selected_buy = _selected_buy(record)
    if selected_buy:
        _add(selected_buy.get("symbol"))
    selected_sell = record.get("selected_sell_candidate")
    if isinstance(selected_sell, dict):
        _add(selected_sell.get("symbol"))

    for candidate in _top_candidates(record):
        if isinstance(candidate, dict):
            _add(candidate.get("symbol"))

    for key in (
        "selected_primary_action_symbol",
        "rate_limit_partial_stop_symbol",
    ):
        _add(record.get(key))

    for list_key in (
        "sell_watch_evaluated_symbols",
        "sell_watch_skipped_symbols",
    ):
        values = record.get(list_key)
        values = values if isinstance(values, list) else []
        for value in values:
            _add(value)

    selection_details = record.get("selection_details")
    selection_details = selection_details if isinstance(selection_details, dict) else {}
    staged_scan = selection_details.get("staged_scan")
    staged_scan = staged_scan if isinstance(staged_scan, dict) else {}
    _add(staged_scan.get("core_rescue_selected_symbol"))
    _add(staged_scan.get("core_rescue_replaced_symbol"))
    return symbols


def _symbol_match(record: dict[str, Any], *, symbol: str) -> bool:
    target = str(symbol or "").strip()
    if not target:
        return True
    return target in _record_symbol_set(record)


def _compact_reason(value: Any, *, default: str = "-") -> str:
    text = str(value or "").strip()
    if not text:
        return default
    if len(text) > 120:
        return text[:117] + "..."
    return text


def _candidate_name(candidate: dict[str, Any] | None) -> str:
    if not isinstance(candidate, dict):
        return "-"
    return str(
        candidate.get("display_name")
        or candidate.get("name")
        or candidate.get("symbol")
        or "-"
    )


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coalesce_float(*values: Any) -> float | None:
    for value in values:
        parsed = _safe_float(value)
        if parsed is not None:
            return parsed
    return None


def _technical_overlay_payload(candidate: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(candidate, dict) or not candidate:
        return {
            "available": False,
            "active": False,
            "trend": None,
            "macd": None,
            "total": None,
            "summary": "없음",
            "names": "-",
        }

    feature_map = candidate.get("feature_map")
    feature_map = feature_map if isinstance(feature_map, dict) else {}
    technical_group = feature_map.get("technical_features")
    technical_group = technical_group if isinstance(technical_group, dict) else {}
    score_components = candidate.get("score_components")
    score_components = score_components if isinstance(score_components, dict) else {}
    feature_summaries = candidate.get("feature_summaries")
    feature_summaries = feature_summaries if isinstance(feature_summaries, dict) else {}

    trend = _coalesce_float(
        technical_group.get("trend_alignment_score"),
        score_components.get("trend_alignment_score"),
    )
    macd = _coalesce_float(
        technical_group.get("macd_momentum_score"),
        score_components.get("macd_momentum_score"),
    )
    available = trend is not None or macd is not None or bool(technical_group)
    total = None
    if trend is not None or macd is not None:
        total = round(float(trend or 0.0) + float(macd or 0.0), 2)
    active = bool(total and total > 0.0)

    parts: list[str] = []
    if trend is not None:
        parts.append(f"trend={trend:.2f}")
    if macd is not None:
        parts.append(f"macd={macd:.2f}")
    summary = "inactive"
    if active:
        summary = "active"
    elif available:
        summary = "available/0"
    elif (
        "trend_alignment" in list(candidate.get("score_highlights") or [])
        or "macd_momentum" in list(candidate.get("score_highlights") or [])
    ):
        summary = "active"
    names = str(feature_summaries.get("technical_features") or "").strip() or "-"
    if parts:
        summary = f"{summary} ({', '.join(parts)})"
    return {
        "available": available,
        "active": active,
        "trend": trend,
        "macd": macd,
        "total": total,
        "summary": summary,
        "names": names,
    }


def _selected_buy(record: dict[str, Any]) -> dict[str, Any]:
    selected = record.get("selected_buy_candidate")
    return selected if isinstance(selected, dict) else {}


def _selected_primary_name(record: dict[str, Any]) -> str:
    selected_sell = record.get("selected_sell_candidate")
    selected_sell = selected_sell if isinstance(selected_sell, dict) else {}
    primary_name = str(
        record.get("selected_primary_action_name")
        or record.get("selected_primary_action_symbol")
        or ""
    ).strip()
    if primary_name:
        return primary_name
    buy_name = _candidate_name(_selected_buy(record))
    if buy_name != "-":
        return buy_name
    sell_name = _candidate_name(selected_sell)
    if sell_name != "-":
        return sell_name
    return "-"


def _sell_final_review_name(record: dict[str, Any]) -> str:
    final_review = record.get("sell_watch_final_review")
    if isinstance(final_review, dict):
        final_review_name = _candidate_name(final_review)
        if final_review_name != "-":
            return final_review_name
    selected_sell = record.get("selected_sell_candidate")
    if isinstance(selected_sell, dict):
        selected_sell_name = _candidate_name(selected_sell)
        if selected_sell_name != "-":
            return selected_sell_name
    evaluated_symbols = record.get("sell_watch_evaluated_symbols")
    evaluated_symbols = (
        evaluated_symbols if isinstance(evaluated_symbols, list) else []
    )
    normalized = sorted(
        str(symbol).strip() for symbol in evaluated_symbols if str(symbol).strip()
    )
    if normalized:
        return normalized[0]
    return "-"


def _top_candidates(record: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = record.get("scanner_candidates_top")
    return candidates if isinstance(candidates, list) else []


def _runner_up(record: dict[str, Any]) -> dict[str, Any]:
    selected_symbol = str((_selected_buy(record).get("symbol") or "")).strip()
    for candidate in _top_candidates(record):
        if not isinstance(candidate, dict):
            continue
        symbol = str(candidate.get("symbol", "")).strip()
        if selected_symbol and symbol == selected_symbol:
            continue
        return candidate
    return {}


def _pre_gating_view(record: dict[str, Any]) -> dict[str, Any]:
    pre_gating = record.get("pre_gating")
    pre_gating = pre_gating if isinstance(pre_gating, dict) else {}
    if pre_gating:
        return {
            "payload": pre_gating,
            "summary": record.get("pre_gating_summary"),
            "rejected_count": int(record.get("pre_gating_rejected_count", 0) or 0),
            "rejected_symbols": list(record.get("pre_gating_rejected_symbols") or []),
            "reasons_by_symbol": dict(record.get("pre_gating_reasons_by_symbol") or {}),
            "stage": record.get("pre_gating_stage"),
            "reentry_state_by_symbol": dict(record.get("reentry_state_by_symbol") or {}),
            "last_exit_reason_by_symbol": dict(record.get("last_exit_reason_by_symbol") or {}),
            "reentry_block_reason_counts": dict(record.get("reentry_block_reason_counts") or {}),
            "reentry_allowed_count": int(record.get("reentry_allowed_count", 0) or 0),
            "reentry_blocked_count": int(record.get("reentry_blocked_count", 0) or 0),
        }

    selection_details = record.get("selection_details")
    selection_details = selection_details if isinstance(selection_details, dict) else {}
    pre_gating = selection_details.get("pre_gating")
    pre_gating = pre_gating if isinstance(pre_gating, dict) else {}
    rejected = list(pre_gating.get("rejected") or [])
    reasons_by_symbol = {
        str(item.get("symbol") or "").strip(): str(item.get("reason_code") or "unknown")
        for item in rejected
        if isinstance(item, dict) and str(item.get("symbol") or "").strip()
    }
    return {
        "payload": pre_gating,
        "summary": None,
        "rejected_count": int(pre_gating.get("early_reject_count", len(rejected)) or 0),
        "rejected_symbols": [item.get("symbol") for item in rejected if isinstance(item, dict)],
        "reasons_by_symbol": reasons_by_symbol,
        "stage": pre_gating.get("stage"),
        "reentry_state_by_symbol": dict(pre_gating.get("reentry_state_by_symbol") or {}),
        "last_exit_reason_by_symbol": dict(pre_gating.get("last_exit_reason_by_symbol") or {}),
        "reentry_block_reason_counts": dict(pre_gating.get("reentry_block_reason_counts") or {}),
        "reentry_allowed_count": int(pre_gating.get("reentry_allowed_count", 0) or 0),
        "reentry_blocked_count": int(pre_gating.get("reentry_blocked_count", 0) or 0),
    }


def _staged_scan_view(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile": record.get("buy_scan_profile"),
        "core_count": int(record.get("buy_scan_universe_core_count", 0) or 0),
        "rotating_count": int(record.get("buy_scan_universe_rotating_count", 0) or 0),
        "exploration_count": int(record.get("buy_scan_universe_exploration_count", 0) or 0),
        "pre_gating_count": int(record.get("buy_scan_pre_gating_count", 0) or 0),
        "shallow_ranked_count": int(record.get("buy_scan_shallow_ranked_count", 0) or 0),
        "deep_eval_limit": int(record.get("buy_scan_deep_eval_limit", 0) or 0),
        "exploration_quota_used": int(record.get("buy_scan_exploration_quota_used", 0) or 0),
        "layered_symbols_preview": dict(record.get("buy_scan_layered_symbols_preview") or {}),
        "shallow_shortlist_preview": list(record.get("buy_scan_shallow_shortlist_preview") or []),
    }


def _buy_gap_text(record: dict[str, Any]) -> str:
    selected = _selected_buy(record)
    if not selected:
        return "-"
    try:
        passed = int(selected.get("passed_count") or 0)
        enabled = int(selected.get("enabled_count") or 0)
    except (TypeError, ValueError):
        return "-"
    required = min(3, enabled) if enabled > 0 else 3
    gap = max(required - passed, 0)
    return f"{passed}/{required} (gap={gap})"


def _buy_funnel_view(record: dict[str, Any]) -> dict[str, Any]:
    summary = record.get("buy_funnel_summary")
    summary = summary if isinstance(summary, dict) else {}
    if summary:
        return summary
    return {
        "universe_size": int(record.get("buy_scan_requested_count", 0) or 0),
        "layered_universe_size": 0,
        "layered_out_count": 0,
        "pre_gate_passed": max(
            int(record.get("buy_scan_requested_count", 0) or 0)
            - int(record.get("pre_gating_rejected_count", 0) or 0),
            0,
        ),
        "pre_gate_rejected_total": int(record.get("pre_gating_rejected_count", 0) or 0),
        "pre_gate_rejection_counts": dict(record.get("pre_gate_rejection_counts") or {}),
        "shallow_ranked_count": int(record.get("buy_scan_shallow_ranked_count", 0) or 0),
        "shallow_shortlist_size": len(record.get("buy_scan_shallow_shortlist_preview") or []),
        "deep_eval_count": int(record.get("buy_scan_evaluated_count", 0) or 0),
        "final_candidate_count": 1 if _selected_buy(record) else 0,
        "executed_order_count": int(record.get("executed_order_count", 0) or 0),
        "sell_evaluated_count": int(record.get("sell_evaluated_count", 0) or 0),
        "sell_triggered_count": int(record.get("sell_triggered_count", 0) or 0),
    }


