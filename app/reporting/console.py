"""Console presentation helpers for the runtime session.

Pure display layer extracted from app.main: every function takes prepared
data and prints operator-facing lines. No broker calls, no state mutation.
app.main imports these under their historical underscore names so existing
characterization-test monkeypatch targets keep working.
"""

from __future__ import annotations

from datetime import datetime

from app.core.formatters import (
    format_bps,
    format_krw,
    format_qty,
    format_signed_krw,
    format_signed_pct,
)
from app.core.time_utils import get_korean_now
import app.reporting.runtime_snapshots as _runtime_snapshots
from app.reporting.runtime_snapshots import log_engine_event as _log_engine_event
import app.scanner.runtime_scan as _rs
from app.execution import calculate_sell_position_sizing
from app.portfolio.equity_state import build_account_state_payload
from app.reporting.performance import build_today_realized_summary
from app.scanner.service import build_buy_strategy_summary
from app.scanner.symbol_names import get_symbol_name
from app.strategy.sell_decision import (
    SellAnalysisResult,
    select_top_sell_analysis,
    select_top_sell_candidate,
)

def emit_status(level: str, message: str) -> None:
    print(f"[{level}] {message}")


def sell_status_letter(rule_result) -> str:
    if not rule_result.enabled:
        return "O"
    return "P" if rule_result.passed else "F"

def build_sell_strategy_summary(result: SellAnalysisResult) -> str:
    return " ".join(
        sell_status_letter(rule_result)
        for rule_result in result.sell_decision.rule_results
    )

def print_buy_strategy(decision, *, display_label: str, candidate: bool) -> None:
    status = "candidate" if candidate else "reject"
    print("=== 매수 전략 판단 ===")
    print(
        f"{display_label} | B: {build_buy_strategy_summary(decision)} | "
        f"{decision.passed_count}/{decision.enabled_count} | {status}"
    )
    print("최종 매수 판단: " + ("통과" if candidate else "차단"))
    print()

def print_buy_score_summary(result) -> None:
    print("=== 매수 스코어 요약 ===")
    print(f"score={result.score:.2f}")
    print(
        "핵심 가산 요인: "
        + (", ".join(result.score_highlights) if result.score_highlights else "없음")
    )
    print(
        "핵심 감점 요인: "
        + (", ".join(result.score_penalties) if result.score_penalties else "없음")
    )
    print(f"요약: {result.score_summary}")
    print(
        "비용 요약: "
        f"예상 총비용 {format_krw(result.expected_total_cost_krw)} | "
        f"cost {result.expected_cost_bps:.1f}bps | "
        f"net edge {result.net_edge_bps:.1f}bps"
    )
    print(
        "수학 요약: "
        f"{result.math_score_summary or 'summary unavailable'}"
    )
    if result.mean_reversion_summary:
        z_text = (
            "-"
            if result.mean_reversion_zscore is None
            else f"{float(result.mean_reversion_zscore or 0.0):.2f}"
        )
        print(
            "평균회귀: "
            f"{result.mean_reversion_summary} | z={z_text}"
        )
    if result.portfolio_risk_summary:
        corr_text = (
            "-"
            if result.portfolio_avg_correlation is None
            else f"{float(result.portfolio_avg_correlation or 0.0):.2f}"
        )
        var_text = (
            "-"
            if result.variance_increase_estimate is None
            else f"{float(result.variance_increase_estimate or 0.0):.3f}"
        )
        print(
            "포트폴리오 위험: "
            f"{result.portfolio_risk_summary} | avg corr={corr_text} | var+={var_text}"
        )
    print()

