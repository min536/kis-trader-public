from __future__ import annotations

import math
from dataclasses import replace

from app.core.formatters import format_bps, format_krw, format_qty
from app.execution.sell_position_sizing import calculate_sell_position_sizing
from app.scanner.service import calculate_selection_score


def position_sizing_requires_rebalance(position_sizing) -> bool:
    details = position_sizing.details
    return (
        position_sizing.recommended_qty <= 0
        and str(details.get("block_reason_code", "")).strip() == "cash_insufficient"
    )


def build_concentration_metrics(
    *,
    cash_orderable_krw: int,
    position_values: dict[str, int],
) -> dict[str, object]:
    cleaned_values = {
        str(symbol): max(int(value or 0), 0)
        for symbol, value in position_values.items()
        if int(value or 0) > 0
    }
    holdings_market_value_krw = sum(cleaned_values.values())
    operating_equity_krw = max(int(cash_orderable_krw or 0), 0) + holdings_market_value_krw
    if operating_equity_krw <= 0:
        return {
            "operating_equity_krw": 0,
            "holdings_market_value_krw": 0,
            "cash_orderable_krw": max(int(cash_orderable_krw or 0), 0),
            "top1_weight_pct": 0.0,
            "top3_weight_pct": 0.0,
            "positions": [],
        }

    positions = sorted(
        cleaned_values.items(),
        key=lambda item: (-item[1], item[0]),
    )
    weighted_positions = [
        {
            "symbol": symbol,
            "market_value_krw": market_value,
            "weight_pct": round((market_value / operating_equity_krw) * 100, 2),
        }
        for symbol, market_value in positions
    ]
    top1_weight_pct = float(weighted_positions[0]["weight_pct"]) if weighted_positions else 0.0
    top3_weight_pct = round(
        sum(float(item["weight_pct"]) for item in weighted_positions[:3]),
        2,
    )
    return {
        "operating_equity_krw": operating_equity_krw,
        "holdings_market_value_krw": holdings_market_value_krw,
        "cash_orderable_krw": max(int(cash_orderable_krw or 0), 0),
        "top1_weight_pct": top1_weight_pct,
        "top3_weight_pct": top3_weight_pct,
        "positions": weighted_positions,
    }


def serialize_rebalance_holding_option(option: dict[str, object]) -> dict[str, object]:
    analysis = option["analysis"]
    return {
        "symbol": analysis.symbol,
        "display_name": analysis.display_name,
        "holding_score": round(float(option.get("current_holding_score", 0.0) or 0.0), 2),
        "holding_quality_score": round(float(option.get("holding_quality_score", 0.0) or 0.0), 2),
        "replacement_pressure_score": round(float(option.get("replacement_pressure_score", 0.0) or 0.0), 2),
        "trend_break_penalty": round(float(option.get("trend_break_penalty", 0.0) or 0.0), 2),
        "momentum_decay_penalty": round(float(option.get("momentum_decay_penalty", 0.0) or 0.0), 2),
        "net_pnl_bps": round(float(option.get("net_pnl_bps", 0.0) or 0.0), 1),
        "score_delta": round(float(option.get("score_delta", 0.0) or 0.0), 2),
        "cost_adjusted_delta": round(float(option.get("cost_adjusted_delta", 0.0) or 0.0), 2),
        "replaceability_score": round(float(option.get("replaceability_score", 0.0) or 0.0), 2),
        "quality_optimizer_score": round(float(option.get("quality_optimizer_score", 0.0) or 0.0), 2),
    }


def serialize_replacement_candidate_option(candidate) -> dict[str, object]:
    return {
        "symbol": candidate.symbol,
        "display_name": candidate.display_name,
        "score": round(float(candidate.score or 0.0), 2),
        "score_summary": candidate.score_summary,
        "score_highlights": list(candidate.score_highlights),
        "score_penalties": list(candidate.score_penalties),
        "expected_total_cost_krw": int(candidate.expected_total_cost_krw or 0),
        "expected_cost_bps": round(float(candidate.expected_cost_bps or 0.0), 1),
        "net_edge_bps": round(float(candidate.net_edge_bps or 0.0), 1),
    }


