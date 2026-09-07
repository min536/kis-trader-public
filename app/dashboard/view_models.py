import json
from datetime import datetime
from typing import Any

from app.dashboard.components import action_label, compact_reason
from app.dashboard.formatters import (
    format_krw,
    format_pct,
    format_signed_pct,
    format_timestamp,
)
from app.dashboard.metrics import (
    build_anomaly_summary,
    build_buy_judgement_snapshot,
    build_engine_state_view_model,
    build_operational_alerts,
    build_portfolio_highlights,
    engine_short_label,
)


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except TypeError:
        return str(value)

def _safe_text(value: Any, default: str = "데이터 없음") -> str:
    text = str(value or "").strip()
    return text or default

def _safe_count(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return "—"

def _to_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None

def _age_minutes(value: str | None) -> int | None:
    dt = _to_datetime(value)
    if dt is None:
        return None
    now = datetime.now(dt.tzinfo) if dt.tzinfo is not None else datetime.now()
    return max(0, int((now - dt).total_seconds() // 60))

def _freshness_label(value: str | None) -> str:
    minutes = _age_minutes(value)
    if minutes is None:
        return "시각 없음"
    if minutes <= 2:
        return "fresh"
    if minutes <= 10:
        return f"{minutes}m stale"
    return f"{minutes}m+ stale"

def _event_tone(kind: str, raw_action: str | None = None, result: str | None = None) -> str:
    action = str(raw_action or "").lower()
    normalized_result = str(result or "").lower()
    if normalized_result == "failed" or "error" in action or "blocked" in action:
        return "danger"
    if "sell" in action:
        return "warning"
    if kind == "cycle":
        return "neutral"
    return "info"

def _build_trace_events(data: dict[str, Any], *, limit: int = 40) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for order in (data.get("orders") or [])[: limit * 2]:
        timestamp = format_timestamp(order.get("timestamp")) or "시각 없음"
        symbol_name = order.get("symbol_name") or order.get("symbol") or "종목 미확인"
        rows.append(
            {
                "id": f"order::{order.get('timestamp')}::{order.get('action')}::{order.get('symbol')}",
                "kind": "order",
                "tone": _event_tone("order", order.get("action"), order.get("result")),
                "title": f"{action_label(order.get('action'))} · {symbol_name}",
                "body": (
                    f"{_safe_text(order.get('side'), '-')} / "
                    f"{int(order.get('qty', 0) or 0)}주 / "
                    f"{compact_reason(order.get('reason'))}"
                ),
                "timestamp": timestamp,
                "sort_key": str(order.get("timestamp") or ""),
                "raw": order,
            }
        )

    for cycle in (data.get("cycles") or [])[: limit * 2]:
        timestamp = format_timestamp(cycle.get("timestamp")) or "시각 없음"
        subject = (
            cycle.get("selected_primary_action_name")
            or cycle.get("selected_buy_candidate")
            or cycle.get("selected_sell_candidate")
            or "선택 없음"
        )
        rows.append(
            {
                "id": f"cycle::{cycle.get('cycle_id')}",
                "kind": "cycle",
                "tone": _event_tone("cycle", cycle.get("final_action")),
                "title": f"{action_label(cycle.get('final_action'))} · {subject}",
                "body": compact_reason(cycle.get("final_reason")),
                "timestamp": timestamp,
                "sort_key": str(cycle.get("timestamp") or ""),
                "raw": cycle,
            }
        )

    for trade in (data.get("trade_journal") or [])[: limit]:
        timestamp = str(trade.get("sell_ts") or trade.get("buy_ts") or "")
        rows.append(
            {
                "id": f"trade::{trade.get('symbol')}::{timestamp}",
                "kind": "trade",
                "tone": "positive" if trade.get("status") == "closed" else "neutral",
                "title": f"{trade.get('symbol_name') or trade.get('symbol') or '종목 미확인'} · {trade.get('status') or 'unknown'}",
                "body": (
                    f"buy {str(trade.get('buy_ts') or '')[:16].replace('T', ' ')} / "
                    f"sell {str(trade.get('sell_ts') or '')[:16].replace('T', ' ')} / "
                    f"reason {compact_reason(trade.get('sell_reason') or trade.get('buy_reason'))}"
                ),
                "timestamp": timestamp[:16].replace("T", " ") if timestamp else "시각 없음",
                "sort_key": timestamp,
                "raw": trade,
            }
        )

    rows.sort(key=lambda item: item.get("sort_key", ""), reverse=True)
    return rows[:limit]

def _build_queue_items(data: dict[str, Any], summary: dict[str, Any]) -> list[dict[str, str]]:
    engine = build_engine_state_view_model(data.get("engine_state_view") or {})
    anomaly = build_anomaly_summary(data)
    alerts = build_operational_alerts(data)
    top_position = summary.get("top_position") or {}
    worst_position = summary.get("bottom_position") or {}
    buy_snapshot = build_buy_judgement_snapshot(data)
    selected_candidate = buy_snapshot.get("selected_candidate") or {}

    items: list[dict[str, str]] = []
    if summary.get("snapshot_health") != "정상":
        items.append(
            {
                "tone": "danger",
                "tag": "Fix",
                "area": "Data Trust",
                "title": "스냅샷 신뢰도를 먼저 확인해야 합니다",
                "body": _safe_text(summary.get("snapshot_health_reason"), "최근 데이터 저장 상태가 불안정합니다."),
            }
        )
    if summary.get("market_session") != "REGULAR":
        items.append(
            {
                "tone": "warning",
                "tag": "Gate",
                "area": "Session",
                "title": "지금은 주문 시간이 아닙니다",
                "body": "실행보다 다음 정규장 전 readiness, stale data, blocked reason 확인이 우선입니다.",
            }
        )
    if int(anomaly.get("no_order_streak", 0) or 0) >= 5:
        items.append(
            {
                "tone": "warning",
                "tag": "Watch",
                "area": "Flow",
                "title": "최근 여러 cycle이 무주문으로 흘렀습니다",
                "body": "세션 게이트인지, cadence 대기인지, risk guard인지 원인을 먼저 좁혀야 합니다.",
            }
        )
    if top_position and float(top_position.get("account_weight_pct", 0.0) or 0.0) >= 20:
        items.append(
            {
                "tone": "danger",
                "tag": "Risk",
                "area": "Capital",
                "title": f"{top_position.get('symbol_name') or top_position.get('symbol')} 비중이 큽니다",
                "body": f"계좌 비중 {format_pct(top_position.get('account_weight_pct'))}로 단일 포지션 영향이 큽니다.",
            }
        )
    if worst_position and (worst_position.get("net_pnl_pct") is not None) and float(worst_position.get("net_pnl_pct") or 0.0) <= -2:
        items.append(
            {
                "tone": "warning",
                "tag": "Watch",
                "area": "Position",
                "title": f"{worst_position.get('symbol_name') or worst_position.get('symbol')} 손실 확대 중",
                "body": f"수익률 {format_signed_pct(worst_position.get('net_pnl_pct'))} 상태입니다.",
            }
        )
    if selected_candidate:
        items.append(
            {
                "tone": "info",
                "tag": "Idea",
                "area": "Selection",
                "title": f"{selected_candidate.get('symbol_name') or selected_candidate.get('symbol')}가 최근 후보의 중심입니다",
                "body": (
                    f"score {round(float(selected_candidate.get('score', 0.0) or 0.0), 2)} / "
                    f"net edge {round(float(selected_candidate.get('net_edge_bps', 0.0) or 0.0), 1)}bps"
                ),
            }
        )
    if str(summary.get("top_blocked_action") or "") != "차단 없음":
        items.append(
            {
                "tone": "neutral",
                "tag": "Pattern",
                "area": "Blockers",
                "title": "대표 차단 패턴이 반복되고 있습니다",
                "body": action_label(summary.get("top_blocked_action")),
            }
        )

    for alert in alerts:
        items.append(
            {
                "tone": alert.get("tone", "neutral"),
                "tag": "Alert",
                "area": "System",
                "title": alert.get("title", "운영 경고"),
                "body": alert.get("body", ""),
            }
        )

    deduped: list[dict[str, str]] = []
    seen_titles: set[str] = set()
    for item in items:
        title = item["title"]
        if title in seen_titles:
            continue
        seen_titles.add(title)
        deduped.append(item)
    return deduped[:7]

def _build_mission(summary: dict[str, Any], data: dict[str, Any]) -> dict[str, str]:
    engine = build_engine_state_view_model(data.get("engine_state_view") or {})
    performance_selection_meta = data.get("performance_selection_meta") or {}
    buy_snapshot = build_buy_judgement_snapshot(data)
    candidate = buy_snapshot.get("selected_candidate") or {}
    highlights = build_portfolio_highlights(data.get("positions") or [])
    fragile = highlights.get("fragile") or {}

    if summary.get("snapshot_health") != "정상":
        return {
            "tone": "danger",
            "eyebrow": "Operator Mission",
            "title": "판단보다 먼저 데이터 신뢰도를 복구해야 합니다.",
            "body": _safe_text(summary.get("snapshot_health_reason"), "최근 저장 오류 때문에 화면 숫자를 바로 믿기 어렵습니다."),
        }
    if performance_selection_meta.get("fallback_applied"):
        return {
            "tone": "warning",
            "eyebrow": "Operator Mission",
            "title": "최신 스냅샷보다 마지막 정상 스냅샷을 기준으로 읽고 있습니다.",
            "body": compact_reason(performance_selection_meta.get("fallback_reason")),
        }
    if summary.get("market_session") != "REGULAR":
        return {
            "tone": "warning",
            "eyebrow": "Operator Mission",
            "title": "지금은 실주문 세션이 아니므로 준비와 진단이 우선입니다.",
            "body": "다음 정규장에 영향을 줄 blocker, stale data, 집중 포지션을 정리하는 것이 이 화면의 주 목적입니다.",
        }
    if engine.get("current_brake_state") in {"BUY_PAUSE", "HARD_STOP_READY", "DATA_INSUFFICIENT"}:
        return {
            "tone": "danger",
            "eyebrow": "Operator Mission",
            "title": "신규 진입보다 브레이크와 regime 해석이 먼저입니다.",
            "body": _safe_text(engine.get("engine_interpretation"), "브레이크 상태가 우선입니다."),
        }
    if candidate:
        return {
            "tone": "info",
            "eyebrow": "Operator Mission",
            "title": f"{candidate.get('symbol_name') or candidate.get('symbol')}가 현재 선택 surface의 중심입니다.",
            "body": (
                f"score {round(float(candidate.get('score', 0.0) or 0.0), 2)}, "
                f"net edge {round(float(candidate.get('net_edge_bps', 0.0) or 0.0), 1)}bps, "
                f"selection reason {compact_reason(buy_snapshot.get('selection_reason'))}"
            ),
        }
    if fragile:
        return {
            "tone": "warning",
            "eyebrow": "Operator Mission",
            "title": f"{fragile.get('symbol_name') or fragile.get('symbol')}를 먼저 감시해야 합니다.",
            "body": (
                f"비중 {format_pct(fragile.get('account_weight_pct', fragile.get('weight_pct')))} / "
                f"수익률 {format_signed_pct(fragile.get('net_pnl_pct'))}"
            ),
        }
    return {
        "tone": "positive",
        "eyebrow": "Operator Mission",
        "title": "즉시 수정할 문제보다 상태 해석과 다음 체크포인트가 중요합니다.",
        "body": _safe_text(engine.get("engine_interpretation"), "엔진은 현재 계획된 cadence 안에서 동작하고 있습니다."),
    }

def _focus_story(summary: dict[str, Any], data: dict[str, Any], focus: str) -> dict[str, str]:
    engine = build_engine_state_view_model(data.get("engine_state_view") or {})
    candidate = build_buy_judgement_snapshot(data).get("selected_candidate") or {}
    account = data.get("account_view") or {}
    highlights = build_portfolio_highlights(data.get("positions") or [])
    research = data.get("research") or {}

    if focus == "Execution":
        return {
            "tone": "neutral",
            "eyebrow": "Execution Surface",
            "title": "최근 결정과 실제 실행 흔적을 시간축에서 읽습니다.",
            "body": (
                f"last action {action_label(summary.get('last_action'))} / "
                f"scheduler {engine_short_label(engine.get('scheduler_decision'))} / "
                f"today submitted {summary.get('today_order_count') or 0}"
            ),
        }
    if focus == "Capital":
        largest = highlights.get("largest") or {}
        return {
            "tone": "warning" if largest else "neutral",
            "eyebrow": "Capital Surface",
            "title": "자본 배치와 집중 위험을 먼저 봅니다.",
            "body": (
                f"총자산 {format_krw(account.get('raw_balance_total_evaluation_amount_krw', account.get('operating_equity_krw')))} / "
                f"보유 평가금액 {format_krw(account.get('holdings_market_value_krw'))} / "
                f"largest {largest.get('symbol_name') or largest.get('symbol') or '-'}"
            ),
        }
    if focus == "Lab":
        proposals = research.get("proposals") or []
        stale = (research.get("snapshot_meta") or {}).get("stale_baselines") or []
        return {
            "tone": "info",
            "eyebrow": "Lab Surface",
            "title": "운영 반영 전 아이디어와 기준선 상태를 읽습니다.",
            "body": f"proposals {len(proposals)} / stale baselines {len(stale)} / snapshot {research.get('snapshot_as_of') or '—'}",
        }
    return {
        "tone": "positive" if summary.get("market_session") == "REGULAR" else "warning",
        "eyebrow": "Triage Surface",
        "title": "지금 무엇을 봐야 하는지 한 surface로 압축합니다.",
        "body": (
            f"market {engine_short_label(summary.get('market_session'))} / "
            f"freshness {_freshness_label(summary.get('recent_cycle_at'))} / "
            f"candidate {candidate.get('symbol_name') or candidate.get('symbol') or '없음'}"
        ),
    }

def _position_status(position: dict[str, Any], highlights: dict[str, Any]) -> str:
    symbol = position.get("symbol")
    if symbol and symbol == (highlights.get("largest") or {}).get("symbol"):
        return "largest"
    if symbol and symbol == (highlights.get("worst") or {}).get("symbol"):
        return "worst"
    if position.get("net_pnl_pct") is not None and float(position.get("net_pnl_pct") or 0.0) < 0:
        return "loss"
    return "steady"

def _priority_sort_value(position: dict[str, Any]) -> float:
    weight = float(position.get("account_weight_pct", position.get("weight_pct", 0.0)) or 0.0)
    pnl_pct = position.get("net_pnl_pct")
    pnl_pct = float(pnl_pct or 0.0) if pnl_pct is not None else 0.0
    return weight + max(-pnl_pct, 0.0) * 2.0

def _sort_positions(positions: list[dict[str, Any]], sort_mode: str) -> list[dict[str, Any]]:
    if sort_mode == "size":
        return sorted(
            positions,
            key=lambda item: float(item.get("account_weight_pct", item.get("weight_pct", 0.0)) or 0.0),
            reverse=True,
        )
    if sort_mode == "pnl":
        return sorted(
            positions,
            key=lambda item: float(item.get("net_pnl_pct", 0.0) or 0.0),
        )
    if sort_mode == "market_value":
        return sorted(
            positions,
            key=lambda item: int(item.get("evaluation_amount_krw", 0) or 0),
            reverse=True,
        )
    return sorted(positions, key=_priority_sort_value, reverse=True)
