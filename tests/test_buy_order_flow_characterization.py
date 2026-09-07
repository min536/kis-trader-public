from __future__ import annotations

from dataclasses import is_dataclass, replace as dataclass_replace
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

import pytest

from app import main as main_module
from app.auth.token import ApiHttpError
from app.core.market_session import MarketSessionStatus
from app.core.time_utils import KOREA_TZ
from app.execution import buy_flow as buy_flow_module
from app.execution.position_sizing import PositionSizingResult
from app.portfolio.schema import PortfolioSnapshot
from app.risk.schema import RiskEvaluationResult


OPEN_SESSION = MarketSessionStatus(
    session="REGULAR",
    order_allowed=True,
    reason="정규장 주문 가능",
    buy_block_action=None,
    sell_block_action=None,
)
CLOSED_SESSION = MarketSessionStatus(
    session="CLOSED",
    order_allowed=False,
    reason="주문 불가 세션",
    buy_block_action="blocked_holiday_or_closed",
    sell_block_action="blocked_sell_holiday_or_closed",
)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        run_mode="trade",
        run_once=True,
        run_interval_seconds=60,
        confirm_buy="YES",
        qty=1,
        target_symbols=("005930",),
        sell_check_interval_seconds=60,
        buy_scan_interval_seconds=60,
        enable_sell_guard_selftest=False,
        enable_sell_test_scenarios=False,
        sell_test_mode="off",
        enable_daily_pnl_brake=False,
        enable_premarket_wait=False,
        live_snapshot_refresh_interval_seconds=30,
        buy_max_budget_per_trade_krw=1_000_000,
        buy_max_account_exposure_pct=100.0,
        buy_max_qty_per_trade=10,
        rebuy_cooldown_minutes=0,
        same_symbol_max_buys_per_day=4,
        buy_daily_max_order_submissions=10,
        buy_daily_max_notional_krw=10_000_000,
        sell_daily_max_order_submissions=10,
        sell_daily_max_notional_krw=10_000_000,
        block_rebuy_symbols_bought_today=True,
        allow_one_buy_per_symbol_per_day=False,
        enable_buy_cooldown=True,
        buy_blocked_cooldown_minutes=0,
        order_cooldown_minutes=0,
        buy_block_on_blocked_preview=True,
        buy_enable_risk_guards=True,
        strict_sell_first=True,
        enable_rebalance_sell=False,
        enable_quality_rebalance_preview=False,
        rebalance_sell_max_submissions_per_day=10,
        scan_symbols_max_per_cycle=1,
        buy_scan_profile_rotation_enabled=False,
        buy_scan_exploration_ratio=0.0,
        buy_scan_core_fraction=1.0,
        buy_scan_rotating_fraction=0.0,
        buy_scan_shallow_top_k=1,
        buy_scan_deep_eval_limit=1,
        buy_scan_core_max=1,
        buy_scan_top_k_candidates=1,
        api_soft_max_requests_per_second=100,
        api_soft_max_quotes_per_tick=100,
        api_backoff_seconds_on_rate_limit=1,
        api_min_inter_request_seconds=0.0,
        api_buy_scan_min_request_reserve=0,
        api_buy_scan_min_quote_reserve=0,
        degraded_mode_enabled=False,
        slack_notify_order_submitted=True,
        buy_fee_bps=0.0,
        sell_fee_bps=0.0,
        sell_tax_bps=0.0,
        buy_slippage_bps=0.0,
        sell_slippage_bps=0.0,
        expected_slippage_bps_base=0.0,
        expected_cost_block_bps=0.0,
        min_net_edge_bps=0.0,
        min_net_profit_buffer_bps=0.0,
        use_cost_aware_pnl=False,
        performance_benchmark_symbol="069500",
    )


def _portfolio(cash_orderable: int = 1_000_000) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        positions=(),
        cash_total=cash_orderable,
        cash_orderable=cash_orderable,
        cash_next_day=cash_orderable,
        total_evaluation_amount=cash_orderable,
    )


