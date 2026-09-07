from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from app import main as main_module
from app.reporting import console as console_module
from app.core.time_utils import KOREA_TZ
from app.market_data.schema import MarketSnapshot
from app.portfolio.schema import PortfolioPosition, PortfolioSnapshot
from app.scanner.service import SymbolAnalysisResult
from app.scanner import runtime_scan as runtime_scan_module


def _settings(**overrides):
    values = {
        "buy_excluded_symbols": (),
        "same_symbol_max_buys_per_day": 2,
        "rebuy_cooldown_minutes": 20,
        "stop_loss_same_day_reentry_min_minutes": 30,
        "buy_scan_profile_rotation_enabled": True,
        "buy_scan_core_fraction": 0.5,
        "buy_scan_core_max": 3,
        "buy_scan_rotating_fraction": 0.25,
        "scan_symbols_max_per_cycle": 4,
        "buy_scan_deep_eval_limit": 2,
        "buy_scan_exploration_ratio": 0.5,
        "buy_scan_shallow_top_k": 3,
        "live_snapshot_ttl_seconds": 420,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _snapshot(symbol: str = "005930", *, price: int = 80_000) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        current_price=price,
        open_price=price - 1_000,
        low_price=price - 2_000,
        prev_day_change_pct=1.2,
    )


def _position(symbol: str, qty: int) -> PortfolioPosition:
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


def _portfolio(*positions: PortfolioPosition) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        positions=tuple(positions),
        cash_total=1_000_000,
        cash_orderable=1_000_000,
        cash_next_day=1_000_000,
        total_evaluation_amount=1_000_000,
    )


def _analysis_result(
    symbol: str = "005930",
    *,
    candidate: bool = True,
    final_reason: str = "candidate",
    cost_block_reason: str | None = None,
    passes_profit_buffer: bool = True,
    should_attempt_buy: bool = True,
    score: float = 1.2,
) -> SymbolAnalysisResult:
    return SymbolAnalysisResult(
        symbol=symbol,
        name=f"Name {symbol}",
        market_snapshot=_snapshot(symbol),
        strategy_result=SimpleNamespace(should_attempt_buy=should_attempt_buy),
        passed_count=3,
        signal_quality_count=3.0,
        enabled_count=4,
        candidate=candidate,
        final_reason=final_reason,
        passed_pattern="intraday_pullback",
        score=score,
        net_profit_buffer_bps=12.5,
        passes_profit_buffer=passes_profit_buffer,
        score_components={},
        score_highlights=(),
        score_penalties=(),
        score_summary="score summary",
        expected_fee_krw=0,
        expected_tax_krw=0,
        expected_slippage_krw=0,
        expected_total_cost_krw=0,
        expected_cost_bps=0.0,
        cost_quality_score=1.0,
        expected_cost_penalty=0.0,
        net_edge_bps=10.0,
        cost_block_reason=cost_block_reason,
        feature_map={},
        feature_vector={},
        feature_summaries={},
        math_score_summary="math summary",
        mean_reversion_zscore=None,
        reversion_quality_score=None,
        overextension_penalty=None,
        ou_half_life_estimate=None,
        mean_reversion_summary=None,
        portfolio_avg_correlation=None,
        portfolio_max_correlation=None,
        variance_increase_estimate=None,
        portfolio_risk_summary=None,
        portfolio_correlation_penalty=None,
        variance_increase_penalty=None,
        hist_percentile_rank=None,
        price_velocity_pct=None,
        price_dynamics_summary=None,
        sort_key=(0, -score, symbol),
    )


def test_count_symbol_buy_entries_prefers_cached_daily_counts() -> None:
    state = {
        "buy_entries_by_symbol_today": {"005930": "2"},
        "recent_orders": [],
    }

    assert runtime_scan_module.count_symbol_buy_entries_today(state=state, symbol="005930") == 2
    assert runtime_scan_module.count_symbol_buy_entries_today(state=state, symbol="000660") == 0


def test_count_symbol_buy_entries_falls_back_to_recent_submitted_buy_orders() -> None:
    now = datetime(2026, 5, 20, 10, 0, tzinfo=KOREA_TZ)
    state = {
        "recent_orders": [
            {"date": "2026-05-20", "side": "BUY", "symbol": "005930", "action": "order_submitted"},
            {"date": "2026-05-20", "side": "BUY", "symbol": "005930", "action": "order_succeeded"},
            {"date": "2026-05-20", "side": "SELL", "symbol": "005930", "action": "order_submitted"},
            {"date": "2026-05-19", "side": "BUY", "symbol": "005930", "action": "order_submitted"},
        ],
    }

    with mock.patch.object(runtime_scan_module, "get_korean_now", return_value=now):
        assert runtime_scan_module.count_symbol_buy_entries_today(state=state, symbol="005930") == 1


def test_is_symbol_in_reentry_cooldown_uses_latest_matching_buy_order() -> None:
    now = datetime(2026, 5, 20, 10, 0, tzinfo=KOREA_TZ)
    state = {
        "recent_orders": [
            {
                "date": "2026-05-20",
                "side": "BUY",
                "symbol": "005930",
                "action": "order_submitted",
                "timestamp": (now - timedelta(minutes=40)).isoformat(),
            },
            {
                "date": "2026-05-20",
                "side": "BUY",
                "symbol": "005930",
                "action": "order_succeeded",
                "timestamp": (now - timedelta(minutes=5)).isoformat(),
            },
        ],
    }

    with mock.patch.object(runtime_scan_module, "get_korean_now", return_value=now):
        assert runtime_scan_module.is_symbol_in_reentry_cooldown(
            state=state,
            symbol="005930",
            cooldown_minutes=10,
        )
        assert not runtime_scan_module.is_symbol_in_reentry_cooldown(
            state=state,
            symbol="000660",
            cooldown_minutes=10,
        )
        assert not runtime_scan_module.is_symbol_in_reentry_cooldown(
            state=state,
            symbol="005930",
            cooldown_minutes=0,
        )


