"""Behavior-lock unit tests for the run_cycle regime phase (Stage B-3 slice I).

``docs/main_run_cycle_slimming_plan_20260703.md`` §3 국면 10: the SELL post-
processing / account-state payload / daily-pnl brake / **regime 확정** / 성과
프린트 block is extracted verbatim from ``run_cycle`` into
``app.runtime.cycle_phases.regime_phase``. The original block has no early
``return`` — the phase always falls through, so ``run_regime_phase`` always
returns ``False`` (convention consistency with the sibling gate phases). These
tests wire the extracted function with a minimal stub ``CycleContext`` seeded
with the upstream-phase products, inject no-op/recorder collaborators, and
assert the load-bearing side effects (regime 확정, ``ctx.effective_buy_settings``
= ``replace`` product, ``state["current_regime"]`` write, daily-pnl-brake
plumbing) without touching any file or network.

Gate C2 (spec): ``ctx.effective_buy_settings`` is written **only** here — the
single writer — so the test asserts the phase both computes it (via the injected
``replace`` recorder) and stores it on ctx.
"""

from __future__ import annotations

import unittest
from unittest import mock

from app.runtime.cycle_phases.context import CycleContext


def _noop(*args, **kwargs):
    return None


def _portfolio_snapshot(*, held=True, position_count=1):
    return mock.Mock(held_positions=held, position_count=position_count)


def _regime_dict(regime="TREND", *, reason="ok", multiplier=1.0):
    return {
        "current_regime": regime,
        "regime_reason": reason,
        "regime_multiplier": multiplier,
        "effective_buy_max_budget_per_trade_krw": 1_000_000,
        "effective_buy_max_account_exposure_pct": 0.5,
        "effective_buy_max_qty_per_trade": 10,
        "effective_rebuy_cooldown_minutes": 30,
        "effective_same_symbol_max_buys_per_day": 2,
        "effective_buy_daily_max_order_submissions": 20,
    }


def _account_state_payload():
    return {
        "deployment_invariant_equity_krw": 5_000_000,
        "operating_equity_krw": 4_000_000,
    }


def _wire(ctx, *, settings, state, sell_check_due, overrides=None):
    """Call run_regime_phase with recorder collaborators; return recorders."""
    from app.runtime.cycle_phases.regime_phase import run_regime_phase

    replace_recorder = mock.Mock(return_value="EFFECTIVE_SETTINGS")
    build_regime = mock.Mock(return_value=_regime_dict())
    build_brake = mock.Mock(return_value={"pause_active": False})
    build_brake_obs = mock.Mock(return_value={"observability": True})

    kwargs = dict(
        settings=settings,
        state=state,
        sell_check_due=sell_check_due,
        build_account_state_payload=lambda **kw: _account_state_payload(),
        build_current_drawdown_state=lambda **kw: {"drawdown": 0.0},
        _build_today_realized_summary=lambda *a, **kw: {"realized": 0},
        _build_daily_pnl_brake_state=build_brake,
        _build_daily_pnl_brake_observability=build_brake_obs,
        _build_regime_state=build_regime,
        replace=replace_recorder,
        _print_account_balance_interpretation=_noop,
        _print_regime_state=_noop,
        _print_daily_pnl_brake_state=_noop,
        _print_portfolio_positions=_noop,
        _print_today_performance_summary=_noop,
        _print_today_bought_tracking=_noop,
    )
    if overrides:
        kwargs.update(overrides)

    result = run_regime_phase(ctx, **kwargs)
    return result, {
        "replace": replace_recorder,
        "build_regime": build_regime,
        "build_brake": build_brake,
        "build_brake_obs": build_brake_obs,
    }


class RunRegimePhaseTest(unittest.TestCase):
    def _seed_ctx(self):
        ctx = CycleContext()
        ctx.portfolio_snapshot = _portfolio_snapshot()
        ctx.sell_analysis_results = ()
        ctx.market_open = True
        ctx.sell_watch_partial = False
        ctx.sell_watch_partial_reason = None
        ctx.sell_watch_cursor_before = 0
        ctx.sell_watch_cursor_after = 0
        ctx.sell_watch_evaluated_symbols = []
        ctx.sell_watch_skipped_symbols = []
        ctx.sell_evaluated_count = 0
        return ctx

    def test_returns_false_and_confirms_regime_and_effective_settings(self) -> None:
        ctx = self._seed_ctx()
        state: dict[str, object] = {}
        settings = mock.Mock(run_mode="normal")

        result, rec = _wire(ctx, settings=settings, state=state, sell_check_due=True)

        # convention: this phase has no early return -> always False
        self.assertFalse(result)
        # regime 확정: ctx.regime_state is the build_regime product, state mirrors it
        self.assertEqual(ctx.regime_state["current_regime"], "TREND")
        self.assertEqual(state["current_regime"], "TREND")
        self.assertEqual(state["regime_reason"], "ok")
        self.assertEqual(state["regime_multiplier"], 1.0)
        # gate C2: effective_buy_settings is the replace() product, stored on ctx
        rec["replace"].assert_called_once()
        self.assertEqual(ctx.effective_buy_settings, "EFFECTIVE_SETTINGS")
        # replace was called with the base settings positionally
        self.assertIs(rec["replace"].call_args.args[0], settings)
        # daily-pnl brake state is confirmed on ctx (base merged with observability)
        self.assertTrue(ctx.daily_pnl_brake_state["observability"])
        # sell_lane_running cleared
        self.assertFalse(ctx.sell_lane_running)

    def test_daily_pnl_brake_state_flows_into_regime_build(self) -> None:
        ctx = self._seed_ctx()
        state: dict[str, object] = {}
        settings = mock.Mock(run_mode="normal")

        # brake state carrying an active pause must be the object fed to regime build
        brake = mock.Mock(return_value={"pause_active": True, "reason": "daily loss"})

        result, rec = _wire(
            ctx,
            settings=settings,
            state=state,
            sell_check_due=True,
            overrides={"_build_daily_pnl_brake_state": brake},
        )

        self.assertFalse(result)
        # regime build received the (observability-merged) brake state via kwarg
        regime_call = rec["build_regime"].call_args
        passed_brake = regime_call.kwargs["daily_pnl_brake_state"]
        self.assertTrue(passed_brake["pause_active"])
        self.assertEqual(passed_brake["reason"], "daily loss")
        # and the merged brake state carries the observability overlay too
        self.assertTrue(passed_brake["observability"])


if __name__ == "__main__":
    unittest.main()
