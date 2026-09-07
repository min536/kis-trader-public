"""Behavior-lock unit tests for the run_cycle BUY-scan phase (Stage B-3 slice J).

``docs/main_run_cycle_slimming_plan_20260703.md`` §3 국면 12·13·14: the BUY
cadence gate → universe/pre-gating/shallow build → deep-eval cap / prefetch join /
scan → ranking/selection/empty-scan classification block is extracted verbatim
from ``run_cycle`` into ``app.runtime.cycle_phases.buy_scan_phase``. The original
block has 13 early ``return`` points (each maps to ``return True``) plus one
``raise RuntimeError`` (kept as a raise, propagates to run_cycle's top-level try);
when a candidate is selected it falls through to ``return False`` and
``run_cycle`` continues into the BUY-order phase 15.

These tests wire the extracted function with a minimal stub ``CycleContext``
seeded with the upstream-phase products, inject no-op/recorder collaborators, and
assert the load-bearing control flow (True on the cadence-skip and budget-skip
early HOLDs; False + ctx selection on the happy path) without touching any file
or network.
"""

from __future__ import annotations

import unittest
from unittest import mock

from app.runtime.cycle_phases.context import CycleContext


def _noop(*args, **kwargs):
    return None


def _session_status(session="REGULAR"):
    return mock.Mock(session=session)


def _portfolio_snapshot():
    return mock.Mock(held_positions=(), position_count=0)


def _base_kwargs(*, state, recorder):
    """Full keyword-argument bundle for run_buy_scan_phase with recorders."""
    return dict(
        settings=mock.Mock(
            run_mode="trade",
            target_symbols=("005930",),
            confirm_buy="NO",
            buy_scan_top_k_candidates=3,
            qty=1,
            strict_sell_first=False,
        ),
        state=state,
        api_budget_state={"rate_limit_hits": 0},
        scheduler_state={"decision": ""},
        cycle_id="CID",
        cycle_budget=mock.Mock(
            should_skip_stage=mock.Mock(return_value=False),
            exceeded=False,
            bounded_wait_seconds=mock.Mock(return_value=1.0),
        ),
        order_type="market_buy",
        note=recorder["note"],
        resolve_buy_universe_symbols=lambda: (("005930",), {"source": "test"}),
        log_buy_universe_source=_noop,
        get_korean_now=lambda: "NOW",
        _api_budget_backoff_active=lambda *a, **k: False,
        _api_budget_backoff_remaining_seconds=lambda *a, **k: 0,
        _api_budget_transient_backoff_active=lambda *a, **k: False,
        _api_budget_request_window_size=lambda *a, **k: 0,
        _api_budget_can_quote=lambda *a, **k: True,
        _api_budget_can_request=lambda *a, **k: True,
        _api_budget_min_wait_for_request_slot=lambda *a, **k: 0.0,
        _api_budget_note_rate_limit=_noop,
        _cap_buy_scan_deep_eval_symbols_for_api_budget=lambda **k: (
            tuple(k["symbols"]),
            {"budget_cap_applied": False, "budget_cap_reasons": []},
        ),
        _select_buy_scan_profile=lambda *a, **k: {"profile": "momentum", "rotation_enabled": False},
        _build_buy_scan_layered_universe=lambda **k: {
            "selected_symbols": ("005930",),
            "layer_by_symbol": {"005930": "core"},
            "preview": {},
        },
        _build_buy_scan_pre_gating=lambda **k: {
            "scan_allowed": True,
            "allowed_symbols": ("005930",),
            "rejected": [],
            "reason_counts": {},
            "early_reject_count": 0,
        },
        _build_buy_scan_shallow_plan=lambda **k: {
            "ranked_count": 1,
            "deep_eval_limit": 1,
            "exploration_quota_used": 0,
            "shortlist_symbols": ("005930",),
            "shortlist_preview": [],
        },
        _build_buy_scan_deep_eval_symbols=lambda **k: ("005930",),
        _apply_buy_runtime_guards_to_scan_results=lambda results, **k: tuple(results),
        _record_cycle_action=recorder["record"],
        _log_engine_event=_noop,
        _print_cycle_conclusion=recorder["conclusion"],
        _print_buy_pre_gating_summary=_noop,
        _print_buy_scan_stage_summary=_noop,
        _print_buy_runtime_filter_summary=_noop,
        _print_buy_strategy=_noop,
        _print_buy_score_summary=_noop,
        scan_target_symbols=_noop,
        get_last_scan_diagnostics=lambda: {},
        get_adaptive_pacing_summary=lambda: {"active": False, "extra_delay_ms": 0.0},
        select_top_candidate=lambda results, **k: (results[0] if results else None),
        select_top_analysis_result=lambda results: (results[0] if results else None),
        serialize_selection_details=lambda **k: {"selection_reason": "picked"},
        buy_scan_uses_separate_quote_account=lambda settings: False,
        build_universe_console_lines=lambda settings: [],
        build_scan_console_lines=lambda **k: [],
        log_order_event=_noop,
        mark_buy_blocked=_noop,
        resolve_mock_buy_price_floor_krw=lambda settings: 0,
    )


def _recorder():
    return {
        "note": mock.Mock(),
        "record": mock.Mock(),
        "conclusion": mock.Mock(),
    }