def test_resolve_sell_exit_reason_prefers_rebalance_then_cycle_then_trigger() -> None:
    analysis = SimpleNamespace(
        sell_decision=SimpleNamespace(triggered_rule_name="take_profit")
    )

    assert runtime_scan_module.resolve_sell_exit_reason(
        analysis=analysis,
        cycle_reason="manual",
        is_rebalance=True,
    ) == "rebalance_sell"
    assert runtime_scan_module.resolve_sell_exit_reason(
        analysis=analysis,
        cycle_reason="stop_loss",
        is_rebalance=False,
    ) == "stop_loss"
    assert runtime_scan_module.resolve_sell_exit_reason(
        analysis=analysis,
        cycle_reason="unmapped",
        is_rebalance=False,
    ) == "take_profit"


def test_build_buy_scan_pre_gating_blocks_entire_scan_for_daily_pnl_brake() -> None:
    pre_gating = runtime_scan_module.build_buy_scan_pre_gating(
        candidate_symbols=("005930", "000660"),
        state={"recent_market_snapshots_by_symbol": {}},
        settings=_settings(),
        portfolio_snapshot=None,
        daily_pnl_brake_state={"buy_paused": True, "pause_reason": "daily stop"},
        regime_state=None,
        mode="trade",
    )

    assert pre_gating["scan_allowed"] is False
    assert pre_gating["scan_block_reason"] == "daily stop"
    assert pre_gating["allowed_symbols"] == ()
    assert pre_gating["reason_counts"] == {"daily_pnl_brake": 2}
    assert pre_gating["early_reject_count"] == 2


def test_build_buy_scan_pre_gating_records_allowed_reentry_state() -> None:
    now = datetime(2026, 5, 20, 10, 0, tzinfo=KOREA_TZ)
    state = {"recent_market_snapshots_by_symbol": {}}

    with mock.patch.object(runtime_scan_module, "get_korean_now", return_value=now):
        pre_gating = runtime_scan_module.build_buy_scan_pre_gating(
            candidate_symbols=("005930",),
            state=state,
            settings=_settings(),
            portfolio_snapshot=None,
            daily_pnl_brake_state=None,
            regime_state=None,
            mode="trade",
        )

    assert pre_gating["allowed_symbols"] == ("005930",)
    assert pre_gating["reentry_allowed_count"] == 1
    assert pre_gating["reentry_state_by_symbol"] == {"005930": "allowed_no_recent_exit"}
    assert state["last_reentry_state_by_symbol"]["005930"] == "allowed_no_recent_exit"


def test_normalize_pre_gating_payload_handles_partial_and_malformed_rows() -> None:
    normalized = runtime_scan_module.normalize_pre_gating_payload(
        {
            "requested_count": "3",
            "allowed_count": 1,
            "early_reject_count": 2,
            "reason_counts": {"excluded_symbol": 1},
            "rejected": [
                "bad row",
                {"symbol": "", "reason_code": "ignored"},
                {
                    "symbol": "005930",
                    "reason": "BUY_EXCLUDED_SYMBOLS 설정",
                    "diagnostics": ("BUY_EXCLUDED_SYMBOLS",),
                },
            ],
            "reentry_state_by_symbol": {"005930": "blocked_churn_risk", "": "ignored"},
            "last_exit_reason_by_symbol": {"005930": "stop_loss", "000660": ""},
            "residual_position_present_by_symbol": {"005930": 1},
            "reentry_block_reason_counts": {"blocked_churn_risk": "2"},
        }
    )

    assert normalized["stage"] == "buy_pre_gating"
    assert normalized["requested_count"] == 3
    assert normalized["allowed_count"] == 1
    assert normalized["rejected_symbols"] == ["005930"]
    assert normalized["reasons_by_symbol"] == {"005930": "BUY_EXCLUDED_SYMBOLS 설정"}
    assert normalized["rejected"][0]["reentry_state"] == "BUY_EXCLUDED_SYMBOLS 설정"
    assert normalized["rejected"][0]["diagnostics"] == ["BUY_EXCLUDED_SYMBOLS"]
    assert normalized["last_exit_reason_by_symbol"] == {"005930": "stop_loss"}
    assert normalized["residual_position_present_by_symbol"] == {"005930": True}
    assert normalized["reentry_block_reason_counts"] == {"blocked_churn_risk": 2}


def test_normalize_buy_funnel_reason_maps_known_and_unknown_reasons() -> None:
    assert runtime_scan_module.normalize_buy_funnel_reason(None) is None
    assert (
        runtime_scan_module.normalize_buy_funnel_reason("BUY_EXCLUDED_SYMBOLS 설정")
        == "excluded_symbol"
    )
    assert (
        runtime_scan_module.normalize_buy_funnel_reason("동일 종목 일일 진입 횟수 제한")
        == "same_symbol_daily_limit"
    )
    assert runtime_scan_module.normalize_buy_funnel_reason("현금 부족") == "cash_insufficient"
    assert runtime_scan_module.normalize_buy_funnel_reason("completely new reason") == "other"


