"""P1 — lane-mode observability parity (docs/eod_lane_account_incident_20260704.md §3).

Under LANE_SCHEDULER_ENABLED=true the run_cycle lane branch back-populated some
sell counters but NOT ctx.session_status / scan-candidate fields, so cycle_stats
rows lost session/universe and candidate_outcomes_*.jsonl stopped being written.
These tests pin the observability parity fixes at the bridge boundary — telemetry
only, no order-path change.
"""

from __future__ import annotations

from unittest import mock

from tests.test_lane_scheduler_no_hooks import (
    _patch_boundaries,
    _session,
    _settings,
)

import app.pipeline.runtime_adapters as adapter_module
from app import main as main_module
from app.pipeline.main_bridge import run_lane_scheduler_main_bridge

from tests.test_lane_vs_legacy_equivalence import (
    _healthy_api_budget_state,
    _patch_legacy_main_boundaries,
    _patch_main_runtime_scaffold,
)


def _drive_lane_run_cycle_capturing_finalize(
    monkeypatch, *, sell_check_due=True, buy_scan_due=True
):
    """Drive run_cycle through the LANE path with the REAL finalize funnel /
    outcome builders (NOT stubbed), capturing what gets appended.

    Returns (captured_cycle_stats, captured_outcome_rows, saved_states).
    """
    orders: list[str] = []
    saved_states: list[dict[str, object]] = []
    _patch_boundaries(monkeypatch, orders=orders)
    _patch_legacy_main_boundaries(monkeypatch)
    _patch_main_runtime_scaffold(monkeypatch, saved_states)

    captured_stats: list[dict[str, object]] = []
    captured_outcomes: list[list[dict[str, object]]] = []
    monkeypatch.setattr(
        main_module,
        "append_cycle_stats",
        lambda stats, ts=None: captured_stats.append(dict(stats)) or True,
    )
    monkeypatch.setattr(
        main_module,
        "append_candidate_outcomes",
        lambda rows, ts=None: captured_outcomes.append(list(rows)) or True,
    )
    # Keep the rest of finalize's presentation boundaries quiet.
    for name in (
        "_run_cycle_market_data_quality_sentinel",
        "_resolve_benchmark_snapshot",
        "build_daily_summary",
        "build_cycle_stats_daily_summary",
        "_print_runtime_mode",
        "_print_applied_settings",
        "_print_test_mode",
        "_print_cycle_header",
        "_print_engine_schedule_state",
        "_print_cycle_timing",
        "_print_api_usage",
        "_print_sell_metrics",
        "_print_buy_scan_metrics",
        "_print_runtime_state_summary",
    ):
        monkeypatch.setattr(main_module, name, lambda *a, **k: None, raising=False)
    monkeypatch.setattr(
        main_module, "build_daily_summary_console_lines", lambda _s: [], raising=False
    )
    monkeypatch.setattr(
        main_module, "build_cycle_stats_console_lines", lambda _s: [], raising=False
    )
    # Real cycle_snapshot build; persist is a no-op capture.
    persisted: list[dict[str, object]] = []
    monkeypatch.setattr(
        main_module, "persist_cycle_snapshot", lambda snap: persisted.append(snap) or "ok"
    )
    monkeypatch.setattr(
        main_module, "get_cycle_snapshots_path", lambda settings=None: "cycle.jsonl"
    )
    monkeypatch.setattr(main_module.time, "sleep", lambda _s: None)

    settings = _settings(lane_scheduler_enabled=True)
    main_module.run_cycle(
        settings,
        sell_check_due=sell_check_due,
        buy_scan_due=buy_scan_due,
        scheduler_state={"cycle_id": "obs-cycle"},
        api_budget_state=_healthy_api_budget_state(),
    )
    return captured_stats, captured_outcomes, saved_states, persisted


def _run_bridge(
    monkeypatch, *, sell_due=True, buy_due=True, scheduler_state=None, session_func=None
):
    orders: list[str] = []
    _patch_boundaries(monkeypatch, orders=orders)
    # Applied LAST so it wins over _patch_boundaries' own session patch.
    if session_func is not None:
        monkeypatch.setattr(adapter_module, "get_korean_market_session", session_func)
    scheduler_state = scheduler_state if scheduler_state is not None else {
        "cycle_id": "obs",
        "lane_scheduler_runtime_context": {"state": {}, "timing_summary": {}},
    }
    result = run_lane_scheduler_main_bridge(
        _settings(),
        cycle_id="obs",
        sell_check_due=sell_due,
        buy_scan_due=buy_due,
        scheduler_state=scheduler_state,
        api_budget_state={},
        state=scheduler_state["lane_scheduler_runtime_context"]["state"],
        timing_summary={},
        send_order_slack_notification=None,
        wait_for_execution_request_budget=None,
    )
    return result, scheduler_state, orders


