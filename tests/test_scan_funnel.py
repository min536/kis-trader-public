from __future__ import annotations

from types import SimpleNamespace

import app.scanner.runtime_scan as origin
from app.market_data.schema import MarketSnapshot
from app.scanner import scan_funnel
from app.strategy.sell_decision import SellAnalysisResult, SellDecisionResult


# --- Pin tests: facade re-exports must be the same object as scan_funnel's ---


def test_pin_resolve_sell_exit_reason() -> None:
    assert getattr(origin, "resolve_sell_exit_reason") is getattr(
        scan_funnel, "resolve_sell_exit_reason"
    )


def test_pin_print_buy_pre_gating_summary() -> None:
    assert getattr(origin, "print_buy_pre_gating_summary") is getattr(
        scan_funnel, "print_buy_pre_gating_summary"
    )


def test_pin_normalize_pre_gating_payload() -> None:
    assert getattr(origin, "normalize_pre_gating_payload") is getattr(
        scan_funnel, "normalize_pre_gating_payload"
    )


def test_pin_normalize_buy_funnel_reason() -> None:
    assert getattr(origin, "normalize_buy_funnel_reason") is getattr(
        scan_funnel, "normalize_buy_funnel_reason"
    )


def test_pin_resolve_deep_eval_rejection_reason() -> None:
    assert getattr(origin, "resolve_deep_eval_rejection_reason") is getattr(
        scan_funnel, "resolve_deep_eval_rejection_reason"
    )


def test_pin_resolve_buy_candidate_rejection_reason() -> None:
    assert getattr(origin, "resolve_buy_candidate_rejection_reason") is getattr(
        scan_funnel, "resolve_buy_candidate_rejection_reason"
    )


def test_pin_resolve_buy_candidate_selection_outcome() -> None:
    assert getattr(origin, "resolve_buy_candidate_selection_outcome") is getattr(
        scan_funnel, "resolve_buy_candidate_selection_outcome"
    )


def test_pin_print_buy_scan_stage_summary() -> None:
    assert getattr(origin, "print_buy_scan_stage_summary") is getattr(
        scan_funnel, "print_buy_scan_stage_summary"
    )


def test_pin_print_buy_runtime_filter_summary() -> None:
    assert getattr(origin, "print_buy_runtime_filter_summary") is getattr(
        scan_funnel, "print_buy_runtime_filter_summary"
    )


# --- normalize_buy_funnel_reason: every branch (input -> output literal) ---


