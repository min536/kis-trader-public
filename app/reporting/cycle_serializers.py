"""Cycle snapshot serializers (R3-S8).

Relocated verbatim from app.reporting.cycle_snapshots. cycle_snapshots keeps
legacy bindings so existing import sites and patch targets remain valid.
"""

from __future__ import annotations

from typing import Any


def _serialize_market_snapshot(snapshot) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    symbol = str(getattr(snapshot, "symbol", "") or "").strip()
    try:
        current_price = int(getattr(snapshot, "current_price", 0) or 0)
    except (TypeError, ValueError):
        current_price = 0
    if not symbol or current_price <= 0:
        return None
    return {
        "symbol": symbol,
        "current_price": current_price,
        "open_price": int(getattr(snapshot, "open_price", 0) or 0),
        "low_price": int(getattr(snapshot, "low_price", 0) or 0),
        "prev_day_change_pct": float(getattr(snapshot, "prev_day_change_pct", 0.0) or 0.0),
    }


def _serialize_observed_market_snapshots(
    observed_market_snapshots: dict[str, object] | None,
) -> list[dict[str, Any]]:
    if not isinstance(observed_market_snapshots, dict):
        return []
    serialized: list[dict[str, Any]] = []
    for symbol, snapshot in sorted(observed_market_snapshots.items(), key=lambda item: str(item[0])):
        payload = _serialize_market_snapshot(snapshot)
        if payload is None:
            continue
        if not payload.get("symbol"):
            payload["symbol"] = str(symbol or "").strip()
        serialized.append(payload)
    return serialized


def _serialize_buy_candidate(result) -> dict[str, Any] | None:
    if result is None:
        return None

    return {
        "symbol": result.symbol,
        "name": result.name,
        "display_name": result.display_name,
        "candidate": result.candidate,
        "passed_count": result.passed_count,
        "signal_quality_count": result.signal_quality_count,
        "enabled_count": result.enabled_count,
        "passed_pattern": result.passed_pattern,
        "score": result.score,
        "net_profit_buffer_bps": result.net_profit_buffer_bps,
        "passes_profit_buffer": result.passes_profit_buffer,
        "expected_fee_krw": result.expected_fee_krw,
        "expected_tax_krw": result.expected_tax_krw,
        "expected_slippage_krw": result.expected_slippage_krw,
        "expected_total_cost_krw": result.expected_total_cost_krw,
        "expected_cost_bps": result.expected_cost_bps,
        "cost_quality_score": result.cost_quality_score,
        "expected_cost_penalty": result.expected_cost_penalty,
        "net_edge_bps": result.net_edge_bps,
        "cost_block_reason": result.cost_block_reason,
        "final_reason": result.final_reason,
        "score_components": result.score_components,
        "score_highlights": list(result.score_highlights),
        "score_penalties": list(result.score_penalties),
        "score_summary": result.score_summary,
        "math_score_summary": result.math_score_summary,
        "mean_reversion_zscore": result.mean_reversion_zscore,
        "reversion_quality_score": result.reversion_quality_score,
        "overextension_penalty": result.overextension_penalty,
        "ou_half_life_estimate": result.ou_half_life_estimate,
        "mean_reversion_summary": result.mean_reversion_summary,
        "portfolio_avg_correlation": result.portfolio_avg_correlation,
        "portfolio_max_correlation": result.portfolio_max_correlation,
        "variance_increase_estimate": result.variance_increase_estimate,
        "portfolio_risk_summary": result.portfolio_risk_summary,
        "portfolio_correlation_penalty": result.portfolio_correlation_penalty,
        "variance_increase_penalty": result.variance_increase_penalty,
        "feature_summaries": result.feature_summaries,
        "feature_map": result.feature_map,
        "feature_vector": result.feature_vector,
        "market_snapshot": _serialize_market_snapshot(result.market_snapshot),
        "strategy_details": result.strategy_result.to_log_payload(),
    }