def build_position_sizing_block_context(position_sizing) -> dict[str, str]:
    code = str(position_sizing.details.get("block_reason_code", "")).strip()
    label = str(position_sizing.details.get("block_reason_label", "")).strip()
    reason = position_sizing.reason
    if code == "cash_insufficient":
        return {
            "code": code,
            "label": label or "현금 부족",
            "action": "blocked_buy_cash_insufficient",
            "cycle_action": "BUY_BLOCKED_CASH_INSUFFICIENT",
            "reason": reason or "BUY 불가: 현금 부족",
            "rebalance_skip_reason": "현금 부족이 아니라 1회 예산/노출 문제가 아닌 경우가 아니므로 rebalance를 검토하지 않습니다.",
        }
    if code == "exposure_limited":
        return {
            "code": code,
            "label": label or "account exposure 한도 부족",
            "action": "blocked_buy_exposure_limited",
            "cycle_action": "BUY_BLOCKED_EXPOSURE_LIMITED",
            "reason": reason or "BUY 불가: account exposure 한도 부족",
            "rebalance_skip_reason": "현금 부족이 아니라 account exposure 한도 제약이라 rebalance를 검토하지 않습니다.",
        }
    if code == "trade_budget_limited":
        return {
            "code": code,
            "label": label or "1회 매수 예산 부족",
            "action": "blocked_buy_trade_budget_limited",
            "cycle_action": "BUY_BLOCKED_TRADE_BUDGET_LIMITED",
            "reason": reason or "BUY 불가: 1회 매수 예산 부족",
            "rebalance_skip_reason": "현금 부족이 아니라 1회 매수 예산 제약이라 rebalance를 검토하지 않습니다.",
        }
    return {
        "code": code or "qty_or_price_limited",
        "label": label or "수량/가격 제약",
        "action": "blocked_buy_qty_or_price_limited",
        "cycle_action": "BUY_BLOCKED_QTY_OR_PRICE_LIMITED",
        "reason": reason or "BUY 불가: 수량/호가/가격 제약",
        "rebalance_skip_reason": "현금 부족이 아니라 수량/호가/가격 제약이라 rebalance를 검토하지 않습니다.",
    }


def build_buy_analysis_block_context(result) -> dict[str, str]:
    cost_block_reason = str(getattr(result, "cost_block_reason", "") or "").strip()
    if cost_block_reason == "expected_cost_too_high":
        return {
            "action": "blocked_buy_expected_cost_too_high",
            "cycle_action": "BUY_BLOCKED_EXPECTED_COST_TOO_HIGH",
            "reason": result.final_reason,
        }
    if cost_block_reason == "net_edge_too_low":
        return {
            "action": "blocked_buy_net_edge_too_low",
            "cycle_action": "BUY_BLOCKED_NET_EDGE_TOO_LOW",
            "reason": result.final_reason,
        }
    return {
        "action": "blocked_strategy_rejected",
        "cycle_action": "HOLD_NO_SIGNAL",
        "reason": result.final_reason,
    }