def _candidate(symbol: str = "005930", price: int = 70_000) -> SimpleNamespace:
    strategy_result = SimpleNamespace(to_log_payload=lambda: {"decision": "buy"})
    return SimpleNamespace(
        symbol=symbol,
        name="삼성전자",
        display_name="삼성전자",
        candidate=True,
        market_snapshot=SimpleNamespace(
            symbol=symbol,
            current_price=price,
            prev_day_change_pct=1.25,
        ),
        strategy_result=strategy_result,
        score=9.5,
        score_summary="score summary",
        score_highlights=("highlight",),
        score_penalties=(),
        math_score_summary="math",
        mean_reversion_summary="mean",
        mean_reversion_zscore=0.1,
        portfolio_risk_summary="risk",
        portfolio_avg_correlation=0.2,
        portfolio_max_correlation=0.3,
        variance_increase_estimate=0.4,
        expected_total_cost_krw=0,
        expected_cost_bps=0.0,
        net_edge_bps=10.0,
        cost_block_reason=None,
        expected_fee_krw=0,
        expected_tax_krw=0,
        expected_slippage_krw=0,
        cost_quality_score=1.0,
        expected_cost_penalty=0.0,
        final_reason="candidate",
    )


def _sizing(qty: int = 3, *, reason: str = "sized") -> PositionSizingResult:
    notional = qty * 70_000
    block_code = "" if qty > 0 else "cash_insufficient"
    block_label = "" if qty > 0 else "현금 부족"
    return PositionSizingResult(
        recommended_qty=qty,
        recommended_notional_krw=notional,
        max_affordable_qty=qty,
        budget_limited_qty=qty,
        exposure_limited_qty=qty,
        max_qty_limited_qty=10,
        reason=reason,
        details={
            "current_price_krw": 70_000,
            "orderable_cash_krw": 1_000_000,
            "orderable_qty": 10,
            "max_affordable_qty": qty,
            "budget_limited_qty": qty,
            "exposure_limited_qty": qty,
            "max_qty_limited_qty": 10,
            "max_budget_per_trade_krw": 1_000_000,
            "exposure_budget_krw": 1_000_000,
            "recommended_qty": qty,
            "recommended_notional_krw": notional,
            "estimated_buy_fee_krw": 0,
            "estimated_buy_slippage_krw": 0,
            "estimated_entry_cost_krw": notional,
            "estimated_round_trip_cost_krw": 0,
            "estimated_break_even_bps": 0.0,
            "block_reason_code": block_code,
            "block_reason_label": block_label,
            "budget_rescue_applied": False,
        },
    )


def _reentry_decision() -> SimpleNamespace:
    return SimpleNamespace(
        state="allowed",
        reason="allowed",
        to_log_payload=lambda: {"state": "allowed", "reason": "allowed"},
    )


def _copying_response(response: dict[str, object] | Exception):
    if isinstance(response, Exception):
        raise response
    return response


