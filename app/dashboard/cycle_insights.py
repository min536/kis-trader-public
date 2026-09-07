from typing import Any


def engine_short_label(value: str | None) -> str:
    normalized = str(value or "").strip().upper()
    if not normalized:
        return "데이터 부족"
    mapping = {
        "WAITING_CLOSED": "휴장 대기",
        "WAITING_PREMARKET_OPEN": "장전 대기",
        "WAITING_AFTER_MARKET": "장후 대기",
        "SELL_PRIORITY_WITH_BUY": "매도 우선, 매수 병행",
        "SELL_ONLY_DUE": "매도 감시만 수행",
        "BUY_ONLY_DUE": "매수 스캔만 수행",
        "RUN_ONCE_FULL_CYCLE": "1회 전체 점검",
        "BUY_PAUSE": "신규 매수 일시정지",
        "HARD_STOP_READY": "강한 손실 경계",
        "NORMAL": "정상",
        "CAUTION": "주의",
        "RISK_OFF": "위험회피",
        "OK": "정상",
        "OFF": "비활성",
        "DUE": "실행 대상",
        "RECENT": "최근 수행",
        "SKIPPED": "건너뜀",
        "DATA_INSUFFICIENT": "데이터 부족",
        "REGULAR": "정규장",
        "PREMARKET": "장전",
        "AFTER_MARKET": "장후",
        "CLOSED": "휴장",
    }
    return mapping.get(normalized, normalized.replace("_", " "))


def build_buy_judgement_snapshot(data: dict[str, Any]) -> dict[str, Any]:
    candidates = data.get("candidate_rows") or []
    cycles = data.get("cycles") or []
    latest_cycle = cycles[0] if cycles else {}
    latest_cycle_raw = latest_cycle.get("raw") or {}
    latest_selected = latest_cycle_raw.get("selected_buy_candidate")
    latest_selected = latest_selected if isinstance(latest_selected, dict) else {}
    selected_symbol = (
        latest_cycle.get("selected_buy_symbol")
        or ((latest_cycle.get("raw") or {}).get("last_selected_symbol"))
    )
    selected_row = next(
        (item for item in candidates if item.get("symbol") == selected_symbol),
        candidates[0] if candidates else None,
    )
    selection_details = latest_cycle.get("selection_details") or {}
    buy_strategy_details = latest_cycle.get("buy_strategy_details") or {}
    rebalance_preview = latest_cycle.get("rebalance_preview") or {}
    buy_position_sizing = latest_cycle.get("buy_position_sizing") or {}
    selected_source = selected_row or latest_selected

    return {
        "selected_candidate": selected_row,
        "selection_reason": str(selection_details.get("selection_reason") or "").strip(),
        "buy_final_reason": str(buy_strategy_details.get("final_reason") or "").strip(),
        "score_summary": str(
            (selected_row or {}).get("score_summary")
            or latest_selected.get("score_summary")
            or ""
        ).strip(),
        "math_score_summary": str(
            (selected_row or {}).get("math_score_summary")
            or latest_selected.get("math_score_summary")
            or ""
        ).strip(),
        "score_highlights": list(
            (selected_row or {}).get("score_highlights")
            or latest_selected.get("score_highlights")
            or []
        ),
        "score_penalties": list(
            (selected_row or {}).get("score_penalties")
            or latest_selected.get("score_penalties")
            or []
        ),
        "expected_total_cost_krw": (
            (selected_row or {}).get("expected_total_cost_krw")
            or latest_selected.get("expected_total_cost_krw")
        ),
        "expected_cost_bps": (
            (selected_row or {}).get("expected_cost_bps")
            or latest_selected.get("expected_cost_bps")
        ),
        "cost_quality_score": (
            (selected_row or {}).get("cost_quality_score")
            or latest_selected.get("cost_quality_score")
        ),
        "expected_cost_penalty": (
            (selected_row or {}).get("expected_cost_penalty")
            or latest_selected.get("expected_cost_penalty")
        ),
        "net_edge_bps": (
            (selected_row or {}).get("net_edge_bps")
            or latest_selected.get("net_edge_bps")
        ),
        "cost_block_reason": str(
            (selected_row or {}).get("cost_block_reason")
            or latest_selected.get("cost_block_reason")
            or ""
        ).strip(),
        "mean_reversion_zscore": (
            selected_source.get("mean_reversion_zscore")
            if isinstance(selected_source, dict)
            else None
        ),
        "reversion_quality_score": (
            selected_source.get("reversion_quality_score")
            if isinstance(selected_source, dict)
            else None
        ),
        "overextension_penalty": (
            selected_source.get("overextension_penalty")
            if isinstance(selected_source, dict)
            else None
        ),
        "mean_reversion_summary": str(
            (selected_row or {}).get("mean_reversion_summary")
            or latest_selected.get("mean_reversion_summary")
            or ""
        ).strip(),
        "portfolio_avg_correlation": (
            selected_source.get("portfolio_avg_correlation")
            if isinstance(selected_source, dict)
            else None
        ),
        "portfolio_max_correlation": (
            selected_source.get("portfolio_max_correlation")
            if isinstance(selected_source, dict)
            else None
        ),
        "variance_increase_estimate": (
            selected_source.get("variance_increase_estimate")
            if isinstance(selected_source, dict)
            else None
        ),
        "portfolio_risk_summary": str(
            (selected_row or {}).get("portfolio_risk_summary")
            or latest_selected.get("portfolio_risk_summary")
            or ""
        ).strip(),
        "effective_math_multiplier": buy_position_sizing.get("effective_math_multiplier"),
        "math_sizing_summary": str(
            buy_position_sizing.get("math_sizing_summary") or ""
        ).strip(),
        "math_sizing_reasons": list(
            buy_position_sizing.get("math_sizing_reasons") or []
        ),
        "cash_insufficient_reason": str(
            latest_cycle.get("cash_insufficient_reason")
            or buy_position_sizing.get("block_reason_label")
            or ""
        ).strip(),
        "rebalance_reason": str(
            rebalance_preview.get("selection_reason")
            or ((rebalance_preview.get("raw") or {}).get("selection_reason") if isinstance(rebalance_preview, dict) else "")
            or rebalance_preview.get("reason")
            or ""
        ).strip(),
        "rebalance_type": str(rebalance_preview.get("preview_type") or "").strip(),
        "rebalance_status": str(rebalance_preview.get("status") or "").strip(),
        "rebalance_selected_pair": (
            rebalance_preview.get("selected_pair")
            if isinstance(rebalance_preview.get("selected_pair"), dict)
            else {}
        ),
        "rebalance_current_weakest_candidates": list(
            rebalance_preview.get("current_weakest_candidates") or []
        )
        if isinstance(rebalance_preview, dict)
        else [],
        "rebalance_replacement_candidates": list(
            rebalance_preview.get("replacement_candidates") or []
        )
        if isinstance(rebalance_preview, dict)
        else [],
        "top_candidates": candidates[:5],
        "updated_at": latest_cycle.get("timestamp"),
    }