def test_normalize_buy_funnel_reason_covers_all_branches() -> None:
    fn = scan_funnel.normalize_buy_funnel_reason
    assert fn(None) is None
    assert fn("") is None
    assert fn("blocked_same_day_stop_loss_reentry") == "same_day_stop_loss_reentry_blocked"
    assert fn("same_day_stop_loss_reentry") == "same_day_stop_loss_reentry_blocked"
    assert fn("blocked_residual_position") == "residual_position_present"
    assert fn("residual_position") == "residual_position_present"
    assert fn("BUY_EXCLUDED_SYMBOLS 설정") == "excluded_symbol"
    assert fn("excluded_symbol") == "excluded_symbol"
    assert fn("buy 제외") == "excluded_symbol"
    assert fn("untradable_today") == "untradable_symbol"
    assert fn("untradable_symbol") == "untradable_symbol"
    assert fn("매매불가") == "untradable_symbol"
    assert fn("blocked_recent_stop_loss") == "recent_stop_loss_no_fresh_setup"
    assert fn("recent_stop_loss") == "recent_stop_loss_no_fresh_setup"
    assert fn("blocked_churn_risk") == "profit_exit_but_setup_not_refreshed"
    assert fn("fresh setup") == "profit_exit_but_setup_not_refreshed"
    assert fn("새로워 보이지") == "profit_exit_but_setup_not_refreshed"
    assert fn("blocked_hard_guard") == "trade_mode_block"
    assert fn("trade_mode") == "trade_mode_block"
    assert fn("scan_only") == "trade_mode_block"
    assert fn("reentry_cooldown") == "cooldown"
    assert fn("cooldown") == "cooldown"
    assert fn("cooldown_xyz") == "cooldown"
    assert fn("already_holding") == "already_holding"
    assert fn("already holding") == "already_holding"
    assert fn("이미 보유") == "already_holding"
    assert fn("same_symbol_daily_limit") == "same_symbol_daily_limit"
    assert fn("동일 종목 일일 진입 횟수 제한") == "same_symbol_daily_limit"
    assert fn("daily_pnl") == "daily_pnl_brake"
    assert fn("daily pnl") == "daily_pnl_brake"
    assert fn("손실 브레이크") == "daily_pnl_brake"
    assert fn("cash_insufficient") == "cash_insufficient"
    assert fn("현금 부족") == "cash_insufficient"
    assert fn("trade_budget_limited") == "trade_budget_limited"
    assert fn("예산") == "trade_budget_limited"
    assert fn("trade budget") == "trade_budget_limited"
    assert fn("exposure_limited") == "exposure_limited"
    assert fn("노출") == "exposure_limited"
    assert fn("exposure") == "exposure_limited"
    assert fn("expected_cost_too_high") == "cost_veto"
    assert fn("cost_veto") == "cost_veto"
    assert fn("cost") == "cost_veto"
    assert fn("net_edge_too_low") == "net_edge_veto"
    assert fn("net_edge_veto") == "net_edge_veto"
    assert fn("net edge below") == "net_edge_veto"
    assert fn("포트폴리오") == "portfolio_fit_veto"
    assert fn("portfolio") == "portfolio_fit_veto"
    assert fn("전략") == "strategy_rejected"
    assert fn("signal rejected") == "strategy_rejected"
    assert fn("rejected") == "strategy_rejected"
    assert fn("completely new unknown reason") == "other"


# --- resolve_deep_eval_rejection_reason: literal cases ---


def _result(
    *,
    candidate: bool = False,
    cost_block_reason: str | None = None,
    final_reason: str = "",
    passes_profit_buffer: bool = True,
    should_attempt_buy: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        candidate=candidate,
        cost_block_reason=cost_block_reason,
        final_reason=final_reason,
        passes_profit_buffer=passes_profit_buffer,
        strategy_result=SimpleNamespace(should_attempt_buy=should_attempt_buy),
    )


def test_resolve_deep_eval_rejection_reason_literal_cases() -> None:
    fn = scan_funnel.resolve_deep_eval_rejection_reason
    assert fn(None) is None
    assert fn(_result(cost_block_reason="expected_cost_too_high")) == "cost_filter_blocked"
    assert fn(_result(cost_block_reason="net_edge_too_low")) == "cost_filter_blocked"
    assert fn(_result(candidate=True)) is None
    assert (
        fn(_result(final_reason="score 최소 기준에 못 미쳐 제외")) == "score_below_threshold"
    )
    assert fn(_result(passes_profit_buffer=False)) == "profit_buffer_insufficient"
    assert fn(_result(should_attempt_buy=False)) == "passed_count_insufficient"
    assert fn(_result()) is None


# --- resolve_buy_candidate_rejection_reason: literal cases ---


def test_resolve_buy_candidate_rejection_reason_literal_cases() -> None:
    fn = scan_funnel.resolve_buy_candidate_rejection_reason
    # pre-gating reason takes priority
    assert fn(
        symbol="005930",
        pre_gating={"reasons_by_symbol": {"005930": "untradable_today"}},
        layer_selected=True,
        raw_result=None,
        guarded_result=None,
        selected_symbol=None,
        final_action=None,
        final_reason=None,
    ) == "untradable_symbol"
    # scan blocked at pre-gate level
    assert fn(
        symbol="005930",
        pre_gating={"scan_allowed": False, "scan_block_reason": "daily pnl brake"},
        layer_selected=True,
        raw_result=None,
        guarded_result=None,
        selected_symbol=None,
        final_action=None,
        final_reason=None,
    ) == "daily_pnl_brake"
    # final action reason for the selected symbol
    assert fn(
        symbol="005930",
        pre_gating={},
        layer_selected=True,
        raw_result=_result(passes_profit_buffer=False),
        guarded_result=None,
        selected_symbol="005930",
        final_action="buy_skipped",
        final_reason="현금 부족",
    ) == "cash_insufficient"
    # deep-eval rejection when not the selected symbol
    assert fn(
        symbol="005930",
        pre_gating={},
        layer_selected=True,
        raw_result=_result(passes_profit_buffer=False),
        guarded_result=None,
        selected_symbol=None,
        final_action=None,
        final_reason=None,
    ) == "profit_buffer_insufficient"
    # nothing matches -> None
    assert fn(
        symbol="005930",
        pre_gating={},
        layer_selected=True,
        raw_result=None,
        guarded_result=None,
        selected_symbol=None,
        final_action=None,
        final_reason=None,
    ) is None


