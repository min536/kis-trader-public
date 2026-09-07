"""R-c phase-wiring tests (gate C5b/C6): _notify_reconciliation injection.

Design: docs/manual_trade_reconciliation_design_20260704.md §4.
- None (default) → notify never called (existing phase tests stay green).
- spy → called once with ctx.reconciliation_report.
- raising spy → phase returns normally (False), no scan_only fallback entered (C5b).

Mirrors the wiring/fixtures of tests/test_account_snapshot_phase.py without
modifying that (unmodified) gate file.
"""
from __future__ import annotations

import unittest
from unittest import mock

from app.runtime.cycle_phases.account_snapshot import run_account_snapshot_phase
from app.runtime.cycle_phases.context import CycleContext


def _noop(*args, **kwargs):
    return None


def _session_status(session="REGULAR", *, order_allowed=True, reason="ok"):
    return mock.Mock(session=session, order_allowed=order_allowed, reason=reason)


def _portfolio_snapshot(position_count=0):
    return mock.Mock(
        position_count=position_count,
        total_evaluation_amount=0,
        cash_total=0,
        cash_orderable=0,
        cash_next_day=0,
        held_positions=[],
    )


def _phase_kwargs(**overrides):
    kwargs = dict(
        settings=mock.Mock(
            run_mode="regular",
            scan_symbols_max_per_cycle=10,
            target_symbols=(),
        ),
        state={},
        api_budget_state={},
        scheduler_state={},
        sell_check_due=False,
        cycle_id="20260704T000000-deadbeef",
        cycle_budget=mock.Mock(should_skip_stage=lambda **kw: False),
        note=mock.Mock(),
        resolve_buy_universe_symbols=lambda: ((), {}),
        log_buy_universe_source=_noop,
        get_korean_now=mock.Mock(),
        issue_access_token=lambda: "TOKEN-XYZ",
        inquire_balance=lambda **kw: {"rt_cd": "0"},
        build_portfolio_snapshot=lambda data: _portfolio_snapshot(),
        build_account_state_payload=mock.Mock(),
        build_current_drawdown_state=mock.Mock(),
        _api_budget_register_requests=_noop,
        _api_budget_register_request=_noop,
        _api_budget_register_measured_extra_requests=lambda *a, **kw: 0,
        _api_budget_min_wait_for_request_slot=lambda *a, **kw: 0.0,
        _api_budget_can_request=lambda *a, **kw: True,
        _api_budget_note_rate_limit=mock.Mock(),
        _api_budget_backoff_remaining_seconds=lambda *a, **kw: 0.0,
        _sync_reconciliation_state=lambda **kw: {"summary": "s", "events": [], "event_count": 0},
        _build_today_realized_summary=mock.Mock(),
        _build_daily_pnl_brake_state=mock.Mock(),
        _build_daily_pnl_brake_observability=mock.Mock(),
        _build_regime_state=mock.Mock(),
        _build_scan_only_runtime_mode_preview=mock.Mock(),
        _build_scan_only_diagnostic_summary=mock.Mock(),
        _record_cycle_action=mock.Mock(),
        _log_engine_event=_noop,
        _print_cycle_conclusion=_noop,
        _print_account_balance_interpretation=_noop,
        _print_regime_state=_noop,
        _print_daily_pnl_brake_state=_noop,
        _print_portfolio_positions=_noop,
        _print_today_performance_summary=_noop,
    )
    kwargs.update(overrides)
    return kwargs


def _fresh_ctx():
    ctx = CycleContext()
    ctx.session_status = _session_status("REGULAR")
    ctx.market_open = True
    ctx.buy_scan_due = False
    ctx.buy_scan_separate_quote_lane = False
    return ctx


class NotifyReconciliationInjectionTests(unittest.TestCase):
    def test_default_none_never_calls_notify(self) -> None:
        ctx = _fresh_ctx()
        # No _notify_reconciliation kwarg passed -> default None -> no-op.
        should_return = run_account_snapshot_phase(ctx, **_phase_kwargs())
        self.assertFalse(should_return)

    def test_spy_called_once_with_report(self) -> None:
        ctx = _fresh_ctx()
        report = {"summary": "s", "events": [], "event_count": 0}
        spy = mock.Mock()
        should_return = run_account_snapshot_phase(
            ctx,
            **_phase_kwargs(
                _sync_reconciliation_state=lambda **kw: report,
                _notify_reconciliation=spy,
            ),
        )
        self.assertFalse(should_return)
        spy.assert_called_once_with(report)

    def test_notify_helper_bound_to_failing_notifier_does_not_break_phase(self) -> None:
        # C5b: the production notify helper is the guard (its whole body is a
        # try/except -> return False, C5a). When it is bound to a *failing*
        # slack notifier and injected into the phase, the phase must still
        # return normally (False) and NOT trip the scan_only diagnostic fallback.
        from functools import partial

        from app.notifications.reconciliation_alerts import (
            maybe_notify_manual_trade_suspects,
        )

        ctx = _fresh_ctx()
        record_cycle_action = mock.Mock()
        report = {
            "summary": "s",
            "event_count": 1,
            "events": [
                {
                    "type": "unexpected_missing_position",
                    "symbol": "005930",
                    "expected_qty": 10,
                    "actual_qty": 0,
                    "reason": "-",
                    "classification": "suspected_manual_sell",
                    "detected_at": "t",
                }
            ],
        }

        def failing_notify(*a, **kw):
            raise RuntimeError("slack down")

        notify_reconciliation = lambda rep: maybe_notify_manual_trade_suspects(
            rep, notify=failing_notify
        )

        should_return = run_account_snapshot_phase(
            ctx,
            **_phase_kwargs(
                _sync_reconciliation_state=lambda **kw: report,
                _notify_reconciliation=notify_reconciliation,
                _record_cycle_action=record_cycle_action,
            ),
        )
        self.assertFalse(should_return)
        # scan_only fallback would have recorded SCAN_ONLY_DIAGNOSTIC.
        actions = [c.kwargs.get("action") for c in record_cycle_action.call_args_list]
        self.assertNotIn("SCAN_ONLY_DIAGNOSTIC", actions)


if __name__ == "__main__":
    unittest.main()
