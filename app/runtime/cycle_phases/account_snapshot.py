"""``run_cycle`` account-snapshot phase — Stage B-3 slice H (국면 8).

``docs/main_run_cycle_slimming_plan_20260703.md`` §3 (국면 지도 8행 / "Stage B
실행 사양"): the token-issuance + BUY-quote-prefetch-start + balance-inquiry block
of ``app.main.run_cycle`` — including its nested ``try/except/finally`` (balance
rate-limit early-HOLD, the 141-line ``scan_only`` diagnostic fallback with its
simulated SELL+BUY paths, and the balance-timing ``finally``) plus the balance
console print and reconciliation-drift note — is moved here **byte-verbatim**
(logic/order/output unchanged; only indentation adjusted). The nested
try/except/finally moves as one unit — the original exception flow is preserved,
not decomposed. Each of the two original early ``return`` points (balance
rate-limit, scan_only fallback) maps 1:1 to ``return True``; when neither fires
the block falls through to ``return False`` and ``run_cycle`` continues into the
SELL-watch phase.

Deps discipline (§3 "deps 번들 주입" — individual keyword params, following the
slice-F/G precedent): every ``app.main`` collaborator that a test may patch
(blocklist-checked against ``main_patched_names.txt`` plus a multiline-``setattr``
scan) is received as an **identically named keyword parameter**, bound at the call
site from ``run_cycle``'s enclosing/module scope (including the run_cycle-local
closures ``note`` / ``resolve_buy_universe_symbols`` / ``log_buy_universe_source``
and the run_cycle-local ``cycle_budget``, and the two main-scope ``partial``
builders ``_build_scan_only_runtime_mode_preview`` /
``_build_scan_only_diagnostic_summary`` which capture patchable collaborators).
This keeps the moved body source-identical and keeps the late-binding patch
surface alive. Only non-blocklisted, un-patched pure utilities are imported
directly (the same imports finalize.py makes). This module must NOT import
``app.main``.

Note: ``ctx.token`` is a credential string; it is stored on the context (same
lifetime as the original local) but never printed or logged — the byte-verbatim
move adds no output the original lacked, so this holds automatically.
"""

from __future__ import annotations

import time
from typing import Any

from app.auth.token import get_request_metrics_summary
from app.core.error_classification import (
    looks_like_rate_limit_error as _looks_like_rate_limit_error,
    rate_limit_source_from_response_body as _rate_limit_source_from_response_body,
)
from app.core.formatters import format_krw
from app.core.reconciliation import print_reconciliation_report
from app.core.runtime_budget import request_metrics_delta as _request_metrics_delta
from app.market_data.live_snapshot import get_live_snapshot_signal
from app.pipeline import BUY_SCAN_LANE_CONTROLLER
from app.reporting.console import (
    phase_timing_summary as _phase_timing_summary,
    print_sell_decision as _print_sell_decision,
)
from app.reporting.capital_scale_report import (
    build_capital_scale_report,
    capital_scale_report_key,
    render_capital_scale_report,
)
from app.runtime.scan_only_diagnostic import (
    build_scan_only_diagnostic_portfolio_snapshot as _build_scan_only_diagnostic_portfolio_snapshot,
    build_scan_only_diagnostic_sell_analyses as _runtime_build_scan_only_diagnostic_sell_analyses,
    print_scan_only_diagnostic_buy_scan as _print_scan_only_diagnostic_buy_scan,
    print_scan_only_diagnostic_summary as _print_scan_only_diagnostic_summary,
    print_scan_only_notice as _print_scan_only_notice,
    print_scan_only_runtime_mode_preview as _print_scan_only_runtime_mode_preview,
)
from app.strategy.sell_decision import build_sell_analysis as _build_sell_analysis


