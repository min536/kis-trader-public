"""Behavior-lock unit tests for the run_cycle account-snapshot phase (slice H).

``docs/main_run_cycle_slimming_plan_20260703.md`` §3 국면 8: the token-issuance +
BUY-quote-prefetch-start + balance-inquiry block (with its nested
try/except/finally, including the balance rate-limit early-HOLD and the scan_only
diagnostic fallback) is extracted verbatim from ``run_cycle`` into
``app.runtime.cycle_phases.account_snapshot``. The function returns ``True`` when
``run_cycle`` must immediately ``return`` (a 1:1 map of the original early
``return`` points), ``False`` to continue into the SELL-watch phase. These tests
wire the extracted function with a stub ``CycleContext`` and injected
collaborators, then assert the load-bearing branch outcomes without touching any
file or network.
"""

from __future__ import annotations

import unittest
from unittest import mock

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
        _sync_reconciliation_state=lambda **kw: None,
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


class RunAccountSnapshotPhaseTest(unittest.TestCase):
    def test_normal_path_returns_false_and_sets_portfolio_snapshot(self) -> None:
        from app.runtime.cycle_phases.account_snapshot import (
            run_account_snapshot_phase,
        )

        ctx = CycleContext()
        ctx.session_status = _session_status("REGULAR")
        ctx.market_open = True
        ctx.buy_scan_due = False
        ctx.buy_scan_separate_quote_lane = False
        snapshot = _portfolio_snapshot(position_count=3)

        should_return = run_account_snapshot_phase(
            ctx,
            **_phase_kwargs(
                build_portfolio_snapshot=lambda data: snapshot,
            ),
        )

        self.assertFalse(should_return)
        self.assertIs(ctx.portfolio_snapshot, snapshot)
        self.assertEqual(ctx.token, "TOKEN-XYZ")
        self.assertIn("balance_inquiry", ctx.timing_summary)

    def test_capital_scale_report_emits_once_and_records_warning(self) -> None:
        from app.runtime.cycle_phases.account_snapshot import (
            run_account_snapshot_phase,
        )

        ctx = CycleContext()
        ctx.session_status = _session_status("REGULAR")
        ctx.market_open = True
        ctx.buy_scan_due = False
        ctx.buy_scan_separate_quote_lane = False
        snapshot = _portfolio_snapshot()
        snapshot.total_evaluation_amount = 100_000_000
        state = {"account_signature": "mock_acct_test"}
        note = mock.Mock()

        with mock.patch("builtins.print") as printed:
            should_return = run_account_snapshot_phase(
                ctx,
                **_phase_kwargs(
                    settings=mock.Mock(
                        run_mode="regular",
                        scan_symbols_max_per_cycle=10,
                        target_symbols=(),
                        buy_max_budget_per_trade_krw=5_000_000,
                        buy_daily_max_notional_krw=500_000,
                        sell_daily_max_notional_krw=7_000_000,
                    ),
                    state=state,
                    note=note,
                    build_portfolio_snapshot=lambda data: snapshot,
                ),
            )

        self.assertFalse(should_return)
        self.assertIn("capital_scale_report_key", state)
        note.assert_any_call(
            "WARN",
            mock.ANY,
        )
        printed_lines = [str(call.args[0]) for call in printed.call_args_list if call.args]
        self.assertTrue(
            any("자본 스케일 점검" in line for line in printed_lines),
            printed_lines,
        )

        with mock.patch("builtins.print") as printed_again:
            second_return = run_account_snapshot_phase(
                ctx,
                **_phase_kwargs(
                    settings=mock.Mock(
                        run_mode="regular",
                        scan_symbols_max_per_cycle=10,
                        target_symbols=(),
                        buy_max_budget_per_trade_krw=5_000_000,
                        buy_daily_max_notional_krw=500_000,
                        sell_daily_max_notional_krw=7_000_000,
                    ),
                    state=state,
                    note=mock.Mock(),
                    build_portfolio_snapshot=lambda data: snapshot,
                ),
            )

        self.assertFalse(second_return)
        printed_again_lines = [
            str(call.args[0]) for call in printed_again.call_args_list if call.args
        ]
        self.assertFalse(
            any("자본 스케일 점검" in line for line in printed_again_lines),
            printed_again_lines,
        )

    def test_balance_rate_limit_returns_true_and_notes_backoff(self) -> None:
        from app.runtime.cycle_phases.account_snapshot import (
            run_account_snapshot_phase,
        )

        ctx = CycleContext()
        ctx.session_status = _session_status("REGULAR")
        ctx.market_open = True
        ctx.buy_scan_due = False
        ctx.buy_scan_separate_quote_lane = False
        note = mock.Mock()
        record_cycle_action = mock.Mock()
        note_rate_limit = mock.Mock()

        def _raise_rate_limit(**kwargs):
            raise RuntimeError("잔고 조회 실패 초당 거래건수 초과 EGW00201")

        should_return = run_account_snapshot_phase(
            ctx,
            **_phase_kwargs(
                inquire_balance=_raise_rate_limit,
                note=note,
                _record_cycle_action=record_cycle_action,
                _api_budget_note_rate_limit=note_rate_limit,
            ),
        )

        self.assertTrue(should_return)
        note_rate_limit.assert_called_once()
        self.assertEqual(note_rate_limit.call_args.kwargs["source"], "balance")
        self.assertTrue(ctx.rate_limit_triggered)
        self.assertEqual(ctx.rate_limit_source, "balance")
        recorded_action = record_cycle_action.call_args.kwargs["action"]
        self.assertEqual(recorded_action, "HOLD_BALANCE_RATE_LIMIT")


if __name__ == "__main__":
    unittest.main()