def test_resolve_deep_eval_rejection_reason_prefers_cost_and_signal_reasons() -> None:
    assert (
        runtime_scan_module.resolve_deep_eval_rejection_reason(
            _analysis_result(cost_block_reason="expected_cost_too_high")
        )
        == "cost_filter_blocked"
    )
    assert (
        runtime_scan_module.resolve_deep_eval_rejection_reason(
            _analysis_result(candidate=False, final_reason="score 최소 기준에 못 미쳐 제외")
        )
        == "score_below_threshold"
    )
    assert (
        runtime_scan_module.resolve_deep_eval_rejection_reason(
            _analysis_result(candidate=False, passes_profit_buffer=False)
        )
        == "profit_buffer_insufficient"
    )
    assert (
        runtime_scan_module.resolve_deep_eval_rejection_reason(
            _analysis_result(candidate=False, should_attempt_buy=False)
        )
        == "passed_count_insufficient"
    )


def test_resolve_buy_candidate_rejection_reason_uses_pre_gate_then_final_action_then_deep_eval() -> None:
    assert (
        runtime_scan_module.resolve_buy_candidate_rejection_reason(
            symbol="005930",
            pre_gating={"reasons_by_symbol": {"005930": "untradable_today"}},
            layer_selected=True,
            raw_result=None,
            guarded_result=None,
            selected_symbol=None,
            final_action=None,
            final_reason=None,
        )
        == "untradable_symbol"
    )
    assert (
        runtime_scan_module.resolve_buy_candidate_rejection_reason(
            symbol="005930",
            pre_gating={"scan_allowed": False, "scan_block_reason": "daily pnl brake"},
            layer_selected=True,
            raw_result=None,
            guarded_result=None,
            selected_symbol=None,
            final_action=None,
            final_reason=None,
        )
        == "daily_pnl_brake"
    )
    assert (
        runtime_scan_module.resolve_buy_candidate_rejection_reason(
            symbol="005930",
            pre_gating={},
            layer_selected=True,
            raw_result=_analysis_result(candidate=False, passes_profit_buffer=False),
            guarded_result=None,
            selected_symbol="005930",
            final_action="buy_skipped",
            final_reason="현금 부족",
        )
        == "cash_insufficient"
    )
    assert (
        runtime_scan_module.resolve_buy_candidate_rejection_reason(
            symbol="005930",
            pre_gating={},
            layer_selected=True,
            raw_result=_analysis_result(candidate=False, passes_profit_buffer=False),
            guarded_result=None,
            selected_symbol=None,
            final_action=None,
            final_reason=None,
        )
        == "profit_buffer_insufficient"
    )


def test_resolve_buy_candidate_selection_outcome_covers_stage_transitions() -> None:
    raw_reject = _analysis_result(candidate=False)
    raw_candidate = _analysis_result(candidate=True)

    assert runtime_scan_module.resolve_buy_candidate_selection_outcome(
        stage_reached="deep_eval",
        raw_result=raw_candidate,
        guarded_result=raw_candidate,
        final_candidate=True,
        executed=True,
    ) == "executed"
    assert runtime_scan_module.resolve_buy_candidate_selection_outcome(
        stage_reached="pre_gate_rejected",
        raw_result=None,
        guarded_result=None,
        final_candidate=False,
        executed=False,
    ) == "pre_gate_blocked"
    assert runtime_scan_module.resolve_buy_candidate_selection_outcome(
        stage_reached="shallow_selected",
        raw_result=None,
        guarded_result=None,
        final_candidate=False,
        executed=False,
    ) == "shortlisted_not_deep_evaluated"
    assert runtime_scan_module.resolve_buy_candidate_selection_outcome(
        stage_reached="deep_eval",
        raw_result=raw_reject,
        guarded_result=None,
        final_candidate=False,
        executed=False,
    ) == "deep_eval_rejected"
    assert runtime_scan_module.resolve_buy_candidate_selection_outcome(
        stage_reached="deep_eval",
        raw_result=raw_candidate,
        guarded_result=raw_candidate,
        final_candidate=False,
        executed=False,
    ) == "not_selected_after_deep_eval"


def test_select_buy_scan_profile_rotates_and_records_state() -> None:
    state = {"buy_scan_profile_cursor": 2}

    first = runtime_scan_module.select_buy_scan_profile(
        state,
        _settings(buy_scan_profile_rotation_enabled=True),
    )
    second = runtime_scan_module.select_buy_scan_profile(
        state,
        _settings(buy_scan_profile_rotation_enabled=False),
    )

    assert first == {
        "profile": "recovery",
        "cursor_before": 2,
        "cursor_after": 0,
        "rotation_enabled": True,
    }
    assert second == {
        "profile": "momentum",
        "cursor_before": 0,
        "cursor_after": 0,
        "rotation_enabled": False,
    }
    assert state["buy_scan_last_profile"] == "momentum"


