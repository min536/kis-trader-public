from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.auth.account_scope import (
    get_order_log_read_paths,
    get_partitioned_log_path,
)
from app.core.jsonl import read_jsonl_objects
from app.core.time_utils import KOREA_TZ, get_korean_now


@dataclass(frozen=True)
class DailySummary:
    total_events: int
    action_counts: dict[str, int] = field(default_factory=dict)
    buy_strategy_passed_count: int = 0
    sell_strategy_passed_count: int = 0
    buy_order_submitted_count: int = 0
    buy_order_failed_count: int = 0
    sell_order_submitted_count: int = 0
    sell_order_failed_count: int = 0
    blocked_market_closed_count: int = 0
    blocked_premarket_count: int = 0
    blocked_after_market_count: int = 0
    blocked_holiday_or_closed_count: int = 0
    blocked_strategy_rejected_count: int = 0
    blocked_buy_daily_order_limit_count: int = 0
    blocked_buy_daily_notional_limit_count: int = 0
    blocked_buy_cooldown_count: int = 0
    blocked_buy_cash_insufficient_count: int = 0
    blocked_buy_exposure_limited_count: int = 0
    blocked_buy_trade_budget_limited_count: int = 0
    blocked_buy_expected_cost_too_high_count: int = 0
    blocked_buy_net_edge_too_low_count: int = 0
    blocked_rebuy_cooldown_count: int = 0
    blocked_same_symbol_daily_limit_count: int = 0
    blocked_daily_pnl_pause_count: int = 0
    blocked_daily_pnl_hard_stop_count: int = 0
    blocked_sell_market_closed_count: int = 0
    blocked_sell_premarket_count: int = 0
    blocked_sell_after_market_count: int = 0
    blocked_sell_holiday_or_closed_count: int = 0
    blocked_sell_strategy_rejected_count: int = 0
    blocked_sell_daily_order_limit_count: int = 0
    blocked_sell_daily_notional_limit_count: int = 0
    blocked_sell_cooldown_count: int = 0
    waiting_premarket_open_count: int = 0
    waiting_after_market_count: int = 0
    waiting_closed_count: int = 0
    skipped_buy_scan_cadence_count: int = 0
    skipped_buy_scan_budget_limited_count: int = 0
    buy_scan_budget_reserved_count: int = 0
    buy_scan_partial_budget_count: int = 0
    sell_watch_capped_for_buy_scan_count: int = 0
    sell_watch_partial_budget_count: int = 0
    cycle_error_count: int = 0
    rebalance_considered_count: int = 0
    rebalance_skipped_score_delta_count: int = 0
    rebalance_skipped_profit_buffer_count: int = 0
    rebalance_skipped_limit_count: int = 0
    rebalance_skipped_no_candidate_count: int = 0
    rebalance_skipped_concentration_count: int = 0
    rebalance_skipped_net_edge_count: int = 0
    rebalance_deferred_sell_watch_incomplete_count: int = 0
    rebalance_quality_preview_count: int = 0
    rebalance_sell_preview_count: int = 0
    sell_preview_only_count: int = 0
    buy_preview_count: int = 0
    sell_preview_count: int = 0


def _is_same_korean_date(timestamp: str, target_date: date) -> bool:
    try:
        record_time = datetime.fromisoformat(timestamp)
    except ValueError:
        return False

    if record_time.tzinfo is not None:
        record_date = record_time.astimezone(KOREA_TZ).date()
    else:
        record_date = record_time.date()
    return record_date == target_date


def _extract_strategy_final_decision(record: dict[str, Any]) -> bool:
    raw_response = record.get("raw_response")
    if not isinstance(raw_response, dict):
        return False

    strategy_details = raw_response.get("strategy_details")
    if not isinstance(strategy_details, dict):
        return False

    return bool(strategy_details.get("final_decision", False))