def _serialize_sell_candidate(result) -> dict[str, Any] | None:
    if result is None:
        return None

    details = result.sell_decision.details
    return {
        "symbol": result.symbol,
        "name": result.name,
        "display_name": result.display_name,
        "holding_qty": result.holding_qty,
        "average_cost": result.average_cost,
        "triggered_rule_name": result.sell_decision.triggered_rule_name,
        "should_attempt_sell": result.sell_decision.should_attempt_sell,
        "reason": result.sell_decision.reason,
        "current_price": result.market_snapshot.current_price,
        "net_pnl_krw": details.get("net_pnl_krw"),
        "net_pnl_pct": details.get("net_pnl_pct"),
        "net_pnl_bps": details.get("net_pnl_bps"),
        "sell_priority_score": details.get("sell_priority_score"),
        "sell_priority_summary": details.get("sell_priority_summary"),
        "buy_strategy_details": result.buy_strategy_result.to_log_payload(),
        "sell_strategy_details": result.sell_decision.to_log_payload(),
    }


def _serialize_positions(portfolio_snapshot, sell_analysis_results) -> list[dict[str, Any]]:
    if portfolio_snapshot is None:
        return []

    analysis_by_symbol = {
        analysis.symbol: analysis for analysis in sell_analysis_results or ()
    }

    positions: list[dict[str, Any]] = []
    total_evaluation_amount = max(portfolio_snapshot.total_evaluation_amount, 1)
    for position in portfolio_snapshot.held_positions:
        analysis = analysis_by_symbol.get(position.symbol)
        market_value = int(position.market_value)
        current_price = int(position.current_price) or None
        gross_pnl_krw = int(position.gross_pnl)
        gross_pnl_pct = float(position.gross_pnl_pct)
        net_pnl_krw = None
        net_pnl_pct = None
        if analysis is not None:
            current_price = analysis.market_snapshot.current_price
            details = analysis.sell_decision.details
            gross_pnl_krw = details.get("gross_pnl_krw")
            gross_pnl_pct = details.get("gross_pnl_pct")
            net_pnl_krw = details.get("net_pnl_krw")
            net_pnl_pct = details.get("net_pnl_pct")

        positions.append(
            {
                "symbol": position.symbol,
                "name": position.name,
                "symbol_name": position.name,
                "holding_qty": position.holding_qty,
                "quantity": position.holding_qty,
                "average_cost": position.average_cost,
                "average_price": position.average_cost,
                "current_price": current_price,
                "market_value": market_value,
                "gross_pnl_krw": gross_pnl_krw,
                "gross_pnl": gross_pnl_krw,
                "gross_pnl_pct": gross_pnl_pct,
                "net_pnl_krw": net_pnl_krw,
                "net_pnl": net_pnl_krw,
                "net_pnl_pct": net_pnl_pct,
                "weight_pct": round((market_value / total_evaluation_amount) * 100, 2),
            }
        )
    return positions


