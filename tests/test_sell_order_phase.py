"""Behavior-lock unit tests for the run_cycle SELL-order phase (Stage B-3 slice L).

``docs/main_run_cycle_slimming_plan_20260703.md`` §3 国면 11: the SELL-candidate
selection + SELL-order execution block (plus the ``scan_only`` diagnostic BUY
path) is extracted verbatim from ``run_cycle`` into
``app.runtime.cycle_phases.sell_order_phase``. The block sits inside the
``if ctx.portfolio_snapshot.held_positions and sell_check_due:`` guard; its three
early ``return``s map 1:1 to ``run_sell_order_phase`` returning ``True``
(scan_only end / strict-sell-first), while the rate-limit defer branch returns
``True`` too (SELL skipped, run_cycle short-circuits BUY). A normal SELL that
does not short-circuit falls through -> ``return False`` so ``run_cycle``
continues into ``run_buy_scan_phase``.

These tests wire the extracted function with a stub ``CycleContext`` seeded with
the upstream products (``portfolio_snapshot`` / ``sell_analysis_results`` /
``prioritized_positions``), inject a ``sell_order_flow`` recorder (the OrderGate
closure stays in run_cycle — L-2), and lock:
  * t1 — a live SELL candidate calls the injected ``sell_order_flow`` exactly once
    with the load-bearing kwargs (incl. ``api_budget_state``); non-strict returns
    ``False`` (BUY continues).
  * t2 — a ``sell_watch`` rate-limit state defers: ``sell_order_flow`` is NOT
    called, the retry cursor is written to ``state``, and the phase returns
    ``True``.
No file or network is touched.
"""

from __future__ import annotations

import unittest
from unittest import mock

from app.runtime.cycle_phases.context import CycleContext


def _noop(*args, **kwargs):
    return None


def _position(symbol):
    return mock.Mock(symbol=symbol, name=symbol)


def _candidate(symbol, *, display_name=None, trigger="STOP_LOSS", holding_qty=10, price=1000):
    return mock.Mock(
        symbol=symbol,
        display_name=display_name or symbol,
        holding_qty=holding_qty,
        sell_decision=mock.Mock(
            triggered_rule_name=trigger,
            to_log_payload=lambda: {"rule": trigger},
        ),
        buy_strategy_result=mock.Mock(to_log_payload=lambda: {"buy": True}),
        market_snapshot=mock.Mock(current_price=price),
    )


def _sizing():
    return mock.Mock(
        recommended_sell_qty=5,
        recommended_notional_krw=5000,
        sell_trigger="STOP_LOSS",
        details={
            "estimated_sell_fee_krw": 1,
            "estimated_sell_tax_krw": 2,
            "estimated_sell_slippage_krw": 3,
            "estimated_net_proceeds_krw": 4994,
        },
    )


def _wire(ctx, *, sell_order_flow, selected_candidate=None, overrides=None):
    from app.runtime.cycle_phases.sell_order_phase import run_sell_order_phase

    settings = mock.Mock(
        run_mode="trade",
        strict_sell_first=True,
        enable_rebalance_sell=False,
        confirm_buy="NO",
    )
    state: dict[str, object] = {}
    api_budget_state: dict[str, object] = {"budget": True}

    kwargs = dict(
        settings=settings,
        state=state,
        api_budget_state=api_budget_state,
        cycle_id="cid",
        cycle_budget=mock.Mock(),
        sell_check_due=True,
        note=_noop,
        sell_order_flow=sell_order_flow,
        execution_budget_wait=_noop,
        resolve_buy_universe_symbols=lambda: ((), {}),
        log_buy_universe_source=_noop,
        get_korean_now=lambda: "NOW",
        get_account_scope_context=lambda: {"account_signature": "acct"},
        scan_target_symbols=_noop,
        get_last_scan_diagnostics=lambda: {},
        get_adaptive_pacing_summary=lambda: {"active": False, "extra_delay_ms": 0.0},
        select_top_candidate=lambda *a, **k: None,
        build_universe_console_lines=lambda *a, **k: [],
        build_scan_console_lines=lambda *a, **k: [],
        resolve_mock_buy_price_floor_krw=lambda *a, **k: 0,
        _apply_buy_runtime_guards_to_scan_results=lambda results, **k: results,
        _record_cycle_action=_noop,
        _log_engine_event=_noop,
        _print_cycle_conclusion=_noop,
        _print_buy_runtime_filter_summary=_noop,
        _api_budget_note_rate_limit=_noop,
        _api_budget_backoff_remaining_seconds=lambda *a, **k: 0,
    )
    if overrides:
        kwargs.update(overrides)

    with (
        mock.patch(
            "app.runtime.cycle_phases.sell_order_phase.calculate_sell_position_sizing",
            return_value=_sizing(),
        ),
        mock.patch(
            "app.runtime.cycle_phases.sell_order_phase.select_top_sell_candidate",
            side_effect=lambda results: selected_candidate,
        ),
        mock.patch(
            "app.runtime.cycle_phases.sell_order_phase.select_top_sell_analysis",
            side_effect=lambda results: selected_candidate,
        ),
    ):
        result = run_sell_order_phase(ctx, **kwargs)
    return result, state