def _extract_sell_strategy_final_decision(record: dict[str, Any]) -> bool:
    raw_response = record.get("raw_response")
    if not isinstance(raw_response, dict):
        return False

    strategy_details = raw_response.get("sell_strategy_details")
    if not isinstance(strategy_details, dict):
        return False

    return bool(strategy_details.get("should_attempt_sell", False))


def _is_buy_preview(record: dict[str, Any]) -> bool:
    order_type = str(record.get("order_type", "")).strip()
    action = str(record.get("action", "")).strip()
    if order_type != "market_buy":
        return False
    return action in {
        "order_submitted",
        "order_succeeded",
        "order_failed",
        "blocked_market_closed",
        "blocked_confirm_buy_off",
        "blocked_position_sizing",
    }


def _is_sell_preview(record: dict[str, Any]) -> bool:
    order_type = str(record.get("order_type", "")).strip()
    action = str(record.get("action", "")).strip()
    if order_type != "market_sell":
        return False
    return action in {
        "sell_preview_only",
        "sell_order_submitted",
        "sell_order_succeeded",
        "sell_order_failed",
        "blocked_sell_market_closed",
        "blocked_sell_strategy_rejected",
    }


def build_daily_summary(today: date | None = None) -> DailySummary:
    target_date = today or get_korean_now().date()
    order_log_files = [path for path in get_order_log_read_paths() if path.exists()]
    if not order_log_files:
        return DailySummary(total_events=0)

    action_counter: Counter[str] = Counter()
    total_events = 0
    buy_strategy_passed_count = 0
    sell_strategy_passed_count = 0
    buy_preview_count = 0
    sell_preview_count = 0

    records: list[dict[str, Any]] = []
    for order_log_file in order_log_files:
        path_records, _errors = read_jsonl_objects(order_log_file)
        records.extend(path_records)
    for record in records:
        if str(record.get("environment", "mock")).strip() != "mock":
            continue
        raw_response = record.get("raw_response")
        if isinstance(raw_response, dict):
            sell_test_mode = str(raw_response.get("sell_test_mode", "")).strip().lower()
            if sell_test_mode and sell_test_mode != "off":
                continue

        timestamp = str(record.get("timestamp", "")).strip()
        if not timestamp or not _is_same_korean_date(timestamp, target_date):
            continue

        total_events += 1
        action = str(record.get("action", "")).strip()
        if action:
            action_counter[action] += 1

        if _extract_strategy_final_decision(record):
            buy_strategy_passed_count += 1
        if _extract_sell_strategy_final_decision(record):
            sell_strategy_passed_count += 1
        if _is_buy_preview(record):
            buy_preview_count += 1
        if _is_sell_preview(record):
            sell_preview_count += 1

    action_counts = dict(action_counter)
    return DailySummary(
        total_events=total_events,
        action_counts=action_counts,
        buy_strategy_passed_count=buy_strategy_passed_count,
        sell_strategy_passed_count=sell_strategy_passed_count,
        buy_order_submitted_count=action_counts.get("order_submitted", 0),
        buy_order_failed_count=action_counts.get("order_failed", 0),
        sell_order_submitted_count=action_counts.get("sell_order_submitted", 0),
        sell_order_failed_count=action_counts.get("sell_order_failed", 0),
        blocked_market_closed_count=(
            action_counts.get("blocked_market_closed", 0)
            + action_counts.get("blocked_premarket", 0)
            + action_counts.get("blocked_after_market", 0)
            + action_counts.get("blocked_holiday_or_closed", 0)
        ),
        blocked_premarket_count=action_counts.get("blocked_premarket", 0),
        blocked_after_market_count=action_counts.get("blocked_after_market", 0),
        blocked_holiday_or_closed_count=action_counts.get("blocked_holiday_or_closed", 0),
        blocked_strategy_rejected_count=action_counts.get("blocked_strategy_rejected", 0),
        blocked_buy_daily_order_limit_count=action_counts.get(
            "blocked_buy_daily_order_limit",
            action_counts.get("blocked_daily_order_limit", 0),
        ),
        blocked_buy_daily_notional_limit_count=action_counts.get(
            "blocked_buy_daily_notional_limit",
            action_counts.get("blocked_daily_notional_limit", 0),
        ),
        blocked_sell_daily_order_limit_count=action_counts.get(
            "blocked_sell_daily_order_limit",
            0,
        ),
        blocked_sell_daily_notional_limit_count=action_counts.get(
            "blocked_sell_daily_notional_limit",
            0,
        ),
        blocked_buy_cooldown_count=action_counts.get("blocked_buy_cooldown", 0),
        blocked_buy_cash_insufficient_count=action_counts.get(
            "blocked_buy_cash_insufficient",
            0,
        ),
        blocked_buy_exposure_limited_count=action_counts.get(
            "blocked_buy_exposure_limited",
            0,
        ),
        blocked_buy_trade_budget_limited_count=action_counts.get(
            "blocked_buy_trade_budget_limited",
            0,
        ),
        blocked_buy_expected_cost_too_high_count=action_counts.get(
            "blocked_buy_expected_cost_too_high",
            0,
        ),
        blocked_buy_net_edge_too_low_count=action_counts.get(
            "blocked_buy_net_edge_too_low",
            0,
        ),
        blocked_rebuy_cooldown_count=(
            action_counts.get("blocked_buy_reentry_cooldown", 0)
            + action_counts.get("blocked_rebuy_cooldown", 0)
        ),
        blocked_same_symbol_daily_limit_count=action_counts.get(
            "blocked_buy_same_symbol_daily_limit",
            action_counts.get("blocked_same_symbol_daily_limit", 0),
        ),
        blocked_daily_pnl_pause_count=action_counts.get("blocked_daily_pnl_pause", 0),
        blocked_daily_pnl_hard_stop_count=action_counts.get(
            "blocked_daily_pnl_hard_stop",
            0,
        ),
        blocked_sell_market_closed_count=(
            action_counts.get("blocked_sell_market_closed", 0)
            + action_counts.get("blocked_sell_premarket", 0)
            + action_counts.get("blocked_sell_after_market", 0)
            + action_counts.get("blocked_sell_holiday_or_closed", 0)
        ),
        blocked_sell_premarket_count=action_counts.get("blocked_sell_premarket", 0),
        blocked_sell_after_market_count=action_counts.get("blocked_sell_after_market", 0),
        blocked_sell_holiday_or_closed_count=action_counts.get("blocked_sell_holiday_or_closed", 0),
        blocked_sell_strategy_rejected_count=action_counts.get(
            "blocked_sell_strategy_rejected",
            0,
        ),
        blocked_sell_cooldown_count=action_counts.get("blocked_sell_cooldown", 0),
        waiting_premarket_open_count=action_counts.get("waiting_premarket_open", 0),
        waiting_after_market_count=action_counts.get("waiting_after_market", 0),
        waiting_closed_count=action_counts.get("waiting_closed", 0),
        skipped_buy_scan_cadence_count=action_counts.get("skipped_buy_scan_cadence", 0),
        skipped_buy_scan_budget_limited_count=action_counts.get(
            "skipped_buy_scan_budget_limited",
            0,
        ),
        buy_scan_budget_reserved_count=action_counts.get("buy_scan_budget_reserved", 0),
        buy_scan_partial_budget_count=action_counts.get("buy_scan_partial_budget", 0),
        sell_watch_capped_for_buy_scan_count=action_counts.get(
            "sell_watch_capped_for_buy_scan",
            0,
        ),
        sell_watch_partial_budget_count=action_counts.get(
            "sell_watch_partial_budget",
            0,
        ),
        cycle_error_count=action_counts.get("cycle_error", 0),
        rebalance_considered_count=action_counts.get("rebalance_considered", 0),
        rebalance_skipped_score_delta_count=action_counts.get(
            "rebalance_skipped_score_delta",
            0,
        ),
        rebalance_skipped_profit_buffer_count=action_counts.get(
            "rebalance_skipped_profit_buffer",
            0,
        ),
        rebalance_skipped_limit_count=action_counts.get("rebalance_skipped_limit", 0),
        rebalance_skipped_no_candidate_count=action_counts.get(
            "rebalance_skipped_no_candidate",
            0,
        ),
        rebalance_skipped_concentration_count=action_counts.get(
            "rebalance_skipped_concentration",
            0,
        ),
        rebalance_skipped_net_edge_count=action_counts.get(
            "rebalance_skipped_net_edge",
            0,
        ),
        rebalance_deferred_sell_watch_incomplete_count=action_counts.get(
            "rebalance_deferred_sell_watch_incomplete",
            0,
        ),
        rebalance_quality_preview_count=action_counts.get(
            "rebalance_quality_preview",
            0,
        ),
        rebalance_sell_preview_count=action_counts.get("rebalance_sell_preview", 0),
        sell_preview_only_count=action_counts.get("sell_preview_only", 0),
        buy_preview_count=buy_preview_count,
        sell_preview_count=sell_preview_count,
    )