def calculate_rebalance_sell_sizing(
    *,
    weakest_analysis,
    position_sizing,
    execution_snapshot,
    settings,
):
    full_sizing = calculate_sell_position_sizing(
        trigger="rebalance",
        holding_qty=weakest_analysis.holding_qty,
        current_price=weakest_analysis.market_snapshot.current_price,
        settings=settings,
    )
    needed_cash_krw = max(
        0,
        int(position_sizing.details.get("estimated_entry_cost_krw", 0) or 0)
        - int(execution_snapshot.orderable_cash or 0),
    )
    if needed_cash_krw <= 0:
        return full_sizing

    full_qty = max(int(full_sizing.recommended_sell_qty or 0), 0)
    if full_qty <= 0:
        return full_sizing

    unit_net_proceeds = int(full_sizing.details.get("estimated_net_proceeds_krw", 0) or 0) // max(full_qty, 1)
    if unit_net_proceeds <= 0:
        return full_sizing

    target_qty = min(
        weakest_analysis.holding_qty,
        max(1, math.ceil(needed_cash_krw / unit_net_proceeds)),
    )
    if target_qty >= weakest_analysis.holding_qty:
        full_details = dict(full_sizing.details)
        full_details["needed_cash_krw"] = needed_cash_krw
        full_details["target_rebalance_sell_qty"] = weakest_analysis.holding_qty
        return replace(full_sizing, details=full_details)

    adjusted_sizing = calculate_sell_position_sizing(
        trigger="rebalance",
        holding_qty=target_qty,
        current_price=weakest_analysis.market_snapshot.current_price,
        settings=settings,
    )
    adjusted_details = dict(adjusted_sizing.details)
    adjusted_details["needed_cash_krw"] = needed_cash_krw
    adjusted_details["target_rebalance_sell_qty"] = target_qty
    return replace(adjusted_sizing, details=adjusted_details)


def estimate_rebalance_concentration_preview(
    *,
    portfolio_snapshot,
    sell_symbol: str,
    replacement_symbol: str,
    replacement_market_value_krw: int,
    settings,
) -> dict[str, object]:
    before_values = {
        position.symbol: int(position.market_value or 0)
        for position in portfolio_snapshot.held_positions
    }
    before = build_concentration_metrics(
        cash_orderable_krw=int(portfolio_snapshot.cash_orderable or 0),
        position_values=before_values,
    )

    after_values = dict(before_values)
    after_values.pop(sell_symbol, None)
    if replacement_market_value_krw > 0:
        after_values[replacement_symbol] = (
            int(after_values.get(replacement_symbol, 0) or 0)
            + int(replacement_market_value_krw)
        )
    after = build_concentration_metrics(
        cash_orderable_krw=int(portfolio_snapshot.cash_orderable or 0),
        position_values=after_values,
    )
    top1_before = float(before.get("top1_weight_pct", 0.0) or 0.0)
    top1_after = float(after.get("top1_weight_pct", 0.0) or 0.0)
    top3_before = float(before.get("top3_weight_pct", 0.0) or 0.0)
    top3_after = float(after.get("top3_weight_pct", 0.0) or 0.0)
    concentration_penalty = max(top1_after - float(settings.rebalance_max_concentration_pct), 0.0)
    if concentration_penalty > 0:
        comment = (
            "교체 후 상위1 포지션 비중이 "
            f"{settings.rebalance_max_concentration_pct:.2f}% 한도를 넘을 수 있습니다."
        )
    elif top3_after < top3_before:
        comment = "교체 후 상위3 집중도가 완화될 것으로 예상됩니다."
    elif top3_after > top3_before:
        comment = "교체 후 상위3 집중도가 다소 높아질 수 있습니다."
    else:
        comment = "교체 후 집중도 구조 변화는 제한적일 것으로 예상됩니다."
    return {
        "before": before,
        "after": after,
        "top1_change_pct": round(top1_after - top1_before, 2),
        "top3_change_pct": round(top3_after - top3_before, 2),
        "concentration_penalty": round(concentration_penalty, 2),
        "concentration_ok": concentration_penalty <= 0,
        "comment": comment,
    }


