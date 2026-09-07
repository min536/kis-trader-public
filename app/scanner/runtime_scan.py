"""Runtime scan pre-gating, normalizer, and planning helpers.

Extracted from app.main Stage 2e-1 through Stage 2e-3.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

from app.core.time_utils import get_korean_now
from app.runtime_state import parse_recent_order_time, record_reentry_decision
from app.scanner.scan_funnel import (
    normalize_buy_funnel_reason,
    normalize_pre_gating_payload,
    print_buy_pre_gating_summary,
    print_buy_runtime_filter_summary,
    print_buy_scan_stage_summary,
    resolve_buy_candidate_rejection_reason,
    resolve_buy_candidate_selection_outcome,
    resolve_deep_eval_rejection_reason,
    resolve_sell_exit_reason,
)
from app.scanner.scan_plan import (
    build_buy_scan_deep_eval_symbols,
    build_buy_scan_layered_universe,
    build_buy_scan_shallow_plan,
    cap_buy_scan_deep_eval_symbols_for_api_budget,
    select_buy_scan_profile,
    _take_circular_window,
)
from app.strategy.reentry import evaluate_reentry_eligibility


def count_symbol_buy_entries_today(*, state: dict, symbol: str) -> int:
    counts = state.get("buy_entries_by_symbol_today")
    if isinstance(counts, dict):
        return int(counts.get(symbol, 0) or 0)
    target_date = get_korean_now().date().isoformat()
    count = 0
    for order in state.get("recent_orders", []):
        if str(order.get("date", "")).strip() != target_date:
            continue
        if str(order.get("side", "")).strip() != "BUY":
            continue
        if str(order.get("symbol", "")).strip() != symbol:
            continue
        if str(order.get("action", "")).strip() != "order_submitted":
            continue
        count += 1
    return count


def is_symbol_in_reentry_cooldown(*, state: dict, symbol: str, cooldown_minutes: int) -> bool:
    if cooldown_minutes <= 0:
        return False
    target_date = get_korean_now().date().isoformat()
    for order in reversed(state.get("recent_orders", [])):
        if str(order.get("date", "")).strip() != target_date:
            continue
        if str(order.get("side", "")).strip() != "BUY":
            continue
        if str(order.get("symbol", "")).strip() != symbol:
            continue
        if str(order.get("action", "")).strip() not in {"order_submitted", "order_succeeded"}:
            continue
        order_time = parse_recent_order_time(order)
        if order_time is None:
            continue
        elapsed_minutes = (get_korean_now() - order_time).total_seconds() / 60
        return elapsed_minutes <= cooldown_minutes
    return False


def build_buy_scan_pre_gating(
    *,
    candidate_symbols: tuple[str, ...],
    state: dict,
    settings,
    portfolio_snapshot,
    daily_pnl_brake_state: dict[str, object] | None,
    regime_state: dict[str, object] | None,
    mode: str,
) -> dict[str, object]:
    requested_symbols = tuple(candidate_symbols)
    if daily_pnl_brake_state and daily_pnl_brake_state.get("buy_paused"):
        pause_reason = str(
            daily_pnl_brake_state.get("pause_reason")
            or daily_pnl_brake_state.get("reason")
            or "일중 손실 브레이크가 발동해 신규 BUY를 중단합니다."
        )
        return {
            "scan_allowed": False,
            "scan_block_reason": pause_reason,
            "allowed_symbols": (),
            "reason_counts": {"daily_pnl_brake": len(requested_symbols)},
            "early_reject_count": len(requested_symbols),
        }
    configured_excluded_symbols = {
        str(item).strip()
        for item in tuple(getattr(settings, "buy_excluded_symbols", ()) or ())
        if str(item).strip()
    }
    runtime_untradable_symbols = {
        str(item).strip()
        for item in tuple(state.get("buy_untradable_symbols_today") or ())
        if str(item).strip()
    }
    allowed_symbols: list[str] = []
    rejected: list[dict[str, str]] = []
    reason_counts: Counter[str] = Counter()
    reentry_state_by_symbol: dict[str, str] = {}
    reentry_allowed_count = 0
    for symbol in requested_symbols:
        if symbol in configured_excluded_symbols:
            reason_counts["excluded_symbol"] += 1
            rejected.append({"symbol": symbol, "reason_code": "excluded_symbol", "reason": "BUY_EXCLUDED_SYMBOLS 설정에 포함된 종목이라 deep scoring 전에 제외했습니다.", "diagnostics": ["BUY_EXCLUDED_SYMBOLS"]})
            continue
        if symbol in runtime_untradable_symbols:
            reason_counts["untradable_today"] += 1
            rejected.append({"symbol": symbol, "reason_code": "untradable_today", "reason": "오늘 매매불가 응답을 받은 종목이라 추가 BUY 시도를 차단했습니다.", "diagnostics": ["buy_untradable_symbols_today"]})
            continue
        reentry_decision = evaluate_reentry_eligibility(
            symbol=symbol,
            runtime_state=state,
            settings=settings,
            regime_state=regime_state,
            daily_pnl_brake_state=daily_pnl_brake_state,
            current_snapshot=(state.get("recent_market_snapshots_by_symbol") or {}).get(symbol),
            residual_position_qty=0,
            candidate=None,
            now=get_korean_now(),
        )
        reentry_state_by_symbol[symbol] = reentry_decision.state
        record_reentry_decision(
            state,
            symbol=symbol,
            reentry_state=reentry_decision.state,
            reason=reentry_decision.reason,
        )
        if reentry_decision.allowed:
            reentry_allowed_count += 1
            allowed_symbols.append(symbol)
    return {
        "scan_allowed": True,
        "allowed_symbols": tuple(allowed_symbols),
        "reentry_allowed_count": reentry_allowed_count,
        "reentry_state_by_symbol": reentry_state_by_symbol,
        "reason_counts": dict(reason_counts),
        "rejected": rejected,
    }


def apply_buy_runtime_guards_to_scan_results(
    results,
    *,
    state: dict,
    settings,
    portfolio_snapshot=None,
    regime_state: dict[str, object] | None = None,
    daily_pnl_brake_state: dict[str, object] | None = None,
):
    guarded_results = []
    now = get_korean_now()
    for result in results:
        if not result.candidate:
            guarded_results.append(result)
            continue

        current_position = (
            portfolio_snapshot.get_position(result.symbol)
            if portfolio_snapshot is not None
            else None
        )
        reentry_decision = evaluate_reentry_eligibility(
            symbol=result.symbol,
            runtime_state=state,
            settings=settings,
            regime_state=regime_state,
            daily_pnl_brake_state=daily_pnl_brake_state,
            current_snapshot=result.market_snapshot,
            residual_position_qty=int(getattr(current_position, "holding_qty", 0) or 0),
            candidate=result,
            now=now,
        )
        record_reentry_decision(
            state,
            symbol=result.symbol,
            reentry_state=reentry_decision.state,
            reason=reentry_decision.reason,
        )
        if not reentry_decision.allowed:
            guarded_results.append(
                replace(
                    result,
                    candidate=False,
                    final_reason=reentry_decision.reason,
                )
            )
            continue

        guarded_results.append(result)

    return tuple(guarded_results)