def build_daily_summary_console_lines(summary: DailySummary) -> list[str]:
    return [
        "=== 오늘 요약 ===",
        f"총 이벤트 수: {summary.total_events}",
        f"buy_strategy_passed: {summary.buy_strategy_passed_count}",
        f"sell_strategy_passed: {summary.sell_strategy_passed_count}",
        f"buy_order_submitted: {summary.buy_order_submitted_count}",
        f"buy_order_failed: {summary.buy_order_failed_count}",
        f"sell_order_submitted: {summary.sell_order_submitted_count}",
        f"sell_order_failed: {summary.sell_order_failed_count}",
        f"buy_preview: {summary.buy_preview_count}",
        f"sell_preview: {summary.sell_preview_count}",
        f"blocked_market_closed: {summary.blocked_market_closed_count}",
        f"blocked_premarket: {summary.blocked_premarket_count}",
        f"blocked_after_market: {summary.blocked_after_market_count}",
        f"blocked_holiday_or_closed: {summary.blocked_holiday_or_closed_count}",
        f"blocked_strategy_rejected: {summary.blocked_strategy_rejected_count}",
        f"blocked_buy_daily_order_limit: {summary.blocked_buy_daily_order_limit_count}",
        f"blocked_buy_daily_notional_limit: {summary.blocked_buy_daily_notional_limit_count}",
        f"blocked_buy_cooldown: {summary.blocked_buy_cooldown_count}",
        f"blocked_buy_cash_insufficient: {summary.blocked_buy_cash_insufficient_count}",
        f"blocked_buy_exposure_limited: {summary.blocked_buy_exposure_limited_count}",
        f"blocked_buy_trade_budget_limited: {summary.blocked_buy_trade_budget_limited_count}",
        f"blocked_buy_expected_cost_too_high: {summary.blocked_buy_expected_cost_too_high_count}",
        f"blocked_buy_net_edge_too_low: {summary.blocked_buy_net_edge_too_low_count}",
        f"blocked_buy_reentry_cooldown: {summary.blocked_rebuy_cooldown_count}",
        f"blocked_buy_same_symbol_daily_limit: {summary.blocked_same_symbol_daily_limit_count}",
        f"blocked_daily_pnl_pause: {summary.blocked_daily_pnl_pause_count}",
        f"blocked_daily_pnl_hard_stop: {summary.blocked_daily_pnl_hard_stop_count}",
        f"blocked_sell_market_closed: {summary.blocked_sell_market_closed_count}",
        f"blocked_sell_premarket: {summary.blocked_sell_premarket_count}",
        f"blocked_sell_after_market: {summary.blocked_sell_after_market_count}",
        f"blocked_sell_holiday_or_closed: {summary.blocked_sell_holiday_or_closed_count}",
        f"blocked_sell_strategy_rejected: {summary.blocked_sell_strategy_rejected_count}",
        f"blocked_sell_daily_order_limit: {summary.blocked_sell_daily_order_limit_count}",
        f"blocked_sell_daily_notional_limit: {summary.blocked_sell_daily_notional_limit_count}",
        f"blocked_sell_cooldown: {summary.blocked_sell_cooldown_count}",
        f"waiting_premarket_open: {summary.waiting_premarket_open_count}",
        f"waiting_after_market: {summary.waiting_after_market_count}",
        f"waiting_closed: {summary.waiting_closed_count}",
        f"skipped_buy_scan_cadence: {summary.skipped_buy_scan_cadence_count}",
        f"skipped_buy_scan_budget_limited: {summary.skipped_buy_scan_budget_limited_count}",
        f"buy_scan_budget_reserved: {summary.buy_scan_budget_reserved_count}",
        f"buy_scan_partial_budget: {summary.buy_scan_partial_budget_count}",
        f"sell_watch_capped_for_buy_scan: {summary.sell_watch_capped_for_buy_scan_count}",
        f"sell_watch_partial_budget: {summary.sell_watch_partial_budget_count}",
        f"cycle_error: {summary.cycle_error_count}",
        f"rebalance_considered: {summary.rebalance_considered_count}",
        f"rebalance_skipped_score_delta: {summary.rebalance_skipped_score_delta_count}",
        f"rebalance_skipped_profit_buffer: {summary.rebalance_skipped_profit_buffer_count}",
        f"rebalance_skipped_limit: {summary.rebalance_skipped_limit_count}",
        f"rebalance_skipped_no_candidate: {summary.rebalance_skipped_no_candidate_count}",
        f"rebalance_skipped_concentration: {summary.rebalance_skipped_concentration_count}",
        f"rebalance_skipped_net_edge: {summary.rebalance_skipped_net_edge_count}",
        f"rebalance_deferred_sell_watch_incomplete: {summary.rebalance_deferred_sell_watch_incomplete_count}",
        f"rebalance_quality_preview: {summary.rebalance_quality_preview_count}",
        f"rebalance_sell_preview: {summary.rebalance_sell_preview_count}",
        f"sell_preview_only: {summary.sell_preview_only_count}",
    ]


