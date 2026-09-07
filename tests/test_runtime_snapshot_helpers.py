from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest import mock

from app import main as main_module
from app.core.time_utils import KOREA_TZ
from app.market_data.schema import MarketSnapshot
from app.portfolio.schema import PortfolioPosition, PortfolioSnapshot
from app.reporting import runtime_snapshots


def _market_snapshot(symbol: str = "005930", *, price: int = 80_000) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        current_price=price,
        open_price=price - 1_000,
        low_price=price - 2_000,
        prev_day_change_pct=1.2,
    )


def _portfolio(*positions: PortfolioPosition) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        positions=tuple(positions),
        cash_total=1_000_000,
        cash_orderable=900_000,
        cash_next_day=1_100_000,
        total_evaluation_amount=2_000_000,
    )


def _position(symbol: str, *, qty: int = 3) -> PortfolioPosition:
    return PortfolioPosition(
        symbol=symbol,
        name=f"Name {symbol}",
        holding_qty=qty,
        average_cost=70_000,
        current_price=80_000,
        market_value=qty * 80_000,
        gross_pnl=qty * 10_000,
        gross_pnl_pct=14.2,
        has_position=qty > 0,
    )


def _scan_result(
    symbol: str = "005930",
    *,
    candidate: bool = True,
    score: float = 1.23456,
    final_reason: str = "candidate",
) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        name=f"Name {symbol}",
        market_snapshot=_market_snapshot(symbol),
        strategy_result=SimpleNamespace(should_attempt_buy=True),
        passed_count=3,
        candidate=candidate,
        final_reason=final_reason,
        passed_pattern="intraday_pullback",
        score=score,
        passes_profit_buffer=True,
        cost_block_reason=None,
        score_components={
            "trend_alignment_score": 0.4,
            "macd_momentum_score": 0.3,
            "gap_up_open_pct": 0.2,
            "pullback_pct": 0.0,
            "range_recovery_ratio": 0.8,
        },
    )


def _shallow_candidate(symbol: str, *, layer: str, score: float) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        name=f"Name {symbol}",
        layer=layer,
        profile="momentum",
        shallow_score=score,
    )


def _candidate_fixture() -> dict[str, object]:
    raw = _scan_result("005930")
    return {
        "cycle_id": "cycle-1",
        "timestamp": "2026-05-26T10:00:00+09:00",
        "market_session": "regular",
        "run_mode": "trade",
        "regime_state": {"current_regime": "normal"},
        "daily_pnl_brake_state": {"status": "clear"},
        "portfolio_snapshot": _portfolio(_position("123456")),
        "requested_symbols": ("005930", "000660", "035420"),
        "layered_universe": {
            "selected_symbols": ("005930", "000660"),
            "layer_by_symbol": {"005930": "core", "000660": "rotating"},
        },
        "pre_gating": {
            "scan_allowed": True,
            "requested_count": 2,
            "allowed_count": 1,
            "early_reject_count": 1,
            "reason_counts": {"cooldown": 1},
            "rejected": [
                {
                    "symbol": "000660",
                    "reason_code": "cooldown",
                    "reason": "재진입 cooldown",
                    "reentry_state": "cooldown",
                }
            ],
        },
        "shallow_plan": {
            "profile": "momentum",
            "shortlist_symbols": ("005930",),
            "candidates": (
                _shallow_candidate("005930", layer="core", score=0.98765),
                _shallow_candidate("000660", layer="rotating", score=0.45678),
            ),
            "ranked_count": 2,
        },
        "raw_scan_results": (raw,),
        "scan_results": (raw,),
        "selected_candidate": raw,
        "runtime_state": {
            "account_signature": "paper:acct",
            "masked_account_display": "****1234",
            "last_action": "BUY_ORDER_SUCCEEDED",
            "last_decision_reason": "submitted",
            "current_regime": "runtime-regime",
        },
    }


def test_build_buy_candidate_outcome_records_preserves_schema_and_stage_fields() -> None:
    rows = runtime_snapshots.build_buy_candidate_outcome_records(**_candidate_fixture())

    assert [row["symbol"] for row in rows] == ["005930", "000660", "035420"]
    executed = rows[0]
    assert executed["cycle_id"] == "cycle-1"
    assert executed["stage_reached"] == "executed"
    assert executed["selection_outcome"] == "executed"
    assert executed["pre_gate_passed"] is True
    assert executed["score_shallow"] == 0.9877
    assert executed["score_deep"] == 1.2346
    assert executed["selection_bucket"] == "core"
    assert executed["core_shadow_evaluated"] is True
    assert executed["buy_signal_executed"] is True

    rejected = rows[1]
    assert rejected["stage_reached"] == "shallow_ranked"
    assert rejected["rejection_reason"] == "cooldown"
    assert rejected["reentry_reason"] == "재진입 cooldown"
    assert rejected["cooldown_blocked"] is True

    layered_out = rows[2]
    assert layered_out["stage_reached"] == "universe_layered_out"
    assert layered_out["selection_bucket"] == "unassigned"
    assert layered_out["core_shadow_evaluated"] is False