def test_bridge_exposes_lane_session_status_by_identity_without_recompute(monkeypatch):
    """C2: the mapping surfaces exactly the object the lane stored at
    scheduler_state['lane_scheduler_runtime_context']['session_status'] — by
    IDENTITY, and get_korean_market_session is NOT called an extra time by the
    mapping (the only legitimate compute is the lane's own account-context setup,
    which returns the SAME object the mapping then re-uses)."""
    real_session = _session()

    def counting_session():
        counting_session.calls += 1
        return real_session

    counting_session.calls = 0

    result, scheduler_state, _orders = _run_bridge(
        monkeypatch, session_func=counting_session
    )

    stored = scheduler_state["lane_scheduler_runtime_context"].get("session_status")
    # Lane stored its own computed session object into the runtime context.
    assert stored is real_session
    # Bridge result carries the SAME object by identity (no recompute, no copy).
    assert result.session_status is real_session
    # If the mapping recomputed, get_korean_market_session would have been called
    # more than the single time the lane's ensure_account_context needs it.
    assert counting_session.calls == 1


def test_bridge_exposes_lane_portfolio_snapshot_by_identity(monkeypatch):
    """The lane already fetched the account balance, so finalize must receive
    that exact snapshot instead of treating performance reporting as N/A."""
    result, scheduler_state, _orders = _run_bridge(monkeypatch)

    stored = scheduler_state["lane_scheduler_runtime_context"]["portfolio_snapshot"]
    assert stored is not None
    assert result.portfolio_snapshot is stored


def test_bridge_session_status_is_none_when_lane_has_no_session(monkeypatch):
    """C2 tail: when the lane stored no session_status, the bridge maps None
    (ctx.session_status stays None)."""
    result, scheduler_state, _orders = _run_bridge(
        monkeypatch,
        scheduler_state={
            "cycle_id": "obs",
            # No prior session stored, and a non-due cycle so the lane never
            # builds account context.
            "lane_scheduler_runtime_context": {"state": {}, "timing_summary": {}},
        },
    )
    # With both lanes not due, no session is computed/stored -> None.
    # (Re-run below with sell/buy not due.)
    result2, scheduler_state2, _ = _run_bridge(
        monkeypatch,
        sell_due=False,
        buy_due=False,
        scheduler_state={
            "cycle_id": "obs2",
            "lane_scheduler_runtime_context": {"state": {}, "timing_summary": {}},
        },
    )
    assert (
        scheduler_state2["lane_scheduler_runtime_context"].get("session_status")
        is None
    )
    assert result2.session_status is None


# ── Stage 1: run_cycle-level session_status mapping ─────────────────────────

def test_lane_run_cycle_populates_cycle_stats_session(monkeypatch):
    """Stage 1: with ctx.session_status mapped, the funnel-stats builder gets a
    real market_session (was None under the lane before the fix)."""
    stats, _outcomes, _saved, _persisted = _drive_lane_run_cycle_capturing_finalize(
        monkeypatch, sell_check_due=True, buy_scan_due=True
    )
    assert stats, "cycle_stats should be appended"
    assert stats[-1]["session"] == "REGULAR"


def test_lane_run_cycle_writes_last_market_session(monkeypatch):
    """C8: state['last_market_session'] mirrors ctx.session_status.session under
    the lane path (matches session_gate.py:51 semantics)."""
    _stats, _outcomes, saved, _persisted = _drive_lane_run_cycle_capturing_finalize(
        monkeypatch, sell_check_due=True, buy_scan_due=True
    )
    assert saved, "runtime state should be persisted"
    assert saved[-1]["last_market_session"] == "REGULAR"


def test_lane_run_cycle_persists_performance_from_lane_account_snapshot(monkeypatch):
    """Regression: lane mode used to end with snapshot/performance=OK/N/A even
    though its production adapter had already built a PortfolioSnapshot."""
    captured_snapshots = []
    monkeypatch.setattr(
        main_module,
        "build_performance_report",
        lambda **kwargs: captured_snapshots.append(kwargs["portfolio_snapshot"])
        or {"equity": {}},
    )
    monkeypatch.setattr(
        main_module,
        "persist_performance_report",
        lambda _report: {"snapshot_saved": True, "report_saved": True},
    )
    monkeypatch.setattr(
        main_module,
        "build_performance_console_lines",
        lambda _report: [],
    )

    _stats, _outcomes, saved, _persisted = _drive_lane_run_cycle_capturing_finalize(
        monkeypatch, sell_check_due=True, buy_scan_due=True
    )

    assert captured_snapshots
    assert captured_snapshots[-1].total_evaluation_amount > 0
    assert saved[-1]["last_performance_write_ok"] is True