def print_sell_decision(
    results: tuple[SellAnalysisResult, ...],
    *,
    settings,
    holdings_count: int,
    partial: bool = False,
    partial_reason: str | None = None,
    cursor_before: int | None = None,
    cursor_after: int | None = None,
    evaluated_symbols: list[str] | None = None,
    skipped_symbols: list[str] | None = None,
) -> None:
    print("=== 매도 전략 판단 ===")
    evaluated_symbols = evaluated_symbols or []
    skipped_symbols = skipped_symbols or []
    if not results:
        if holdings_count <= 0:
            print("보유 종목 없음")
        elif partial:
            print("보유 종목 일부만 평가함")
            print(
                partial_reason
                or "예산 부족으로 남은 종목은 다음 tick으로 미룹니다."
            )
            print(
                f"이번 tick 평가 종목: {len(evaluated_symbols)}개 / "
                f"전체 보유 종목: {holdings_count}개"
            )
        else:
            print("보유 종목은 있으나 이번 tick에서 평가 가능한 종목이 없었습니다.")
            print("평가 결과가 비어 있어 매도 후보를 만들지 못했습니다.")
        if holdings_count > 0 and cursor_before is not None:
            print(
                f"SELL watch cursor: start={cursor_before} -> next={cursor_after if cursor_after is not None else '-'}"
            )
        if skipped_symbols:
            print(
                "다음 tick 이월 종목: "
                + ", ".join(skipped_symbols[:5])
                + (" ..." if len(skipped_symbols) > 5 else "")
            )
        print()
        return

    selected_candidate = select_top_sell_candidate(results)
    chosen_analysis = selected_candidate or select_top_sell_analysis(results)

    for result in results:
        status = "sell_candidate" if result.sell_decision.should_attempt_sell else "hold"
        trigger = result.sell_decision.triggered_rule_name or "-"
        sell_sizing = calculate_sell_position_sizing(
            trigger=result.sell_decision.triggered_rule_name,
            holding_qty=result.holding_qty,
            current_price=result.market_snapshot.current_price,
            settings=settings,
        )
        sell_qty_text = ""
        if sell_sizing.recommended_sell_qty > 0:
            sell_qty_text = f" | sell_qty={sell_sizing.recommended_sell_qty}"
        print(
            f"{result.display_name} | S: {build_sell_strategy_summary(result)} | "
            f"trigger={trigger}{sell_qty_text} | priority={float(result.sell_decision.details.get('sell_priority_score', 0.0) or 0.0):.2f} | {status}"
        )

    if chosen_analysis is not None:
        print(f"최종 매도 검토 종목: {chosen_analysis.display_name}")
        print(
            "매도 우선순위 요약: "
            + str(
                chosen_analysis.sell_decision.details.get(
                    "sell_priority_summary",
                    "우선순위 정보 없음",
                )
                )
            )
    if partial:
        print("평가한 종목 중 매도 후보 없음" if chosen_analysis is None else "보유 종목 일부만 평가했으며 남은 종목은 다음 tick에서 이어서 확인합니다.")
        if partial_reason:
            print(f"partial 사유: {partial_reason}")
    elif chosen_analysis is None and holdings_count > 0:
        print("평가한 종목 중 매도 후보 없음")
    if holdings_count > 0 and cursor_before is not None:
        print(
            f"SELL watch cursor: start={cursor_before} -> next={cursor_after if cursor_after is not None else '-'}"
        )
    print()

def print_sell_preview(result: SellAnalysisResult, sell_sizing) -> None:
    net_pnl_bps = float(result.sell_decision.details.get("net_pnl_bps", 0.0))
    print("=== 매도 주문 미리보기 ===")
    print(f"주문 종목: {result.display_name}")
    print(f"주문 수량: {format_qty(sell_sizing.recommended_sell_qty)}")
    print("주문 방식: 시장가")
    print(f"trigger: {sell_sizing.sell_trigger or '-'}")
    print(f"보유 수량: {format_qty(result.holding_qty)}")
    print(f"비용 포함 현재 순손익률: {format_bps(net_pnl_bps)}")
    print(f"예상 매도 금액: {format_krw(sell_sizing.recommended_notional_krw)}")
    print(
        f"예상 매도 수수료: {format_krw(sell_sizing.details['estimated_sell_fee_krw'])}"
    )
    print(
        f"예상 매도세금: {format_krw(sell_sizing.details['estimated_sell_tax_krw'])}"
    )
    print(
        f"예상 매도 슬리피지: {format_krw(sell_sizing.details['estimated_sell_slippage_krw'])}"
    )
    print(
        f"예상 순회수금액: {format_krw(sell_sizing.details['estimated_net_proceeds_krw'])}"
    )
    print()

def print_buy_orderable_preview(execution_snapshot, position_sizing) -> None:
    details = position_sizing.details
    print("=== 주문 가능 조회 ===")
    print(
        f"주문가능현금(주문가능조회): {format_krw(execution_snapshot.orderable_cash)}"
    )
    print(f"현재가 기준 가능 수량: {format_qty(execution_snapshot.orderable_qty)}")
    print(f"추천 매수 수량: {format_qty(position_sizing.recommended_qty)}")
    print(
        f"추천 매수 금액: {format_krw(position_sizing.recommended_notional_krw)}"
    )
    print(f"예상 매수 수수료: {format_krw(details['estimated_buy_fee_krw'])}")
    print(
        f"예상 진입 슬리피지: {format_krw(details['estimated_buy_slippage_krw'])}"
    )
    print(f"예상 진입 총비용: {format_krw(details['estimated_entry_cost_krw'])}")
    print(
        f"예상 왕복 비용: {format_krw(details['estimated_round_trip_cost_krw'])}"
    )
    print(
        f"비용 포함 손익분기 상승률: {format_bps(float(details['estimated_break_even_bps']))}"
    )
    if details.get("effective_math_multiplier") is not None:
        print(
            "수학 보정 multiplier="
            f"{float(details.get('effective_math_multiplier', 1.0) or 1.0):.2f}x"
        )
        print(
            "수학 보정 요약: "
            f"{details.get('math_sizing_summary') or '기본 수량 유지'}"
        )
    if details.get("budget_rescue_applied"):
        print(
            "최소 1주 rescue: "
            f"{details.get('budget_rescue_reason') or '적용'}"
        )
    print()

def print_runtime_mode(settings) -> None:
    print("=== 실행 모드 ===")
    print(f"mode={settings.run_mode}")
    print(f"run_once={'true' if settings.run_once else 'false'}")
    print(f"interval={settings.run_interval_seconds}s")
    print()