# --- resolve_buy_candidate_selection_outcome: literal cases ---


def test_resolve_buy_candidate_selection_outcome_literal_cases() -> None:
    fn = scan_funnel.resolve_buy_candidate_selection_outcome
    raw_candidate = _result(candidate=True)
    raw_reject = _result(candidate=False)
    assert fn(
        stage_reached="deep_eval",
        raw_result=raw_candidate,
        guarded_result=raw_candidate,
        final_candidate=True,
        executed=True,
    ) == "executed"
    assert fn(
        stage_reached="pre_gate_rejected",
        raw_result=None,
        guarded_result=None,
        final_candidate=False,
        executed=False,
    ) == "pre_gate_blocked"
    assert fn(
        stage_reached="shallow_selected",
        raw_result=None,
        guarded_result=None,
        final_candidate=False,
        executed=False,
    ) == "shortlisted_not_deep_evaluated"
    assert fn(
        stage_reached="deep_eval",
        raw_result=None,
        guarded_result=None,
        final_candidate=False,
        executed=False,
    ) is None
    assert fn(
        stage_reached="deep_eval",
        raw_result=raw_reject,
        guarded_result=None,
        final_candidate=False,
        executed=False,
    ) == "deep_eval_rejected"
    assert fn(
        stage_reached="deep_eval",
        raw_result=raw_candidate,
        guarded_result=raw_candidate,
        final_candidate=False,
        executed=False,
    ) == "not_selected_after_deep_eval"


# --- resolve_sell_exit_reason: real SellAnalysisResult object cases ---


def _sell_analysis(trigger: str | None) -> SellAnalysisResult:
    snapshot = MarketSnapshot(
        symbol="005930",
        current_price=80_000,
        open_price=79_000,
        low_price=78_000,
        prev_day_change_pct=1.2,
    )
    sell_decision = SellDecisionResult(
        should_attempt_sell=True,
        reason="signal",
        details={},
        triggered_rule_name=trigger,
        rule_results=(),
    )
    return SellAnalysisResult(
        symbol="005930",
        name="TestCo",
        market_snapshot=snapshot,
        buy_strategy_result=SimpleNamespace(to_log_payload=lambda: {}),
        sell_decision=sell_decision,
        holding_qty=3,
        average_cost=70_000,
    )


def test_resolve_sell_exit_reason_prefers_rebalance_then_cycle_then_trigger() -> None:
    analysis = _sell_analysis("take_profit")
    assert scan_funnel.resolve_sell_exit_reason(
        analysis=analysis, cycle_reason="manual", is_rebalance=True
    ) == "rebalance_sell"
    assert scan_funnel.resolve_sell_exit_reason(
        analysis=analysis, cycle_reason="stop_loss", is_rebalance=False
    ) == "stop_loss"
    assert scan_funnel.resolve_sell_exit_reason(
        analysis=analysis, cycle_reason="unmapped", is_rebalance=False
    ) == "take_profit"
    assert scan_funnel.resolve_sell_exit_reason(
        analysis=_sell_analysis(None), cycle_reason="unmapped", is_rebalance=False
    ) == "unknown"


# --- normalize_pre_gating_payload: hand payload -> full dict equality ---