def run_account_snapshot_phase(
    ctx: Any,
    *,
    settings: Any,
    state: Any,
    api_budget_state: Any,
    scheduler_state: Any,
    sell_check_due: Any,
    cycle_id: Any,
    cycle_budget: Any,
    note: Any,
    resolve_buy_universe_symbols: Any,
    log_buy_universe_source: Any,
    get_korean_now: Any,
    issue_access_token: Any,
    inquire_balance: Any,
    build_portfolio_snapshot: Any,
    build_account_state_payload: Any,
    build_current_drawdown_state: Any,
    _api_budget_register_requests: Any,
    _api_budget_register_request: Any,
    _api_budget_register_measured_extra_requests: Any,
    _api_budget_min_wait_for_request_slot: Any,
    _api_budget_can_request: Any,
    _api_budget_note_rate_limit: Any,
    _api_budget_backoff_remaining_seconds: Any,
    _sync_reconciliation_state: Any,
    _build_today_realized_summary: Any,
    _build_daily_pnl_brake_state: Any,
    _build_daily_pnl_brake_observability: Any,
    _build_regime_state: Any,
    _build_scan_only_runtime_mode_preview: Any,
    _build_scan_only_diagnostic_summary: Any,
    _record_cycle_action: Any,
    _log_engine_event: Any,
    _print_cycle_conclusion: Any,
    _print_account_balance_interpretation: Any,
    _print_regime_state: Any,
    _print_daily_pnl_brake_state: Any,
    _print_portfolio_positions: Any,
    _print_today_performance_summary: Any,
    _notify_reconciliation: Any = None,
) -> bool:
    balance_started_perf = time.perf_counter()
    balance_metrics_before = get_request_metrics_summary()
    balance_budget_registered_requests = 0
    try:
        token_metrics_before = get_request_metrics_summary()
        ctx.token = issue_access_token()
        token_metrics_after = get_request_metrics_summary()
        token_request_count = int(
            (
                (
                    (_request_metrics_delta(token_metrics_before, token_metrics_after).get("categories") or {})
                    .get("token")
                    or {}
                ).get("count", 0)
            )
        )
        if token_request_count > 0:
            _api_budget_register_requests(
                api_budget_state,
                now=get_korean_now(),
                request_count=token_request_count,
            )

        if (
            ctx.buy_scan_due
            and ctx.market_open
            and ctx.buy_scan_separate_quote_lane
        ):
            (
                ctx.buy_quote_prefetch_symbols,
                ctx.buy_quote_prefetch_snapshot_info,
            ) = resolve_buy_universe_symbols()
            prefetch_source_symbols = tuple(
                ctx.buy_quote_prefetch_symbols or settings.target_symbols
            )
            prefetch_symbols = prefetch_source_symbols[
                : settings.scan_symbols_max_per_cycle
            ]
            if prefetch_symbols:
                if cycle_budget.should_skip_stage(min_remaining_seconds=3.0):
                    ctx.buy_scan_due = False
                    ctx.buy_scan_skipped_reason = "cycle_budget_low"
                    ctx.buy_status_text = "skipped(cycle_budget_low)"
                else:
                    prefetch_start = BUY_SCAN_LANE_CONTROLLER.start_quote_prefetch(
                        scan_id=f"{cycle_id}:buy_scan",
                        symbols=prefetch_symbols,
                        settings=settings,
                        execution_token=ctx.token,
                        now=get_korean_now(),
                    )
                    ctx.buy_lane_running = prefetch_start.running
                    if prefetch_start.decision.allowed and prefetch_start.handle is not None:
                        ctx.buy_quote_prefetch_future = prefetch_start.handle.future
                        print(
                            "[info] BUY quote prefetch started"
                            " | lane=separate"
                            f" | symbols={len(prefetch_symbols)}"
                            f" | deadline={ctx.buy_quote_prefetch_deadline_seconds:.1f}s"
                        )
                    else:
                        ctx.buy_scan_due = False
                        ctx.buy_scan_skipped_reason = "previous_scan_running"
                        ctx.buy_status_text = "skipped(previous_scan_running)"
                        ctx.buy_lane_previous_scan_id = prefetch_start.decision.previous_scan_id
                        ctx.buy_lane_previous_started_at = (
                            prefetch_start.decision.previous_started_at
                        )
                        note(
                            "DEGRADED",
                            "BUY scan skipped because a previous BUY scan is still running.",
                        )

        balance_request_at = get_korean_now()
        # Stabilize the request window before issuing balance. Without this
        # guard, a fast cycle that just finished sell_watch / order
        # submission can fire balance immediately, bursting the next
        # 1-second budget window and tripping KIS EGW00201. Treat balance
        # like an internal multi-page call (~2 sub-requests) so the wait
        # leaves room for the pagination loop to complete inside the same
        # window without re-bursting.
        balance_guard_wait_seconds = _api_budget_min_wait_for_request_slot(
            api_budget_state,
            now=balance_request_at,
            request_cost=2,
        )
        if balance_guard_wait_seconds > 0:
            waited_ms = round(balance_guard_wait_seconds * 1000.0, 1)
            ctx.timing_summary["balance_guard_wait_ms"] = waited_ms
            print(
                "[info] balance inquiry reserve wait"
                f" | wait={int(round(waited_ms))}ms"
            )
            note(
                "INFO",
                "잔고 조회 전 request window를 안정화하기 위해 "
                f"{int(round(waited_ms))}ms 대기합니다.",
            )
            time.sleep(balance_guard_wait_seconds)
            balance_request_at = get_korean_now()
        if not _api_budget_can_request(api_budget_state, now=balance_request_at):
            raise RuntimeError("잔고 조회 전 API request 예산이 부족합니다.")
        _api_budget_register_request(api_budget_state, now=balance_request_at)
        balance_budget_registered_requests = 1
        try:
            balance_data = inquire_balance(token=ctx.token)
        except Exception as _bal_exc:
            if _looks_like_rate_limit_error(_bal_exc):
                ctx.rate_limit_triggered = True
                ctx.rate_limit_source = "balance"
                _api_budget_note_rate_limit(
                    api_budget_state,
                    now=get_korean_now(),
                    source="balance",
                )
                ctx.backoff_applied_seconds = int(
                    _api_budget_backoff_remaining_seconds(
                        api_budget_state, now=get_korean_now()
                    )
                )
                ctx.buy_status_text = "skipped(balance_rate_limit)"
                ctx.sell_status_text = "skipped(balance_rate_limit)"
                _bal_reason = (
                    "잔고 조회에서 KIS rate limit(EGW00201)을 감지해 "
                    f"이번 사이클을 건너뜁니다. (backoff={ctx.backoff_applied_seconds}s)"
                )
                note("DEGRADED", _bal_reason)
                print()
                _print_cycle_conclusion(
                    side="HOLD",
                    display_name="-",
                    reason=_bal_reason,
                    planned_qty=0,
                )
                _record_cycle_action(
                    state,
                    action="HOLD_BALANCE_RATE_LIMIT",
                    reason=_bal_reason,
                    selected_symbol=state.get("last_selected_symbol"),
                )
                _log_engine_event(
                    action="skipped_balance_rate_limit_backoff",
                    reason=_bal_reason,
                    cycle_id=cycle_id,
                    market_open=ctx.market_open,
                    market_session=ctx.session_status.session,
                )
                return True
            raise
        if balance_data.get("rt_cd") != "0":
            ctx.rate_limit_source = (
                _rate_limit_source_from_response_body(
                    balance_data,
                    source="balance",
                )
                or ctx.rate_limit_source
            )
            raise RuntimeError(f"잔고 조회 실패: {balance_data}")
        ctx.portfolio_snapshot = build_portfolio_snapshot(balance_data)
        ctx.reconciliation_report = _sync_reconciliation_state(
            state=state,
            portfolio_snapshot=ctx.portfolio_snapshot,
        )
        if _notify_reconciliation is not None:
            _notify_reconciliation(ctx.reconciliation_report)
    except Exception as exc:
        if settings.run_mode == "scan_only":
            print(
                "[warn] scan_only diagnostic fallback activated"
                f" | reason={exc}"
            )
            live_snapshot_symbols, snapshot_info = resolve_buy_universe_symbols()
            log_buy_universe_source(snapshot_info)
            ctx.portfolio_snapshot = _build_scan_only_diagnostic_portfolio_snapshot(
                settings=settings,
                symbols=tuple(live_snapshot_symbols or settings.target_symbols),
            )
            ctx.sell_analysis_results = _runtime_build_scan_only_diagnostic_sell_analyses(
                settings=settings,
                portfolio_snapshot=ctx.portfolio_snapshot,
                build_sell_analysis=_build_sell_analysis,
                get_live_snapshot_signal=get_live_snapshot_signal,
            )
            ctx.sell_evaluated_count = len(ctx.sell_analysis_results)
            ctx.sell_watch_total_holdings = ctx.portfolio_snapshot.position_count
            ctx.sell_watch_evaluated_symbols = [
                result.symbol for result in ctx.sell_analysis_results
            ]
            ctx.sell_status_text = f"diagnostic_simulated({ctx.sell_evaluated_count})"
            _print_account_balance_interpretation(
                portfolio_snapshot=ctx.portfolio_snapshot,
                sell_analysis_results=ctx.sell_analysis_results,
            )
            account_state_payload = build_account_state_payload(
                portfolio_snapshot=ctx.portfolio_snapshot,
                sell_analysis_results=ctx.sell_analysis_results,
            )
            realized_summary = _build_today_realized_summary(settings)
            ctx.daily_pnl_brake_state = _build_daily_pnl_brake_state(
                state=state,
                settings=settings,
                current_equity_krw=int(
                    account_state_payload["deployment_invariant_equity_krw"]
                ),
                operating_equity_krw=int(
                    account_state_payload["operating_equity_krw"]
                ),
            )
            ctx.daily_pnl_brake_state = {
                **ctx.daily_pnl_brake_state,
                **_build_daily_pnl_brake_observability(
                    brake_state=ctx.daily_pnl_brake_state,
                    account_state_payload=account_state_payload,
                    realized_summary=realized_summary,
                    portfolio_snapshot=ctx.portfolio_snapshot,
                ),
            }
            drawdown_state = build_current_drawdown_state(
                current_equity_krw=int(
                    account_state_payload["deployment_invariant_equity_krw"]
                ),
            )
            ctx.regime_state = _build_regime_state(
                settings=settings,
                daily_pnl_brake_state=ctx.daily_pnl_brake_state,
                drawdown_state=drawdown_state,
            )
            _print_regime_state(regime_state=ctx.regime_state)
            _print_daily_pnl_brake_state(
                brake_state=ctx.daily_pnl_brake_state,
                current_equity_krw=int(
                    account_state_payload["deployment_invariant_equity_krw"]
                ),
                settings=settings,
            )
            _print_portfolio_positions(ctx.portfolio_snapshot, ctx.sell_analysis_results)
            _print_today_performance_summary(
                portfolio_snapshot=ctx.portfolio_snapshot,
                sell_analysis_results=ctx.sell_analysis_results,
                settings=settings,
                realized_summary=realized_summary,
            )
            print("[info] sell cadence: due | SELL evaluation simulated (diagnostic)")
            _print_sell_decision(
                ctx.sell_analysis_results,
                settings=settings,
                holdings_count=ctx.portfolio_snapshot.position_count,
                partial=False,
            )
            ctx.requested_buy_symbols = tuple(live_snapshot_symbols or settings.target_symbols)
            ctx.selection_details["live_snapshot"] = snapshot_info
            buy_scan_summary = _print_scan_only_diagnostic_buy_scan(
                settings=settings,
                snapshot_info=snapshot_info,
                requested_symbols=ctx.requested_buy_symbols[
                    : settings.scan_symbols_max_per_cycle
                ],
            )
            ctx.buy_scan_requested_count = len(
                tuple(buy_scan_summary.get("requested_symbols") or ())
            )
            ctx.buy_scan_evaluated_count = len(
                tuple(buy_scan_summary.get("deep_eval_symbols") or ())
            )
            ctx.buy_scan_top_k_count = len(
                tuple(buy_scan_summary.get("shortlist_symbols") or ())
            )
            ctx.buy_status_text = (
                f"diagnostic_simulated({ctx.buy_scan_evaluated_count}/{ctx.buy_scan_requested_count})"
            )
            preview_rows = _build_scan_only_runtime_mode_preview(settings=settings)
            _print_scan_only_runtime_mode_preview(preview_rows=preview_rows)
            diagnostic_summary = _build_scan_only_diagnostic_summary(
                runtime_rate_control=(
                    scheduler_state.get("runtime_rate_control")
                    if isinstance(
                        scheduler_state.get("runtime_rate_control"), dict
                    )
                    else {}
                ),
                snapshot_info=snapshot_info,
                daily_pnl_brake_state=ctx.daily_pnl_brake_state,
                buy_scan_summary=buy_scan_summary,
                sell_analysis_results=ctx.sell_analysis_results,
                fallback_reason=str(exc),
            )
            _print_scan_only_notice()
            _print_scan_only_diagnostic_summary(diagnostic_summary)
            _print_cycle_conclusion(
                side="HOLD",
                display_name=str(
                    buy_scan_summary.get("selected_symbol") or "-"
                ),
                reason="scan_only diagnostic cycle complete",
                planned_qty=0,
            )
            _record_cycle_action(
                state,
                action="SCAN_ONLY_DIAGNOSTIC",
                reason=str(exc),
                selected_symbol=str(
                    buy_scan_summary.get("selected_symbol") or ""
                )
                or None,
            )
            return True
        raise
    finally:
        balance_metrics_after = get_request_metrics_summary()
        balance_request_delta = _request_metrics_delta(
            balance_metrics_before,
            balance_metrics_after,
        )
        balance_extra_request_count = _api_budget_register_measured_extra_requests(
            api_budget_state,
            request_delta=balance_request_delta,
            category="balance",
            already_registered_count=balance_budget_registered_requests,
            now=get_korean_now(),
        )
        if balance_extra_request_count > 0:
            print(
                "[info] balance inquiry measured extra requests"
                f" | count={balance_extra_request_count}"
            )
        ctx.timing_summary["balance_inquiry"] = _phase_timing_summary(
            elapsed_ms=(time.perf_counter() - balance_started_perf) * 1000,
            request_delta=balance_request_delta,
        )

    print("=== 잔고 조회 ===")
    print(f"보유 종목 수: {ctx.portfolio_snapshot.position_count}")
    print(
        "총 평가금액(잔고 raw): "
        f"{format_krw(ctx.portfolio_snapshot.total_evaluation_amount)}"
    )
    print(f"총예수금(원본): {format_krw(ctx.portfolio_snapshot.cash_total)}")
    print(
        f"주문가능현금(원본): {format_krw(ctx.portfolio_snapshot.cash_orderable)}"
    )
    print(f"익일정산예수금(원본): {format_krw(ctx.portfolio_snapshot.cash_next_day)}")
    print(
        "잔고 raw 필드: "
        f"dnca_tot_amt={format_krw(ctx.portfolio_snapshot.cash_total)}, "
        f"prvs_rcdl_excc_amt={format_krw(ctx.portfolio_snapshot.cash_orderable)}, "
        f"nxdy_excc_amt={format_krw(ctx.portfolio_snapshot.cash_next_day)}"
    )
    print()
    report_key = capital_scale_report_key(
        settings=settings,
        account_signature=str(state.get("account_signature") or ""),
    )
    if state.get("capital_scale_report_key") != report_key:
        capital_scale_report = build_capital_scale_report(
            settings=settings,
            portfolio_snapshot=ctx.portfolio_snapshot,
        )
        report_lines = render_capital_scale_report(capital_scale_report)
        if report_lines:
            for line in report_lines:
                print(line)
            state["capital_scale_report_key"] = report_key
            state["capital_scale_report_status"] = capital_scale_report.status
            state["capital_scale_report_warnings"] = list(
                capital_scale_report.warnings
            )
            for warning in capital_scale_report.warnings:
                note("WARN", warning)
    if ctx.reconciliation_report is not None:
        print_reconciliation_report(ctx.reconciliation_report)
        if ctx.reconciliation_report.get("event_count"):
            note(
                "WARN",
                f"reconciliation drift detected: {int(ctx.reconciliation_report.get('event_count', 0) or 0)}건",
            )
    return False
