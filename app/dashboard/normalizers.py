from datetime import datetime
from typing import Any

from app.core.market_session import get_korean_market_session
from app.scanner.symbol_names import get_symbol_name


def _successful_sell_orders_since(
    records: list[dict[str, Any]],
    *,
    since_timestamp: str | None,
) -> list[dict[str, Any]]:
    if not since_timestamp:
        return [
            record
            for record in records
            if str(record.get("action", "")).strip() == "sell_order_succeeded"
        ]
    return [
        record
        for record in records
        if str(record.get("timestamp", "")).strip() >= since_timestamp
        and str(record.get("action", "")).strip() == "sell_order_succeeded"
    ]

def _parse_iso_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None

def _summary_has_cycle_support(
    summary: dict[str, Any],
    *,
    cycle_records: list[dict[str, Any]],
) -> bool:
    summary_dt = _parse_iso_datetime(summary.get("generated_at"))
    if summary_dt is None:
        return False

    nearest_delta_seconds: float | None = None
    for record in cycle_records:
        cycle_dt = _parse_iso_datetime(record.get("timestamp"))
        if cycle_dt is None:
            continue
        delta_seconds = abs((cycle_dt - summary_dt).total_seconds())
        if nearest_delta_seconds is None or delta_seconds < nearest_delta_seconds:
            nearest_delta_seconds = delta_seconds
        if delta_seconds > 300:
            continue
        holdings_summary = record.get("holdings_summary")
        if not isinstance(holdings_summary, dict):
            continue
        position_count = int(holdings_summary.get("position_count", 0) or 0)
        if position_count > 0:
            return True
    return False if nearest_delta_seconds is not None else False

def _is_closed_session_summary_without_support(
    summary: dict[str, Any],
    *,
    cycle_records: list[dict[str, Any]],
) -> bool:
    summary_dt = _parse_iso_datetime(summary.get("generated_at"))
    if summary_dt is None:
        return False

    session = get_korean_market_session(now=summary_dt)
    if getattr(session, "order_allowed", False):
        return False

    account = summary.get("account_summary")
    account = account if isinstance(account, dict) else {}
    positions_count = int(account.get("positions_count", 0) or 0)
    holdings_market_value = int(account.get("holdings_market_value_krw", 0) or 0)
    if positions_count <= 0 and holdings_market_value <= 0:
        return False

    return not _summary_has_cycle_support(summary, cycle_records=cycle_records)

def _summary_equity(summary: dict[str, Any]) -> int | None:
    account = summary.get("account_summary")
    account = account if isinstance(account, dict) else {}
    equity = account.get("current_equity_krw")
    if equity is None:
        equity = account.get("operating_equity_krw")
    if equity is None:
        equity = (summary.get("equity") or {}).get("total_equity_krw")
    if equity is None:
        return None
    return int(equity or 0)