def print_applied_settings(settings) -> None:
    print("=== 적용 설정 ===")
    print(f"RUN_MODE={settings.run_mode}")
    print(f"RUN_ONCE={'true' if settings.run_once else 'false'}")
    print(f"RUN_INTERVAL_SECONDS={settings.run_interval_seconds}")
    print(f"CONFIRM_BUY={settings.confirm_buy}")
    print(f"BUY_DAILY_MAX_NOTIONAL_KRW={settings.buy_daily_max_notional_krw:,}")
    print(f"SELL_DAILY_MAX_NOTIONAL_KRW={settings.sell_daily_max_notional_krw:,}")
    print(
        "BUY_DAILY_MAX_ORDER_SUBMISSIONS="
        f"{settings.buy_daily_max_order_submissions}"
    )
    print(
        "SELL_DAILY_MAX_ORDER_SUBMISSIONS="
        f"{settings.sell_daily_max_order_submissions}"
    )
    print(f"SELL_CHECK_INTERVAL_SECONDS={settings.sell_check_interval_seconds}")
    print(f"BUY_SCAN_INTERVAL_SECONDS={settings.buy_scan_interval_seconds}")
    print(f"SCAN_SYMBOLS_MAX_PER_CYCLE={settings.scan_symbols_max_per_cycle}")
    print(
        "BUY_SCAN_PROFILE_ROTATION_ENABLED="
        f"{'true' if settings.buy_scan_profile_rotation_enabled else 'false'}"
    )
    print(f"BUY_SCAN_EXPLORATION_RATIO={settings.buy_scan_exploration_ratio}")
    print(f"BUY_SCAN_CORE_FRACTION={settings.buy_scan_core_fraction}")
    print(f"BUY_SCAN_ROTATING_FRACTION={settings.buy_scan_rotating_fraction}")
    print(f"BUY_SCAN_SHALLOW_TOP_K={settings.buy_scan_shallow_top_k}")
    print(f"BUY_SCAN_DEEP_EVAL_LIMIT={settings.buy_scan_deep_eval_limit}")
    print(f"BUY_SCAN_TOP_K_CANDIDATES={settings.buy_scan_top_k_candidates}")
    print(f"LIVE_SNAPSHOT_TTL_SECONDS={settings.live_snapshot_ttl_seconds}")
    print(
        "LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS="
        f"{settings.live_snapshot_refresh_interval_seconds}"
    )
    print(
        "API_SOFT_MAX_REQUESTS_PER_SECOND="
        f"{settings.api_soft_max_requests_per_second}"
    )
    print(f"API_SOFT_MAX_QUOTES_PER_TICK={settings.api_soft_max_quotes_per_tick}")
    print(
        "API_BACKOFF_SECONDS_ON_RATE_LIMIT="
        f"{settings.api_backoff_seconds_on_rate_limit}"
    )
    print(f"API_MIN_INTER_REQUEST_SECONDS={settings.api_min_inter_request_seconds}")
    print(
        "API_BUY_SCAN_MIN_REQUEST_RESERVE="
        f"{settings.api_buy_scan_min_request_reserve}"
    )
    print(
        "API_BUY_SCAN_MIN_QUOTE_RESERVE="
        f"{settings.api_buy_scan_min_quote_reserve}"
    )
    print(
        "ADAPTIVE_MIDDAY_ENABLED="
        f"{'true' if settings.adaptive_midday_enabled else 'false'}"
    )
    print(f"ADAPTIVE_MIDDAY_WINDOW={settings.adaptive_midday_window}")
    print(
        "ADAPTIVE_MIDDAY_BUY_SCAN_INTERVAL_SECONDS="
        f"{settings.adaptive_midday_buy_scan_interval_seconds}"
    )
    print(
        "ADAPTIVE_MIDDAY_SELL_CHECK_INTERVAL_SECONDS="
        f"{settings.adaptive_midday_sell_check_interval_seconds}"
    )
    print(
        "ADAPTIVE_MIDDAY_SCAN_SYMBOLS_MAX_PER_CYCLE="
        f"{settings.adaptive_midday_scan_symbols_max_per_cycle}"
    )
    print(
        "ADAPTIVE_MIDDAY_BUY_SCAN_DEEP_EVAL_LIMIT="
        f"{settings.adaptive_midday_buy_scan_deep_eval_limit}"
    )
    print(
        "DEGRADED_MODE_ENABLED="
        f"{'true' if settings.degraded_mode_enabled else 'false'}"
    )
    print(
        "DEGRADED_MODE_RATE_LIMIT_HITS_IN_10M="
        f"{settings.degraded_mode_rate_limit_hits_in_10m}"
    )
    print(
        "DEGRADED_MODE_CONSECUTIVE_BACKOFF_CYCLES="
        f"{settings.degraded_mode_consecutive_backoff_cycles}"
    )
    print(
        "DEGRADED_MODE_DURATION_SECONDS="
        f"{settings.degraded_mode_duration_seconds}"
    )
    print(
        "DEGRADED_MODE_BUY_SCAN_INTERVAL_SECONDS="
        f"{settings.degraded_mode_buy_scan_interval_seconds}"
    )
    print(
        "DEGRADED_MODE_SELL_CHECK_INTERVAL_SECONDS="
        f"{settings.degraded_mode_sell_check_interval_seconds}"
    )
    print(
        "DEGRADED_MODE_SCAN_SYMBOLS_MAX_PER_CYCLE="
        f"{settings.degraded_mode_scan_symbols_max_per_cycle}"
    )
    print(
        "DEGRADED_MODE_BUY_SCAN_DEEP_EVAL_LIMIT="
        f"{settings.degraded_mode_buy_scan_deep_eval_limit}"
    )
    print(
        "DEGRADED_MODE_SELL_WATCH_MAX_HOLDINGS_PER_TICK="
        f"{settings.degraded_mode_sell_watch_max_holdings_per_tick}"
    )
    print(
        f"BUY_REENTRY_COOLDOWN_MINUTES={settings.rebuy_cooldown_minutes}"
    )
    print(
        "BUY_STOP_LOSS_SAME_DAY_MIN_COOLDOWN_MINUTES="
        f"{settings.stop_loss_same_day_reentry_min_minutes}"
    )
    print(
        f"BUY_SAME_SYMBOL_MAX_ENTRIES_PER_DAY={settings.same_symbol_max_buys_per_day}"
    )
    print(
        f"ENABLE_DAILY_PNL_BRAKE={'true' if settings.enable_daily_pnl_brake else 'false'}"
    )
    print(f"DAILY_PNL_WARNING_PCT={settings.daily_pnl_warning_pct}")
    print(f"DAILY_BUY_PAUSE_PCT={settings.daily_pnl_buy_pause_pct}")
    print(f"DAILY_HARD_STOP_PCT={settings.daily_pnl_hard_stop_pct}")
    print(f"DAILY_PNL_COOLDOWN_MINUTES={settings.daily_pnl_cooldown_minutes}")
    print(f"REGIME_CAUTION_DRAWDOWN_PCT={settings.regime_caution_drawdown_pct}")
    print(f"REGIME_RISK_OFF_DRAWDOWN_PCT={settings.regime_risk_off_drawdown_pct}")
    print(f"REGIME_NORMAL_MULTIPLIER={settings.regime_normal_multiplier}")
    print(f"REGIME_CAUTION_MULTIPLIER={settings.regime_caution_multiplier}")
    print(f"REGIME_RISK_OFF_MULTIPLIER={settings.regime_risk_off_multiplier}")
    print(
        f"ENABLE_PREMARKET_WAIT={'true' if settings.enable_premarket_wait else 'false'}"
    )
    print(
        f"ENABLE_REBALANCE_SELL={'true' if settings.enable_rebalance_sell else 'false'}"
    )
    print(
        "ENABLE_QUALITY_REBALANCE_PREVIEW="
        f"{'true' if settings.enable_quality_rebalance_preview else 'false'}"
    )
    print(f"REBALANCE_MAX_CONCENTRATION_PCT={settings.rebalance_max_concentration_pct}")
    print(f"REBALANCE_MIN_NET_EDGE_BPS={settings.rebalance_min_net_edge_bps}")
    print(
        f"SCAN_SYMBOLS_SOURCE={settings.target_symbols_source} | "
        f"SCAN_SYMBOLS_COUNT={len(settings.target_symbols)}"
    )
    print(f"BUY_EXCLUDED_SYMBOLS_COUNT={len(getattr(settings, 'buy_excluded_symbols', ()) or ())}")
    print()