def test_build_buy_scan_layered_universe_splits_layers_and_advances_cursors() -> None:
    state = {
        "buy_scan_core_cursor": 1,
        "buy_scan_rotating_cursor": 0,
        "buy_scan_exploration_cursor": 0,
    }

    universe = runtime_scan_module.build_buy_scan_layered_universe(
        state=state,
        settings=_settings(
            buy_scan_core_fraction=0.5,
            buy_scan_core_max=3,
            buy_scan_rotating_fraction=0.25,
            scan_symbols_max_per_cycle=4,
            buy_scan_profile_rotation_enabled=True,
        ),
        raw_symbols=("A", "B", "C", "D", "E", "F"),
        excluded_symbols=("D",),
    )

    assert universe["core_symbols"] == ("A", "B", "C")
    assert universe["rotating_symbols"] == ("E",)
    assert universe["exploration_symbols"] == ("F",)
    assert universe["selected_symbols"] == ("B", "C", "E", "F")
    assert universe["layer_by_symbol"] == {
        "B": "core",
        "C": "core",
        "E": "rotating",
        "F": "exploration",
    }
    assert universe["excluded_symbols"] == ("D",)
    assert state["buy_scan_core_cursor"] == 0
    assert state["buy_scan_exploration_cursor"] == 0


def test_build_buy_scan_shallow_plan_returns_shortlist_and_cap_metadata() -> None:
    now = datetime.now(timezone.utc)
    state = {
        "recent_market_snapshots_by_symbol": {
            "005930": {
                "current_price": 80_000,
                "open_price": 79_000,
                "low_price": 78_000,
                "prev_day_change_pct": 1.2,
                "observed_at": now.isoformat(),
            },
            "000660": {
                "current_price": 100_000,
                "open_price": 99_000,
                "low_price": 98_000,
                "prev_day_change_pct": 0.7,
                "observed_at": now.isoformat(),
            },
            "035420": {
                "current_price": 180_000,
                "open_price": 181_000,
                "low_price": 178_000,
                "prev_day_change_pct": -0.3,
                "observed_at": now.isoformat(),
            },
        }
    }

    plan = runtime_scan_module.build_buy_scan_shallow_plan(
        state=state,
        settings=_settings(
            buy_scan_shallow_top_k=2,
            buy_scan_deep_eval_limit=2,
            buy_scan_exploration_ratio=0.5,
        ),
        profile="momentum",
        symbols=("005930", "000660", "035420"),
        layer_by_symbol={"005930": "core", "000660": "rotating", "035420": "exploration"},
    )

    assert plan["ranked_count"] == 2
    assert plan["deep_eval_limit"] == 2
    assert len(plan["shortlist_symbols"]) == 2
    assert set(plan["selected_layers"]).issubset({"005930", "000660", "035420"})
    assert plan["shallow_cap_applied"] is True
    assert plan["shallow_cap_original_count"] == 3
    assert plan["shallow_cap_limit"] == 2


def test_build_buy_scan_deep_eval_symbols_promotes_rescued_core_symbol() -> None:
    assert runtime_scan_module.build_buy_scan_deep_eval_symbols(
        shallow_plan={
            "shortlist_symbols": ("000660", "005930", "035420"),
            "core_rescue_applied": True,
            "core_rescue_selected_symbol": "005930",
        }
    ) == ("005930", "000660", "035420")
    assert runtime_scan_module.build_buy_scan_deep_eval_symbols(
        shallow_plan={"shortlist_symbols": ("000660", "035420")}
    ) == ("000660", "035420")


def test_apply_buy_runtime_guards_replaces_blocked_candidate_and_records_state() -> None:
    state = {}
    before = (
        _analysis_result("005930", candidate=True),
        _analysis_result("000660", candidate=False, final_reason="already rejected"),
    )

    guarded = runtime_scan_module.apply_buy_runtime_guards_to_scan_results(
        before,
        state=state,
        settings=_settings(),
        portfolio_snapshot=_portfolio(_position("005930", 3)),
        regime_state=None,
        daily_pnl_brake_state=None,
    )

    assert guarded[0] is not before[0]
    assert guarded[0].candidate is False
    assert "잔여 포지션" in guarded[0].final_reason
    assert guarded[1] is before[1]
    assert state["last_reentry_state_by_symbol"]["005930"] == "blocked_residual_position"


def test_print_buy_pre_gating_summary_outputs_key_markers_and_logs_event(capsys) -> None:
    pre_gating = {
        "scan_allowed": True,
        "requested_symbols": ("005930", "000660"),
        "allowed_symbols": ("005930",),
        "rejected": [{"symbol": "000660", "reason_code": "untradable_today"}],
        "reason_counts": {"untradable_today": 1},
    }

    with mock.patch.object(console_module, "_log_engine_event") as log_event:
        main_module._print_buy_pre_gating_summary(
            pre_gating=pre_gating,
            cycle_id="cycle-1",
            market_open=True,
            market_session="regular",
        )

    output = capsys.readouterr().out
    assert "BUY 사전 게이트" in output
    assert "requested=2 | allowed=1 | early_rejected=1" in output
    assert "untradable_today=1" in output
    log_event.assert_called_once()


def test_print_buy_scan_stage_summary_outputs_layer_and_shortlist_markers(capsys) -> None:
    runtime_scan_module.print_buy_scan_stage_summary(
        profile_state={"profile": "momentum", "rotation_enabled": True},
        layered_universe={
            "core_count": 3,
            "selected_core_count": 2,
            "rotating_count": 2,
            "selected_rotating_count": 1,
            "exploration_count": 1,
            "selected_exploration_count": 1,
        },
        pre_gating={"requested_count": 4, "allowed_count": 3, "early_reject_count": 1},
        shallow_plan={
            "ranked_count": 3,
            "shortlist_symbols": ("005930", "000660"),
            "deep_eval_limit": 2,
            "exploration_quota_used": 1,
            "shortlist_preview": [
                {"symbol": "005930", "layer": "core", "shallow_score": 1.23}
            ],
            "core_rescue_applied": True,
            "core_rescue_selected_symbol": "005930",
            "core_rescue_replaced_symbol": "035420",
            "core_rescue_selected_score": 1.23,
            "deep_eval_symbols": ("005930", "000660"),
        },
    )

    output = capsys.readouterr().out
    assert "BUY staged scan" in output
    assert "profile=momentum" in output
    assert "layers: core=3" in output
    assert "shortlist preview" in output
    assert "core shortlist rescue" in output