class BuyFlowHarness:
    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        order_response: dict[str, object] | Exception | None = None,
        sizing: PositionSizingResult | None = None,
        sessions: tuple[MarketSessionStatus, ...] = (OPEN_SESSION, OPEN_SESSION),
    ) -> None:
        self.settings = _settings()
        self.state = {
            "recent_orders": [],
            "buy_attempted_symbols_today": [],
            "symbols_bought_today": [],
            "buy_blocked_symbols_today": [],
            "blocked_buy_symbols_today": [],
            "buy_untradable_symbols_today": [],
        }
        self.logs: list[dict[str, object]] = []
        self.slack: list[dict[str, object]] = []
        self.budget_calls: list[tuple[str, dict[str, object]]] = []
        self.buy_market = mock.Mock(
            side_effect=lambda **kwargs: _copying_response(
                order_response or {"rt_cd": "0", "msg1": "accepted", "output": {"odno": "1"}}
            )
        )
        self.sizing = sizing or _sizing()
        candidate = _candidate()
        portfolio = _portfolio()
        fixed_now = datetime(2026, 5, 28, 9, 30, tzinfo=KOREA_TZ)

        def fake_replace(obj, **changes):
            if is_dataclass(obj):
                return dataclass_replace(obj, **changes)
            data = vars(obj).copy()
            data.update(changes)
            return SimpleNamespace(**data)

        def capture_log(**kwargs):
            self.logs.append(kwargs)

        def capture_slack(**kwargs):
            self.slack.append(kwargs)

        def register_request(_state, **kwargs):
            self.budget_calls.append(("register_request", kwargs))

        def register_requests(_state, **kwargs):
            self.budget_calls.append(("register_requests", kwargs))

        patches: dict[str, object] = {
            "replace": fake_replace,
            "get_korean_now": lambda: fixed_now,
            "_print_runtime_mode": lambda *_a, **_k: None,
            "_print_applied_settings": lambda *_a, **_k: None,
            "_print_test_mode": lambda *_a, **_k: None,
            "_print_cycle_header": lambda *_a, **_k: None,
            "_print_engine_schedule_state": lambda *_a, **_k: None,
            "_print_account_balance_interpretation": lambda *_a, **_k: None,
            "_print_regime_state": lambda *_a, **_k: None,
            "_print_daily_pnl_brake_state": lambda *_a, **_k: None,
            "_print_portfolio_positions": lambda *_a, **_k: None,
            "_print_today_performance_summary": lambda *_a, **_k: None,
            "_print_today_bought_tracking": lambda *_a, **_k: None,
            "_print_buy_strategy": lambda *_a, **_k: None,
            "_print_buy_score_summary": lambda *_a, **_k: None,
            "_print_buy_orderable_preview": lambda *_a, **_k: None,
            "_print_cycle_conclusion": lambda *_a, **_k: None,
            "_print_cycle_timing": lambda *_a, **_k: None,
            "_print_api_usage": lambda *_a, **_k: None,
            "_print_sell_metrics": lambda *_a, **_k: None,
            "_print_buy_scan_metrics": lambda *_a, **_k: None,
            "_print_runtime_state_summary": lambda *_a, **_k: None,
            "build_market_session_console_lines": lambda *_a, **_k: [],
            "build_universe_console_lines": lambda *_a, **_k: [],
            "build_scan_console_lines": lambda *_a, **_k: [],
            "build_risk_guard_console_lines": lambda *_a, **_k: [],
            "build_risk_guard_skipped_console_lines": lambda *_a, **_k: [],
            "sync_account_scope_meta": lambda *_a, **_k: {"account_scope_changed": False},
            "get_account_scope_context": lambda *_a, **_k: {
                "account_signature": "mock_test_01",
                "account_environment": "mock",
                "masked_account_display": "mock_****_01",
            },
            "load_runtime_state": lambda: self.state,
            "save_runtime_state": lambda state: True,
            "_write_slack_runtime_status_snapshot": lambda *_a, **_k: True,
            "get_korean_market_session": lambda: sessions[0],
            "_should_run_light_session_cycle": lambda *_a, **_k: False,
            "_api_budget_transient_backoff_active": lambda *_a, **_k: False,
            "_api_budget_backoff_active": lambda *_a, **_k: False,
            "_api_budget_can_request": lambda *_a, **_k: True,
            "_api_budget_can_quote": lambda *_a, **_k: True,
            "_api_budget_remaining_requests": lambda *_a, **_k: 100,
            "_api_budget_remaining_quotes": lambda *_a, **_k: 100,
            "_api_budget_request_window_size": lambda *_a, **_k: 0,
            "_api_budget_min_wait_for_request_slot": lambda *_a, **_k: 0,
            "_api_budget_backoff_remaining_seconds": lambda *_a, **_k: 0,
            "_api_budget_transient_backoff_remaining_seconds": lambda *_a, **_k: 0,
            "_api_budget_register_request": register_request,
            "_api_budget_register_requests": register_requests,
            "_api_budget_register_measured_extra_requests": lambda *_a, **_k: 0,
            "_api_budget_note_transient_api_error": lambda *_a, **_k: None,
            "_api_budget_update_rate_limit_recovery_state": lambda *_a, **_k: None,
            "_api_budget_update_transient_recovery_state": lambda *_a, **_k: None,
            "_summarize_api_budget_state": lambda *_a, **_k: {},
            "issue_access_token": lambda: "TOKEN",
            "inquire_balance": lambda *, token: {"rt_cd": "0", "output1": [], "output2": []},
            "build_portfolio_snapshot": lambda _data: portfolio,
            "_sync_reconciliation_state": lambda *_a, **_k: {},
            "build_sell_watch_budget_plan": lambda **_k: SimpleNamespace(
                pressure_level="none",
                reason="",
                max_evaluations=0,
            ),
            "build_account_state_payload": lambda *_a, **_k: {
                "deployment_invariant_equity_krw": 1_000_000,
                "operating_equity_krw": 1_000_000,
            },
            "_build_today_realized_summary": lambda *_a, **_k: {},
            "_build_daily_pnl_brake_state": lambda *_a, **_k: {"buy_paused": False},
            "_build_daily_pnl_brake_observability": lambda *_a, **_k: {},
            "build_current_drawdown_state": lambda *_a, **_k: {},
            "_build_regime_state": lambda *_a, **_k: {
                "current_regime": "NORMAL",
                "regime_reason": "normal",
                "regime_multiplier": 1.0,
                "effective_buy_max_budget_per_trade_krw": self.settings.buy_max_budget_per_trade_krw,
                "effective_buy_max_account_exposure_pct": self.settings.buy_max_account_exposure_pct,
                "effective_buy_max_qty_per_trade": self.settings.buy_max_qty_per_trade,
                "effective_rebuy_cooldown_minutes": self.settings.rebuy_cooldown_minutes,
                "effective_same_symbol_max_buys_per_day": self.settings.same_symbol_max_buys_per_day,
                "effective_buy_daily_max_order_submissions": self.settings.buy_daily_max_order_submissions,
            },
            "load_live_snapshot_symbols": lambda: None,
            "live_snapshot_status": lambda: {},
            "live_snapshot_worker_health": lambda *_a, **_k: {"warnings": ()},
            "_select_buy_scan_profile": lambda *_a, **_k: {"profile": "momentum", "rotation_enabled": False},
            "_build_buy_scan_layered_universe": lambda *_a, **_k: {
                "selected_symbols": ("005930",),
                "layer_by_symbol": {},
                "core_count": 1,
                "rotating_count": 0,
                "exploration_count": 0,
                "selected_core_count": 1,
                "selected_rotating_count": 0,
                "selected_exploration_count": 0,
                "excluded_count": 0,
                "preview": {},
            },
            "_build_buy_scan_pre_gating": lambda *_a, **_k: {
                "scan_allowed": True,
                "allowed_symbols": ("005930",),
                "rejected": [],
                "reason_counts": {},
                "early_reject_count": 0,
            },
            "_build_buy_scan_shallow_plan": lambda *_a, **_k: {
                "ranked_count": 1,
                "deep_eval_limit": 1,
                "shortlist_symbols": ("005930",),
                "shortlist_preview": [],
                "exploration_quota_used": 0,
            },
            "_build_buy_scan_deep_eval_symbols": lambda *_a, **_k: ("005930",),
            "_cap_buy_scan_deep_eval_symbols_for_api_budget": lambda *, symbols, **_k: (
                tuple(symbols),
                {
                    "budget_cap_applied": False,
                    "budget_cap_reasons": [],
                    "quote_budget_original_count": len(tuple(symbols)),
                    "budget_capped_count": len(tuple(symbols)),
                    "quote_budget_remaining_before_scan": 100,
                    "request_budget_remaining_before_scan": 100,
                    "execution_request_reserve": 2,
                    "min_scan_request_floor": 1,
                },
            ),
            "get_adaptive_pacing_summary": lambda: {},
            "scan_target_symbols": lambda *_a, **_k: (candidate,),
            "get_last_scan_diagnostics": lambda: {},
            "_apply_buy_runtime_guards_to_scan_results": lambda results, **_k: tuple(results),
            "select_top_candidate": lambda _results, **_k: candidate,
            "select_top_analysis_result": lambda _results: candidate,
            "serialize_selection_details": lambda **_k: {
                "selection_reason": "best candidate",
                "selected_symbol": "005930",
            },
            "_build_math_sizing_context": lambda *_a, **_k: (
                {},
                {
                    "max_budget_per_trade_krw": 1_000_000,
                    "max_account_exposure_pct": 100.0,
                    "max_qty_per_trade": 10,
                },
            ),
            "_wait_for_execution_request_budget": lambda *_a, **kwargs: self.budget_calls.append(
                ("wait", kwargs)
            )
            or 0.0,
            "_send_order_slack_notification": capture_slack,
            "_resolve_benchmark_snapshot": lambda *_a, **_k: None,
            "build_performance_report": lambda *_a, **_k: {},
            "persist_performance_report": lambda *_a, **_k: {
                "snapshot_saved": False,
                "report_saved": False,
            },
            "build_performance_console_lines": lambda *_a, **_k: [],
            "build_cycle_snapshot": lambda **_k: {},
            "_build_buy_cycle_funnel_stats": lambda **_k: {},
            "_build_buy_candidate_outcome_records": lambda **_k: [],
            "persist_cycle_snapshot": lambda *_a, **_k: True,
            "append_candidate_outcomes": lambda *_a, **_k: True,
            "append_cycle_stats": lambda *_a, **_k: True,
            "_format_cycle_summary": lambda **_k: "cycle summary",
            "_update_recent_market_snapshots": lambda *_a, **_k: None,
            "build_daily_summary": lambda: {},
            "build_daily_summary_console_lines": lambda *_a, **_k: [],
            "build_cycle_stats_daily_summary": lambda: {},
            "build_cycle_stats_console_lines": lambda *_a, **_k: [],
        }
        monkeypatch.setattr(main_module.time, "sleep", lambda _seconds: None)
        for name, value in patches.items():
            monkeypatch.setattr(main_module, name, value)
        flow_patches: dict[str, object] = {
            "time.sleep": lambda _seconds: None,
            "get_korean_now": lambda: fixed_now,
            "get_korean_market_session": lambda: sessions[-1],
            "build_market_session_console_lines": lambda *_a, **_k: [],
            "build_risk_guard_console_lines": lambda *_a, **_k: [],
            "build_risk_guard_skipped_console_lines": lambda *_a, **_k: [],
            "print_buy_strategy": lambda *_a, **_k: None,
            "print_buy_score_summary": lambda *_a, **_k: None,
            "print_buy_orderable_preview": lambda *_a, **_k: None,
            "print_cycle_conclusion": lambda *_a, **_k: None,
            "record_reentry_decision": lambda *_a, **_k: None,
            "api_budget_backoff_remaining_seconds": lambda *_a, **_k: 0,
            "api_budget_can_request": lambda *_a, **_k: True,
            "api_budget_register_request": register_request,
            "api_budget_register_requests": register_requests,
            "inquire_orderable_cash": lambda *, symbol, price, token: {
                "rt_cd": "0",
                "output": {"ord_psbl_cash": "1000000", "nrcvb_buy_qty": "10"},
            },
            "evaluate_reentry_eligibility": lambda *_a, **_k: _reentry_decision(),
            "build_math_sizing_context": lambda *_a, **_k: (
                {},
                {
                    "max_budget_per_trade_krw": 1_000_000,
                    "max_account_exposure_pct": 100.0,
                    "max_qty_per_trade": 10,
                },
            ),
            "calculate_position_sizing": lambda *_a, **_k: self.sizing,
            "position_sizing_requires_rebalance": lambda _sizing: False,
            "evaluate_buy_order_guard": lambda *_a, **_k: SimpleNamespace(
                allowed=True,
                action=None,
                reason="allowed",
                is_cooldown=False,
            ),
            "evaluate_buy_risk_guards": lambda *_a, **_k: RiskEvaluationResult(
                evaluated=True,
                allowed=True,
                action=None,
                reason="passed",
                details={},
            ),
            "buy_market": self.buy_market,
            "log_order_event": capture_log,
        }
        monkeypatch.setattr(buy_flow_module.time, "sleep", lambda _seconds: None)
        for name, value in flow_patches.items():
            if name == "time.sleep":
                continue
            monkeypatch.setattr(buy_flow_module, name, value)

    def run(self) -> None:
        main_module.run_cycle(
            self.settings,
            sell_check_due=False,
            buy_scan_due=True,
            scheduler_state={},
            api_budget_state={},
        )

    @property
    def log_actions(self) -> list[str]:
        return [str(entry["action"]) for entry in self.logs]

    @property
    def slack_actions(self) -> list[str]:
        return [str(entry["action"]) for entry in self.slack]