def build_rebalance_pair_evaluation(
    *,
    analysis,
    replacement_candidate,
    portfolio_snapshot,
    settings,
) -> dict[str, object]:
    current_holding_score, _ = calculate_selection_score(
        snapshot=analysis.market_snapshot,
        strategy_result=analysis.buy_strategy_result,
        rebound_from_low_pct=settings.buy_rule_rebound_from_low_pct,
        controlled_down_day_min=settings.buy_rule_controlled_down_day_min,
        controlled_down_day_max=settings.buy_rule_controlled_down_day_max,
        gap_down_open_min_pct=settings.buy_rule_gap_down_open_min_pct,
        gap_down_open_max_pct=settings.buy_rule_gap_down_open_max_pct,
        range_recovery_min_ratio=settings.buy_rule_range_recovery_min_ratio,
    )
    sell_details = analysis.sell_decision.details
    holding_quality_score = float(sell_details.get("holding_quality_score", 0.0) or 0.0)
    replacement_pressure_score = float(
        sell_details.get("replacement_pressure_score", 0.0) or 0.0
    )
    trend_break_penalty = float(sell_details.get("trend_break_penalty", 0.0) or 0.0)
    momentum_decay_penalty = float(
        sell_details.get("momentum_decay_penalty", 0.0) or 0.0
    )
    net_pnl_bps = float(sell_details.get("net_pnl_bps", 0.0) or 0.0)
    score_delta = float(replacement_candidate.score) - float(current_holding_score)
    replaceability_score = (
        replacement_pressure_score
        + trend_break_penalty
        + momentum_decay_penalty
        + (score_delta * 0.35)
        + max(-net_pnl_bps, 0.0) / 100.0
        - (holding_quality_score * 0.55)
        - max(net_pnl_bps, 0.0) / 250.0
    )
    cost_adjusted_delta = (
        score_delta
        + float(replacement_candidate.cost_quality_score or 0.0)
        - float(replacement_candidate.expected_cost_penalty or 0.0)
    )
    full_sizing = calculate_sell_position_sizing(
        trigger="rebalance",
        holding_qty=analysis.holding_qty,
        current_price=analysis.market_snapshot.current_price,
        settings=settings,
    )
    expected_cash_unlock_krw = int(
        full_sizing.details.get("estimated_net_proceeds_krw", 0) or 0
    )
    next_cycle_buyable_qty = (
        expected_cash_unlock_krw // max(int(replacement_candidate.market_snapshot.current_price or 0), 1)
        if int(replacement_candidate.market_snapshot.current_price or 0) > 0
        else 0
    )
    concentration_preview = estimate_rebalance_concentration_preview(
        portfolio_snapshot=portfolio_snapshot,
        sell_symbol=analysis.symbol,
        replacement_symbol=replacement_candidate.symbol,
        replacement_market_value_krw=int(analysis.market_snapshot.current_price * analysis.holding_qty),
        settings=settings,
    )
    quality_optimizer_score = (
        replaceability_score
        + cost_adjusted_delta
        + max(float(replacement_candidate.net_edge_bps or 0.0), 0.0) / 100.0
        - float(concentration_preview.get("concentration_penalty", 0.0) or 0.0) / 5.0
    )
    reasons: list[str] = []
    if score_delta < settings.rebalance_min_score_delta:
        reasons.append(
            f"score delta 부족 ({score_delta:.2f} < {settings.rebalance_min_score_delta:.2f})"
        )
    if net_pnl_bps < settings.rebalance_min_profit_buffer_bps:
        reasons.append(
            f"net profit buffer 부족 ({format_bps(net_pnl_bps)} < {format_bps(settings.rebalance_min_profit_buffer_bps)})"
        )
    if float(replacement_candidate.net_edge_bps or 0.0) < settings.rebalance_min_net_edge_bps:
        reasons.append(
            f"비용 반영 순우위 부족 ({format_bps(float(replacement_candidate.net_edge_bps or 0.0))} < {format_bps(settings.rebalance_min_net_edge_bps)})"
        )
    if not concentration_preview.get("concentration_ok", True):
        reasons.append(
            f"집중도 한도 초과 가능 ({float(concentration_preview['after']['top1_weight_pct']):.2f}% > {settings.rebalance_max_concentration_pct:.2f}%)"
        )
    selection_reason = (
        "보유 지속 가치 약화와 교체 후보의 비용 반영 순우위가 함께 확인됐습니다."
        if not reasons
        else " / ".join(reasons)
    )
    return {
        "analysis": analysis,
        "replacement_candidate": replacement_candidate,
        "current_holding_score": float(current_holding_score),
        "holding_quality_score": holding_quality_score,
        "replacement_pressure_score": replacement_pressure_score,
        "trend_break_penalty": trend_break_penalty,
        "momentum_decay_penalty": momentum_decay_penalty,
        "net_pnl_bps": net_pnl_bps,
        "score_delta": score_delta,
        "cost_adjusted_delta": float(cost_adjusted_delta),
        "replaceability_score": float(replaceability_score),
        "quality_optimizer_score": float(quality_optimizer_score),
        "expected_cash_unlock_krw": expected_cash_unlock_krw,
        "next_cycle_buyable_qty": int(next_cycle_buyable_qty),
        "concentration_preview": concentration_preview,
        "selection_reason": selection_reason,
        "blocked_reasons": reasons,
    }