def test_print_buy_runtime_filter_summary_outputs_exclusions_and_logs_block(capsys) -> None:
    before = (_analysis_result("005930", candidate=True),)
    after = (
        _analysis_result(
            "005930",
            candidate=False,
            final_reason="동일 종목 일일 진입 횟수 제한",
        ),
    )

    with mock.patch.object(console_module, "_log_engine_event") as log_event:
        main_module._print_buy_runtime_filter_summary(
            before_results=before,
            after_results=after,
            cycle_id="cycle-1",
            market_open=True,
            market_session="regular",
        )

    output = capsys.readouterr().out
    assert "유니버스 필터" in output
    assert "필터 적용 전 후보 수: 1" in output
    assert "필터 적용 후 후보 수: 0" in output
    assert "제외 종목" in output
    log_event.assert_called_once()


# --- Stage 2e-1 extraction contract ---


def test_build_buy_scan_pre_gating_runtime_scan_records_allowed_reentry_state() -> None:
    now = datetime(2026, 5, 20, 10, 0, tzinfo=KOREA_TZ)
    state = {"recent_market_snapshots_by_symbol": {}}
    with mock.patch.object(runtime_scan_module, "get_korean_now", return_value=now):
        pre_gating = runtime_scan_module.build_buy_scan_pre_gating(
            candidate_symbols=("005930",),
            state=state,
            settings=_settings(),
            portfolio_snapshot=None,
            daily_pnl_brake_state=None,
            regime_state=None,
            mode="trade",
        )
    assert pre_gating["allowed_symbols"] == ("005930",)
    assert pre_gating["reentry_allowed_count"] == 1
    assert pre_gating["reentry_state_by_symbol"] == {"005930": "allowed_no_recent_exit"}
    assert state["last_reentry_state_by_symbol"]["005930"] == "allowed_no_recent_exit"


def test_build_buy_scan_pre_gating_runtime_scan_rejects_excluded_and_untradable_symbols() -> None:
    state = {
        "buy_untradable_symbols_today": ["123456"],
        "recent_market_snapshots_by_symbol": {},
    }
    pre_gating = runtime_scan_module.build_buy_scan_pre_gating(
        candidate_symbols=("252710", "123456"),
        state=state,
        settings=_settings(buy_excluded_symbols=("252710",)),
        portfolio_snapshot=None,
        daily_pnl_brake_state=None,
        regime_state=None,
        mode="trade",
    )
    assert pre_gating["allowed_symbols"] == ()
    assert pre_gating["reason_counts"] == {"excluded_symbol": 1, "untradable_today": 1}


def test_build_buy_scan_pre_gating_runtime_scan_blocks_for_daily_pnl_brake() -> None:
    pre_gating = runtime_scan_module.build_buy_scan_pre_gating(
        candidate_symbols=("005930", "000660"),
        state={"recent_market_snapshots_by_symbol": {}},
        settings=_settings(),
        portfolio_snapshot=None,
        daily_pnl_brake_state={"buy_paused": True, "pause_reason": "daily stop"},
        regime_state=None,
        mode="trade",
    )
    assert pre_gating["scan_allowed"] is False
    assert pre_gating["scan_block_reason"] == "daily stop"
    assert pre_gating["allowed_symbols"] == ()
    assert pre_gating["reason_counts"] == {"daily_pnl_brake": 2}
    assert pre_gating["early_reject_count"] == 2


def test_normalize_pre_gating_payload_runtime_scan_handles_partial_and_malformed_rows() -> None:
    normalized = runtime_scan_module.normalize_pre_gating_payload(
        {
            "requested_count": "3",
            "allowed_count": 1,
            "early_reject_count": 2,
            "reason_counts": {"excluded_symbol": 1},
            "rejected": [
                "bad row",
                {"symbol": "", "reason_code": "ignored"},
                {
                    "symbol": "005930",
                    "reason": "BUY_EXCLUDED_SYMBOLS 설정",
                    "diagnostics": ("BUY_EXCLUDED_SYMBOLS",),
                },
            ],
            "reentry_state_by_symbol": {"005930": "blocked_churn_risk", "": "ignored"},
            "last_exit_reason_by_symbol": {"005930": "stop_loss", "000660": ""},
            "residual_position_present_by_symbol": {"005930": 1},
            "reentry_block_reason_counts": {"blocked_churn_risk": "2"},
        }
    )
    assert normalized["stage"] == "buy_pre_gating"
    assert normalized["requested_count"] == 3
    assert normalized["allowed_count"] == 1
    assert normalized["rejected_symbols"] == ["005930"]
    assert normalized["reasons_by_symbol"] == {"005930": "BUY_EXCLUDED_SYMBOLS 설정"}
    assert normalized["rejected"][0]["reentry_state"] == "BUY_EXCLUDED_SYMBOLS 설정"
    assert normalized["rejected"][0]["diagnostics"] == ["BUY_EXCLUDED_SYMBOLS"]
    assert normalized["last_exit_reason_by_symbol"] == {"005930": "stop_loss"}
    assert normalized["residual_position_present_by_symbol"] == {"005930": True}
    assert normalized["reentry_block_reason_counts"] == {"blocked_churn_risk": 2}