# ---------------------------------------------------------------------------
# Task 4 & 5: cycle_stats aggregation for core/non-core breakdown and
# buy non-execution drop reason histogram.
# ---------------------------------------------------------------------------


def _get_cycle_stats_path(today: date | None = None) -> Path:
    target_date = today or get_korean_now().date()
    suffix = target_date.strftime("%Y%m%d")
    return get_partitioned_log_path("cycle_stats", suffix=suffix)


def build_cycle_stats_daily_summary(today: date | None = None) -> dict[str, Any]:
    """Aggregate cycle_stats JSONL records for today into a daily summary dict.

    Returns per-bucket candidate funnel counts and buy non-execution reason histogram.
    """
    target_date = today or get_korean_now().date()
    path = _get_cycle_stats_path(target_date)
    if not path.exists():
        return {}

    core_layered_total = 0
    noncore_layered_total = 0
    core_shortlisted_total = 0
    noncore_shortlisted_total = 0
    core_deep_eval_total = 0
    noncore_deep_eval_total = 0
    core_final_candidate = 0
    noncore_final_candidate = 0
    core_executed = 0
    noncore_executed = 0
    buy_non_execution_reasons: Counter[str] = Counter()
    buy_scan_rl_skips = 0
    cycles_with_final_candidate = 0
    cycles_with_executed = 0
    total_cycles = 0

    records, _errors = read_jsonl_objects(path)
    for record in records:
        # Filter to today's records
        ts = str(record.get("ts", "")).strip()
        if ts:
            try:
                record_time = datetime.fromisoformat(ts)
                if record_time.tzinfo is not None:
                    record_date = record_time.astimezone(KOREA_TZ).date()
                else:
                    record_date = record_time.date()
                if record_date != target_date:
                    continue
            except ValueError:
                continue

        total_cycles += 1
        core_layered_total += int(record.get("core_layered_count", 0) or 0)
        noncore_layered_total += int(record.get("noncore_layered_count", 0) or 0)
        core_shortlisted_total += int(record.get("core_shortlisted_count", 0) or 0)
        noncore_shortlisted_total += int(record.get("noncore_shortlisted_count", 0) or 0)
        core_deep_eval_total += int(record.get("core_deep_eval_count", 0) or 0)
        noncore_deep_eval_total += int(record.get("noncore_deep_eval_count", 0) or 0)
        selected_layer = str(record.get("selected_candidate_layer", "") or "").strip()
        has_final = bool(int(record.get("final_candidate_count", 0) or 0))
        has_executed = bool(int(record.get("executed_order_count", 0) or 0))
        if has_final:
            cycles_with_final_candidate += 1
            if selected_layer == "core":
                core_final_candidate += 1
            elif selected_layer:
                noncore_final_candidate += 1
        if has_executed:
            cycles_with_executed += 1
            if selected_layer == "core":
                core_executed += 1
            elif selected_layer:
                noncore_executed += 1
        drop_reason = str(record.get("buy_non_execution_reason", "") or "").strip()
        if drop_reason and has_final and not has_executed:
            buy_non_execution_reasons[drop_reason] += 1

    return {
        "total_cycles": total_cycles,
        "cycles_with_final_candidate": cycles_with_final_candidate,
        "cycles_with_executed": cycles_with_executed,
        # Task 5: core/non-core breakdown
        "core_layered_total": core_layered_total,
        "noncore_layered_total": noncore_layered_total,
        "core_shortlisted_total": core_shortlisted_total,
        "noncore_shortlisted_total": noncore_shortlisted_total,
        "core_deep_eval_total": core_deep_eval_total,
        "noncore_deep_eval_total": noncore_deep_eval_total,
        "core_final_candidate": core_final_candidate,
        "noncore_final_candidate": noncore_final_candidate,
        "core_executed": core_executed,
        "noncore_executed": noncore_executed,
        # Task 4: buy non-execution drop reason histogram
        "buy_non_execution_reasons": dict(buy_non_execution_reasons),
    }


