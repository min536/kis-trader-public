"""Behavior-lock unit tests for the run_cycle SELL-watch phase (Stage B-3 slice K).

``docs/main_run_cycle_slimming_plan_20260703.md`` §3 国면 9: the SELL watch
evaluation loop (priority ordering / cursor / budget plan / per-symbol quote /
rate-limit) is extracted verbatim from ``run_cycle`` into
``app.runtime.cycle_phases.sell_watch_phase``. The block has no early ``return``
(only ``break``/``continue`` inside the ``for`` loop), so ``run_sell_watch_phase``
always returns ``False``; ``run_cycle`` continues into the phase-10 regime call.

These tests wire the extracted function with a stub ``CycleContext`` seeded with
the upstream account-snapshot products (``portfolio_snapshot`` / ``market_open`` /
``token``), inject recorder/no-op collaborators, and assert the load-bearing side
effects — analysis append + cursor-state write on the happy path, and the 200-OK
``EGW00201`` rate-limit branch's retry-anchor / ``next_start_index`` writes +
``ctx.rate_limit_triggered`` + loop ``break`` — without touching any file or
network. The pure builders imported into the phase module
(``build_sell_watch_priority`` / ``build_market_snapshot`` / ``build_sell_analysis``)
are patched on the module seam so the test locks the phase's *wiring*, not the
builders (which have their own tests).
"""

from __future__ import annotations

import unittest
from unittest import mock

from app.runtime.cycle_phases.context import CycleContext


def _noop(*args, **kwargs):
    return None


def _position(symbol, *, name=None, holding_qty=10, average_cost=1000):
    return mock.Mock(
        symbol=symbol,
        name=name or symbol,
        holding_qty=holding_qty,
        average_cost=average_cost,
    )


def _priority_dict():
    return {"priority_score": 1.0, "summary": "ok", "weight_pct": 0.0, "loss_pressure": 0.0}


class _CursorPlan:
    def __init__(self, ordered_indices, *, start_index=0, retry_index=0):
        self.ordered_indices = ordered_indices
        self.start_index = start_index
        self.retry_index = retry_index
        self.clear_retry_symbol = False
        self.used_retry_anchor = False


class _BudgetPlan:
    def __init__(self, *, max_evaluations, pressure_level="ok", reason=None):
        self.max_evaluations = max_evaluations
        self.pressure_level = pressure_level
        self.reason = reason


def _permissive_api_budget():
    """Every ``_api_budget_*`` gate returns a value that lets the loop proceed."""
    return dict(
        _api_budget_request_window_size=lambda *a, **k: 0,
        _api_budget_remaining_requests=lambda *a, **k: 10,
        _api_budget_remaining_quotes=lambda *a, **k: 10,
        _api_budget_min_wait_for_request_slot=lambda *a, **k: 0.0,
        _api_budget_can_quote=lambda *a, **k: True,
        _api_budget_can_request=lambda *a, **k: True,
        _api_budget_register_request=lambda *a, **k: None,
        _api_budget_backoff_remaining_seconds=lambda *a, **k: 0,
    )


def _wire(ctx, *, inquire_price, budget_plan=None, cursor_indices=(0, 1), overrides=None):
    from app.runtime.cycle_phases.sell_watch_phase import run_sell_watch_phase

    settings = mock.Mock(
        api_soft_max_requests_per_second=5,
        api_buy_scan_min_request_reserve=0,
        api_buy_scan_min_quote_reserve=0,
        confirm_buy="NO",
    )
    state: dict[str, object] = {}
    api_budget_state: dict[str, object] = {}
    scheduler_state: dict[str, object] = {"decision": "SCAN"}

    kwargs = dict(
        settings=settings,
        state=state,
        api_budget_state=api_budget_state,
        scheduler_state=scheduler_state,
        cycle_id="cid",
        sell_check_due=True,
        note=_noop,
        get_korean_now=lambda: "NOW",
        inquire_price=inquire_price,
        get_account_scope_context=lambda: {"account_signature": "acct"},
        build_sell_watch_budget_plan=lambda **kw: budget_plan or _BudgetPlan(max_evaluations=99),
        _log_engine_event=_noop,
        _api_budget_note_rate_limit=_noop,
        **_permissive_api_budget(),
    )
    if overrides:
        kwargs.update(overrides)

    with (
        mock.patch(
            "app.runtime.cycle_phases.sell_watch_phase.build_sell_watch_priority",
            side_effect=lambda **kw: _priority_dict(),
        ),
        mock.patch(
            "app.runtime.cycle_phases.sell_watch_phase.build_sell_watch_cursor_plan",
            side_effect=lambda **kw: _CursorPlan(list(cursor_indices)),
        ),
        mock.patch(
            "app.runtime.cycle_phases.sell_watch_phase.retry_anchor_to_restore_after_empty_budget",
            return_value=None,
        ),
        mock.patch(
            "app.runtime.cycle_phases.sell_watch_phase.build_market_snapshot",
            side_effect=lambda output: mock.Mock(payload=output),
        ),
        mock.patch(
            "app.runtime.cycle_phases.sell_watch_phase._build_sell_analysis",
            side_effect=lambda **kw: f"ANALYSIS::{kw['symbol']}",
        ),
    ):
        result = run_sell_watch_phase(ctx, **kwargs)
    return result, state