def _select_trusted_performance_summary(
    summaries: list[dict[str, Any]],
    *,
    order_records: list[dict[str, Any]],
    cycle_records: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if not summaries:
        return None, {"fallback_applied": False}

    filtered_summaries = [
        summary
        for summary in summaries
        if not _is_closed_session_summary_without_support(
            summary,
            cycle_records=cycle_records,
        )
    ]
    selected_pool = filtered_summaries or summaries
    selected = selected_pool[-1]
    metadata = {
        "fallback_applied": False,
        "fallback_reason": None,
        "selected_generated_at": selected.get("generated_at"),
        "latest_generated_at": summaries[-1].get("generated_at"),
    }
    if (
        filtered_summaries
        and len(filtered_summaries) != len(summaries)
        and selected is not summaries[-1]
    ):
        metadata = {
            "fallback_applied": True,
            "fallback_reason": "휴장/비거래 시간대에 holdings 근거 없이 생성된 성과 요약을 제외하고 마지막 정상 스냅샷을 사용했습니다.",
            "selected_generated_at": selected.get("generated_at"),
            "latest_generated_at": summaries[-1].get("generated_at"),
        }
    if len(selected_pool) < 2:
        return selected, metadata

    latest = selected_pool[-1]
    latest_account = latest.get("account_summary")
    latest_account = latest_account if isinstance(latest_account, dict) else {}
    latest_positions = int(latest_account.get("positions_count", 0) or 0)
    latest_holdings = int(latest_account.get("holdings_market_value_krw", 0) or 0)
    latest_realized = int(latest_account.get("realized_net_pnl_krw", 0) or 0)
    for candidate in reversed(selected_pool[:-1]):
        candidate_account = candidate.get("account_summary")
        candidate_account = candidate_account if isinstance(candidate_account, dict) else {}
        candidate_positions = int(candidate_account.get("positions_count", 0) or 0)
        candidate_holdings = int(candidate_account.get("holdings_market_value_krw", 0) or 0)
        candidate_realized = int(candidate_account.get("realized_net_pnl_krw", 0) or 0)
        sells_since_candidate = _successful_sell_orders_since(
            order_records,
            since_timestamp=str(candidate.get("generated_at") or ""),
        )

        positions_collapsed = (
            candidate_positions >= 8
            and latest_positions > 0
            and latest_positions <= max(2, candidate_positions // 2)
        )
        holdings_collapsed = (
            candidate_holdings > 0
            and latest_holdings > 0
            and latest_holdings <= int(candidate_holdings * 0.35)
        )
        realized_unchanged = latest_realized == candidate_realized

        if positions_collapsed and holdings_collapsed and realized_unchanged and not sells_since_candidate:
            selected = candidate
            metadata = {
                "fallback_applied": True,
                "fallback_reason": "최근 성과 요약의 포지션/보유금액이 급감했지만 대응되는 매도 체결 로그가 없어 마지막 정상 스냅샷으로 대체했습니다.",
                "selected_generated_at": candidate.get("generated_at"),
                "latest_generated_at": summaries[-1].get("generated_at"),
            }
            break

    return selected, metadata

def _resolve_symbol_name(symbol: str | None, *candidates: Any) -> str | None:
    for candidate in candidates:
        if isinstance(candidate, str):
            text = candidate.strip()
            if text and text.lower() != "null":
                return text
    if symbol:
        return get_symbol_name(symbol)
    return None

def _normalize_positions(performance_summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not performance_summary:
        return []
    positions = performance_summary.get("positions")
    if not isinstance(positions, list):
        return []

    normalized: list[dict[str, Any]] = []
    for position in positions:
        if not isinstance(position, dict):
            continue
        symbol = str(position.get("symbol", "")).strip()
        if not symbol:
            continue
        normalized.append(
            {
                "symbol": symbol,
                "symbol_name": _resolve_symbol_name(
                    symbol,
                    position.get("symbol_name"),
                    position.get("name"),
                ),
                "holding_qty": int(
                    position.get("holding_qty", position.get("quantity", 0)) or 0
                ),
                "quantity": int(
                    position.get("quantity", position.get("holding_qty", 0)) or 0
                ),
                "average_cost_krw": int(
                    position.get("average_cost_krw", position.get("average_price", 0)) or 0
                ),
                "average_price": int(
                    position.get("average_price", position.get("average_cost_krw", 0)) or 0
                ),
                "current_price_krw": int(
                    position.get("current_price_krw", position.get("current_price", 0)) or 0
                ),
                "current_price": int(
                    position.get("current_price", position.get("current_price_krw", 0)) or 0
                ),
                "evaluation_amount_krw": int(
                    position.get("evaluation_amount_krw", position.get("market_value", 0)) or 0
                ),
                "market_value": int(
                    position.get("market_value", position.get("evaluation_amount_krw", 0)) or 0
                ),
                "gross_pnl_krw": int(
                    position.get("gross_pnl_krw", position.get("gross_pnl", 0)) or 0
                ),
                "gross_pnl": int(
                    position.get("gross_pnl", position.get("gross_pnl_krw", 0)) or 0
                ),
                "gross_pnl_pct": float(position.get("gross_pnl_pct", 0.0) or 0.0),
                "net_pnl_krw": int(
                    position.get("net_pnl_krw", position.get("net_pnl", 0)) or 0
                ),
                "net_pnl": int(
                    position.get("net_pnl", position.get("net_pnl_krw", 0)) or 0
                ),
                "net_pnl_pct": float(position.get("net_pnl_pct", 0.0) or 0.0),
                "weight_pct": float(position.get("weight_pct", 0.0) or 0.0),
            }
        )
    return sorted(
        normalized,
        key=lambda item: int(item["evaluation_amount_krw"]),
        reverse=True,
    )

def _normalize_positions_from_cycle(latest_cycle: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not latest_cycle:
        return []
    holdings_summary = latest_cycle.get("holdings_summary")
    if not isinstance(holdings_summary, dict):
        return []
    positions = holdings_summary.get("positions")
    if not isinstance(positions, list):
        return []
    normalized: list[dict[str, Any]] = []
    for position in positions:
        if not isinstance(position, dict):
            continue
        symbol = str(position.get("symbol", "")).strip()
        if not symbol:
            continue
        normalized.append(
            {
                "symbol": symbol,
                "symbol_name": _resolve_symbol_name(
                    symbol,
                    position.get("symbol_name"),
                    position.get("name"),
                ),
                "holding_qty": int(position.get("holding_qty", position.get("quantity", 0)) or 0),
                "quantity": int(position.get("quantity", position.get("holding_qty", 0)) or 0),
                "average_cost_krw": int(position.get("average_cost", position.get("average_price", 0)) or 0),
                "average_price": int(position.get("average_price", position.get("average_cost", 0)) or 0),
                "current_price_krw": int(position.get("current_price", 0) or 0),
                "current_price": int(position.get("current_price", 0) or 0),
                "evaluation_amount_krw": int(position.get("market_value", 0) or 0),
                "market_value": int(position.get("market_value", 0) or 0),
                "gross_pnl_krw": int(position.get("gross_pnl_krw", position.get("gross_pnl", 0)) or 0),
                "gross_pnl": int(position.get("gross_pnl", position.get("gross_pnl_krw", 0)) or 0),
                "gross_pnl_pct": float(position.get("gross_pnl_pct", 0.0) or 0.0),
                "net_pnl_krw": (
                    None
                    if position.get("net_pnl_krw", position.get("net_pnl")) is None
                    else int(position.get("net_pnl_krw", position.get("net_pnl", 0)) or 0)
                ),
                "net_pnl": (
                    None
                    if position.get("net_pnl", position.get("net_pnl_krw")) is None
                    else int(position.get("net_pnl", position.get("net_pnl_krw", 0)) or 0)
                ),
                "net_pnl_pct": (
                    None
                    if position.get("net_pnl_pct") is None
                    else float(position.get("net_pnl_pct", 0.0) or 0.0)
                ),
                "weight_pct": float(position.get("weight_pct", 0.0) or 0.0),
            }
        )
    return sorted(
        normalized,
        key=lambda item: int(item["evaluation_amount_krw"]),
        reverse=True,
    )

def _build_account_view(
    performance_summary: dict[str, Any] | None,
    *,
    positions: list[dict[str, Any]],
    latest_cycle: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = performance_summary or {}
    account = summary.get("account_summary")
    account = account if isinstance(account, dict) else {}
    latest_cycle_raw = latest_cycle.get("raw") if isinstance(latest_cycle, dict) else None
    latest_cycle_raw = latest_cycle_raw if isinstance(latest_cycle_raw, dict) else {}
    holdings_summary = latest_cycle_raw.get("holdings_summary")
    holdings_summary = holdings_summary if isinstance(holdings_summary, dict) else {}

    position_count = account.get("positions_count")
    if not isinstance(position_count, int) or position_count <= 0:
        position_count = len(positions) if positions else holdings_summary.get("position_count")
    holdings_market_value = account.get("holdings_market_value_krw")
    if not holdings_market_value and positions:
        holdings_market_value = sum(
            int(item.get("evaluation_amount_krw", 0) or 0) for item in positions
        )
    if not holdings_market_value:
        holdings_market_value = holdings_summary.get("holdings_market_value_krw")
    total_cost_basis = account.get("total_cost_basis_krw")
    if not total_cost_basis and positions:
        total_cost_basis = sum(
            int(item.get("average_cost_krw", 0) or 0) * int(item.get("holding_qty", 0) or 0)
            for item in positions
        )
    orderable_cash = account.get("orderable_cash_krw")
    if orderable_cash is None:
        orderable_cash = holdings_summary.get("cash_orderable_krw")
    cash_total = account.get("cash_total_krw")
    if cash_total is None:
        cash_total = holdings_summary.get("cash_total_krw")
    operating_equity = account.get("operating_equity_krw")
    if operating_equity is None and orderable_cash is not None and holdings_market_value is not None:
        operating_equity = int(orderable_cash) + int(holdings_market_value)
    total_unrealized_net_pnl = account.get("total_unrealized_net_pnl_krw")
    if total_unrealized_net_pnl is None and positions:
        net_values = [item.get("net_pnl_krw") for item in positions if item.get("net_pnl_krw") is not None]
        if net_values:
            total_unrealized_net_pnl = sum(int(value or 0) for value in net_values)
        else:
            total_unrealized_net_pnl = sum(
                int(item.get("gross_pnl_krw", 0) or 0) for item in positions
            )
    cash_weight_pct = account.get("cash_weight_pct")
    if cash_weight_pct is None and operating_equity and orderable_cash is not None and int(operating_equity) > 0:
        cash_weight_pct = round((int(orderable_cash) / int(operating_equity)) * 100, 2)
    holdings_weight_pct = None
    if operating_equity is not None and holdings_market_value is not None and int(operating_equity) > 0:
        holdings_weight_pct = round((int(holdings_market_value) / int(operating_equity)) * 100, 2)
    allocation_gap_pct = None
    allocation_consistency = "unknown"
    if cash_weight_pct is not None and holdings_weight_pct is not None:
        allocation_gap_pct = round(abs(100.0 - (float(cash_weight_pct) + float(holdings_weight_pct))), 2)
        allocation_consistency = "ok" if allocation_gap_pct <= 1.0 else "fallback"
    total_return_pct = account.get("total_return_pct")
    if total_return_pct is None and total_cost_basis and total_unrealized_net_pnl is not None and int(total_cost_basis) > 0:
        realized_net = int(account.get("realized_net_pnl_krw", 0) or 0)
        total_return_pct = round(((int(total_unrealized_net_pnl) + realized_net) / int(total_cost_basis)) * 100, 2)
    display_equity = account.get("current_equity_krw")
    equity_source: str | None
    if display_equity is not None:
        equity_source = "account_summary"
    elif operating_equity is not None:
        display_equity = operating_equity
        # operating_equity comes from orderable_cash + holdings_market_value,
        # which are sourced from the account summary first, cycle holdings second.
        equity_source = (
            "account_summary"
            if account.get("orderable_cash_krw") is not None
            or account.get("holdings_market_value_krw") is not None
            else "cycle_holdings"
        )
    else:
        equity_source = None
    equity_unavailable_reason: str | None = None
    if equity_source is None:
        equity_unavailable_reason = (
            "no equity source — performance summary absent and cycle "
            "holdings_summary empty (account snapshot did not populate equity)"
        )

    return {
        **account,
        "positions_count": position_count,
        "holdings_market_value_krw": holdings_market_value,
        "total_cost_basis_krw": total_cost_basis,
        "orderable_cash_krw": orderable_cash,
        "cash_total_krw": cash_total,
        "operating_equity_krw": operating_equity,
        "total_unrealized_net_pnl_krw": total_unrealized_net_pnl,
        "cash_weight_pct": cash_weight_pct,
        "holdings_weight_pct": holdings_weight_pct,
        "allocation_gap_pct": allocation_gap_pct,
        "allocation_consistency": allocation_consistency,
        "total_return_pct": total_return_pct,
        "display_equity_krw": display_equity,
        "data_quality": {
            "used_cycle_fallback": bool(latest_cycle and not summary.get("account_summary")),
            "positions_from_cycle": bool(latest_cycle and not summary.get("positions")),
            "is_data_insufficient": operating_equity is None,
            "allocation_consistency": allocation_consistency,
            "equity_source": equity_source,
            "equity_unavailable_reason": equity_unavailable_reason,
        },
    }

def _apply_account_weights(
    positions: list[dict[str, Any]],
    *,
    account_view: dict[str, Any],
) -> list[dict[str, Any]]:
    operating_equity = account_view.get("operating_equity_krw")
    if operating_equity is None or int(operating_equity or 0) <= 0:
        for position in positions:
            position["account_weight_pct"] = None
        return positions

    operating_equity_int = int(operating_equity)
    for position in positions:
        evaluation_amount = int(position.get("evaluation_amount_krw", 0) or 0)
        position["account_weight_pct"] = round(
            (evaluation_amount / operating_equity_int) * 100, 2
        )
    return positions

def _infer_order_side(record: dict[str, Any]) -> str:
    action = str(record.get("action", "")).strip().lower()
    order_type = str(record.get("order_type", "")).strip().lower()
    if action.startswith("sell_") or "sell" in order_type:
        return "SELL"
    return "BUY"

def _normalize_orders(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for record in sorted(
        records,
        key=lambda item: str(item.get("timestamp", "")),
        reverse=True,
    ):
        symbol = str(record.get("symbol", "")).strip()
        raw_response = record.get("raw_response")
        raw_response = raw_response if isinstance(raw_response, dict) else {}
        symbol_name = _resolve_symbol_name(
            symbol,
            record.get("symbol_name"),
            raw_response.get("symbol_name"),
            raw_response.get("name"),
        )
        normalized.append(
            {
                "timestamp": str(record.get("timestamp", "")),
                "cycle_id": record.get("cycle_id"),
                "side": _infer_order_side(record),
                "symbol": symbol,
                "symbol_name": symbol_name,
                "qty": int(record.get("qty", 0) or 0),
                "action": str(record.get("action", "")),
                "result": str(record.get("result", "")),
                "reason": str(record.get("reason", "")),
                "environment": str(record.get("environment", "")),
            }
        )
    return normalized

def _normalize_cycles(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for record in sorted(
        records,
        key=lambda item: str(item.get("timestamp", "")),
        reverse=True,
    ):
        market_session = record.get("market_session")
        market_session = market_session if isinstance(market_session, dict) else {}
        selected_buy = record.get("selected_buy_candidate")
        selected_buy = selected_buy if isinstance(selected_buy, dict) else {}
        selected_sell = record.get("selected_sell_candidate")
        selected_sell = selected_sell if isinstance(selected_sell, dict) else {}
        scanner_candidates_top = record.get("scanner_candidates_top")
        scanner_candidates_top = (
            scanner_candidates_top if isinstance(scanner_candidates_top, list) else []
        )
        selected_buy_symbol = str(selected_buy.get("symbol", "") or "")
        runner_up_candidate: dict[str, Any] = {}
        for candidate in scanner_candidates_top:
            if not isinstance(candidate, dict):
                continue
            candidate_symbol = str(candidate.get("symbol", "") or "")
            if selected_buy_symbol and candidate_symbol == selected_buy_symbol:
                continue
            runner_up_candidate = candidate
            break
        rebalance_preview = record.get("rebalance_preview")
        rebalance_preview = rebalance_preview if isinstance(rebalance_preview, dict) else {}
        rebalance_selected_pair = rebalance_preview.get("selected_pair")
        rebalance_selected_pair = (
            rebalance_selected_pair if isinstance(rebalance_selected_pair, dict) else {}
        )
        buy_position_sizing = record.get("buy_position_sizing")
        buy_position_sizing = buy_position_sizing if isinstance(buy_position_sizing, dict) else {}
        selected_primary_action_name = str(
            record.get("selected_primary_action_name")
            or record.get("selected_primary_action_symbol")
            or record.get("last_selected_symbol")
            or ""
        )
        normalized.append(
            {
                "timestamp": str(record.get("timestamp", "")),
                "cycle_id": str(record.get("cycle_id", "")),
                "market_session": str(market_session.get("session", "")),
                "market_reason": str(market_session.get("reason", "")),
                "final_action": str(record.get("final_action", "")),
                "final_reason": str(record.get("final_reason", "")),
                "scheduler_decision": str(
                    ((record.get("scheduler_state") or {}).get("decision") or "")
                ),
                "cycle_elapsed_ms": record.get("cycle_elapsed_ms"),
                "api_request_count": record.get("api_request_count"),
                "buy_scan_requested_count": record.get("buy_scan_requested_count"),
                "buy_scan_evaluated_count": record.get("buy_scan_evaluated_count"),
                "buy_scan_skipped_reason": str(record.get("buy_scan_skipped_reason", "")),
                "sell_evaluated_count": record.get("sell_evaluated_count"),
                "selected_primary_action_name": selected_primary_action_name,
                "selected_primary_action_symbol": record.get("selected_primary_action_symbol"),
                "selected_primary_action_side": record.get("selected_primary_action_side"),
                "selected_buy_candidate": selected_buy.get("display_name")
                or selected_buy.get("name")
                or selected_buy.get("symbol"),
                "selected_buy_symbol": selected_buy.get("symbol"),
                "selected_sell_candidate": selected_sell.get("display_name")
                or selected_sell.get("name")
                or selected_sell.get("symbol"),
                "selected_sell_symbol": selected_sell.get("symbol"),
                "selection_details": record.get("selection_details") or {},
                "buy_strategy_details": record.get("buy_strategy_details") or {},
                "sell_strategy_details": record.get("sell_strategy_details") or {},
                "risk_guard_result": record.get("risk_guard_result") or {},
                "scanner_candidates_top": scanner_candidates_top,
                "top_candidate_count": len(scanner_candidates_top),
                "selected_buy_candidate_data": selected_buy,
                "runner_up_candidate_data": runner_up_candidate,
                "runner_up_candidate": runner_up_candidate.get("display_name")
                or runner_up_candidate.get("name")
                or runner_up_candidate.get("symbol"),
                "runner_up_symbol": runner_up_candidate.get("symbol"),
                "buy_position_sizing": buy_position_sizing,
                "buy_scan_budget_reserved": bool(record.get("buy_scan_budget_reserved")),
                "buy_scan_reserve_used": bool(record.get("buy_scan_reserve_used")),
                "buy_scan_partial_budget": bool(record.get("buy_scan_partial_budget")),
                "sell_watch_capped_for_buy_scan": bool(record.get("sell_watch_capped_for_buy_scan")),
                "rebalance_preview": rebalance_preview,
                "rebalance_type": str(rebalance_preview.get("preview_type", "")),
                "rebalance_status": str(rebalance_preview.get("status", "")),
                "rebalance_selected_pair": rebalance_selected_pair,
                "rebalance_selected_pair_reason": str(
                    rebalance_selected_pair.get("selection_reason")
                    or rebalance_preview.get("selection_reason")
                    or rebalance_preview.get("reason")
                    or rebalance_preview.get("next_reason")
                    or ""
                ),
                "cash_insufficient_reason": str(
                    buy_position_sizing.get("block_reason_label")
                    or buy_position_sizing.get("cash_insufficient_reason")
                    or ""
                ),
                "cost_block_reason": str(
                    selected_buy.get("cost_block_reason")
                    or ""
                ),
                "math_score_summary": str(selected_buy.get("math_score_summary", "")),
                "mean_reversion_zscore": selected_buy.get("mean_reversion_zscore"),
                "reversion_quality_score": selected_buy.get("reversion_quality_score"),
                "overextension_penalty": selected_buy.get("overextension_penalty"),
                "mean_reversion_summary": str(
                    selected_buy.get("mean_reversion_summary", "")
                ),
                "portfolio_avg_correlation": selected_buy.get("portfolio_avg_correlation"),
                "portfolio_max_correlation": selected_buy.get("portfolio_max_correlation"),
                "variance_increase_estimate": selected_buy.get("variance_increase_estimate"),
                "portfolio_risk_summary": str(
                    selected_buy.get("portfolio_risk_summary", "")
                ),
                "effective_math_multiplier": buy_position_sizing.get("effective_math_multiplier"),
                "math_sizing_summary": str(buy_position_sizing.get("math_sizing_summary", "")),
                "math_sizing_reasons": list(buy_position_sizing.get("math_sizing_reasons") or []),
                "expected_cost_bps": selected_buy.get("expected_cost_bps"),
                "expected_total_cost_krw": selected_buy.get("expected_total_cost_krw"),
                "net_edge_bps": selected_buy.get("net_edge_bps"),
                "score_summary": str(selected_buy.get("score_summary", "")),
                "score_highlights": list(selected_buy.get("score_highlights") or []),
                "score_penalties": list(selected_buy.get("score_penalties") or []),
                "raw": record,
            }
        )
    return normalized

def _normalize_candidates(cycles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not cycles:
        return []
    latest_candidates = cycles[0].get("scanner_candidates_top") or []
    rows: list[dict[str, Any]] = []
    for candidate in latest_candidates:
        if not isinstance(candidate, dict):
            continue
        symbol = str(candidate.get("symbol", "")).strip()
        rows.append(
            {
                "symbol": symbol,
                "symbol_name": _resolve_symbol_name(
                    symbol,
                    candidate.get("name"),
                    candidate.get("display_name"),
                ),
                "passed_count": int(candidate.get("passed_count", 0) or 0),
                "score": float(candidate.get("score", 0.0) or 0.0),
                "candidate": bool(candidate.get("candidate", False)),
                "pass_pattern": str(candidate.get("pass_pattern", "")),
                "profit_buffer_bps": float(candidate.get("net_profit_buffer_bps", 0.0) or 0.0),
                "final_reason": str(candidate.get("final_reason", "")),
                "score_summary": str(candidate.get("score_summary", "")),
                "score_highlights": list(candidate.get("score_highlights") or []),
                "score_penalties": list(candidate.get("score_penalties") or []),
                "expected_total_cost_krw": int(candidate.get("expected_total_cost_krw", 0) or 0),
                "expected_cost_bps": float(candidate.get("expected_cost_bps", 0.0) or 0.0),
                "cost_quality_score": float(candidate.get("cost_quality_score", 0.0) or 0.0),
                "expected_cost_penalty": float(candidate.get("expected_cost_penalty", 0.0) or 0.0),
                "net_edge_bps": float(candidate.get("net_edge_bps", 0.0) or 0.0),
                "cost_block_reason": str(candidate.get("cost_block_reason", "")),
                "math_score_summary": str(candidate.get("math_score_summary", "")),
                "mean_reversion_zscore": candidate.get("mean_reversion_zscore"),
                "reversion_quality_score": candidate.get("reversion_quality_score"),
                "overextension_penalty": float(candidate.get("overextension_penalty", 0.0) or 0.0),
                "mean_reversion_summary": str(candidate.get("mean_reversion_summary", "")),
                "portfolio_avg_correlation": candidate.get("portfolio_avg_correlation"),
                "portfolio_max_correlation": candidate.get("portfolio_max_correlation"),
                "variance_increase_estimate": candidate.get("variance_increase_estimate"),
                "portfolio_risk_summary": str(candidate.get("portfolio_risk_summary", "")),
            }
        )
    return rows

def _latest_action_record(
    orders: list[dict[str, Any]],
    actions: set[str],
) -> dict[str, Any] | None:
    for order in orders:
        if str(order.get("action", "")).strip() in actions:
            return order
    return None

def _build_engine_state_view(
    *,
    runtime_state: dict[str, Any],
    cycles: list[dict[str, Any]],
    orders: list[dict[str, Any]],
    market_status: Any,
) -> dict[str, Any]:
    latest_cycle = cycles[0] if cycles else {}
    latest_cycle_raw = latest_cycle.get("raw") if isinstance(latest_cycle, dict) else {}
    latest_cycle_raw = latest_cycle_raw if isinstance(latest_cycle_raw, dict) else {}
    scheduler_state = latest_cycle_raw.get("scheduler_state")
    scheduler_state = scheduler_state if isinstance(scheduler_state, dict) else {}
    budget_status = runtime_state.get("last_budget_status")
    if not isinstance(budget_status, dict):
        budget_status = latest_cycle_raw.get("last_budget_status")
    budget_status = budget_status if isinstance(budget_status, dict) else {}

    reentry_record = _latest_action_record(
        orders,
        {
            "blocked_buy_reentry_cooldown",
            "blocked_buy_same_symbol_daily_limit",
            "blocked_rebuy_cooldown",
            "blocked_same_symbol_daily_limit",
        },
    )
    buy_skip_record = _latest_action_record(
        orders,
        {
            "skipped_buy_scan_cadence",
            "skipped_buy_scan_budget_limited",
            "waiting_premarket_open",
            "waiting_after_market",
            "waiting_closed",
            "blocked_daily_pnl_pause",
            "blocked_daily_pnl_hard_stop",
        },
    )

    reentry_block_count = 0
    same_symbol_limit_block_count = 0
    for order in orders:
        action = str(order.get("action", "")).strip()
        if action in {"blocked_buy_reentry_cooldown", "blocked_rebuy_cooldown"}:
            reentry_block_count += 1
        if action in {
            "blocked_buy_same_symbol_daily_limit",
            "blocked_same_symbol_daily_limit",
        }:
            same_symbol_limit_block_count += 1

    market_session = (
        runtime_state.get("last_market_session")
        or latest_cycle.get("market_session")
        or getattr(market_status, "session", None)
    )
    cycle_market_reason = latest_cycle.get("market_reason")
    budget_requests_used = budget_status.get("recent_request_count")
    budget_quotes_used = budget_status.get("quotes_used_this_tick")
    backoff_remaining = budget_status.get("backoff_remaining_seconds")
    latest_regime_state = latest_cycle_raw.get("regime_state")
    latest_regime_state = latest_regime_state if isinstance(latest_regime_state, dict) else {}

    return {
        "market_session": market_session,
        "market_reason": cycle_market_reason,
        "last_action": runtime_state.get("last_action"),
        "last_cycle_result": runtime_state.get("last_cycle_result"),
        "last_cycle_elapsed_ms": runtime_state.get("last_cycle_elapsed_ms"),
        "last_warning_count": runtime_state.get("last_warning_count"),
        "last_error_count": runtime_state.get("last_error_count"),
        "last_snapshot_write_ok": runtime_state.get("last_snapshot_write_ok"),
        "last_performance_write_ok": runtime_state.get("last_performance_write_ok"),
        "scheduler_decision": scheduler_state.get("decision"),
        "current_brake_state": runtime_state.get("current_brake_state")
        or latest_cycle_raw.get("current_brake_state"),
        "current_regime": runtime_state.get("current_regime")
        or latest_cycle_raw.get("current_regime")
        or latest_regime_state.get("current_regime"),
        "regime_reason": runtime_state.get("regime_reason")
        or latest_cycle_raw.get("regime_reason")
        or latest_regime_state.get("regime_reason"),
        "regime_multiplier": runtime_state.get("regime_multiplier")
        or latest_cycle_raw.get("regime_multiplier")
        or latest_regime_state.get("regime_multiplier"),
        "regime_current_drawdown_pct": latest_regime_state.get("current_drawdown_pct"),
        "last_sell_check_at": runtime_state.get("last_sell_check_at")
        or scheduler_state.get("last_sell_check_at"),
        "last_buy_scan_at": runtime_state.get("last_buy_scan_at")
        or scheduler_state.get("last_buy_scan_at"),
        "sell_check_due": scheduler_state.get("sell_check_due"),
        "buy_scan_due": scheduler_state.get("buy_scan_due"),
        "budget_status": budget_status,
        "budget_requests_used": budget_requests_used,
        "budget_quotes_used": budget_quotes_used,
        "budget_backoff_remaining": backoff_remaining,
        "buy_scan_skip_action": (buy_skip_record or {}).get("action"),
        "buy_scan_skip_reason": (buy_skip_record or {}).get("reason"),
        "buy_scan_skip_at": (buy_skip_record or {}).get("timestamp"),
        "reentry_block_count": reentry_block_count,
        "same_symbol_limit_block_count": same_symbol_limit_block_count,
        "recent_reentry_blocked_symbol": (reentry_record or {}).get("symbol"),
        "recent_reentry_blocked_symbol_name": (reentry_record or {}).get("symbol_name"),
        "recent_reentry_blocked_at": (reentry_record or {}).get("timestamp")
        or runtime_state.get("last_reentry_blocked_at"),
        "buy_entries_by_symbol_today": runtime_state.get("buy_entries_by_symbol_today") or {},
        "last_buy_entry_at_by_symbol": runtime_state.get("last_buy_entry_at_by_symbol") or {},
        "last_reentry_blocked_at_by_symbol": runtime_state.get("last_reentry_blocked_at_by_symbol")
        or {},
        "buy_scan_requested_count": latest_cycle_raw.get("buy_scan_requested_count"),
        "buy_scan_evaluated_count": latest_cycle_raw.get("buy_scan_evaluated_count"),
        "api_request_count": latest_cycle_raw.get("api_request_count"),
        "cycle_elapsed_ms": latest_cycle_raw.get("cycle_elapsed_ms"),
    }

def _build_trade_journal(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pair BUY order_succeeded with subsequent SELL order_succeeded by symbol (FIFO)."""
    buy_queue: dict[str, list[dict[str, Any]]] = {}
    trades: list[dict[str, Any]] = []

    for order in sorted(orders, key=lambda x: x.get("timestamp", "")):
        action = order.get("action", "")
        symbol = order.get("symbol", "")
        if not symbol:
            continue

        if action == "order_succeeded":
            buy_queue.setdefault(symbol, []).append(order)
        elif action == "sell_order_succeeded":
            if buy_queue.get(symbol):
                buy_order = buy_queue[symbol].pop(0)
                buy_ts = buy_order.get("timestamp", "")
                sell_ts = order.get("timestamp", "")
                # holding duration
                hold_mins: int | None = None
                try:
                    buy_dt = datetime.fromisoformat(buy_ts)
                    sell_dt = datetime.fromisoformat(sell_ts)
                    hold_mins = int((sell_dt - buy_dt).total_seconds() / 60)
                except Exception:
                    pass
                trades.append(
                    {
                        "symbol": symbol,
                        "symbol_name": order.get("symbol_name") or buy_order.get("symbol_name") or symbol,
                        "buy_ts": buy_ts,
                        "sell_ts": sell_ts,
                        "hold_mins": hold_mins,
                        "qty": order.get("qty") or buy_order.get("qty"),
                        "buy_reason": buy_order.get("reason", ""),
                        "sell_reason": order.get("reason", ""),
                        "status": "closed",
                    }
                )
            else:
                # sell without matching buy in this session
                trades.append(
                    {
                        "symbol": symbol,
                        "symbol_name": order.get("symbol_name") or symbol,
                        "buy_ts": "",
                        "sell_ts": order.get("timestamp", ""),
                        "hold_mins": None,
                        "qty": order.get("qty"),
                        "buy_reason": "",
                        "sell_reason": order.get("reason", ""),
                        "status": "sell_only",
                    }
                )

    # remaining open buys
    for symbol, open_buys in buy_queue.items():
        for buy_order in open_buys:
            trades.append(
                {
                    "symbol": symbol,
                    "symbol_name": buy_order.get("symbol_name") or symbol,
                    "buy_ts": buy_order.get("timestamp", ""),
                    "sell_ts": "",
                    "hold_mins": None,
                    "qty": buy_order.get("qty"),
                    "buy_reason": buy_order.get("reason", ""),
                    "sell_reason": "",
                    "status": "open",
                }
            )

    trades.sort(key=lambda x: x.get("buy_ts") or x.get("sell_ts") or "", reverse=True)
    return trades
