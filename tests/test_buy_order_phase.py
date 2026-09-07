"""Behavior-lock unit tests for the run_cycle BUY-order phase (Stage B-3 slice M).

``docs/main_run_cycle_slimming_plan_20260703.md`` §3 国면 15: the BUY-order
execution block (cycle-budget skip gate -> ``buy_flow_context`` seed ->
``run_buy_order_flow_handler`` / ``_apply_buy_flow_context`` block-local defs ->
``OrderGate(buy_handler=...).process_ready_intents`` -> success ``return`` /
order-gate-skip ``return`` / ``except: _apply_buy_flow_context(); raise``) is
extracted verbatim from ``run_cycle`` into
``app.runtime.cycle_phases.buy_order_phase``.

Early-``return``/``raise`` mapping (国면 outcome -> ``run_cycle`` signal):
  * budget skip (:1148 orig) -> ``return True``
  * order-gate skip (buy_flow_result is None) -> ``return True``
  * success end -> ``return True`` (run_cycle returns; finally still finalizes)
  * ``except`` re-``raise`` -> re-``raise`` (after ``_apply_buy_flow_context``)
Every path returns or raises; the block is the tail of the top-level ``try``
body, so the phase never falls through (a trailing ``return False`` is
unreachable-by-construction and only guards against a future edit).

M-1 (closure reference injection): ``run_buy_order_flow_handler`` /
``_apply_buy_flow_context`` are block-local defs that move with the block; the
collaborators the handler forwards to ``_buy_flow.run_buy_order_flow`` —
``sell_order_flow`` (run_cycle OrderGate closure), ``execution_budget_wait``,
``_send_order_slack_notification`` — plus ``record_order_gate_summary`` stay in
run_cycle and are injected **by reference**; this module never reconstructs them.

M-2 (dual-path ctx reflection): the ``try / except Exception:
_apply_buy_flow_context(); raise`` skeleton is preserved verbatim — a raising
flow reflects the partial ``buy_flow_context`` into ``ctx`` before re-raising so
the run_cycle E/finalize sees it.

No file or network is touched.
"""

from __future__ import annotations

import unittest
from unittest import mock

from app.runtime.cycle_phases.context import CycleContext


def _noop(*args, **kwargs):
    return None


def _flow_result(*, snapshot="snap", sizing="sizing"):
    return mock.Mock(
        execution_snapshot=snapshot,
        position_sizing=sizing,
        sell_position_sizing="sell_sizing",
        buy_risk_guard_payload={"buy_guard": True},
        sell_risk_guard_payload={"sell_guard": True},
        rebalance_buy_preview={"rebalance": True},
        quality_rebalance_preview={"quality": True},
        active_sell_analysis="active_sell",
        rebalance_selection_ms=12.5,
        execution_tail_backoff_drain_ms=3.0,
        rate_limit_source="  buy_scan  ",
    )


def _seed_ctx():
    ctx = CycleContext()
    ctx.selected_candidate = mock.Mock(symbol="005930", display_name="삼성전자", score=1.5)
    ctx.execution_snapshot = None
    ctx.position_sizing = None
    ctx.sell_position_sizing = None
    ctx.buy_risk_guard_payload = None
    ctx.sell_risk_guard_payload = None
    ctx.rebalance_buy_preview = None
    ctx.quality_rebalance_preview = None
    ctx.active_sell_analysis = None
    ctx.rebalance_selection_ms = None
    ctx.execution_tail_backoff_drain_ms = 0.0
    ctx.rate_limit_source = None
    ctx.effective_buy_settings = mock.Mock()
    ctx.token = "tok"
    ctx.portfolio_snapshot = mock.Mock()
    ctx.scan_results = ()
    ctx.sell_analysis_results = ()
    ctx.selection_details = {"selection_reason": "best candidate"}
    ctx.regime_state = mock.Mock()
    ctx.daily_pnl_brake_state = mock.Mock()
    ctx.market_open = True
    ctx.session_status = mock.Mock(session="REGULAR")
    ctx.sell_watch_partial = False
    ctx.sell_watch_partial_reason = None
    ctx.timing_summary = {}
    ctx.quote_age_max_ms = None
    ctx.order_gate_last_skip_reason = None
    return ctx


def _wire(ctx, *, buy_handler_flow, cycle_budget=None, order_gate_enabled=True, overrides=None):
    from app.runtime.cycle_phases import buy_order_phase as mod

    settings = mock.Mock(
        order_gate_enabled=order_gate_enabled,
        buy_scan_total_budget_seconds=25.0,
    )
    state: dict[str, object] = {}
    api_budget_state: dict[str, object] = {"budget": True}
    cycle_budget = cycle_budget or mock.Mock(
        remaining_seconds=20.0,
        exceeded=False,
        should_skip_stage=mock.Mock(return_value=False),
    )

    kwargs = dict(
        settings=settings,
        state=state,
        api_budget_state=api_budget_state,
        cycle_id="cid",
        cycle_budget=cycle_budget,
        order_type="market_buy",
        sell_check_due=False,
        record_order_gate_summary=_noop,
        sell_order_flow=_noop,
        execution_budget_wait=_noop,
        get_korean_now=lambda: __import__("datetime").datetime(2026, 7, 4, 9, 30, 0),
        _record_cycle_action=_noop,
        _print_cycle_conclusion=_noop,
        _send_order_slack_notification=_noop,
    )
    if overrides:
        kwargs.update(overrides)
    # ``_buy_flow`` is imported directly by the phase module (not patched on
    # main scope in the suite); the handler calls ``_buy_flow.run_buy_order_flow``
    # so we patch that seam to inject a recorder/raiser.
    with mock.patch.object(mod._buy_flow, "run_buy_order_flow", side_effect=buy_handler_flow):
        result = mod.run_buy_order_phase(ctx, **kwargs)
    return result, state