def test_build_buy_cycle_funnel_stats_preserves_counts_and_payload_shape() -> None:
    fixture = _candidate_fixture()
    stats = runtime_snapshots.build_buy_cycle_funnel_stats(
        cycle_id=fixture["cycle_id"],
        timestamp=fixture["timestamp"],
        market_session=fixture["market_session"],
        run_mode=fixture["run_mode"],
        regime_state=fixture["regime_state"],
        requested_symbols=fixture["requested_symbols"],
        layered_universe=fixture["layered_universe"],
        pre_gating=fixture["pre_gating"],
        shallow_plan=fixture["shallow_plan"],
        raw_scan_results=fixture["raw_scan_results"],
        selected_candidate=fixture["selected_candidate"],
        runtime_state=fixture["runtime_state"],
        sell_analysis_results=(
            SimpleNamespace(sell_decision=SimpleNamespace(should_attempt_sell=True)),
        ),
        sell_evaluated_count=2,
        api_usage_summary={"total_requests": 7, "categories": {"quote": {"count": 3}}},
        cycle_elapsed_ms=123.45,
    )

    assert stats["cycle_id"] == "cycle-1"
    assert stats["universe_size"] == 3
    assert stats["layered_universe_size"] == 2
    assert stats["layered_out_count"] == 1
    assert stats["pre_gate_passed"] == 1
    assert stats["pre_gate_rejected_total"] == 1
    assert stats["pre_gate_rejection_counts"] == {"cooldown": 1}
    assert stats["shallow_ranked_count"] == 2
    assert stats["shallow_shortlist_size"] == 1
    assert stats["deep_eval_count"] == 1
    assert stats["final_candidate_count"] == 1
    assert stats["executed_order_count"] == 1
    assert stats["sell_evaluated_count"] == 2
    assert stats["sell_triggered_count"] == 1
    assert stats["api_request_count"] == 7
    assert stats["api_quote_request_count"] == 3
    assert stats["cycle_elapsed_ms"] == 123.5
    assert stats["core_layered_count"] == 1
    assert stats["noncore_layered_count"] == 1
    assert stats["selected_candidate_layer"] == "core"
    assert stats["buy_non_execution_reason"] is None


def _funnel_stats_with_guard(runtime_state, buy_risk_guard_payload):
    fixture = _candidate_fixture()
    return runtime_snapshots.build_buy_cycle_funnel_stats(
        cycle_id=fixture["cycle_id"],
        timestamp=fixture["timestamp"],
        market_session=fixture["market_session"],
        run_mode=fixture["run_mode"],
        regime_state=fixture["regime_state"],
        requested_symbols=fixture["requested_symbols"],
        layered_universe=fixture["layered_universe"],
        pre_gating=fixture["pre_gating"],
        shallow_plan=fixture["shallow_plan"],
        raw_scan_results=fixture["raw_scan_results"],
        selected_candidate=fixture["selected_candidate"],
        runtime_state=runtime_state,
        sell_analysis_results=(),
        sell_evaluated_count=0,
        api_usage_summary={},
        cycle_elapsed_ms=1.0,
        buy_risk_guard_payload=buy_risk_guard_payload,
    )


def test_funnel_flags_order_log_untrusted_distinct_from_generic_guard() -> None:
    # record_cycle_action collapses every risk-guard block to
    # "BUY_BLOCKED_RISK_GUARD", so last_action alone maps to order_guard_blocked.
    # The guard payload distinguishes the order-log-integrity failure.
    runtime_state = {
        "last_action": "BUY_BLOCKED_RISK_GUARD",
        "last_decision_reason": "주문 로그를 신뢰할 수 없어 fail-closed 차단",
    }
    guard_payload = {
        "evaluated": True,
        "allowed": False,
        "action": "blocked_buy_order_log_untrusted",
        "guard_results": {
            "order_log_integrity": {
                "passed": False,
                "reason": "주문 로그를 신뢰할 수 없어 리스크 가드를 fail-closed로 차단합니다.",
                "action": "blocked_buy_order_log_untrusted",
                "details": {"order_log_error_code": "order_log_too_large"},
            }
        },
    }
    stats = _funnel_stats_with_guard(runtime_state, guard_payload)
    assert stats["buy_non_execution_reason"] == "order_log_untrusted"


def test_funnel_generic_guard_when_integrity_ok() -> None:
    runtime_state = {"last_action": "BUY_BLOCKED_RISK_GUARD"}
    guard_payload = {"guard_results": {"order_log_integrity": {"passed": True}}}
    stats = _funnel_stats_with_guard(runtime_state, guard_payload)
    assert stats["buy_non_execution_reason"] == "order_guard_blocked"