def test_is_symbol_in_reentry_cooldown_runtime_scan_uses_latest_matching_buy_order() -> None:
    now = datetime(2026, 5, 20, 10, 0, tzinfo=KOREA_TZ)
    state = {
        "recent_orders": [
            {
                "date": "2026-05-20", "side": "BUY", "symbol": "005930",
                "action": "order_submitted",
                "timestamp": (now - timedelta(minutes=40)).isoformat(),
            },
            {
                "date": "2026-05-20", "side": "BUY", "symbol": "005930",
                "action": "order_succeeded",
                "timestamp": (now - timedelta(minutes=5)).isoformat(),
            },
        ],
    }
    with mock.patch.object(runtime_scan_module, "get_korean_now", return_value=now):
        assert runtime_scan_module.is_symbol_in_reentry_cooldown(
            state=state, symbol="005930", cooldown_minutes=10
        )
        assert not runtime_scan_module.is_symbol_in_reentry_cooldown(
            state=state, symbol="000660", cooldown_minutes=10
        )
        assert not runtime_scan_module.is_symbol_in_reentry_cooldown(
            state=state, symbol="005930", cooldown_minutes=0
        )


def test_count_symbol_buy_entries_today_runtime_scan_prefers_cached_then_orders() -> None:
    # cached path
    state = {"buy_entries_by_symbol_today": {"005930": "2"}, "recent_orders": []}
    assert runtime_scan_module.count_symbol_buy_entries_today(state=state, symbol="005930") == 2
    assert runtime_scan_module.count_symbol_buy_entries_today(state=state, symbol="000660") == 0
    # fallback path: count today's submitted BUY orders
    now = datetime(2026, 5, 20, 10, 0, tzinfo=KOREA_TZ)
    state2 = {
        "recent_orders": [
            {"date": "2026-05-20", "side": "BUY", "symbol": "005930", "action": "order_submitted"},
            {"date": "2026-05-20", "side": "BUY", "symbol": "005930", "action": "order_succeeded"},
            {"date": "2026-05-20", "side": "SELL", "symbol": "005930", "action": "order_submitted"},
            {"date": "2026-05-19", "side": "BUY", "symbol": "005930", "action": "order_submitted"},
        ],
    }
    with mock.patch.object(runtime_scan_module, "get_korean_now", return_value=now):
        assert runtime_scan_module.count_symbol_buy_entries_today(state=state2, symbol="005930") == 1


def test_resolve_buy_candidate_rejection_reason_runtime_scan_pre_gate_then_action_then_deep_eval() -> None:
    fn = runtime_scan_module.resolve_buy_candidate_rejection_reason
    # pre-gating reason takes priority
    assert fn(
        symbol="005930",
        pre_gating={"reasons_by_symbol": {"005930": "untradable_today"}},
        layer_selected=True, raw_result=None, guarded_result=None,
        selected_symbol=None, final_action=None, final_reason=None,
    ) == "untradable_symbol"
    # scan blocked at pre-gate level
    assert fn(
        symbol="005930",
        pre_gating={"scan_allowed": False, "scan_block_reason": "daily pnl brake"},
        layer_selected=True, raw_result=None, guarded_result=None,
        selected_symbol=None, final_action=None, final_reason=None,
    ) == "daily_pnl_brake"
    # action reason (현금 부족) for the selected symbol
    assert fn(
        symbol="005930",
        pre_gating={},
        layer_selected=True,
        raw_result=_analysis_result(candidate=False, passes_profit_buffer=False),
        guarded_result=None,
        selected_symbol="005930",
        final_action="buy_skipped",
        final_reason="현금 부족",
    ) == "cash_insufficient"
    # deep eval rejection when not selected
    assert fn(
        symbol="005930",
        pre_gating={},
        layer_selected=True,
        raw_result=_analysis_result(candidate=False, passes_profit_buffer=False),
        guarded_result=None,
        selected_symbol=None, final_action=None, final_reason=None,
    ) == "profit_buffer_insufficient"


def test_resolve_deep_eval_rejection_reason_runtime_scan_prefers_cost_and_signal() -> None:
    fn = runtime_scan_module.resolve_deep_eval_rejection_reason
    assert fn(None) is None
    assert fn(_analysis_result(cost_block_reason="expected_cost_too_high")) == "cost_filter_blocked"
    assert fn(_analysis_result(candidate=False, final_reason="score 최소 기준에 못 미쳐 제외")) == "score_below_threshold"
    assert fn(_analysis_result(candidate=False, passes_profit_buffer=False)) == "profit_buffer_insufficient"
    assert fn(_analysis_result(candidate=False, should_attempt_buy=False)) == "passed_count_insufficient"
    assert fn(_analysis_result(candidate=True)) is None


def test_resolve_buy_candidate_selection_outcome_runtime_scan_covers_stage_transitions() -> None:
    raw_reject = _analysis_result(candidate=False)
    raw_candidate = _analysis_result(candidate=True)
    fn = runtime_scan_module.resolve_buy_candidate_selection_outcome
    assert fn(stage_reached="deep_eval", raw_result=raw_candidate, guarded_result=raw_candidate, final_candidate=True, executed=True) == "executed"
    assert fn(stage_reached="pre_gate_rejected", raw_result=None, guarded_result=None, final_candidate=False, executed=False) == "pre_gate_blocked"
    assert fn(stage_reached="shallow_selected", raw_result=None, guarded_result=None, final_candidate=False, executed=False) == "shortlisted_not_deep_evaluated"
    assert fn(stage_reached="deep_eval", raw_result=raw_reject, guarded_result=None, final_candidate=False, executed=False) == "deep_eval_rejected"
    assert fn(stage_reached="deep_eval", raw_result=raw_candidate, guarded_result=raw_candidate, final_candidate=False, executed=False) == "not_selected_after_deep_eval"