def _serialize_top_scan_candidates(results, *, limit: int = 5) -> list[dict[str, Any]]:
    if not results:
        return []

    ordered = sorted(results, key=lambda result: result.sort_key)[:limit]
    return [
        {
            "symbol": result.symbol,
            "name": result.name,
            "candidate": result.candidate,
            "passed_count": result.passed_count,
            "signal_quality_count": result.signal_quality_count,
            "enabled_count": result.enabled_count,
            "passed_pattern": result.passed_pattern,
            "score": result.score,
            "net_profit_buffer_bps": result.net_profit_buffer_bps,
            "expected_fee_krw": result.expected_fee_krw,
            "expected_tax_krw": result.expected_tax_krw,
            "expected_slippage_krw": result.expected_slippage_krw,
            "expected_total_cost_krw": result.expected_total_cost_krw,
            "expected_cost_bps": result.expected_cost_bps,
            "cost_quality_score": result.cost_quality_score,
            "expected_cost_penalty": result.expected_cost_penalty,
            "net_edge_bps": result.net_edge_bps,
            "cost_block_reason": result.cost_block_reason,
            "final_reason": result.final_reason,
            "score_summary": result.score_summary,
            "math_score_summary": result.math_score_summary,
            "mean_reversion_zscore": result.mean_reversion_zscore,
            "reversion_quality_score": result.reversion_quality_score,
            "overextension_penalty": result.overextension_penalty,
            "ou_half_life_estimate": result.ou_half_life_estimate,
            "mean_reversion_summary": result.mean_reversion_summary,
            "portfolio_avg_correlation": result.portfolio_avg_correlation,
            "portfolio_max_correlation": result.portfolio_max_correlation,
            "variance_increase_estimate": result.variance_increase_estimate,
            "portfolio_risk_summary": result.portfolio_risk_summary,
            "portfolio_correlation_penalty": result.portfolio_correlation_penalty,
            "variance_increase_penalty": result.variance_increase_penalty,
            "feature_summaries": result.feature_summaries,
            "feature_map": result.feature_map,
            "feature_vector": result.feature_vector,
            "score_highlights": list(result.score_highlights),
            "score_penalties": list(result.score_penalties),
            "score_components": result.score_components,
            "market_snapshot": _serialize_market_snapshot(result.market_snapshot),
        }
        for result in ordered
    ]


def _resolve_primary_action_context(
    *,
    runtime_state: dict[str, Any],
    selected_buy_candidate,
    selected_sell_candidate,
) -> dict[str, Any]:
    selected_symbol = str(runtime_state.get("last_selected_symbol") or "").strip()
    order_side = str(runtime_state.get("last_order_side") or "").strip().upper() or None
    final_action = str(runtime_state.get("last_action") or "").strip().upper()

    primary_name: str | None = None
    primary_symbol: str | None = None
    primary_side = order_side
    if selected_buy_candidate is not None and selected_buy_candidate.symbol == selected_symbol:
        primary_symbol = selected_symbol or None
        primary_name = selected_buy_candidate.display_name
        primary_side = primary_side or "BUY"
    elif selected_sell_candidate is not None and selected_sell_candidate.symbol == selected_symbol:
        primary_symbol = selected_symbol or None
        primary_name = selected_sell_candidate.display_name
        primary_side = primary_side or "SELL"
    elif final_action.startswith("BUY"):
        primary_symbol = selected_symbol or None
        primary_name = selected_symbol or None
        primary_side = primary_side or "BUY"
    elif final_action.startswith("SELL"):
        primary_symbol = selected_symbol or None
        primary_name = selected_symbol or None
        primary_side = primary_side or "SELL"

    return {
        "selected_primary_action_symbol": primary_symbol,
        "selected_primary_action_name": primary_name,
        "selected_primary_action_side": primary_side,
    }