def print_runtime_parameter_validation(report: dict[str, object]) -> None:
    print("=== 런타임 핵심 파라미터 검증 ===")
    print("parameter | effective | source category | recommended | status")
    for entry in report.get("entries", ()):
        status = (
            "OK"
            if entry.get("matches_recommended_default")
            else "MISMATCH"
        )
        print(
            f"{entry.get('name')} | {entry.get('effective_value')} | "
            f"{entry.get('source')} | {entry.get('recommended_value')} | {status}"
        )
    warnings = tuple(report.get("warnings", ()))
    if warnings:
        for warning in warnings:
            print(
                "[warn] critical runtime parameter override differs from recommended default"
                f" | {warning}"
            )
    else:
        print("[info] critical runtime parameters are aligned with recommended defaults.")
    if report.get("regular_session_runtime_profile_enforced"):
        print("REGULAR_SESSION_RUNTIME_PROFILE=enforced")
        if report.get("session_env_file_path"):
            print(f"SESSION_ENV_FILE={report.get('session_env_file_path')}")
        print(
            "[info] source categories: default / config file / session config file / "
            "environment override / code-enforced runtime control"
        )
    else:
        print("REGULAR_SESSION_RUNTIME_PROFILE=inactive")
    strict_mode_enabled = bool(report.get("strict_mode_enabled"))
    print(
        "STRICT_RUNTIME_PARAM_OVERRIDES="
        f"{'true' if strict_mode_enabled else 'false'}"
    )
    if strict_mode_enabled and report.get("strict_blocked"):
        print(
            "[error] strict runtime override validation blocked startup"
            f" | violations={len(tuple(report.get('strict_violations', ())))}"
        )
    print()

def print_startup_sanity_report(report: dict[str, object], *, run_mode: str) -> None:
    print("=== startup sanity check ===")
    print(f"run_mode={run_mode}")
    for warning in tuple(report.get("warnings", ())):
        print(f"[warn] {warning}")
    errors = tuple(report.get("errors", ()))
    if errors:
        for error in errors:
            print(f"[error] {error}")
    else:
        print("[info] startup sanity checks passed.")
    print(
        "BYPASS_STARTUP_SANITY_CHECK="
        f"{'true' if report.get('bypass_enabled') else 'false'}"
    )
    if report.get("blocked"):
        print(
            "[error] startup sanity check blocked trading session startup"
            f" | violations={len(errors)}"
        )
    print()