def test_resolve_sell_exit_reason_runtime_scan_prefers_rebalance_then_cycle_then_trigger() -> None:
    analysis = SimpleNamespace(
        sell_decision=SimpleNamespace(triggered_rule_name="take_profit")
    )
    assert runtime_scan_module.resolve_sell_exit_reason(
        analysis=analysis, cycle_reason="manual", is_rebalance=True
    ) == "rebalance_sell"
    assert runtime_scan_module.resolve_sell_exit_reason(
        analysis=analysis, cycle_reason="stop_loss", is_rebalance=False
    ) == "stop_loss"
    assert runtime_scan_module.resolve_sell_exit_reason(
        analysis=analysis, cycle_reason="unmapped", is_rebalance=False
    ) == "take_profit"


def test_normalize_buy_funnel_reason_runtime_scan_covers_all_code_paths() -> None:
    fn = runtime_scan_module.normalize_buy_funnel_reason
    # None/empty → None
    assert fn(None) is None
    assert fn("") is None
    # stop-loss reentry block
    assert fn("blocked_same_day_stop_loss_reentry") == "same_day_stop_loss_reentry_blocked"
    assert fn("same_day_stop_loss_reentry") == "same_day_stop_loss_reentry_blocked"
    # residual position
    assert fn("blocked_residual_position") == "residual_position_present"
    assert fn("residual_position") == "residual_position_present"
    # excluded symbol
    assert fn("BUY_EXCLUDED_SYMBOLS 설정") == "excluded_symbol"
    assert fn("excluded_symbol") == "excluded_symbol"
    # untradable
    assert fn("untradable_today") == "untradable_symbol"
    assert fn("매매불가") == "untradable_symbol"
    # recent stop-loss
    assert fn("blocked_recent_stop_loss") == "recent_stop_loss_no_fresh_setup"
    # churn risk / setup not refreshed
    assert fn("blocked_churn_risk") == "profit_exit_but_setup_not_refreshed"
    # hard guard / trade mode block
    assert fn("blocked_hard_guard") == "trade_mode_block"
    assert fn("trade_mode") == "trade_mode_block"
    assert fn("scan_only") == "trade_mode_block"
    # cooldown
    assert fn("reentry_cooldown") == "cooldown"
    assert fn("cooldown_xyz") == "cooldown"
    # already holding
    assert fn("already_holding") == "already_holding"
    assert fn("이미 보유") == "already_holding"
    # same-symbol daily limit
    assert fn("same_symbol_daily_limit") == "same_symbol_daily_limit"
    assert fn("동일 종목 일일 진입 횟수 제한") == "same_symbol_daily_limit"
    # daily pnl brake
    assert fn("daily_pnl") == "daily_pnl_brake"
    assert fn("손실 브레이크") == "daily_pnl_brake"
    # cash insufficient
    assert fn("cash_insufficient") == "cash_insufficient"
    assert fn("현금 부족") == "cash_insufficient"
    # trade budget limited
    assert fn("trade_budget_limited") == "trade_budget_limited"
    assert fn("예산") == "trade_budget_limited"
    # exposure limited
    assert fn("exposure_limited") == "exposure_limited"
    assert fn("노출") == "exposure_limited"
    # cost veto
    assert fn("expected_cost_too_high") == "cost_veto"
    assert fn("cost_veto") == "cost_veto"
    # net edge veto
    assert fn("net_edge_too_low") == "net_edge_veto"
    assert fn("net edge below") == "net_edge_veto"
    # portfolio fit
    assert fn("포트폴리오") == "portfolio_fit_veto"
    # strategy rejected
    assert fn("전략") == "strategy_rejected"
    assert fn("signal rejected") == "strategy_rejected"
    # completely unknown → other
    assert fn("completely new unknown reason") == "other"


def test_runtime_scan_module_exports_pre_gating_and_normalizer_api() -> None:
    """All stage 2e-1 and 2e-2 helpers must be importable from app.scanner.runtime_scan."""
    assert callable(runtime_scan_module.count_symbol_buy_entries_today)
    assert callable(runtime_scan_module.is_symbol_in_reentry_cooldown)
    assert callable(runtime_scan_module.resolve_sell_exit_reason)
    assert callable(runtime_scan_module.build_buy_scan_pre_gating)
    assert callable(runtime_scan_module.normalize_pre_gating_payload)
    assert callable(runtime_scan_module.normalize_buy_funnel_reason)
    assert callable(runtime_scan_module.resolve_deep_eval_rejection_reason)
    assert callable(runtime_scan_module.resolve_buy_candidate_rejection_reason)
    assert callable(runtime_scan_module.resolve_buy_candidate_selection_outcome)
    # Stage 2e-2
    assert callable(runtime_scan_module.select_buy_scan_profile)
    assert callable(runtime_scan_module.build_buy_scan_layered_universe)
    assert callable(runtime_scan_module.build_buy_scan_shallow_plan)
    assert callable(runtime_scan_module.build_buy_scan_deep_eval_symbols)
    assert callable(runtime_scan_module.cap_buy_scan_deep_eval_symbols_for_api_budget)