def build_rebalance_candidate(
    *,
    sell_analysis_results: tuple,
    selected_candidate,
    scan_results,
    portfolio_snapshot,
    settings,
):
    replacement_candidates = [selected_candidate]
    for result in scan_results:
        if result.symbol == selected_candidate.symbol:
            continue
        if not result.candidate:
            continue
        replacement_candidates.append(result)
        if len(replacement_candidates) >= 3:
            break

    # Coverage check first — if sell_watch did not evaluate every held
    # position this cycle (rate limit, quote failure, partial budget, etc.),
    # the weakest-holding selection would be biased toward the small evaluated
    # subset, so defer to a later cycle when coverage is complete. We do this
    # before building holdings so the (relatively expensive) per-position
    # rebalance pair evaluation never runs on incomplete data.
    held_positions_other_than_candidate = [
        position
        for position in portfolio_snapshot.held_positions
        if position.symbol != selected_candidate.symbol
    ]
    evaluated_symbols = {analysis.symbol for analysis in sell_analysis_results}
    unevaluated_holding_symbols = [
        position.symbol
        for position in held_positions_other_than_candidate
        if position.symbol not in evaluated_symbols
    ]
    if held_positions_other_than_candidate and unevaluated_holding_symbols:
        reason = (
            f"이번 cycle에 sell_watch가 평가하지 못한 보유 종목이 "
            f"{len(unevaluated_holding_symbols)}개 남아 있어 rebalance 평가를 보류합니다 "
            f"(다음 cycle sell_watch 평가 후 재검토)."
        )
        return {
            "candidate": None,
            "reason": reason,
            "reason_code": "sell_watch_incomplete",
            "unevaluated_holding_count": len(unevaluated_holding_symbols),
            "unevaluated_holding_symbols_sample": list(
                unevaluated_holding_symbols[:10]
            ),
            "current_weakest_candidates": [],
            "replacement_candidates": [
                serialize_replacement_candidate_option(item)
                for item in replacement_candidates[:3]
            ],
        }

    holdings = [
        build_rebalance_pair_evaluation(
            analysis=analysis,
            replacement_candidate=selected_candidate,
            portfolio_snapshot=portfolio_snapshot,
            settings=settings,
        )
        for analysis in sell_analysis_results
        if analysis.symbol != selected_candidate.symbol
    ]

    if not holdings:
        return {
            "candidate": None,
            "reason": "weakest holding이 없어 rebalance를 검토하지 않습니다.",
            "reason_code": "no_candidate",
            "current_weakest_candidates": [],
            "replacement_candidates": [
                serialize_replacement_candidate_option(item)
                for item in replacement_candidates[:3]
            ],
        }

    weakest = sorted(
        holdings,
        key=lambda item: (
            -float(item["quality_optimizer_score"]),
            -float(item["replaceability_score"]),
            -float(item["cost_adjusted_delta"]),
            float(item["holding_quality_score"]),
            float(item["net_pnl_bps"]),
            item["analysis"].symbol,
        ),
    )[0]
    blocked_reasons = list(weakest.get("blocked_reasons") or [])
    if blocked_reasons:
        primary_reason = str(blocked_reasons[0])
        if "score delta 부족" in primary_reason:
            blocked_reason_code = "blocked_score_delta"
        elif "비용 반영 순우위 부족" in primary_reason:
            blocked_reason_code = "blocked_net_edge"
        elif "집중도 한도 초과" in primary_reason:
            blocked_reason_code = "blocked_concentration"
        elif "net profit buffer 부족" in primary_reason:
            blocked_reason_code = "blocked_profit_buffer"
        else:
            blocked_reason_code = "blocked_other"
        reason = primary_reason
        return {
            "candidate": None,
            "reason": f"{reason} 으로 rebalance를 검토하지 않습니다.",
            "reason_code": blocked_reason_code,
            "current_weakest_candidates": [
                serialize_rebalance_holding_option(item)
                for item in sorted(
                    holdings,
                    key=lambda item: (-float(item["quality_optimizer_score"]), item["analysis"].symbol),
                )[:3]
            ],
            "replacement_candidates": [
                serialize_replacement_candidate_option(item)
                for item in replacement_candidates[:3]
            ],
        }

    return {
        "candidate": {
            "analysis": weakest["analysis"],
            "current_holding_score": float(weakest["current_holding_score"]),
            "holding_quality_score": float(weakest["holding_quality_score"]),
            "replacement_pressure_score": float(weakest["replacement_pressure_score"]),
            "trend_break_penalty": float(weakest["trend_break_penalty"]),
            "momentum_decay_penalty": float(weakest["momentum_decay_penalty"]),
            "replaceability_score": float(weakest["replaceability_score"]),
            "score_delta": float(weakest["score_delta"]),
            "cost_adjusted_delta": float(weakest["cost_adjusted_delta"]),
            "net_pnl_bps": float(weakest["net_pnl_bps"]),
            "quality_optimizer_score": float(weakest["quality_optimizer_score"]),
            "expected_cash_unlock_krw": int(weakest["expected_cash_unlock_krw"]),
            "next_cycle_buyable_qty": int(weakest["next_cycle_buyable_qty"]),
            "concentration_preview": weakest["concentration_preview"],
            "selection_reason": str(weakest["selection_reason"]),
        },
        "reason": "rebalance 후보를 선정했습니다.",
        "current_weakest_candidates": [
            serialize_rebalance_holding_option(item)
            for item in sorted(
                holdings,
                key=lambda item: (-float(item["quality_optimizer_score"]), item["analysis"].symbol),
            )[:3]
        ],
        "replacement_candidates": [
            serialize_replacement_candidate_option(item)
            for item in replacement_candidates[:3]
        ],
    }