def print_cycle_header(settings) -> None:
    print("=== 실행 사이클 ===")
    print(f"started_at={datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"mode={settings.run_mode}")
    print()

def format_cycle_summary(
    *,
    market_session: str | None,
    final_action: str | None,
    sell_status: str,
    buy_status: str,
    requests: int,
    elapsed_ms: float,
    warning_count: int,
    error_count: int,
    rate_limit_triggered: bool = False,
    rate_limit_source: str | None = None,
    adaptive_pacing_used: bool = False,
    adaptive_pacing_extra_delay_ms: float = 0.0,
) -> str:
    session = market_session or "-"
    action = final_action or "-"
    return (
        "cycle result | "
        f"session={session} | "
        f"action={action} | "
        f"sell={sell_status} | "
        f"buy={buy_status} | "
        f"requests={requests} | "
        f"elapsed={int(round(elapsed_ms))}ms | "
        f"rate_limit={'YES' if rate_limit_triggered else 'NO'}"
        f"{'' if not rate_limit_source else f'({rate_limit_source})'} | "
        f"adaptive={'YES' if adaptive_pacing_used else 'NO'}"
        f"{'' if not adaptive_pacing_used else f'({int(round(adaptive_pacing_extra_delay_ms))}ms)'} | "
        f"warn={warning_count} | "
        f"error={error_count}"
    )

def phase_timing_summary(
    *,
    elapsed_ms: float,
    request_delta: dict[str, object] | None = None,
) -> dict[str, float]:
    network_ms = float((request_delta or {}).get("total_elapsed_ms", 0.0))
    total_ms = round(max(0.0, float(elapsed_ms)), 1)
    network_ms = round(max(0.0, network_ms), 1)
    local_ms = round(max(0.0, total_ms - network_ms), 1)
    return {
        "elapsed_ms": total_ms,
        "network_ms": network_ms,
        "local_ms": local_ms,
    }

def format_timing_line(label: str, timing: dict[str, float] | None) -> str:
    if not timing:
        return f"{label}: -"
    return (
        f"{label}: {int(round(float(timing.get('elapsed_ms', 0.0))))}ms "
        f"(api {int(round(float(timing.get('network_ms', 0.0))))}ms / "
        f"local {int(round(float(timing.get('local_ms', 0.0))))}ms)"
    )

def print_cycle_timing(
    *,
    timing_summary: dict[str, object],
) -> None:
    print("=== cycle timing ===")
    print(
        f"total: {int(round(float(timing_summary.get('total_cycle_ms', 0.0))))}ms"
    )
    print(
        format_timing_line(
            "session check",
            timing_summary.get("session_check"),
        )
    )
    print(
        format_timing_line(
            "settings/scheduler",
            timing_summary.get("settings_scheduler"),
        )
    )
    print(
        format_timing_line(
            "balance",
            timing_summary.get("balance_inquiry"),
        )
    )
    print(
        format_timing_line(
            "sell eval",
            timing_summary.get("sell_evaluation"),
        )
    )
    print(
        format_timing_line(
            "buy scan",
            timing_summary.get("buy_scan"),
        )
    )
    if timing_summary.get("buy_ranking_ms") is not None:
        print(
            f"buy ranking/local: {int(round(float(timing_summary.get('buy_ranking_ms', 0.0))))}ms"
        )
    if timing_summary.get("rebalance_selection_ms") is not None:
        print(
            "rebalance selection/local: "
            f"{int(round(float(timing_summary.get('rebalance_selection_ms', 0.0))))}ms"
        )
    print(
        format_timing_line(
            "order/rebalance preview",
            timing_summary.get("order_preview"),
        )
    )
    print(
        format_timing_line(
            "reporting",
            timing_summary.get("reporting"),
        )
    )
    print()

def print_api_usage(api_usage_summary: dict[str, object]) -> None:
    categories = api_usage_summary.get("categories") or {}
    print("=== api usage ===")
    print(
        f"total requests: {int(api_usage_summary.get('total_requests', 0))}"
    )
    print(
        f"quotes: {int((categories.get('quote') or {}).get('count', 0))} "
        f"({int(round(float((categories.get('quote') or {}).get('elapsed_ms', 0.0))))}ms)"
    )
    print(
        f"balance: {int((categories.get('balance') or {}).get('count', 0))} "
        f"({int(round(float((categories.get('balance') or {}).get('elapsed_ms', 0.0))))}ms)"
    )
    print(
        f"orderable: {int((categories.get('orderable') or {}).get('count', 0))} "
        f"({int(round(float((categories.get('orderable') or {}).get('elapsed_ms', 0.0))))}ms)"
    )
    print(
        f"order: {int((categories.get('order') or {}).get('count', 0))} "
        f"({int(round(float((categories.get('order') or {}).get('elapsed_ms', 0.0))))}ms)"
    )
    print(
        f"token: {int((categories.get('token') or {}).get('count', 0))} "
        f"({int(round(float((categories.get('token') or {}).get('elapsed_ms', 0.0))))}ms)"
    )
    print()

def print_buy_scan_metrics(
    *,
    requested_count: int,
    evaluated_count: int,
    quote_request_count: int,
    top_k_count: int,
    skipped_reason: str | None,
    guard_wait_ms: float | None = None,
    sleep_ms: float | None = None,
    effective_total_sleep_ms: float | None = None,
    quote_response_ms: float | None = None,
    quote_calc_ms: float | None = None,
    quote_parse_ms: float | None = None,
    score_calc_ms: float | None = None,
    candidate_build_ms: float | None = None,
    ranking_ms: float | None = None,
    logging_ms: float | None = None,
    throttle_sleep_events: int | None = None,
    throttle_min_sleep_ms: float | None = None,
    throttle_total_sleep_ms: float | None = None,
    throttle_immediate_pass_count: int | None = None,
    average_sleep_per_event_ms: float | None = None,
    sample_symbols: list[dict[str, object]] | None = None,
) -> None:
    print("=== buy scan 계측 ===")
    print(f"requested universe: {requested_count}")
    print(f"evaluated universe: {evaluated_count}")
    print(
        "skipped reason: "
        + (skipped_reason if skipped_reason else "-")
    )
    print(f"actual quote request count: {quote_request_count}")
    print(f"top K candidate count: {top_k_count}")
    print(f"guard wait before scan: {int(round(float(guard_wait_ms or 0.0)))}ms")
    print(f"quote wait sleep total: {int(round(float(sleep_ms or 0.0)))}ms")
    print(
        f"effective total sleep: {int(round(float(effective_total_sleep_ms or 0.0)))}ms"
    )
    print(f"quote response total: {int(round(float(quote_response_ms or 0.0)))}ms")
    print(f"quote calc total: {int(round(float(quote_calc_ms or 0.0)))}ms")
    print(f"quote parse total: {int(round(float(quote_parse_ms or 0.0)))}ms")
    print(f"score calc total: {int(round(float(score_calc_ms or 0.0)))}ms")
    print(f"candidate build total: {int(round(float(candidate_build_ms or 0.0)))}ms")
    print(f"ranking total: {int(round(float(ranking_ms or 0.0)))}ms")
    print(f"logging total: {int(round(float(logging_ms or 0.0)))}ms")
    print(f"throttle sleep events: {int(throttle_sleep_events or 0)}")
    min_sleep_text = (
        "-"
        if throttle_min_sleep_ms is None
        else f"{int(round(float(throttle_min_sleep_ms or 0.0)))}ms"
    )
    print(f"throttle min sleep: {min_sleep_text}")
    print(f"throttle total sleep: {int(round(float(throttle_total_sleep_ms or 0.0)))}ms")
    print(f"throttle immediate pass: {int(throttle_immediate_pass_count or 0)}")
    print(
        f"average sleep per event: {int(round(float(average_sleep_per_event_ms or 0.0)))}ms"
    )
    samples = sample_symbols or []
    if samples:
        print("recent quote samples:")
        for sample in samples[-5:]:
            print(
                "  "
                f"{sample.get('symbol', '-')} | "
                f"sleep {int(round(float(sample.get('sleep_ms', 0.0) or 0.0)))}ms | "
                f"api {int(round(float(sample.get('api_ms', 0.0) or 0.0)))}ms | "
                f"calc {int(round(float(sample.get('calc_ms', 0.0) or 0.0)))}ms"
            )
    print()

def print_sell_metrics(
    *,
    holdings_count: int,
    evaluated_count: int,
    quote_request_count: int,
    elapsed_ms: float,
    guard_wait_ms: float | None = None,
    throttle_sleep_ms: float | None = None,
    effective_total_sleep_ms: float | None = None,
    quote_response_ms: float | None = None,
    calc_ms: float | None = None,
    cursor_before: int | None = None,
    cursor_after: int | None = None,
    partial: bool = False,
    skipped_count: int = 0,
) -> None:
    print("=== sell 계측 ===")
    print(f"holdings count: {holdings_count}")
    print(f"sell quote request count: {quote_request_count}")
    print(f"evaluated holdings count: {evaluated_count}")
    if cursor_before is not None:
        print(
            f"sell watch cursor: {cursor_before} -> {cursor_after if cursor_after is not None else '-'}"
        )
    print(f"sell watch partial: {'YES' if partial else 'NO'}")
    if partial:
        print(f"sell watch skipped holdings: {skipped_count}")
    print(f"guard wait before sell_watch: {int(round(float(guard_wait_ms or 0.0)))}ms")
    print(
        f"per-request throttle sleep total: {int(round(float(throttle_sleep_ms or 0.0)))}ms"
    )
    print(
        f"effective total sleep: {int(round(float(effective_total_sleep_ms or 0.0)))}ms"
    )
    print(f"quote response total: {int(round(float(quote_response_ms or 0.0)))}ms")
    print(f"local calc total: {int(round(float(calc_ms or 0.0)))}ms")
    print(f"sell evaluation elapsed: {int(round(elapsed_ms))}ms")
    print()

def print_portfolio_positions(portfolio_snapshot, sell_analysis_results) -> None:
    print("=== 보유 종목 ===")
    held_positions = portfolio_snapshot.held_positions if portfolio_snapshot is not None else ()
    if not held_positions:
        print("보유 종목 없음")
        print()
        return

    analysis_by_symbol = {
        result.symbol: result for result in sell_analysis_results or ()
    }
    total_evaluation_amount = max(int(portfolio_snapshot.total_evaluation_amount), 0)
    for position in held_positions:
        result = analysis_by_symbol.get(position.symbol)
        evaluation_amount = int(position.market_value)
        current_price = int(position.current_price)
        gross_pnl_krw = int(position.gross_pnl)
        gross_pnl_pct = float(position.gross_pnl_pct)
        net_pnl_krw = gross_pnl_krw
        net_pnl_pct = gross_pnl_pct
        display_name = (
            result.display_name
            if result is not None
            else f"{position.symbol} {position.name}".strip()
        )
        holding_qty = int(position.holding_qty)
        average_cost = int(position.average_cost)
        if result is not None:
            details = result.sell_decision.details
            evaluation_amount = result.market_snapshot.current_price * result.holding_qty
            current_price = int(result.market_snapshot.current_price)
            gross_pnl_krw = int(details["gross_pnl_krw"])
            gross_pnl_pct = float(details["gross_pnl_pct"])
            net_pnl_krw = int(details["net_pnl_krw"])
            net_pnl_pct = float(details["net_pnl_pct"])
            holding_qty = int(result.holding_qty)
            average_cost = int(result.average_cost)
        weight_pct = 0.0
        if total_evaluation_amount > 0:
            weight_pct = (evaluation_amount / total_evaluation_amount) * 100
        print(
            f"{display_name} | {format_qty(holding_qty)} | "
            f"평균 {format_krw(average_cost)} | 현재 {format_krw(current_price)} | "
            f"평가 {format_krw(evaluation_amount)} | "
            f"gross {format_signed_krw(gross_pnl_krw)} ({format_signed_pct(gross_pnl_pct)}) | "
            f"net {format_signed_krw(net_pnl_krw)} ({format_signed_pct(net_pnl_pct)}) | "
            f"비중 {weight_pct:.2f}%"
        )
    print()

def print_account_balance_interpretation(
    *,
    portfolio_snapshot,
    sell_analysis_results,
) -> None:
    analysis_by_symbol = {
        result.symbol: result for result in sell_analysis_results or ()
    }
    total_gross_pnl = 0
    total_net_pnl = 0
    for position in portfolio_snapshot.held_positions:
        analysis = analysis_by_symbol.get(position.symbol)
        if analysis is not None:
            total_gross_pnl += int(analysis.sell_decision.details.get("gross_pnl_krw", 0))
            total_net_pnl += int(analysis.sell_decision.details.get("net_pnl_krw", 0))
            continue
        total_gross_pnl += int(position.gross_pnl)
        total_net_pnl += int(position.gross_pnl)

    account_state_payload = build_account_state_payload(
        portfolio_snapshot=portfolio_snapshot,
        sell_analysis_results=sell_analysis_results,
    )
    holdings_market_value = int(account_state_payload["holdings_market_value_krw"])
    total_cost_basis = int(account_state_payload["total_cost_basis_krw"])
    operating_equity = int(account_state_payload["operating_equity_krw"])
    deployment_invariant_cash_leg = int(
        account_state_payload["deployment_invariant_cash_leg_krw"]
    )
    deployment_invariant_equity = int(
        account_state_payload["deployment_invariant_equity_krw"]
    )

    print("=== 계좌 해석 ===")
    print(
        f"총예수금(원본): {format_krw(portfolio_snapshot.cash_total)} | "
        f"주문가능현금(원본): {format_krw(portfolio_snapshot.cash_orderable)} | "
        f"익일정산예수금(원본): {format_krw(portfolio_snapshot.cash_next_day)}"
    )
    print(
        f"보유 평가금액(실시간): {format_krw(holdings_market_value)} | "
        f"총 매입원금(보유기준): {format_krw(total_cost_basis)}"
    )
    print(
        f"총 평가손익 gross: {format_signed_krw(total_gross_pnl)} | "
        f"총 평가손익 net: {format_signed_krw(total_net_pnl)}"
    )
    print(
        f"운영 equity: {format_krw(operating_equity)} "
        "(주문가능현금 + 보유 평가금액 기준)"
    )
    print(
        f"브레이크 equity: {format_krw(deployment_invariant_equity)} "
        "(max(총예수금, 익일정산예수금, 주문가능현금) + 보유 평가금액 기준, cash deployment invariant)"
    )
    print()

def print_today_bought_tracking(*, state: dict, portfolio_snapshot, sell_analysis_results) -> None:
    print("=== 오늘 매수 종목 추적 ===")
    symbols_bought_today = list(state.get("symbols_bought_today", []))
    if not symbols_bought_today:
        print("오늘 매수 완료 종목 없음")
        print()
        return

    analysis_by_symbol = {result.symbol: result for result in sell_analysis_results}
    latest_buy_qty_by_symbol: dict[str, int] = {}
    target_date = get_korean_now().date().isoformat()
    for order in state.get("recent_orders", []):
        if str(order.get("date", "")).strip() != target_date:
            continue
        if str(order.get("side", "")).strip() != "BUY":
            continue
        if str(order.get("action", "")).strip() not in {"order_submitted", "order_succeeded"}:
            continue
        latest_buy_qty_by_symbol[str(order.get("symbol", "")).strip()] = int(order.get("qty", 0) or 0)

    printed = False
    for symbol in symbols_bought_today:
        position = portfolio_snapshot.get_position(symbol)
        analysis = analysis_by_symbol.get(symbol)
        name = None
        if position is not None:
            name = position.name
        if not name and analysis is not None:
            name = analysis.name
        if not name:
            name = get_symbol_name(symbol)
        display_name = f"{symbol} {name}" if name else symbol
        buy_qty = latest_buy_qty_by_symbol.get(symbol, position.holding_qty if position is not None else 0)

        if position is None or analysis is None:
            print(f"{display_name} | 매수수량 {format_qty(buy_qty)} | 현재 미보유 또는 시세 미확인")
            printed = True
            continue

        details = analysis.sell_decision.details
        print(
            f"{display_name} | 매수수량 {format_qty(buy_qty)} | "
            f"진입단가 {format_krw(position.average_cost)} | 현재가 {format_krw(analysis.market_snapshot.current_price)} | "
            f"gross {format_signed_pct(float(details['gross_pnl_pct']))} | "
            f"net {format_signed_pct(float(details['net_pnl_pct']))}"
        )
        printed = True

    if not printed:
        print("오늘 매수 완료 종목 없음")
    print()

def print_rebalance_skip(reason: str) -> None:
    from app.execution.rebalance import print_rebalance_skip
    print_rebalance_skip(reason)


def print_today_performance_summary(
    *,
    portfolio_snapshot,
    sell_analysis_results,
    settings,
    realized_summary: dict[str, object] | None = None,
) -> None:
    realized_summary = realized_summary or build_today_realized_summary(settings)
    analysis_by_symbol = {
        result.symbol: result for result in sell_analysis_results or ()
    }
    unrealized_gross = 0
    unrealized_net = 0
    contribution_rows: list[tuple[str, int]] = []
    for position in portfolio_snapshot.held_positions:
        analysis = analysis_by_symbol.get(position.symbol)
        display_name = f"{position.symbol} {position.name}".strip()
        if analysis is not None:
            net_pnl = int(analysis.sell_decision.details.get("net_pnl_krw", 0))
            unrealized_gross += int(analysis.sell_decision.details.get("gross_pnl_krw", 0))
            unrealized_net += net_pnl
            contribution_rows.append((analysis.display_name, net_pnl))
            continue
        gross_pnl = int(position.gross_pnl)
        unrealized_gross += gross_pnl
        unrealized_net += gross_pnl
        contribution_rows.append((display_name, gross_pnl))
    top_contributor = None
    bottom_contributor = None
    if contribution_rows:
        top_contributor = max(contribution_rows, key=lambda item: item[1])
        bottom_contributor = min(contribution_rows, key=lambda item: item[1])

    print("=== 오늘 성과 요약 ===")
    print(f"실현손익: {format_signed_krw(int(realized_summary['realized_gross_pnl_krw']))}")
    print(f"미실현손익: {format_signed_krw(unrealized_gross)}")
    print(
        f"비용 반영 순손익: "
        f"{format_signed_krw(int(realized_summary['realized_net_pnl_krw']) + unrealized_net)}"
    )
    if top_contributor is not None:
        print(f"최고 기여 종목: {top_contributor[0]} ({format_signed_krw(int(top_contributor[1]))})")
    else:
        print("최고 기여 종목: 없음")
    if bottom_contributor is not None:
        print(f"최저 기여 종목: {bottom_contributor[0]} ({format_signed_krw(int(bottom_contributor[1]))})")
    else:
        print("최저 기여 종목: 없음")
    print()


# ---------------------------------------------------------------------------
# Cycle-conclusion / buy-summary console helpers (relocated from app.main,
# Stage A slice A1). Kept as thin delegators to the runtime snapshot/scan
# printers so the exact operator-facing output is preserved. app.main imports
# these under their historical underscore names.
# ---------------------------------------------------------------------------


def _print_cycle_conclusion(
    *,
    side: str,
    display_name: str,
    reason: str,
    planned_qty: int | None = None,
) -> None:
    return _runtime_snapshots.print_cycle_conclusion(
        side=side,
        display_name=display_name,
        reason=reason,
        planned_qty=planned_qty,
    )


def _print_buy_pre_gating_summary(
    *,
    pre_gating: dict[str, object],
    cycle_id: str,
    market_open: bool,
    market_session: str | None,
) -> None:
    return _rs.print_buy_pre_gating_summary(
        pre_gating=pre_gating,
        cycle_id=cycle_id,
        market_open=market_open,
        market_session=market_session,
        log_engine_event=_log_engine_event,
    )


def _print_buy_runtime_filter_summary(
    *,
    before_results,
    after_results,
    cycle_id: str | None = None,
    market_open: bool = False,
    market_session: str | None = None,
) -> None:
    return _rs.print_buy_runtime_filter_summary(
        before_results=before_results,
        after_results=after_results,
        cycle_id=cycle_id,
        market_open=market_open,
        market_session=market_session,
        log_engine_event=_log_engine_event,
    )
