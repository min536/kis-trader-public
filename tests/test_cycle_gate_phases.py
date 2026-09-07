"""Behavior-lock unit tests for the run_cycle gate phases (Stage B-3 slice G).

``docs/main_run_cycle_slimming_plan_20260703.md`` §3 국면 6·7: the session
判定 / light-session early-HOLD block and the API-budget gate block are extracted
verbatim from ``run_cycle`` into ``app.runtime.cycle_phases.session_gate`` /
``budget_gate``. Each gate returns ``True`` when ``run_cycle`` must immediately
``return`` (a 1:1 map of the original early ``return`` points), ``False`` to
continue. These tests wire the extracted functions with a minimal stub
``CycleContext`` and all-no-op injected collaborators, then assert the
load-bearing branch outcomes without touching any file or network.
"""

from __future__ import annotations

import unittest
from unittest import mock

from app.runtime.cycle_phases.context import CycleContext


def _noop(*args, **kwargs):
    return None


def _session_status(session="REGULAR", *, order_allowed=True, reason="ok"):
    return mock.Mock(session=session, order_allowed=order_allowed, reason=reason)


class RunSessionGateTest(unittest.TestCase):
    def test_light_session_returns_true_and_records_waiting(self) -> None:
        from app.runtime.cycle_phases.session_gate import run_session_gate

        ctx = CycleContext()
        ctx.regime_state = {
            "current_regime": "TREND",
            "regime_reason": "seed",
            "regime_multiplier": 1.0,
        }
        state: dict[str, object] = {}
        record_cycle_action = mock.Mock()

        should_return = run_session_gate(
            ctx,
            settings=mock.Mock(),
            state=state,
            api_budget_state={},
            cycle_id="20260704T000000-deadbeef",
            session_check_started_perf=0.0,
            get_korean_market_session=lambda: _session_status(
                "PREMARKET", order_allowed=False, reason="장전"
            ),
            get_korean_now=mock.Mock(),
            build_market_session_console_lines=lambda *a, **kw: [],
            _summarize_api_budget_state=lambda *a, **kw: {},
            _should_run_light_session_cycle=lambda **kw: True,
            _print_premarket_wait_notice=_noop,
            _print_cycle_conclusion=_noop,
            _record_cycle_action=record_cycle_action,
            _log_engine_event=_noop,
            _write_slack_runtime_status_snapshot=_noop,
        )

        self.assertTrue(should_return)
        record_cycle_action.assert_called_once()
        recorded_action = record_cycle_action.call_args.kwargs["action"]
        self.assertTrue(str(recorded_action).startswith("WAITING"))

    def test_regular_session_returns_false_and_sets_market_open(self) -> None:
        from app.runtime.cycle_phases.session_gate import run_session_gate

        ctx = CycleContext()
        ctx.regime_state = {
            "current_regime": "TREND",
            "regime_reason": "seed",
            "regime_multiplier": 1.0,
        }
        state: dict[str, object] = {}
        record_cycle_action = mock.Mock()

        should_return = run_session_gate(
            ctx,
            settings=mock.Mock(),
            state=state,
            api_budget_state={},
            cycle_id="20260704T000000-deadbeef",
            session_check_started_perf=0.0,
            get_korean_market_session=lambda: _session_status(
                "REGULAR", order_allowed=True
            ),
            get_korean_now=mock.Mock(),
            build_market_session_console_lines=lambda *a, **kw: [],
            _summarize_api_budget_state=lambda *a, **kw: {},
            _should_run_light_session_cycle=lambda **kw: False,
            _print_premarket_wait_notice=_noop,
            _print_cycle_conclusion=_noop,
            _record_cycle_action=record_cycle_action,
            _log_engine_event=_noop,
            _write_slack_runtime_status_snapshot=_noop,
        )

        self.assertFalse(should_return)
        self.assertTrue(ctx.market_open)
        record_cycle_action.assert_not_called()


def _budget_gate_kwargs(**overrides):
    kwargs = dict(
        settings=mock.Mock(
            api_buy_scan_min_request_reserve=1,
            api_buy_scan_min_quote_reserve=1,
        ),
        state={},
        api_budget_state={},
        scheduler_state={},
        sell_check_due=False,
        cycle_id="20260704T000000-deadbeef",
        note=mock.Mock(),
        get_korean_now=mock.Mock(),
        _buy_scan_reserve_active=lambda **kw: False,
        _api_budget_transient_backoff_active=lambda *a, **kw: False,
        _api_budget_transient_backoff_remaining_seconds=lambda *a, **kw: 0.0,
        _api_budget_backoff_active=lambda *a, **kw: False,
        _api_budget_backoff_remaining_seconds=lambda *a, **kw: 0.0,
        _api_budget_can_request=lambda *a, **kw: True,
        _print_cycle_conclusion=_noop,
        _record_cycle_action=_noop,
        _log_engine_event=_noop,
    )
    kwargs.update(overrides)
    return kwargs


class RunBudgetGateTest(unittest.TestCase):
    def test_transient_backoff_returns_true_and_notes_degraded(self) -> None:
        from app.runtime.cycle_phases.budget_gate import run_budget_gate

        ctx = CycleContext()
        ctx.session_status = _session_status("REGULAR")
        ctx.market_open = True
        note = mock.Mock()
        record_cycle_action = mock.Mock()

        should_return = run_budget_gate(
            ctx,
            **_budget_gate_kwargs(
                note=note,
                _record_cycle_action=record_cycle_action,
                _api_budget_transient_backoff_active=lambda *a, **kw: True,
                _api_budget_transient_backoff_remaining_seconds=lambda *a, **kw: 30.0,
            ),
        )

        self.assertTrue(should_return)
        note.assert_called_once()
        self.assertEqual(note.call_args.args[0], "DEGRADED")
        recorded_action = record_cycle_action.call_args.kwargs["action"]
        self.assertEqual(recorded_action, "HOLD_API_TRANSIENT_BACKOFF")

    def test_all_gates_pass_returns_false(self) -> None:
        from app.runtime.cycle_phases.budget_gate import run_budget_gate

        ctx = CycleContext()
        ctx.session_status = _session_status("REGULAR")
        ctx.market_open = True
        record_cycle_action = mock.Mock()

        should_return = run_budget_gate(
            ctx,
            **_budget_gate_kwargs(
                _record_cycle_action=record_cycle_action,
                _api_budget_transient_backoff_active=lambda *a, **kw: False,
                _api_budget_backoff_active=lambda *a, **kw: False,
                _api_budget_can_request=lambda *a, **kw: True,
            ),
        )

        self.assertFalse(should_return)
        record_cycle_action.assert_not_called()