def test_buy_success_path_logs_notifies_submits_and_mutates_state(monkeypatch, capsys) -> None:
    harness = BuyFlowHarness(monkeypatch)

    harness.run()
    capsys.readouterr()

    harness.buy_market.assert_called_once_with(symbol="005930", qty=3, token="TOKEN")
    assert harness.log_actions[-2:] == ["order_submitted", "order_succeeded"]
    assert harness.slack_actions[-2:] == ["order_submitted", "order_succeeded"]
    assert harness.state["symbols_bought_today"] == ["005930"]
    assert harness.state["buy_attempted_symbols_today"] == ["005930"]
    assert harness.state["buy_entries_by_symbol_today"] == {"005930": 1}
    assert [order["action"] for order in harness.state["recent_orders"]] == [
        "order_submitted"
    ]
    assert harness.state["last_buy_attempt_signature"] == "BUY:005930:3"
    assert harness.state["last_action"] == "BUY_ORDER_SUCCEEDED"
    assert any(
        name == "wait"
        and kwargs.get("phase") == "orderable_lookup"
        and kwargs.get("request_cost") == 2
        and kwargs.get("request_reserve") == 1
        for name, kwargs in harness.budget_calls
    )
    assert any(
        name == "wait"
        and kwargs.get("phase") == "order_submit"
        and kwargs.get("request_cost") == 2
        and kwargs.get("request_reserve") == 0
        for name, kwargs in harness.budget_calls
    )
    assert any(
        name == "register_requests" and kwargs.get("request_count") == 2
        for name, kwargs in harness.budget_calls
    )