def _extract_pre_gating_fields(
    selection_details: dict[str, Any] | None,
) -> dict[str, Any]:
    details = selection_details if isinstance(selection_details, dict) else {}
    pre_gating = details.get("pre_gating")
    pre_gating = pre_gating if isinstance(pre_gating, dict) else {}

    rejected = pre_gating.get("rejected")
    rejected = rejected if isinstance(rejected, list) else []
    rejected_symbols = pre_gating.get("rejected_symbols")
    rejected_symbols = rejected_symbols if isinstance(rejected_symbols, list) else []
    reasons_by_symbol = pre_gating.get("reasons_by_symbol")
    reasons_by_symbol = reasons_by_symbol if isinstance(reasons_by_symbol, dict) else {}
    reentry_state_by_symbol = pre_gating.get("reentry_state_by_symbol")
    reentry_state_by_symbol = (
        reentry_state_by_symbol if isinstance(reentry_state_by_symbol, dict) else {}
    )
    last_exit_reason_by_symbol = pre_gating.get("last_exit_reason_by_symbol")
    last_exit_reason_by_symbol = (
        last_exit_reason_by_symbol if isinstance(last_exit_reason_by_symbol, dict) else {}
    )
    residual_position_present_by_symbol = pre_gating.get(
        "residual_position_present_by_symbol"
    )
    residual_position_present_by_symbol = (
        residual_position_present_by_symbol
        if isinstance(residual_position_present_by_symbol, dict)
        else {}
    )
    reentry_block_reason_counts = pre_gating.get("reentry_block_reason_counts")
    reentry_block_reason_counts = (
        reentry_block_reason_counts if isinstance(reentry_block_reason_counts, dict) else {}
    )

    summary = None
    if pre_gating:
        reason_counts = pre_gating.get("reason_counts")
        reason_counts = reason_counts if isinstance(reason_counts, dict) else {}
        parts = [
            f"requested={int(pre_gating.get('requested_count', 0) or 0)}",
            f"allowed={int(pre_gating.get('allowed_count', 0) or 0)}",
            f"rejected={int(pre_gating.get('early_reject_count', len(rejected)) or 0)}",
        ]
        if reason_counts:
            parts.append(
                "reasons="
                + ", ".join(
                    f"{key}={value}" for key, value in sorted(reason_counts.items())
                )
            )
        if not bool(pre_gating.get("scan_allowed", True)):
            parts.append(
                f"blocked={str(pre_gating.get('scan_block_reason') or '-').strip() or '-'}"
            )
        summary = " | ".join(parts)

    return {
        "pre_gating": pre_gating or {},
        "pre_gating_summary": summary,
        "pre_gating_rejected_count": int(
            pre_gating.get("early_reject_count", len(rejected)) or 0
        )
        if pre_gating
        else 0,
        "pre_gating_rejected_symbols": list(rejected_symbols),
        "pre_gating_reasons_by_symbol": dict(reasons_by_symbol),
        "pre_gating_stage": pre_gating.get("stage") if pre_gating else None,
        "reentry_state_by_symbol": dict(reentry_state_by_symbol),
        "last_exit_reason_by_symbol": dict(last_exit_reason_by_symbol),
        "residual_position_present_by_symbol": dict(residual_position_present_by_symbol),
        "reentry_block_reason_counts": dict(reentry_block_reason_counts),
        "reentry_allowed_count": int(pre_gating.get("reentry_allowed_count", 0) or 0)
        if pre_gating
        else 0,
        "reentry_blocked_count": int(pre_gating.get("reentry_blocked_count", 0) or 0)
        if pre_gating
        else 0,
    }


def _extract_staged_scan_fields(
    selection_details: dict[str, Any] | None,
) -> dict[str, Any]:
    details = selection_details if isinstance(selection_details, dict) else {}
    staged = details.get("staged_scan")
    staged = staged if isinstance(staged, dict) else {}
    return {
        "buy_scan_profile": staged.get("profile"),
        "buy_scan_universe_core_count": int(staged.get("core_count", 0) or 0),
        "buy_scan_universe_rotating_count": int(staged.get("rotating_count", 0) or 0),
        "buy_scan_universe_exploration_count": int(staged.get("exploration_count", 0) or 0),
        "buy_scan_pre_gating_count": int(staged.get("pre_gating_count", 0) or 0),
        "buy_scan_shallow_ranked_count": int(staged.get("shallow_ranked_count", 0) or 0),
        "buy_scan_deep_eval_limit": int(staged.get("deep_eval_limit", 0) or 0),
        "buy_scan_exploration_quota_used": int(
            staged.get("exploration_quota_used", 0) or 0
        ),
        "buy_scan_layered_symbols_preview": dict(
            staged.get("layered_symbols_preview") or {}
        ),
        "buy_scan_shallow_shortlist_preview": list(
            staged.get("shallow_shortlist_preview") or []
        ),
    }