def build_quality_rebalance_preview(
    *,
    sell_analysis_results: tuple,
    scan_results,
    portfolio_snapshot,
    settings,
):
    replacement_candidates = [
        result for result in scan_results if getattr(result, "candidate", False)
    ][:3]
    if not replacement_candidates or not sell_analysis_results:
        return {
            "preview_type": "quality_improvement",
            "status": "skipped",
            "reason": "quality rebalance를 비교할 후보 또는 보유 종목이 부족합니다.",
            "current_weakest_candidates": [],
            "replacement_candidates": [],
            "pair_candidates": [],
        }

    pair_candidates: list[dict[str, object]] = []
    for replacement_candidate in replacement_candidates:
        for analysis in sell_analysis_results:
            if analysis.symbol == replacement_candidate.symbol:
                continue
            pair_candidates.append(
                build_rebalance_pair_evaluation(
                    analysis=analysis,
                    replacement_candidate=replacement_candidate,
                    portfolio_snapshot=portfolio_snapshot,
                    settings=settings,
                )
            )

    if not pair_candidates:
        return {
            "preview_type": "quality_improvement",
            "status": "skipped",
            "reason": "quality rebalance를 비교할 교체 쌍이 없습니다.",
            "current_weakest_candidates": [],
            "replacement_candidates": [
                serialize_replacement_candidate_option(item)
                for item in replacement_candidates
            ],
            "pair_candidates": [],
        }

    ranked_pairs = sorted(
        pair_candidates,
        key=lambda item: (
            len(item.get("blocked_reasons") or []),
            -float(item["quality_optimizer_score"]),
            item["analysis"].symbol,
            item["replacement_candidate"].symbol,
        ),
    )
    best_pair = ranked_pairs[0]
    status = "preview" if not best_pair.get("blocked_reasons") else "skipped"
    reason = (
        str(best_pair["selection_reason"])
        if status == "preview"
        else f"{str((best_pair.get('blocked_reasons') or ['quality rebalance 조건 미충족'])[0])} 으로 quality rebalance preview만 유지합니다."
    )
    return {
        "preview_type": "quality_improvement",
        "status": status,
        "reason": reason,
        "current_weakest_candidates": [
            serialize_rebalance_holding_option(item)
            for item in sorted(
                pair_candidates,
                key=lambda item: (-float(item["quality_optimizer_score"]), item["analysis"].symbol),
            )[:3]
        ],
        "replacement_candidates": [
            serialize_replacement_candidate_option(item)
            for item in replacement_candidates
        ],
        "pair_candidates": [
            {
                "sell_symbol": item["analysis"].symbol,
                "sell_display_name": item["analysis"].display_name,
                "buy_symbol": item["replacement_candidate"].symbol,
                "buy_display_name": item["replacement_candidate"].display_name,
                "score_delta": round(float(item["score_delta"]), 2),
                "cost_adjusted_delta": round(float(item["cost_adjusted_delta"]), 2),
                "quality_optimizer_score": round(float(item["quality_optimizer_score"]), 2),
                "expected_cash_unlock_krw": int(item["expected_cash_unlock_krw"]),
                "next_cycle_buyable_qty": int(item["next_cycle_buyable_qty"]),
                "selection_reason": str(item["selection_reason"]),
                "blocked_reasons": list(item.get("blocked_reasons") or []),
                "concentration_before": item["concentration_preview"]["before"],
                "concentration_after": item["concentration_preview"]["after"],
                "concentration_comment": item["concentration_preview"]["comment"],
            }
            for item in ranked_pairs[:3]
        ],
        "selected_pair": {
            "sell_symbol": best_pair["analysis"].symbol,
            "sell_display_name": best_pair["analysis"].display_name,
            "buy_symbol": best_pair["replacement_candidate"].symbol,
            "buy_display_name": best_pair["replacement_candidate"].display_name,
            "score_delta": round(float(best_pair["score_delta"]), 2),
            "cost_adjusted_delta": round(float(best_pair["cost_adjusted_delta"]), 2),
            "quality_optimizer_score": round(float(best_pair["quality_optimizer_score"]), 2),
            "expected_cash_unlock_krw": int(best_pair["expected_cash_unlock_krw"]),
            "next_cycle_buyable_qty": int(best_pair["next_cycle_buyable_qty"]),
            "selection_reason": str(best_pair["selection_reason"]),
            "blocked_reasons": list(best_pair.get("blocked_reasons") or []),
            "concentration_before": best_pair["concentration_preview"]["before"],
            "concentration_after": best_pair["concentration_preview"]["after"],
            "concentration_comment": best_pair["concentration_preview"]["comment"],
        },
    }