def test_normalize_pre_gating_payload_full_dict_equality() -> None:
    normalized = scan_funnel.normalize_pre_gating_payload(
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
            "reentry_allowed_count": 3,
            "reentry_blocked_count": 1,
            "scan_allowed": True,
            "scan_block_reason": None,
        }
    )
    assert normalized == {
        "stage": "buy_pre_gating",
        "requested_count": 3,
        "allowed_count": 1,
        "early_reject_count": 2,
        "reason_counts": {"excluded_symbol": 1},
        "rejected": [
            {
                "symbol": "005930",
                "reason_code": "BUY_EXCLUDED_SYMBOLS 설정",
                "reason": "BUY_EXCLUDED_SYMBOLS 설정",
                "reentry_state": "BUY_EXCLUDED_SYMBOLS 설정",
                "exit_reason": None,
                "residual_position_present": False,
                "diagnostics": ["BUY_EXCLUDED_SYMBOLS"],
            }
        ],
        "rejected_symbols": ["005930"],
        "reasons_by_symbol": {"005930": "BUY_EXCLUDED_SYMBOLS 설정"},
        "reentry_state_by_symbol": {"005930": "blocked_churn_risk"},
        "last_exit_reason_by_symbol": {"005930": "stop_loss"},
        "residual_position_present_by_symbol": {"005930": True},
        "reentry_block_reason_counts": {"blocked_churn_risk": 2},
        "reentry_allowed_count": 3,
        "reentry_blocked_count": 1,
        "scan_allowed": True,
        "scan_block_reason": None,
    }


def test_normalize_pre_gating_payload_none_returns_defaults() -> None:
    assert scan_funnel.normalize_pre_gating_payload(None) == {
        "stage": "buy_pre_gating",
        "requested_count": 0,
        "allowed_count": 0,
        "early_reject_count": 0,
        "reason_counts": {},
        "rejected": [],
        "rejected_symbols": [],
        "reasons_by_symbol": {},
        "reentry_state_by_symbol": {},
        "last_exit_reason_by_symbol": {},
        "residual_position_present_by_symbol": {},
        "reentry_block_reason_counts": {},
        "reentry_allowed_count": 0,
        "reentry_blocked_count": 0,
        "scan_allowed": True,
        "scan_block_reason": None,
    }


# --- print_buy_pre_gating_summary: full capsys line equality ---


def test_print_buy_pre_gating_summary_allowed_with_rejects(capsys) -> None:
    scan_funnel.print_buy_pre_gating_summary(
        pre_gating={
            "scan_allowed": True,
            "requested_symbols": ("005930", "000660", "035420"),
            "allowed_symbols": ("005930",),
            "rejected": [
                {"symbol": "000660", "reason_code": "untradable_today"},
                {"symbol": "035420", "reason_code": "excluded_symbol"},
            ],
            "reason_counts": {"untradable_today": 1, "excluded_symbol": 1},
        },
        cycle_id="cycle-1",
        market_open=True,
        market_session="regular",
        log_engine_event=None,
    )
    out = capsys.readouterr().out
    assert out.splitlines() == [
        "=== BUY 사전 게이트 ===",
        "requested=3 | allowed=1 | early_rejected=2",
        "early reject reasons: excluded_symbol=1, untradable_today=1",
        "sample rejected: 000660(untradable_today), 035420(excluded_symbol)",
        "",
    ]


def test_print_buy_pre_gating_summary_scan_blocked(capsys) -> None:
    scan_funnel.print_buy_pre_gating_summary(
        pre_gating={"scan_allowed": False, "scan_block_reason": "daily stop"},
        cycle_id="c",
        market_open=False,
        market_session=None,
        log_engine_event=None,
    )
    out = capsys.readouterr().out
    assert out.splitlines() == [
        "=== BUY 사전 게이트 ===",
        "requested=0 | allowed=0 | early_rejected=0",
        "scan blocked: daily stop",
        "",
    ]


# --- print_buy_scan_stage_summary: full capsys line equality ---