class RunSellOrderPhaseTest(unittest.TestCase):
    def _seed_ctx(self, candidate, *, positions=None):
        ctx = CycleContext()
        ctx.portfolio_snapshot = mock.Mock(held_positions=[_position("005930")])
        ctx.sell_analysis_results = (candidate,) if candidate else ()
        ctx.prioritized_positions = positions or [_position("005930")]
        ctx.rate_limit_triggered = False
        ctx.rate_limit_source = ""
        ctx.market_open = True
        ctx.token = "tok"
        ctx.session_status = mock.Mock(session="REGULAR")
        return ctx

    def test_live_candidate_calls_sell_order_flow_once_and_continues(self) -> None:
        candidate = _candidate("005930")
        ctx = self._seed_ctx(candidate, positions=[_position("005930")])

        recorded: list[dict] = []

        def _flow(**kwargs):
            recorded.append(kwargs)
            return False  # not consumed -> BUY continues

        # non-strict so a consumed SELL would still continue; here not consumed
        result, _state = _wire(
            ctx,
            sell_order_flow=_flow,
            selected_candidate=candidate,
        )

        # not consumed -> falls through -> False (run_cycle continues to BUY)
        self.assertFalse(result)
        self.assertEqual(len(recorded), 1)
        call = recorded[0]
        self.assertIs(call["analysis"], candidate)
        self.assertEqual(call["api_budget_state"], {"budget": True})
        self.assertEqual(call["cycle_action_label"], "매도 주문")
        self.assertIn("sell_sizing", call)
        self.assertIn("flow_context", call)

    def test_strict_sell_first_consumed_returns_true(self) -> None:
        candidate = _candidate("005930")
        ctx = self._seed_ctx(candidate, positions=[_position("005930")])

        def _flow(**kwargs):
            return True  # consumed

        result, _state = _wire(
            ctx,
            sell_order_flow=_flow,
            selected_candidate=candidate,
        )
        # strict_sell_first + consumed -> early return True (BUY skipped)
        self.assertTrue(result)

    def test_rate_limit_defer_skips_flow_and_records_cursor(self) -> None:
        candidate = _candidate("000660")
        ctx = self._seed_ctx(candidate, positions=[_position("005930"), _position("000660")])
        ctx.rate_limit_triggered = True
        ctx.rate_limit_source = "sell_watch"

        called: list[dict] = []

        def _flow(**kwargs):
            called.append(kwargs)
            return True

        result, state = _wire(
            ctx,
            sell_order_flow=_flow,
            selected_candidate=candidate,
        )

        # defer path: SELL order flow NOT invoked, cursor recorded, phase returns True
        self.assertTrue(result)
        self.assertEqual(called, [])
        self.assertEqual(state["sell_watch_retry_symbol"], "000660")
        self.assertEqual(state["sell_watch_next_start_index"], 1)  # index of 000660
        self.assertEqual(ctx.sell_status_text, "skipped(rate_limit_backoff)")

    def test_no_candidate_falls_through_returns_false(self) -> None:
        ctx = self._seed_ctx(None)

        result, _state = _wire(
            ctx,
            sell_order_flow=_noop,
            selected_candidate=None,
        )
        # no SELL candidate -> falls through the guard body -> False
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
