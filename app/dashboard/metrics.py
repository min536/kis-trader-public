from datetime import datetime
from typing import Any

from app.auth.settings import get_settings
from app.dashboard.cycle_insights import (
    build_buy_judgement_snapshot,
    build_cycle_readable_summary,
    engine_short_label,
)
from app.core.time_utils import KOREA_TZ, get_korean_now


def _to_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KOREA_TZ)
    return dt.astimezone(KOREA_TZ)


def _today_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    today = get_korean_now().date()
    items: list[dict[str, Any]] = []
    for record in records:
        dt = _to_dt(record.get("timestamp"))
        if dt and dt.date() == today:
            items.append(record)
    return items


def _top_blocked_reason(records: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for record in records:
        action = str(record.get("action", "")).strip()
        if "blocked" not in action.lower():
            continue
        counts[action] = counts.get(action, 0) + 1
    if not counts:
        return "차단 없음"
    return max(counts.items(), key=lambda item: item[1])[0]


def _position_extremes(positions: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not positions:
        return None, None
    top = max(positions, key=lambda item: float(item.get("net_pnl_krw", 0) or 0))
    bottom = min(positions, key=lambda item: float(item.get("net_pnl_krw", 0) or 0))
    return top, bottom


def build_engine_state_view_model(engine_state: dict[str, Any]) -> dict[str, Any]:
    budget = engine_state.get("budget_status")
    budget = budget if isinstance(budget, dict) else {}
    requests_used = budget.get("recent_request_count")
    quotes_used = budget.get("quotes_used_this_tick")
    backoff_remaining = budget.get("backoff_remaining_seconds")
    if isinstance(backoff_remaining, (int, float)) and backoff_remaining > 0:
        budget_health = "backoff 중"
    elif isinstance(quotes_used, (int, float)) and quotes_used >= 4:
        budget_health = "여유 적음"
    else:
        budget_health = "정상"

    market_session = str(engine_state.get("market_session") or "")
    scheduler_decision = str(engine_state.get("scheduler_decision") or "")
    brake_state = str(engine_state.get("current_brake_state") or "")
    regime = str(engine_state.get("current_regime") or "")
    buy_skip_action = str(engine_state.get("buy_scan_skip_action") or "")

    if market_session == "PREMARKET":
        operating_mode = "경량 대기"
    elif market_session in {"AFTER_MARKET", "CLOSED"}:
        operating_mode = "주문 없는 대기"
    elif regime == "RISK_OFF":
        operating_mode = "위험회피 운용"
    elif regime == "CAUTION":
        operating_mode = "주의 운용"
    elif brake_state in {"BUY_PAUSE", "HARD_STOP_READY"}:
        operating_mode = engine_short_label(brake_state)
    elif scheduler_decision == "SELL_ONLY_DUE":
        operating_mode = "SELL 우선 감시"
    else:
        operating_mode = "정상 운영"

    sell_due = engine_state.get("sell_check_due")
    buy_due = engine_state.get("buy_scan_due")
    sell_status = "due" if sell_due is True else "recent" if engine_state.get("last_sell_check_at") else "데이터 부족"
    if market_session in {"PREMARKET", "AFTER_MARKET", "CLOSED"}:
        buy_status = "skipped"
    elif buy_due is True:
        buy_status = "due"
    elif engine_state.get("last_buy_scan_at"):
        buy_status = "recent"
    else:
        buy_status = "데이터 부족"

    if market_session == "PREMARKET":
        buy_skip_label = "장전 대기"
    elif market_session == "AFTER_MARKET":
        buy_skip_label = "장후 대기"
    elif market_session == "CLOSED":
        buy_skip_label = "휴장 대기"
    elif brake_state == "BUY_PAUSE":
        buy_skip_label = "손실 기준으로 일시정지"
    elif brake_state == "HARD_STOP_READY":
        buy_skip_label = "강한 손실 경계"
    elif buy_skip_action:
        buy_skip_label = engine_short_label(buy_skip_action)
    elif buy_status == "recent":
        buy_skip_label = "cadence 대기"
    else:
        buy_skip_label = "데이터 부족"

    if market_session == "PREMARKET":
        interpretation = "현재는 장전 상태이므로 경량 대기 중입니다. 09:00 이후 자동으로 운영을 시작합니다."
    elif market_session in {"AFTER_MARKET", "CLOSED"}:
        interpretation = "현재는 주문 가능 세션이 아니므로 경량 대기 중입니다."
    elif regime == "RISK_OFF":
        interpretation = "현재는 위험회피 상태라 신규 BUY 크기를 크게 줄이고 SELL 감시는 유지합니다."
    elif regime == "CAUTION":
        interpretation = "현재는 주의 상태라 신규 BUY 크기와 재진입 강도를 보수적으로 낮춥니다."
    elif brake_state == "BUY_PAUSE":
        interpretation = "손실 브레이크가 활성화되어 신규 BUY는 멈추고 SELL 감시만 유지합니다."
    elif brake_state == "HARD_STOP_READY":
        interpretation = "손실 상태가 좋지 않아 신규 BUY는 강하게 제한되고 있습니다."
    elif scheduler_decision == "SELL_ONLY_DUE":
        interpretation = "현재는 SELL 감시를 우선 수행하고 있으며 BUY scan은 다음 cadence를 기다립니다."
    elif scheduler_decision == "SELL_PRIORITY_WITH_BUY":
        interpretation = "현재는 SELL 감시를 우선 수행하면서 BUY scan도 함께 평가하고 있습니다."
    else:
        interpretation = "엔진 상태 해석에 필요한 데이터가 부족합니다."

    return {
        **engine_state,
        "market_session_label": engine_short_label(market_session),
        "operating_mode_label": operating_mode,
        "scheduler_decision_label": engine_short_label(scheduler_decision),
        "brake_state_label": engine_short_label(brake_state),
        "regime_label": engine_short_label(regime),
        "regime_reason": str(engine_state.get("regime_reason") or ""),
        "regime_multiplier_text": (
            "데이터 부족"
            if engine_state.get("regime_multiplier") is None
            else f"{float(engine_state.get('regime_multiplier') or 0.0):.2f}x"
        ),
        "buy_scan_skip_label": buy_skip_label,
        "sell_check_status_label": engine_short_label(sell_status),
        "buy_scan_status_label": engine_short_label(buy_status),
        "budget_health_label": budget_health,
        "budget_requests_text": (
            "데이터 부족"
            if requests_used is None
            else f"{int(requests_used)} / {get_settings().api_soft_max_requests_per_second}"
        ),
        "budget_quotes_text": (
            "데이터 부족"
            if quotes_used is None
            else f"{int(quotes_used)} / {get_settings().api_soft_max_quotes_per_tick}"
        ),
        "budget_backoff_text": (
            "데이터 부족"
            if backoff_remaining is None
            else f"{int(backoff_remaining)}초"
        ),
        "cycle_elapsed_text": (
            "데이터 부족"
            if engine_state.get("last_cycle_elapsed_ms") is None
            else f"{int(round(float(engine_state.get('last_cycle_elapsed_ms') or 0)))}ms"
        ),
        "engine_interpretation": interpretation,
    }


def _unknown_name_count(data: dict[str, Any]) -> int:
    count = 0
    for position in data.get("positions") or []:
        if not position.get("symbol_name"):
            count += 1
    for order in data.get("orders") or []:
        if not order.get("symbol_name"):
            count += 1
    for candidate in data.get("candidate_rows") or []:
        if not candidate.get("symbol_name"):
            count += 1
    return count


def _recent_failed_orders(orders: list[dict[str, Any]], *, limit: int = 20) -> int:
    return len([item for item in orders[:limit] if item.get("result") == "failed"])


def _recent_guard_blocks(orders: list[dict[str, Any]], *, limit: int = 30) -> int:
    count = 0
    for item in orders[:limit]:
        action = str(item.get("action", "")).lower()
        if "blocked_" in action and ("risk" in action or "daily_" in action or "cooldown" in action):
            count += 1
    return count


def _consecutive_no_order_cycles(cycles: list[dict[str, Any]], *, limit: int = 5) -> int:
    streak = 0
    for cycle in cycles[:limit]:
        final_action = str(cycle.get("final_action", "")).upper()
        if "ORDER_SUBMITTED" in final_action or "ORDER_SUCCEEDED" in final_action:
            break
        streak += 1
    return streak


def build_anomaly_summary(data: dict[str, Any]) -> dict[str, Any]:
    orders = data.get("orders") or []
    cycles = data.get("cycles") or []
    runtime_state = data.get("runtime_state") or {}
    no_order_streak = _consecutive_no_order_cycles(cycles, limit=5)
    snapshot_status_reason = None
    if cycles:
        raw = cycles[0].get("raw") or {}
        snapshot_status_reason = raw.get("snapshot_status_reason")

    return {
        "no_order_streak": no_order_streak,
        "top_blocked_action": _top_blocked_reason(_today_records(orders)),
        "snapshot_issue": snapshot_status_reason,
        "unknown_name_count": _unknown_name_count(data),
        "recent_failed_orders": _recent_failed_orders(orders),
        "recent_guard_blocks": _recent_guard_blocks(orders),
        "last_action": runtime_state.get("last_action"),
    }


def build_operational_alerts(data: dict[str, Any]) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    summary = build_top_summary(data)
    anomaly = build_anomaly_summary(data)
    market_session = str(summary.get("market_session") or "")
    buy_limit = summary.get("buy_limit_usage") or {}
    sell_limit = summary.get("sell_limit_usage") or {}

    if market_session != "REGULAR":
        alerts.append(
            {
                "tone": "warning",
                "title": "지금은 주문 가능 시간이 아닙니다",
                "body": "시장 세션상 실제 주문은 차단되고 preview/판단만 진행될 수 있습니다.",
            }
        )
    if int(buy_limit.get("used", 0)) >= int(buy_limit.get("limit", 0) or 0) > 0:
        alerts.append(
            {
                "tone": "danger",
                "title": "오늘 BUY 한도를 모두 사용했습니다",
                "body": "추가 매수 주문은 일일 제출 한도 때문에 차단될 수 있습니다.",
            }
        )
    if int(sell_limit.get("used", 0)) >= int(sell_limit.get("limit", 0) or 0) > 0:
        alerts.append(
            {
                "tone": "danger",
                "title": "오늘 SELL 한도를 모두 사용했습니다",
                "body": "추가 매도 주문은 일일 제출 한도 때문에 차단될 수 있습니다.",
            }
        )
    if int(anomaly.get("no_order_streak", 0)) >= 5:
        alerts.append(
            {
                "tone": "warning",
                "title": "최근 5사이클 연속 무주문입니다",
                "body": "전략 미통과, 세션 차단, cooldown 또는 리스크 가드 차단을 함께 확인하세요.",
            }
        )
    if str(anomaly.get("top_blocked_action") or "").lower().endswith("cooldown"):
        alerts.append(
            {
                "tone": "info",
                "title": "동일 신호 반복 차단이 발생 중입니다",
                "body": "최근 차단 원인이 cooldown 계열입니다. 동일 종목 반복 진입 여부를 확인하세요.",
            }
        )
    if anomaly.get("snapshot_issue"):
        alerts.append(
            {
                "tone": "danger",
                "title": "최근 snapshot 저장 내용이 불완전합니다",
                "body": "잔고 조회 이전에 사이클이 종료되었을 가능성이 있습니다.",
            }
        )
    return alerts[:5]


def build_top_summary(data: dict[str, Any]) -> dict[str, Any]:
    runtime_state = data.get("runtime_state") or {}
    runtime_summary = data.get("runtime_state_summary") or {}
    performance_summary = data.get("performance_summary") or {}
    account = data.get("account_view") or {}
    latest_cycle = None
    cycles = data.get("cycles") or []
    if cycles:
        latest_cycle = cycles[0]
    positions = data.get("positions") or []
    orders = data.get("orders") or []
    today_orders = _today_records(orders)
    submitted_orders = [
        item
        for item in today_orders
        if item.get("action") in {"order_submitted", "sell_order_submitted"}
    ]
    latest_order = today_orders[0] if today_orders else None
    top_position, bottom_position = _position_extremes(positions)
    cost = performance_summary.get("cost_and_execution")
    cost = cost if isinstance(cost, dict) else {}
    drawdown = performance_summary.get("drawdown")
    drawdown = drawdown if isinstance(drawdown, dict) else {}
    settings = get_settings()
    buy_submitted = len(
        [item for item in today_orders if item.get("action") == "order_submitted"]
    )
    sell_submitted = len(
        [item for item in today_orders if item.get("action") == "sell_order_submitted"]
    )
    total_pnl_krw = int(account.get("realized_net_pnl_krw", 0) or 0) + int(
        account.get("total_unrealized_net_pnl_krw", 0) or 0
    )
    environment = str((latest_order or {}).get("environment", "") or "").upper()
    if not environment:
        environment = "MOCK"
    snapshot_errors = [
        data.get("runtime_state_error"),
        data.get("orders_error"),
        data.get("cycles_error"),
        data.get("performance_snapshots_error"),
        data.get("performance_summary_error"),
    ]
    snapshot_health = "정상" if not any(snapshot_errors) else "주의"
    performance_equity = performance_summary.get("equity")
    performance_equity = performance_equity if isinstance(performance_equity, dict) else {}
    account_sync_at = (
        performance_summary.get("generated_at")
        or performance_equity.get("timestamp")
        or (latest_cycle or {}).get("timestamp")
        or runtime_state.get("last_cycle_started_at")
    )

    return {
        "market_session": getattr(data.get("market_status"), "session", "데이터 없음"),
        "recent_cycle_at": (latest_cycle or {}).get("timestamp")
        or runtime_state.get("last_cycle_started_at"),
        "account_sync_at": account_sync_at,
        "last_action": runtime_state.get("last_action"),
        "total_equity_krw": account.get(
            "raw_balance_total_evaluation_amount_krw",
            account.get("operating_equity_krw", account.get("display_equity_krw")),
        ),
        "settlement_equity_krw": account.get("display_equity_krw", account.get("operating_equity_krw")),
        "holdings_market_value_krw": account.get("holdings_market_value_krw", 0),
        "total_pnl_krw": total_pnl_krw,
        "unrealized_net_pnl_krw": account.get("total_unrealized_net_pnl_krw", 0),
        "total_return_pct": account.get("total_return_pct"),
        "cash_total_krw": account.get("cash_total_krw"),
        "orderable_cash_krw": account.get("orderable_cash_krw"),
        "positions_count": account.get("positions_count"),
        "today_order_count": len(submitted_orders),
        "buy_completed_count": runtime_summary.get("buy_completed_count", 0),
        "sell_completed_count": runtime_summary.get("sell_completed_count", 0),
        "buy_blocked_count": runtime_summary.get("buy_blocked_count", 0),
        "sell_blocked_count": runtime_summary.get("sell_blocked_count", 0),
        "current_drawdown_pct": drawdown.get("current_drawdown_pct"),
        "max_drawdown_pct": drawdown.get("max_drawdown_pct"),
        "buy_limit_usage": {
            "used": buy_submitted,
            "limit": settings.buy_daily_max_order_submissions,
        },
        "sell_limit_usage": {
            "used": sell_submitted,
            "limit": settings.sell_daily_max_order_submissions,
        },
        "latest_order": latest_order,
        "top_blocked_action": _top_blocked_reason(today_orders),
        "top_position": top_position,
        "bottom_position": bottom_position,
        "win_rate_pct": cost.get("win_rate_pct"),
        "profit_factor": cost.get("profit_factor"),
        "environment_label": environment,
        "snapshot_health": snapshot_health,
        "snapshot_health_reason": next((item for item in snapshot_errors if item), None),
        "account_data_quality": account.get("data_quality") or {},
        "performance_selection_meta": data.get("performance_selection_meta") or {},
    }


def build_blocked_reason_rows(orders: list[dict[str, Any]], *, limit: int = 10) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for order in orders:
        action = str(order.get("action", "")).strip()
        if "blocked" not in action.lower():
            continue
        counts[action] = counts.get(action, 0) + 1
    rows = [{"label": key, "value": value} for key, value in counts.items()]
    return sorted(rows, key=lambda item: int(item["value"]), reverse=True)[:limit]


def build_action_distribution_rows(orders: list[dict[str, Any]], *, limit: int = 8) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for order in orders:
        action = str(order.get("action", "")).strip()
        if not action:
            continue
        counts[action] = counts.get(action, 0) + 1
    rows = [{"label": key, "value": value} for key, value in counts.items()]
    return sorted(rows, key=lambda item: int(item["value"]), reverse=True)[:limit]


def build_concentration_rows(positions: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for position in positions[:limit]:
        weight_pct = position.get("account_weight_pct", position.get("weight_pct"))
        rows.append(
            {
                "symbol": position.get("symbol"),
                "label": position.get("symbol_name") or position.get("symbol") or "종목명 미확인",
                "value": float(weight_pct or 0.0),
                "market_value_krw": int(position.get("evaluation_amount_krw", 0) or 0),
                "net_pnl_krw": int(position.get("net_pnl_krw", 0) or 0),
                "net_pnl_pct": (
                    None
                    if position.get("net_pnl_pct") is None
                    else float(position.get("net_pnl_pct", 0.0) or 0.0)
                ),
            }
        )
    return rows


def build_focus_position_rows(positions: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for position in positions[:limit]:
        weight_pct = position.get("account_weight_pct", position.get("weight_pct"))
        rows.append(
            {
                "symbol": position.get("symbol"),
                "symbol_name": position.get("symbol_name") or position.get("symbol") or "종목명 미확인",
                "weight_pct": weight_pct,
                "market_value_krw": position.get("evaluation_amount_krw"),
                "net_pnl_krw": position.get("net_pnl_krw"),
                "net_pnl_pct": position.get("net_pnl_pct"),
                "holding_qty": position.get("holding_qty"),
            }
        )
    return rows


def build_concentration_summary(positions: list[dict[str, Any]]) -> dict[str, Any]:
    if not positions:
        return {
            "top1_weight_pct": None,
            "top3_weight_pct": None,
            "positions_count": 0,
            "rows": [],
            "interpretation": "보유 종목 데이터가 없어 집중도를 해석할 수 없습니다.",
        }

    top_rows = build_concentration_rows(positions, limit=3)
    top1_weight = float(top_rows[0].get("value", 0.0) or 0.0) if top_rows else None
    top3_weight = sum(float(row.get("value", 0.0) or 0.0) for row in top_rows)
    positions_count = len(positions)

    if top3_weight >= 80:
        interpretation = f"상위 3개 종목이 계좌의 {top3_weight:.1f}%를 차지합니다. 특정 종목 변동 영향이 큽니다."
    elif top3_weight >= 60:
        interpretation = f"상위 3개 종목 비중이 {top3_weight:.1f}%로 높습니다. 집중도 변화를 함께 보세요."
    else:
        interpretation = f"상위 3개 종목 비중은 {top3_weight:.1f}%입니다. 현재는 상대적으로 분산된 편입니다."

    return {
        "top1_weight_pct": round(top1_weight, 2) if top1_weight is not None else None,
        "top3_weight_pct": round(top3_weight, 2),
        "positions_count": positions_count,
        "rows": top_rows,
        "interpretation": interpretation,
    }


def build_allocation_summary(account: dict[str, Any], positions: list[dict[str, Any]]) -> dict[str, Any]:
    concentration = build_concentration_summary(positions)
    cash_weight_pct = account.get("cash_weight_pct")
    holdings_weight_pct = account.get("holdings_weight_pct")
    top3_weight_pct = concentration.get("top3_weight_pct")
    remaining_positions_weight_pct = None
    if holdings_weight_pct is not None and top3_weight_pct is not None:
        remaining_positions_weight_pct = max(
            0.0,
            float(holdings_weight_pct) - float(top3_weight_pct),
        )
    consistency = str(account.get("allocation_consistency") or "unknown")
    gap = account.get("allocation_gap_pct")

    if cash_weight_pct is None:
        interpretation = "현금 비중은 주문가능현금과 운영 equity가 모두 있을 때만 계산합니다."
    elif top3_weight_pct is None:
        interpretation = f"현금 비중 {float(cash_weight_pct):.2f}%는 전체 운영 equity 중 주문가능현금 비중입니다."
    else:
        interpretation = (
            f"현금 비중 {float(cash_weight_pct):.2f}%는 전체 운영 equity 중 주문가능현금 비중이며, "
            f"상위 3개 포지션 비중은 전체 자산의 {float(top3_weight_pct):.2f}%입니다."
        )
    if consistency != "ok":
        interpretation += " 일부 비중 계산은 fallback 데이터 기준일 수 있습니다."

    return {
        "top1_weight_pct": concentration.get("top1_weight_pct"),
        "top3_weight_pct": top3_weight_pct,
        "cash_weight_pct": cash_weight_pct,
        "holdings_weight_pct": holdings_weight_pct,
        "remaining_positions_weight_pct": remaining_positions_weight_pct,
        "positions_count": concentration.get("positions_count"),
        "rows": concentration.get("rows") or [],
        "interpretation": interpretation,
        "allocation_consistency": consistency,
        "allocation_gap_pct": gap,
    }


def build_allocation_chart_rows(
    account: dict[str, Any],
    positions: list[dict[str, Any]],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    top_positions = positions[:limit]
    for position in top_positions:
        rows.append(
            {
                "label": position.get("symbol_name") or position.get("symbol") or "종목명 미확인",
                "value_krw": int(position.get("evaluation_amount_krw", 0) or 0),
                "weight_pct": float(
                    position.get("account_weight_pct", position.get("weight_pct", 0.0)) or 0.0
                ),
                "symbol": position.get("symbol"),
            }
        )

    remaining_value = 0
    for position in positions[limit:]:
        remaining_value += int(position.get("evaluation_amount_krw", 0) or 0)
    remaining_weight = 0.0
    if account.get("holdings_weight_pct") is not None:
        remaining_weight = max(
            0.0,
            float(account.get("holdings_weight_pct", 0.0) or 0.0)
            - sum(float(row.get("weight_pct", 0.0) or 0.0) for row in rows),
        )
    if remaining_value > 0 or remaining_weight > 0:
        rows.append(
            {
                "label": "기타 포지션",
                "value_krw": remaining_value,
                "weight_pct": round(remaining_weight, 2),
                "symbol": None,
            }
        )

    cash_value = account.get("orderable_cash_krw")
    cash_weight = account.get("cash_weight_pct")
    if cash_value is not None:
        rows.append(
            {
                "label": "주문가능현금",
                "value_krw": int(cash_value or 0),
                "weight_pct": cash_weight,
                "symbol": None,
            }
        )

    return [row for row in rows if int(row.get("value_krw", 0) or 0) > 0]


def build_home_engine_insights(summary: dict[str, Any]) -> dict[str, str]:
    market_session = str(summary.get("market_session") or "")
    last_action = str(summary.get("last_action") or "")
    blocked_action = str(summary.get("top_blocked_action") or "")

    if market_session != "REGULAR":
        engine_status = "장 종료 상태이며 신규 주문은 차단되어 있습니다."
        next_checkpoint = "다음 정규장 시작 전 주문 가능 여부와 최근 차단 사유를 확인하세요."
    elif last_action.endswith("COOLDOWN"):
        engine_status = "엔진은 동작 중이지만 최근 신호가 cooldown으로 건너뛰어졌습니다."
        next_checkpoint = "같은 종목 재진입 cooldown 해제 여부를 먼저 확인하세요."
    elif "BLOCKED" in last_action:
        engine_status = "엔진은 동작 중이며 최근 액션은 차단 상태입니다."
        next_checkpoint = "최근 차단 사유와 리스크 가드 조건을 먼저 확인하세요."
    else:
        engine_status = "엔진은 정상 동작 중이며 현재 기록 기준 치명적 차단은 보이지 않습니다."
        next_checkpoint = "최근 주문 상태와 cycle 판단 이력을 함께 확인하세요."

    if blocked_action and blocked_action != "차단 없음":
        warning = f"최근 주요 차단 사유는 {blocked_action} 입니다."
    elif market_session != "REGULAR":
        warning = "현재 세션상 실제 주문은 차단되고 판단/기록 위주로 동작합니다."
    else:
        warning = "지금은 눈에 띄는 운영 경고가 없습니다."

    return {
        "engine_status": engine_status,
        "next_checkpoint": next_checkpoint,
        "primary_warning": warning,
    }


def build_portfolio_highlights(positions: list[dict[str, Any]]) -> dict[str, Any]:
    if not positions:
        return {
            "best": None,
            "worst": None,
            "largest": None,
            "fragile": None,
        }

    best = max(
        positions,
        key=lambda item: float(item.get("net_pnl_pct") if item.get("net_pnl_pct") is not None else -10**9),
    )
    worst = min(
        positions,
        key=lambda item: float(item.get("net_pnl_pct") if item.get("net_pnl_pct") is not None else 10**9),
    )
    largest = max(
        positions,
        key=lambda item: float(item.get("account_weight_pct", item.get("weight_pct", 0.0)) or 0.0),
    )

    def _fragility_score(item: dict[str, Any]) -> float:
        weight = float(item.get("account_weight_pct", item.get("weight_pct", 0.0)) or 0.0)
        pnl_pct = float(item.get("net_pnl_pct", 0.0) or 0.0) if item.get("net_pnl_pct") is not None else 0.0
        pnl_krw = float(item.get("net_pnl_krw", 0) or 0.0)
        return weight + max(-pnl_pct, 0.0) * 1.5 + (1.0 if pnl_krw < 0 else 0.0)

    fragile = max(positions, key=_fragility_score)
    return {
        "best": best,
        "worst": worst,
        "largest": largest,
        "fragile": fragile,
    }


def build_position_status(item: dict[str, Any]) -> str:
    weight = float(item.get("account_weight_pct", item.get("weight_pct", 0.0)) or 0.0)
    pnl_pct = item.get("net_pnl_pct")
    if pnl_pct is None:
        return "데이터 부족"
    pnl_pct = float(pnl_pct or 0.0)
    if weight >= 35:
        return "집중도 높음"
    if pnl_pct <= -3:
        return "관찰 필요"
    if pnl_pct > 0:
        return "정상"
    return "보합"