def test_print_buy_scan_stage_summary_full_output(capsys) -> None:
    scan_funnel.print_buy_scan_stage_summary(
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
            "shallow_cap_applied": True,
            "shallow_cap_limit": 2,
            "shallow_cap_original_count": 3,
            "shortlist_symbols": ("005930", "000660"),
            "deep_eval_limit": 2,
            "exploration_quota_used": 1,
            "shortlist_preview": [
                {"symbol": "005930", "layer": "core", "shallow_score": 1.23},
                {"symbol": "000660", "layer": "rotating", "shallow_score": 0.5},
            ],
            "core_rescue_applied": True,
            "core_rescue_selected_symbol": "005930",
            "core_rescue_replaced_symbol": "035420",
            "core_rescue_selected_score": 1.23,
            "deep_eval_symbols": ("005930", "000660", "035420"),
        },
    )
    out = capsys.readouterr().out
    assert out.splitlines() == [
        "=== BUY staged scan ===",
        "profile=momentum | rotation=YES",
        "layers: core=3 (selected 2) | rotating=2 (selected 1) | exploration=1 (selected 1)",
        "pre-gating: requested=4 | allowed=3 | rejected=1",
        "shallow/deep: ranked=3 | cap=2/3 | shortlist=2 | deep_limit=2 | exploration_quota_used=1",
        "shortlist preview: 005930(core, score=1.23), 000660(rotating, score=0.50)",
        "core shortlist rescue: 005930(score=1.23) <- 035420",
        "deep-eval order override: 005930, 000660, 035420",
        "",
    ]


# --- print_buy_runtime_filter_summary: full capsys line equality + logging ---


def test_print_buy_runtime_filter_summary_excludes_and_logs(capsys) -> None:
    logged: list[dict[str, object]] = []

    def log_event(**kwargs):
        logged.append(kwargs)

    before = (
        SimpleNamespace(
            candidate=True, display_name="삼성전자", final_reason="candidate", symbol="005930"
        ),
        SimpleNamespace(
            candidate=True, display_name="SK하이닉스", final_reason="candidate", symbol="000660"
        ),
    )
    after = (
        SimpleNamespace(
            candidate=False,
            display_name="삼성전자",
            final_reason="동일 종목 일일 진입 횟수 제한",
            symbol="005930",
        ),
        SimpleNamespace(
            candidate=True, display_name="SK하이닉스", final_reason="candidate", symbol="000660"
        ),
    )
    scan_funnel.print_buy_runtime_filter_summary(
        before_results=before,
        after_results=after,
        cycle_id="cycle-1",
        market_open=True,
        market_session="regular",
        log_engine_event=log_event,
    )
    out = capsys.readouterr().out
    assert out.splitlines() == [
        "=== 유니버스 필터 ===",
        "필터 적용 전 후보 수: 2",
        "필터 적용 후 후보 수: 1",
        "제외 종목:",
        "삼성전자 | 이유: 동일 종목 일일 진입 횟수 제한",
        "",
    ]
    assert logged == [
        {
            "action": "blocked_buy_same_symbol_daily_limit",
            "reason": "동일 종목 일일 진입 횟수 제한",
            "cycle_id": "cycle-1",
            "market_open": True,
            "market_session": "regular",
        }
    ]


def test_print_buy_runtime_filter_summary_no_exclusions(capsys) -> None:
    before = (
        SimpleNamespace(
            candidate=True, display_name="삼성전자", final_reason="candidate", symbol="005930"
        ),
    )
    after = (
        SimpleNamespace(
            candidate=True, display_name="삼성전자", final_reason="candidate", symbol="005930"
        ),
    )
    scan_funnel.print_buy_runtime_filter_summary(
        before_results=before,
        after_results=after,
        cycle_id=None,
        market_open=False,
        market_session=None,
        log_engine_event=None,
    )
    out = capsys.readouterr().out
    assert out.splitlines() == [
        "=== 유니버스 필터 ===",
        "필터 적용 전 후보 수: 1",
        "필터 적용 후 후보 수: 1",
        "제외된 종목 없음",
        "",
    ]