# Stage 2e-2 extraction: direct module-level contract tests


def test_select_buy_scan_profile_direct_from_runtime_scan_module() -> None:
    """select_buy_scan_profile must behave identically when called via runtime_scan module."""
    state: dict = {"buy_scan_profile_cursor": 2}
    result = runtime_scan_module.select_buy_scan_profile(
        state,
        _settings(buy_scan_profile_rotation_enabled=True),
    )
    assert result == {
        "profile": "recovery",
        "cursor_before": 2,
        "cursor_after": 0,
        "rotation_enabled": True,
    }
    assert state["buy_scan_last_profile"] == "recovery"


def test_build_buy_scan_layered_universe_direct_from_runtime_scan_module() -> None:
    """build_buy_scan_layered_universe must behave identically when called via runtime_scan."""
    state: dict = {
        "buy_scan_core_cursor": 1,
        "buy_scan_rotating_cursor": 0,
        "buy_scan_exploration_cursor": 0,
    }
    universe = runtime_scan_module.build_buy_scan_layered_universe(
        state=state,
        settings=_settings(
            buy_scan_core_fraction=0.5,
            buy_scan_core_max=3,
            buy_scan_rotating_fraction=0.25,
            scan_symbols_max_per_cycle=4,
            buy_scan_profile_rotation_enabled=True,
        ),
        raw_symbols=("A", "B", "C", "D", "E", "F"),
        excluded_symbols=("D",),
    )
    assert universe["core_symbols"] == ("A", "B", "C")
    assert universe["rotating_symbols"] == ("E",)
    assert universe["exploration_symbols"] == ("F",)
    assert universe["selected_symbols"] == ("B", "C", "E", "F")
    assert state["buy_scan_core_cursor"] == 0


def test_build_buy_scan_shallow_plan_direct_from_runtime_scan_module() -> None:
    """build_buy_scan_shallow_plan must behave identically when called via runtime_scan."""
    now = datetime.now(timezone.utc)
    state = {
        "recent_market_snapshots_by_symbol": {
            "005930": {
                "current_price": 80_000,
                "open_price": 79_000,
                "low_price": 78_000,
                "prev_day_change_pct": 1.2,
                "observed_at": now.isoformat(),
            },
            "000660": {
                "current_price": 100_000,
                "open_price": 99_000,
                "low_price": 98_000,
                "prev_day_change_pct": 0.7,
                "observed_at": now.isoformat(),
            },
            "035420": {
                "current_price": 180_000,
                "open_price": 181_000,
                "low_price": 178_000,
                "prev_day_change_pct": -0.3,
                "observed_at": now.isoformat(),
            },
        }
    }
    plan = runtime_scan_module.build_buy_scan_shallow_plan(
        state=state,
        settings=_settings(buy_scan_shallow_top_k=2, buy_scan_deep_eval_limit=2, buy_scan_exploration_ratio=0.5),
        profile="momentum",
        symbols=("005930", "000660", "035420"),
        layer_by_symbol={"005930": "core", "000660": "rotating", "035420": "exploration"},
    )
    assert plan["ranked_count"] == 2
    assert plan["shallow_cap_applied"] is True
    assert plan["shallow_cap_original_count"] == 3
    assert "shortlist_symbols" in plan
    assert "deep_eval_limit" in plan
    assert "profile" in plan
    assert "candidates" in plan
    assert "shallow_cap_limit" in plan
    assert plan["shallow_cap_limit"] == 2
    assert plan["profile"] == "momentum"
    assert len(plan["shortlist_symbols"]) == 2
    assert plan["deep_eval_limit"] == 2
    assert "exploration_quota_target" in plan
    assert "exploration_quota_used" in plan
    assert "shortlist_preview" in plan
    assert "core_rescue_applied" in plan
    assert "selected_layers" in plan
    assert plan["exploration_quota_target"] >= 0
    assert isinstance(plan["core_rescue_applied"], bool)
    assert isinstance(plan["selected_layers"], dict)


def test_build_buy_scan_deep_eval_symbols_direct_from_runtime_scan_module() -> None:
    """build_buy_scan_deep_eval_symbols must promote rescued core symbol to front."""
    result = runtime_scan_module.build_buy_scan_deep_eval_symbols(
        shallow_plan={
            "shortlist_symbols": ("000660", "005930", "035420"),
            "core_rescue_applied": True,
            "core_rescue_selected_symbol": "005930",
        }
    )
    assert result == ("005930", "000660", "035420")
    result2 = runtime_scan_module.build_buy_scan_deep_eval_symbols(
        shallow_plan={"shortlist_symbols": ("000660", "035420")}
    )
    assert result2 == ("000660", "035420")


def test_cap_buy_scan_deep_eval_symbols_for_api_budget_direct_from_runtime_scan_module() -> None:
    """cap_buy_scan_deep_eval_symbols_for_api_budget must cap by quote budget."""
    capped, metadata = runtime_scan_module.cap_buy_scan_deep_eval_symbols_for_api_budget(
        symbols=("005930", "000660", "035420", "051910"),
        api_budget_state={
            "quotes_used_this_tick": 3,
            "soft_max_quotes_per_tick": 5,
            "recent_requests": [],
            "soft_max_requests_per_second": 5,
        },
        now=datetime(2026, 4, 25, 10, 0, 0),
    )
    assert capped == ("005930", "000660")
    assert metadata["budget_cap_applied"] is True
    assert metadata["budget_cap_reasons"] == ["quote_budget"]
    assert metadata["budget_capped_count"] == 2