class RunSellWatchPhaseTest(unittest.TestCase):
    def _seed_ctx(self, positions):
        ctx = CycleContext()
        ctx.portfolio_snapshot = mock.Mock(
            held_positions=positions,
            position_count=len(positions),
        )
        ctx.market_open = True
        ctx.token = "tok"
        ctx.buy_scan_due = False
        ctx.buy_scan_budget_reserved = False
        ctx.session_status = mock.Mock(session="REGULAR")
        return ctx

    def test_happy_path_appends_analysis_and_updates_cursor(self) -> None:
        positions = [_position("005930"), _position("000660")]
        ctx = self._seed_ctx(positions)

        def _ok_quote(symbol, *, token=None):
            return {"rt_cd": "0", "output": {"symbol": symbol}}

        result, state = _wire(ctx, inquire_price=_ok_quote)

        # convention: no early return -> always False
        self.assertFalse(result)
        # both holdings evaluated -> analysis appended for each. Priority rows tie on
        # score (both 1.0) so the real tiebreak sorts by symbol string: 000660 first.
        self.assertEqual(
            ctx.sell_analysis_results,
            ("ANALYSIS::000660", "ANALYSIS::005930"),
        )
        self.assertEqual(ctx.sell_evaluated_count, 2)
        self.assertEqual(ctx.sell_watch_evaluated_symbols, ["000660", "005930"])
        # gate K-1: the priority-ordered list is stored on ctx for phase 11
        self.assertEqual(
            [p.symbol for p in ctx.prioritized_positions],
            ["000660", "005930"],
        )
        # cursor advanced and persisted to state
        self.assertEqual(ctx.sell_watch_cursor_after, 0)  # (0 + 2) % 2
        self.assertEqual(state["sell_watch_next_start_index"], 0)
        self.assertFalse(ctx.sell_watch_partial)

    def test_rate_limit_200_ok_body_sets_retry_anchor_and_breaks(self) -> None:
        positions = [_position("005930"), _position("000660")]
        ctx = self._seed_ctx(positions)

        calls: list[str] = []

        def _rate_limited_quote(symbol, *, token=None):
            calls.append(symbol)
            # 200-OK body carrying the KIS rate-limit code
            return {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과"}

        result, state = _wire(ctx, inquire_price=_rate_limited_quote)

        self.assertFalse(result)
        # rate-limit ctx flags set on the 200-OK body path
        self.assertTrue(ctx.rate_limit_triggered)
        self.assertEqual(ctx.rate_limit_source, "sell_watch")
        self.assertTrue(ctx.sell_watch_partial)
        # retry anchor + next_start_index recorded for the next tick. The priority
        # tiebreak sorts 000660 ahead of 005930, so it is the first symbol queried.
        self.assertEqual(state["sell_watch_retry_symbol"], "000660")
        self.assertIn("sell_watch_next_start_index", state)
        # loop broke on the very first symbol (second never queried)
        self.assertEqual(calls, ["000660"])
        # no analysis appended (broke before build)
        self.assertEqual(ctx.sell_analysis_results, ())


if __name__ == "__main__":
    unittest.main()