def test_buy_success_path_captures_reference_and_quote_prices(monkeypatch, capsys) -> None:
    harness = BuyFlowHarness(monkeypatch)

    harness.run()
    capsys.readouterr()

    submitted = next(e for e in harness.logs if e["action"] == "order_submitted")
    succeeded = next(e for e in harness.logs if e["action"] == "order_succeeded")
    for record in (submitted, succeeded):
        raw = record["raw_response"]
        assert raw["reference_price_krw"] == 70000.0
        assert raw["quote_at_submit"] == 70000.0


def test_buy_rt_cd_failure_preserves_response_and_skips_success_mutation(monkeypatch, capsys) -> None:
    response = {"rt_cd": "1", "msg1": "broker rejected", "output": {"odno": "R1"}}
    harness = BuyFlowHarness(monkeypatch, order_response=response)

    with pytest.raises(RuntimeError, match="주문 실패"):
        harness.run()
    capsys.readouterr()

    failed_log = next(entry for entry in harness.logs if entry["action"] == "order_failed")
    assert failed_log["raw_response"]["order_response"] is response
    assert harness.slack_actions[-1] == "order_failed"
    assert harness.state["symbols_bought_today"] == []
    assert [order["action"] for order in harness.state["recent_orders"]] == [
        "order_submitted",
        "order_failed",
    ]