def build_cycle_readable_summary(cycle: dict[str, Any]) -> dict[str, str]:
    risk_guard = cycle.get("risk_guard_result") or {}
    buy_risk = risk_guard.get("buy") or {}
    sell_risk = risk_guard.get("sell") or {}
    risk_summary = "리스크 정보 없음"
    if buy_risk.get("reason"):
        risk_summary = str(buy_risk.get("reason"))
    elif sell_risk.get("reason"):
        risk_summary = str(sell_risk.get("reason"))

    selection_details = cycle.get("selection_details") or {}
    selection_reason = str(selection_details.get("selection_reason") or "").strip() or "선택 근거 없음"

    buy_strategy_details = cycle.get("buy_strategy_details") or {}
    sell_strategy_details = cycle.get("sell_strategy_details") or {}
    buy_reason = str(buy_strategy_details.get("final_reason") or "").strip() or "매수 전략 정보 없음"
    sell_reason = str(sell_strategy_details.get("reason") or "").strip() or "매도 전략 정보 없음"
    selected_buy_data = cycle.get("selected_buy_candidate_data") or {}
    runner_up_data = cycle.get("runner_up_candidate_data") or {}
    rebalance_preview = cycle.get("rebalance_preview") or {}
    rebalance_reason = "리밸런싱 정보 없음"
    if isinstance(rebalance_preview, dict) and rebalance_preview:
        rebalance_reason = str(
            rebalance_preview.get("selection_reason")
            or rebalance_preview.get("next_reason")
            or rebalance_preview.get("reason")
            or "리밸런싱 preview 있음"
        )
    cash_block_reason = str(cycle.get("cash_insufficient_reason") or "").strip() or "현금 부족 차단 정보 없음"
    cost_block_reason = str(cycle.get("cost_block_reason") or "").strip() or "비용 기반 차단 정보 없음"
    scheduler_decision = engine_short_label(cycle.get("scheduler_decision"))
    buy_scan_requested = cycle.get("buy_scan_requested_count")
    buy_scan_evaluated = cycle.get("buy_scan_evaluated_count")
    cycle_elapsed_ms = cycle.get("cycle_elapsed_ms")
    api_request_count = cycle.get("api_request_count")
    cadence_summary = (
        f"{int(buy_scan_evaluated)} / {int(buy_scan_requested)} 평가"
        if buy_scan_requested is not None and buy_scan_evaluated is not None
        else "BUY scan 데이터 부족"
    )
    execution_summary = (
        f"requests {int(api_request_count)} · elapsed {int(round(float(cycle_elapsed_ms)))}ms"
        if api_request_count is not None and cycle_elapsed_ms is not None
        else "실행 계측 데이터 부족"
    )
    selected_name = (
        selected_buy_data.get("display_name")
        or selected_buy_data.get("name")
        or selected_buy_data.get("symbol")
        or cycle.get("selected_buy_candidate")
        or "-"
    )
    runner_up_name = (
        runner_up_data.get("display_name")
        or runner_up_data.get("name")
        or runner_up_data.get("symbol")
        or cycle.get("runner_up_candidate")
        or "-"
    )
    selected_vs_runner_up = (
        f"{selected_name} vs {runner_up_name}"
        if selected_name != "-" or runner_up_name != "-"
        else "후보 비교 정보 없음"
    )
    selected_score_summary = str(
        cycle.get("score_summary")
        or selected_buy_data.get("score_summary")
        or "score 요약 없음"
    )
    math_score_summary = str(
        cycle.get("math_score_summary")
        or selected_buy_data.get("math_score_summary")
        or "수학 요약 없음"
    )
    score_highlights = list(
        cycle.get("score_highlights")
        or selected_buy_data.get("score_highlights")
        or []
    )
    score_penalties = list(
        cycle.get("score_penalties")
        or selected_buy_data.get("score_penalties")
        or []
    )
    runner_up_summary = (
        f"score {round(float(runner_up_data.get('score', 0.0) or 0.0), 2)} · "
        f"통과 {int(runner_up_data.get('passed_count', 0) or 0)}개"
        if runner_up_data
        else "runner-up 정보 없음"
    )
    buy_position_sizing = cycle.get("buy_position_sizing") or {}
    mean_reversion_summary = str(
        selected_buy_data.get("mean_reversion_summary")
        or "평균회귀 요약 없음"
    )
    portfolio_risk_summary = str(
        selected_buy_data.get("portfolio_risk_summary")
        or "포트폴리오 위험 요약 없음"
    )
    effective_math_multiplier = buy_position_sizing.get("effective_math_multiplier")
    effective_math_multiplier_text = (
        "데이터 부족"
        if effective_math_multiplier is None
        else f"{float(effective_math_multiplier or 0.0):.2f}x"
    )
    math_sizing_summary = str(
        buy_position_sizing.get("math_sizing_summary")
        or "수량 보정 요약 없음"
    )

    return {
        "risk_summary": risk_summary,
        "selection_reason": selection_reason,
        "buy_reason": buy_reason,
        "sell_reason": sell_reason,
        "rebalance_reason": rebalance_reason,
        "rebalance_type": str(cycle.get("rebalance_type") or rebalance_preview.get("preview_type") or ""),
        "rebalance_status": str(cycle.get("rebalance_status") or rebalance_preview.get("status") or ""),
        "rebalance_pair": (
            cycle.get("rebalance_selected_pair")
            if isinstance(cycle.get("rebalance_selected_pair"), dict)
            else {}
        ),
        "cash_block_reason": cash_block_reason,
        "cost_block_reason": cost_block_reason,
        "scheduler_summary": scheduler_decision,
        "cadence_summary": cadence_summary,
        "execution_summary": execution_summary,
        "selected_vs_runner_up": selected_vs_runner_up,
        "runner_up_summary": runner_up_summary,
        "selected_score_summary": selected_score_summary,
        "math_score_summary": math_score_summary,
        "mean_reversion_summary": mean_reversion_summary,
        "portfolio_risk_summary": portfolio_risk_summary,
        "effective_math_multiplier_text": effective_math_multiplier_text,
        "math_sizing_summary": math_sizing_summary,
        "selected_score_highlights_text": ", ".join(score_highlights) if score_highlights else "가산 요인 정보 없음",
        "selected_score_penalties_text": ", ".join(score_penalties) if score_penalties else "감점 요인 없음",
    }