class RunBuyOrderPhaseTest(unittest.TestCase):
    def test_success_path_returns_true_and_reflects_flow_context(self) -> None:
        ctx = _seed_ctx()
        calls: list[dict] = []

        def _flow(**kwargs):
            calls.append(kwargs)
            return _flow_result()

        result, _state = _wire(ctx, buy_handler_flow=_flow)

        self.assertTrue(result)
        # handler invoked once with the load-bearing kwargs
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertIs(call["effective_buy_settings"], ctx.effective_buy_settings)
        self.assertEqual(call["order_type"], "market_buy")
        # M-2: _apply_buy_flow_context reflected the flow result into ctx
        self.assertEqual(ctx.execution_snapshot, "snap")
        self.assertEqual(ctx.position_sizing, "sizing")
        self.assertEqual(ctx.buy_risk_guard_payload, {"buy_guard": True})
        self.assertEqual(ctx.rebalance_buy_preview, {"rebalance": True})
        self.assertEqual(ctx.execution_tail_backoff_drain_ms, 3.0)
        # rate_limit_source is stripped -> "buy_scan"
        self.assertEqual(ctx.rate_limit_source, "buy_scan")

    def test_budget_skip_returns_true_records_hold_and_skips_flow(self) -> None:
        ctx = _seed_ctx()
        cycle_budget = mock.Mock(
            remaining_seconds=1.0,
            exceeded=True,
            should_skip_stage=mock.Mock(return_value=True),
        )
        recorded: list[dict] = []

        def _flow(**kwargs):
            recorded.append(kwargs)
            return _flow_result()

        actions: list[str] = []

        result, _state = _wire(
            ctx,
            buy_handler_flow=_flow,
            cycle_budget=cycle_budget,
            overrides={
                "_record_cycle_action": lambda _state, *, action, **_k: actions.append(action),
            },
        )

        self.assertTrue(result)
        self.assertEqual(recorded, [])  # flow NOT invoked
        self.assertEqual(ctx.buy_status_text, "skipped(cycle_budget_low)")
        self.assertEqual(ctx.buy_scan_skipped_reason, "cycle_budget_low")
        self.assertEqual(ctx.budget_exceeded_stage, "before_buy_order")
        self.assertTrue(ctx.cycle_budget_exceeded)
        self.assertEqual(actions, ["HOLD_CYCLE_BUDGET_BUY_ORDER"])

    def test_flow_raises_propagates_and_reflects_partial_context(self) -> None:
        ctx = _seed_ctx()

        class _Boom(RuntimeError):
            pass

        def _flow(*, flow_context, **kwargs):
            # simulate a partial write into flow_context before raising
            flow_context["execution_snapshot"] = "partial_snap"
            flow_context["rate_limit_source"] = "buy_scan"
            raise _Boom("flow blew up")

        with self.assertRaises(_Boom):
            _wire(ctx, buy_handler_flow=_flow)

        # M-2: except path applied buy_flow_context into ctx before re-raise
        self.assertEqual(ctx.execution_snapshot, "partial_snap")
        self.assertEqual(ctx.rate_limit_source, "buy_scan")

    def test_order_gate_skip_returns_true_and_records_hold(self) -> None:
        ctx = _seed_ctx()
        ctx.order_gate_last_skip_reason = "duplicate_intent"

        # Patch OrderGate so the gate returns no processed handler_result ->
        # buy_flow_result is None -> order-gate skip return True.
        actions: list[str] = []

        def _flow(**kwargs):  # pragma: no cover - should not be reached via gate skip
            raise AssertionError("handler should not be called directly on gate skip")

        from app.runtime.cycle_phases import buy_order_phase as mod

        skip_decision = mock.Mock(status="skipped", payload={})
        gate_result = mock.Mock(last_decision=skip_decision)
        fake_gate = mock.Mock()
        fake_gate.process_ready_intents.return_value = gate_result

        with mock.patch.object(mod, "OrderGate", return_value=fake_gate):
            result, _state = _wire(
                ctx,
                buy_handler_flow=_flow,
                overrides={
                    "_record_cycle_action": lambda _state, *, action, **_k: actions.append(action),
                },
            )

        self.assertTrue(result)
        self.assertEqual(ctx.buy_scan_skipped_reason, "duplicate_intent")
        self.assertEqual(ctx.buy_status_text, "skipped(duplicate_intent)")
        self.assertEqual(actions, ["HOLD_ORDER_GATE_BUY"])


if __name__ == "__main__":
    unittest.main()