def test_lane_market_open_not_mapped_keeps_sell_cadence_memory_frozen(monkeypatch):
    """C3 (ORCHESTRATOR DECISION): ctx.market_open must NOT be populated under the
    lane path, so the sell-cadence partial-streak memory
    (api_budget_state['consecutive_sell_watch_partial_cycles']) stays frozen —
    the lane path never advances it. This pins TODAY's behavior deliberately."""
    orders: list[str] = []
    saved_states: list[dict[str, object]] = []
    _patch_boundaries(monkeypatch, orders=orders)
    _patch_legacy_main_boundaries(monkeypatch)
    _patch_main_runtime_scaffold(monkeypatch, saved_states)
    for name in (
        "_run_cycle_market_data_quality_sentinel",
        "_resolve_benchmark_snapshot",
        "_build_buy_cycle_funnel_stats",
        "_build_buy_candidate_outcome_records",
        "append_candidate_outcomes",
        "append_cycle_stats",
        "build_daily_summary",
        "build_cycle_stats_daily_summary",
        "_print_runtime_mode",
        "_print_applied_settings",
        "_print_test_mode",
        "_print_cycle_header",
        "_print_engine_schedule_state",
        "_print_cycle_timing",
        "_print_api_usage",
        "_print_sell_metrics",
        "_print_buy_scan_metrics",
        "_print_runtime_state_summary",
    ):
        monkeypatch.setattr(main_module, name, lambda *a, **k: None, raising=False)
    monkeypatch.setattr(
        main_module, "build_daily_summary_console_lines", lambda _s: [], raising=False
    )
    monkeypatch.setattr(
        main_module, "build_cycle_stats_console_lines", lambda _s: [], raising=False
    )
    monkeypatch.setattr(main_module.time, "sleep", lambda _s: None)

    api_budget_state = _healthy_api_budget_state()
    api_budget_state["consecutive_sell_watch_partial_cycles"] = 3

    settings = _settings(lane_scheduler_enabled=True)
    main_module.run_cycle(
        settings,
        sell_check_due=True,
        buy_scan_due=True,
        scheduler_state={"cycle_id": "obs-frozen"},
        api_budget_state=api_budget_state,
    )
    # Frozen: the lane path did not touch the partial-streak counter.
    assert api_budget_state["consecutive_sell_watch_partial_cycles"] == 3


# ── Stage 2: candidate_outcomes regeneration ────────────────────────────────

def test_lane_run_cycle_regenerates_candidate_outcomes(monkeypatch):
    """Stage 2 (regeneration proof): a lane-mode scan that evaluated N symbols
    produces non-empty candidate-outcome rows with the correct market_session.
    Before the fix, ctx.requested_buy_symbols was empty so the builder returned
    []."""
    _stats, outcomes, _saved, _persisted = _drive_lane_run_cycle_capturing_finalize(
        monkeypatch, sell_check_due=True, buy_scan_due=True
    )
    assert outcomes, "append_candidate_outcomes should be called"
    rows = outcomes[-1]
    assert rows, "candidate-outcome rows must be non-empty under the lane path"
    assert all(row.get("session") == "REGULAR" for row in rows)


def test_lane_run_cycle_populates_buy_funnel_universe(monkeypatch):
    """Stage 2: cycle_stats universe/deep-eval/final-candidate fields are
    populated from the lane's genuinely-scanned symbols."""
    stats, _outcomes, _saved, _persisted = _drive_lane_run_cycle_capturing_finalize(
        monkeypatch, sell_check_due=True, buy_scan_due=True
    )
    row = stats[-1]
    assert row["universe_size"] >= 1
    assert row["deep_eval_count"] >= 1
    assert row["final_candidate_count"] == 1


def test_lane_outcomes_do_not_fabricate_universe_layered_out(monkeypatch):
    """C5: the lane scanned every symbol in its flat universe, so NO outcome row
    may report stage_reached='universe_layered_out' for a scanned symbol
    (layered_universe.selected_symbols must cover the scanned set)."""
    _stats, outcomes, _saved, _persisted = _drive_lane_run_cycle_capturing_finalize(
        monkeypatch, sell_check_due=True, buy_scan_due=True
    )
    rows = outcomes[-1]
    assert rows
    for row in rows:
        assert row.get("stage_reached") != "universe_layered_out", row


def test_lane_cycle_snapshot_omits_bulk_selection_details(monkeypatch):
    """C4 (LOAD-BEARING): the lane-mode cycle_snapshot row must NOT embed bulky
    per-symbol raw scan payloads in selection_details (that untruncated field at
    cycle_snapshots.py:377 is the 79KB/row bloat driver). We map the scan-result
    OBJECTS (embedded truncated as scanner_candidates_top) but keep
    selection_details free of the per-symbol `candidates[*].feature_map` payloads."""
    _stats, _outcomes, _saved, persisted = _drive_lane_run_cycle_capturing_finalize(
        monkeypatch, sell_check_due=True, buy_scan_due=True
    )
    assert persisted, "cycle_snapshot should be persisted"
    snapshot = persisted[-1]
    selection_details = snapshot.get("selection_details") or {}
    candidates = selection_details.get("candidates") or []
    # No per-symbol raw scan payloads leaked into the untruncated field.
    for candidate in candidates:
        assert "feature_map" not in candidate
        assert "feature_vector" not in candidate
        assert "score_components" not in candidate