def print_rebalance_preview(
    *,
    sell_analysis,
    sell_sizing,
    buy_candidate,
    score_delta: float,
    current_holding_score: float,
    holding_quality_score: float,
    replaceability_score: float,
    cost_adjusted_delta: float,
    quality_optimizer_score: float,
    net_pnl_bps: float,
    expected_cash_unlock_krw: int,
    concentration_preview: dict[str, object] | None,
    selection_reason: str,
    consideration_reason: str,
) -> None:
    print("=== 리밸런싱 검토 ===")
    print(f"매도 후보: {sell_analysis.display_name}")
    print(f"리밸런싱 매도 수량: {format_qty(sell_sizing.recommended_sell_qty)}")
    if sell_sizing.details.get("needed_cash_krw") is not None:
        print(
            "추가 확보 필요 현금: "
            f"{format_krw(int(sell_sizing.details.get('needed_cash_krw', 0) or 0))}"
        )
    print(f"신규 매수 후보: {buy_candidate.display_name}")
    print(f"score 차이: {score_delta:.2f}")
    print(f"보유 종목 score: {current_holding_score:.2f}")
    print(f"보유 유지 품질 점수: {holding_quality_score:.2f}")
    print(f"교체 우선순위 점수: {replaceability_score:.2f}")
    print(f"비용 반영 교체 delta: {cost_adjusted_delta:.2f}")
    print(f"quality optimizer 점수: {quality_optimizer_score:.2f}")
    print(f"신규 후보 최종 score: {buy_candidate.score:.2f}")
    print(f"보유 종목 순손익률: {format_bps(net_pnl_bps)}")
    print(f"예상 확보 현금: {format_krw(expected_cash_unlock_krw)}")
    if concentration_preview:
        before = concentration_preview.get("before") or {}
        after = concentration_preview.get("after") or {}
        print(
            "집중도 변화: "
            f"top1 {float(before.get('top1_weight_pct', 0.0) or 0.0):.2f}% → {float(after.get('top1_weight_pct', 0.0) or 0.0):.2f}% | "
            f"top3 {float(before.get('top3_weight_pct', 0.0) or 0.0):.2f}% → {float(after.get('top3_weight_pct', 0.0) or 0.0):.2f}%"
        )
        print(f"집중도 해석: {str(concentration_preview.get('comment') or '-')}")
    print(f"선정 이유: {selection_reason}")
    print(f"검토 사유: {consideration_reason}")
    print()