def test_buy_untradable_response_marks_untradable_without_success_mutation(monkeypatch, capsys) -> None:
    response = {
        "rt_cd": "1",
        "msg_cd": "APBK0918",
        "msg1": "모의투자 장내채권/ETF/ETN/ELW 매매불가",
    }
    harness = BuyFlowHarness(monkeypatch, order_response=response)

    with pytest.raises(RuntimeError, match="주문 실패"):
        harness.run()
    capsys.readouterr()

    failed_log = next(entry for entry in harness.logs if entry["action"] == "order_failed")
    assert failed_log["raw_response"]["buy_untradable_detected"] is True
    assert harness.state["buy_untradable_symbols_today"] == ["005930"]
    assert harness.state["blocked_buy_symbols_today"] == ["005930"]
    assert harness.state["symbols_bought_today"] == []


def test_buy_api_http_error_logs_failed_order_and_rate_limit_source(monkeypatch, capsys) -> None:
    rate_limit_calls: list[dict[str, object]] = []
    exc = ApiHttpError(
        "EGW00201 초당 거래건수를 초과하였습니다.",
        429,
        {"rt_cd": "1", "msg1": "EGW00201 초당 거래건수를 초과하였습니다."},
    )
    harness = BuyFlowHarness(monkeypatch, order_response=exc)
    monkeypatch.setattr(
        main_module,
        "_api_budget_note_rate_limit",
        lambda _state, **kwargs: rate_limit_calls.append(kwargs),
    )

    with pytest.raises(ApiHttpError):
        harness.run()
    capsys.readouterr()

    failed_log = next(entry for entry in harness.logs if entry["action"] == "order_failed")
    assert failed_log["raw_response"]["order_response"] is exc.data
    assert harness.slack_actions[-1] == "order_failed"
    assert harness.state["symbols_bought_today"] == []
    assert rate_limit_calls[-1]["source"] == "buy_order"