def test_funnel_bucket_backward_compatible_without_guard_payload() -> None:
    runtime_state = {"last_action": "BUY_BLOCKED_RISK_GUARD"}
    stats = _funnel_stats_with_guard(runtime_state, None)
    assert stats["buy_non_execution_reason"] == "order_guard_blocked"


def test_runtime_market_snapshot_serialization_and_cache_update_are_stable() -> None:
    now = datetime(2026, 5, 26, 10, 30, tzinfo=KOREA_TZ)
    snapshot = _market_snapshot("005930", price=81_000)
    state = {"recent_market_snapshots_by_symbol": {"000660": {"current_price": 100_000}}}

    with mock.patch.object(runtime_snapshots, "get_korean_now", return_value=now):
        payload = runtime_snapshots.serialize_runtime_market_snapshot(snapshot)
        runtime_snapshots.update_recent_market_snapshots(
            state,
            {"005930": snapshot, "": _market_snapshot("")},
        )

    assert payload == {
        "symbol": "005930",
        "current_price": 81_000,
        "open_price": 80_000,
        "low_price": 79_000,
        "prev_day_change_pct": 1.2,
        "observed_at": "2026-05-26T10:30:00+09:00",
    }
    assert state["recent_market_snapshots_by_symbol"]["000660"]["current_price"] == 100_000
    assert state["recent_market_snapshots_by_symbol"]["005930"] == payload
    assert "" not in state["recent_market_snapshots_by_symbol"]
    assert runtime_snapshots.serialize_runtime_market_snapshot(None) is None


def test_cycle_action_and_print_helpers_preserve_console_and_state_shape(capsys) -> None:
    state: dict[str, object] = {}

    runtime_snapshots.print_cycle_conclusion(
        side="BUY",
        display_name="005930 Name 005930",
        reason="candidate selected",
        planned_qty=3,
    )
    runtime_snapshots.record_cycle_action(
        state,
        action="BUY_ORDER_SUBMITTED",
        reason="submitted",
        order_side="BUY",
        symbol="005930",
        qty=3,
        selected_symbol="005930",
    )

    output = capsys.readouterr().out
    assert "이번 사이클 결론" in output
    assert "우선 실행 대상: BUY" in output
    assert "예정 수량: 3주" in output
    assert "last_action=BUY_ORDER_SUBMITTED" in output
    assert state["last_action"] == "BUY_ORDER_SUBMITTED"
    assert state["last_final_action"] == "BUY_ORDER_SUBMITTED"
    assert state["last_decision_reason"] == "submitted"
    assert state["last_order_side"] == "BUY"
    assert state["last_buy_symbol"] == "005930"
    assert state["last_buy_qty"] == 3
    assert state["last_selected_symbol"] == "005930"


def test_log_engine_event_writes_order_log_payload_shape() -> None:
    with mock.patch.object(runtime_snapshots, "log_order_event") as log_order_event:
        main_module._log_engine_event(
            action="buy_scan_pre_gated",
            reason="daily stop",
            cycle_id="cycle-1",
            market_open=True,
            market_session="regular",
        )

    log_order_event.assert_called_once_with(
        symbol="",
        qty=0,
        order_type="engine_event",
        confirm_buy="-",
        market_open=True,
        cycle_id="cycle-1",
        action="buy_scan_pre_gated",
        result="skipped",
        reason="daily stop",
        raw_response={"engine_event": True, "market_session": "regular"},
    )


def test_print_runtime_state_summary_preserves_status_lines(capsys) -> None:
    state = {
        "account_signature": "paper:acct",
        "masked_account_display": "****1234",
        "symbols_bought_today": ["005930"],
        "symbols_sold_today": ["000660"],
        "blocked_buy_symbols_today": ["035420"],
        "blocked_sell_symbols_today": ["051910"],
        "buy_cooldown_blocked_symbols_today": ["068270"],
        "buy_untradable_symbols_today": ["123456"],
        "sell_cooldown_blocked_symbols_today": ["096770"],
        "rebalance_sell_submissions_today": 2,
        "last_warning_count": 1,
        "last_error_count": 0,
        "last_snapshot_write_ok": True,
        "last_performance_write_ok": False,
    }

    runtime_snapshots.print_runtime_state_summary(state, state_write_ok=False)

    output = capsys.readouterr().out
    assert "운영 상태 요약" in output
    assert "계정 스코프: paper:acct (****1234)" in output
    assert "오늘 매수 완료 종목 수: 1" in output
    assert "오늘 SELL cooldown 차단 종목 수: 1" in output
    assert "오늘 리밸런싱 매도 실행 수: 2" in output
    assert "최근 cycle 경고 수: 1" in output
    assert "snapshot/performance 저장 상태: OK / WARN" in output
    assert "상태 파일 기록: 실패" in output