def print_quality_rebalance_preview(*, preview: dict[str, object]) -> None:
    print("=== quality rebalance preview ===")
    print(f"상태: {preview.get('status') or '데이터 부족'}")
    print(f"사유: {preview.get('reason') or '데이터 부족'}")
    selected_pair = preview.get("selected_pair") or {}
    if selected_pair:
        print(
            f"교체 쌍: {selected_pair.get('sell_display_name') or '-'} → "
            f"{selected_pair.get('buy_display_name') or '-'}"
        )
        print(
            f"score delta {float(selected_pair.get('score_delta', 0.0) or 0.0):.2f} | "
            f"cost-adjusted delta {float(selected_pair.get('cost_adjusted_delta', 0.0) or 0.0):.2f} | "
            f"optimizer {float(selected_pair.get('quality_optimizer_score', 0.0) or 0.0):.2f}"
        )
        print(
            f"예상 확보 현금 {format_krw(int(selected_pair.get('expected_cash_unlock_krw', 0) or 0))} | "
            f"다음 사이클 예상 BUY 가능 수량 {format_qty(int(selected_pair.get('next_cycle_buyable_qty', 0) or 0))}"
        )
        if selected_pair.get("concentration_before") and selected_pair.get("concentration_after"):
            before = selected_pair["concentration_before"]
            after = selected_pair["concentration_after"]
            print(
                "집중도 변화: "
                f"top1 {float(before.get('top1_weight_pct', 0.0) or 0.0):.2f}% → {float(after.get('top1_weight_pct', 0.0) or 0.0):.2f}% | "
                f"top3 {float(before.get('top3_weight_pct', 0.0) or 0.0):.2f}% → {float(after.get('top3_weight_pct', 0.0) or 0.0):.2f}%"
            )
            print(f"집중도 해석: {selected_pair.get('concentration_comment') or '-'}")
    print()


def print_rebalance_skip(reason: str) -> None:
    print(f"리밸런싱 미검토: {reason}")
    print()