def _seed_ctx(**over):
    ctx = CycleContext()
    ctx.buy_scan_due = True
    ctx.session_status = _session_status()
    ctx.market_open = True
    ctx.portfolio_snapshot = _portfolio_snapshot()
    ctx.effective_buy_settings = mock.Mock(run_mode="trade")
    ctx.daily_pnl_brake_state = {}
    ctx.regime_state = {}
    ctx.buy_quote_prefetch_future = None
    ctx.buy_quote_prefetch_snapshot_info = None
    ctx.buy_quote_prefetch_price_data_by_symbol = {}
    for k, v in over.items():
        setattr(ctx, k, v)
    return ctx


class RunBuyScanPhaseTest(unittest.TestCase):
    def test_cadence_not_due_returns_true_and_records_skip(self) -> None:
        # t1: buy_scan_due=False -> cadence-gate early HOLD -> return True + skip note
        ctx = _seed_ctx(buy_scan_due=False)
        state: dict[str, object] = {}
        rec = _recorder()

        from app.runtime.cycle_phases.buy_scan_phase import run_buy_scan_phase

        result = run_buy_scan_phase(ctx, **_base_kwargs(state=state, recorder=rec))

        self.assertTrue(result)
        self.assertEqual(ctx.buy_status_text, "skipped(cadence)")
        self.assertEqual(ctx.buy_scan_skipped_reason, "cadence_not_reached")
        rec["record"].assert_called_once()
        self.assertEqual(rec["record"].call_args.kwargs["action"], "HOLD_BUY_SCAN_WAIT")

    def test_cycle_budget_skip_returns_true_and_holds(self) -> None:
        # t2: budget skip path -> return True + HOLD action observed by recorder
        ctx = _seed_ctx()
        state: dict[str, object] = {}
        rec = _recorder()
        kwargs = _base_kwargs(state=state, recorder=rec)
        # cadence passes (buy_scan_due True), but cycle budget is exhausted
        kwargs["cycle_budget"] = mock.Mock(
            should_skip_stage=mock.Mock(return_value=True),
            exceeded=True,
        )

        from app.runtime.cycle_phases.buy_scan_phase import run_buy_scan_phase

        result = run_buy_scan_phase(ctx, **kwargs)

        self.assertTrue(result)
        self.assertEqual(ctx.buy_status_text, "skipped(cycle_budget_low)")
        self.assertEqual(ctx.buy_scan_skipped_reason, "cycle_budget_low")
        rec["record"].assert_called_once()
        self.assertEqual(
            rec["record"].call_args.kwargs["action"], "HOLD_CYCLE_BUDGET_BUY_SCAN"
        )

    def test_happy_path_selects_candidate_and_returns_false(self) -> None:
        # t3: injected scanner/ranker return one candidate -> fall-through -> False
        ctx = _seed_ctx()
        state: dict[str, object] = {}
        rec = _recorder()
        kwargs = _base_kwargs(state=state, recorder=rec)

        candidate = mock.Mock(
            symbol="005930",
            display_name="삼성전자",
            market_snapshot=mock.Mock(),
        )
        kwargs["scan_target_symbols"] = lambda **k: (candidate,)
        kwargs["select_top_candidate"] = lambda results, **k: candidate
        kwargs["select_top_analysis_result"] = lambda results: candidate

        from app.runtime.cycle_phases.buy_scan_phase import run_buy_scan_phase

        result = run_buy_scan_phase(ctx, **kwargs)

        self.assertFalse(result)
        self.assertIs(ctx.selected_candidate, candidate)
        self.assertIs(ctx.top_analysis_result, candidate)
        self.assertEqual(ctx.raw_scan_results, (candidate,))

    def test_quarantined_symbol_is_filtered_from_scan_universe(self) -> None:
        # F4 (E4): a symbol quarantined earlier today is dropped before the scan.
        ctx = _seed_ctx()
        state: dict[str, object] = {"dead_scan_symbols_today": ["008560"]}
        rec = _recorder()
        kwargs = _base_kwargs(state=state, recorder=rec)
        # 2-symbol universe so removing the quarantined one still leaves a live
        # scan; widen pre-gating so only the quarantine filter can drop 008560.
        kwargs["_build_buy_scan_deep_eval_symbols"] = lambda **k: ("005930", "008560")
        kwargs["_build_buy_scan_pre_gating"] = lambda **k: {
            "scan_allowed": True,
            "allowed_symbols": ("005930", "008560"),
            "rejected": [],
            "reason_counts": {},
            "early_reject_count": 0,
        }

        candidate = mock.Mock(symbol="005930", display_name="삼성전자", market_snapshot=mock.Mock())
        captured: dict[str, object] = {}

        def _capture_scan(**k):
            captured["symbols"] = k.get("symbols")
            return (candidate,)

        kwargs["scan_target_symbols"] = _capture_scan
        kwargs["select_top_candidate"] = lambda results, **k: candidate
        kwargs["select_top_analysis_result"] = lambda results: candidate

        from app.runtime.cycle_phases.buy_scan_phase import run_buy_scan_phase

        run_buy_scan_phase(ctx, **kwargs)

        self.assertIn("symbols", captured)
        symbols = tuple(captured["symbols"] or ())
        self.assertNotIn("008560", symbols)  # quarantined -> filtered
        self.assertIn("005930", symbols)  # healthy -> retained


if __name__ == "__main__":
    unittest.main()