def build_cycle_stats_console_lines(stats: dict[str, Any]) -> list[str]:
    """Build console lines for the cycle_stats daily aggregation (Tasks 4 & 5)."""
    if not stats or not stats.get("total_cycles", 0):
        return []

    lines: list[str] = ["=== 버킷별 / 비실행 사유 일일 집계 ==="]

    # Task 5: core/non-core funnel breakdown
    lines.append(
        f"core   layered={stats.get('core_layered_total', 0)} | "
        f"shortlisted={stats.get('core_shortlisted_total', 0)} | "
        f"deep_eval={stats.get('core_deep_eval_total', 0)} | "
        f"final_candidate={stats.get('core_final_candidate', 0)} | "
        f"executed={stats.get('core_executed', 0)}"
    )
    lines.append(
        f"noncore layered={stats.get('noncore_layered_total', 0)} | "
        f"shortlisted={stats.get('noncore_shortlisted_total', 0)} | "
        f"deep_eval={stats.get('noncore_deep_eval_total', 0)} | "
        f"final_candidate={stats.get('noncore_final_candidate', 0)} | "
        f"executed={stats.get('noncore_executed', 0)}"
    )
    lines.append(
        f"cycles_with_final={stats.get('cycles_with_final_candidate', 0)} | "
        f"cycles_executed={stats.get('cycles_with_executed', 0)}"
    )

    # Task 4: buy non-execution drop reason histogram
    drop_reasons = dict(stats.get("buy_non_execution_reasons", {}))
    if drop_reasons:
        sorted_reasons = sorted(drop_reasons.items(), key=lambda kv: -kv[1])
        reason_parts = [f"{reason}={count}" for reason, count in sorted_reasons]
        lines.append("buy_non_execution(final→executed 미달): " + " | ".join(reason_parts))
    else:
        lines.append("buy_non_execution(final→executed 미달): (없음)")

    return lines