def test_buy_generic_exception_logs_failed_order_without_success_mutation(monkeypatch, capsys) -> None:
    harness = BuyFlowHarness(monkeypatch, order_response=ValueError("network down"))

    with pytest.raises(ValueError, match="network down"):
        harness.run()
    capsys.readouterr()

    failed_log = next(entry for entry in harness.logs if entry["action"] == "order_failed")
    assert failed_log["raw_response"]["order_response"] == "network down"
    assert harness.slack_actions[-1] == "order_failed"
    assert harness.state["symbols_bought_today"] == []


def test_buy_zero_sizing_blocks_before_broker_call(monkeypatch, capsys) -> None:
    harness = BuyFlowHarness(
        monkeypatch,
        sizing=_sizing(qty=0, reason="BUY 불가: 주문가능현금이 부족해 추천 매수 수량이 0주입니다."),
    )

    harness.run()
    capsys.readouterr()

    harness.buy_market.assert_not_called()
    assert "blocked_buy_cash_insufficient" in harness.log_actions
    assert "order_submitted" not in harness.log_actions
    assert harness.state["blocked_buy_symbols_today"] == ["005930"]


def test_buy_market_session_recheck_blocks_before_submitted_log_and_broker_call(monkeypatch, capsys) -> None:
    harness = BuyFlowHarness(monkeypatch, sessions=(OPEN_SESSION, CLOSED_SESSION))

    harness.run()
    capsys.readouterr()

    harness.buy_market.assert_not_called()
    assert "order_submitted" not in harness.log_actions
    assert harness.log_actions[-1] == "blocked_holiday_or_closed"
    assert harness.state["blocked_buy_symbols_today"] == ["005930"]


def test_rebalance_buy_preview_schema_and_print_smoke(monkeypatch, capsys) -> None:
    candidate = _candidate()
    portfolio = _portfolio(cash_orderable=10_000)
    execution_snapshot = main_module.ExecutionSnapshot(
        symbol="005930",
        orderable_cash=10_000,
        orderable_qty=0,
        current_price=70_000,
        expected_notional_krw=0,
    )
    current_sizing = _sizing(qty=0, reason="cash insufficient")
    next_sizing = _sizing(qty=2, reason="after rebalance")
    sell_sizing = SimpleNamespace(details={"estimated_net_proceeds_krw": 200_000})
    monkeypatch.setattr(
        buy_flow_module,
        "build_math_sizing_context",
        lambda *_a, **_k: (
            {"math_overlay": "kept"},
            {
                "max_budget_per_trade_krw": 1_000_000,
                "max_account_exposure_pct": 100.0,
                "max_qty_per_trade": 10,
            },
        ),
    )
    monkeypatch.setattr(buy_flow_module, "calculate_position_sizing", lambda *_a, **_k: next_sizing)
    monkeypatch.setattr(
        buy_flow_module,
        "estimate_rebalance_concentration_preview",
        lambda **_k: {"before": {"top1_weight_pct": 10.0}, "after": {"top1_weight_pct": 20.0}, "comment": "ok"},
    )

    preview = main_module._build_rebalance_buy_preview(
        selected_candidate=candidate,
        sell_analysis=SimpleNamespace(symbol="000660"),
        position_sizing=current_sizing,
        execution_snapshot=execution_snapshot,
        portfolio_snapshot=portfolio,
        sell_sizing=sell_sizing,
        settings=_settings(),
    )
    preview.update(
        {
            "buy_expected_total_cost_krw": 0,
            "buy_expected_cost_bps": 0.0,
            "buy_net_edge_bps": 10.0,
        }
    )
    main_module._print_rebalance_buy_preview(
        selected_candidate=candidate,
        rebalance_buy_preview=preview,
    )
    output = capsys.readouterr().out

    assert set(preview) >= {
        "estimated_cash_after_sell",
        "synthetic_execution_snapshot",
        "position_sizing",
        "math_overlay",
        "current_recommended_qty",
        "next_cycle_buyable_qty",
        "concentration_preview",
        "next_action",
        "next_reason",
    }
    assert preview["estimated_cash_after_sell"] == 210_000
    assert preview["next_cycle_buyable_qty"] == 3
    assert preview["next_action"] == "BUY"
    assert "=== 리밸런싱 후 BUY 재평가 미리보기 ===" in output
