"""S6 — legacy inline path vs lane pipeline path ORDER-DECISION equivalence.

Characterization net (LOW risk, NEW test file only — no production change).

The two runtime paths must agree on the observable ORDER DECISION for identical
mock inputs, so flipping ``LANE_SCHEDULER_ENABLED`` (or rolling it back) is a
behaviour-preserving change:

- **Legacy inline path**: ``main_module.run_cycle(...)`` with a settings whose
  ``lane_scheduler_enabled`` is False → runs the legacy SELL/BUY flow inline.
- **Lane path**: same ``run_cycle(...)`` with ``lane_scheduler_enabled=True`` →
  enters ``run_lane_scheduler_main_bridge``.

Both paths ultimately execute orders through the *same* module objects
(``app.execution.sell_flow`` / ``app.execution.buy_flow``); the existing
harness in ``tests.test_lane_scheduler_no_hooks`` patches ``run_sell_order_flow``
/ ``run_buy_order_flow`` as attributes on those shared module objects, so a
single set of order-flow stubs records executed orders for BOTH paths. The
lane path reads quote/balance/scan boundaries from ``adapter_module`` while the
legacy inline path reads its own ``main_module`` module-level bindings, so the
legacy path needs the SAME boundaries patched on ``main_module`` too — that is
the only extra wiring below.
"""

from __future__ import annotations

from tests.test_lane_scheduler_no_hooks import (
    _Settings,
    _balance_response,
    _buy_result,
    _patch_boundaries,
    _quote_response,
    _session,
    _settings,
)

import pytest

import app.core.throttle as throttle_module
import app.pipeline.runtime_adapters as adapter_module
from app import main as main_module
from app.auth.token import ApiHttpError


@pytest.fixture(autouse=True)
def _reset_adaptive_pacing():
    """The rate-limit scenario trips ``note_rate_limit`` → module-global
    adaptive-pacing penalty in app.core.throttle, which would leak into later
    test files (e.g. benchmark snapshot lookups are skipped while pacing is
    active). Reset it after every test, mirroring
    tests/test_throttle_adaptive_pacing.py's setUp."""
    yield
    throttle_module._ADAPTIVE_PENALTY_UNTIL = 0.0
    throttle_module._ADAPTIVE_PENALTY_EXTRA_DELAY_SECONDS = 0.0
    throttle_module.reset_throttle_metrics()


def _settings_replace_shim(settings, **overrides):
    """A ``dataclasses.replace`` stand-in for the ``SimpleNamespace``-based test
    settings.

    The legacy inline path calls ``dataclasses.replace(settings, ...)`` to build
    the regime-adjusted BUY settings — but the shared harness ``_Settings`` is a
    ``SimpleNamespace`` (with lazy ``__getattr__`` defaults), not a real
    ``Settings`` dataclass, so ``replace`` raises. The lane path never calls
    ``replace``. To keep BOTH paths on the *identical* mock settings object,
    patch ``main_module.replace`` to a shim that returns a copy carrying the
    overrides. The result (``effective_buy_settings``) only feeds the BUY path;
    it is decision-neutral for these characterization comparisons.
    """
    merged = dict(getattr(settings, "__dict__", {}))
    merged.update(overrides)
    return _Settings(**merged)


def _healthy_api_budget_state() -> dict[str, object]:
    """A permissive per-tick API budget so the legacy inline path clears its
    pre-balance ``api_budget_can_request`` gate (the lane path has no such gate,
    so it runs fine on an empty ``{}``). Both paths get the SAME object per run;
    equivalence is asserted on the resulting order decision, not the counters."""
    return {
        "recent_requests": [],
        "quotes_used_this_tick": 0,
        "soft_max_requests_per_second": 8,
        "soft_max_quotes_per_tick": 40,
        "backoff_seconds_on_rate_limit": 1.0,
    }


def _patch_legacy_main_boundaries(monkeypatch, *, buy_candidate: bool = True) -> None:
    """Patch the boundaries the LEGACY inline path reads from ``main_module``.

    The lane path reads these from ``adapter_module`` (patched by
    ``_patch_boundaries``); the legacy inline ``run_cycle`` body reads the
    same names off its own ``main_module`` bindings, so mirror them here.
    """
    monkeypatch.setattr(main_module, "get_korean_market_session", _session)
    monkeypatch.setattr(main_module, "issue_access_token", lambda: "dry-token")
    monkeypatch.setattr(main_module, "inquire_balance", lambda token=None: _balance_response())
    monkeypatch.setattr(
        main_module,
        "inquire_price",
        lambda symbol, token=None: _quote_response(symbol),
    )
    monkeypatch.setattr(
        main_module,
        "scan_target_symbols",
        lambda **_kwargs: (_buy_result(candidate=buy_candidate),),
    )


def _patch_main_runtime_scaffold(monkeypatch, saved_states: list[dict[str, object]]) -> None:
    """Patch the ``main_module`` runtime scaffold (state load/save, snapshot and
    print helpers) so ``run_cycle`` runs to completion without touching disk —
    same set the existing no-hooks harness uses around ``run_cycle``."""
    monkeypatch.setattr(main_module, "sync_account_scope_meta", lambda settings: {})
    monkeypatch.setattr(
        main_module,
        "get_account_scope_context",
        lambda settings=None: {
            "account_signature": "mock-test",
            "account_environment": "mock",
            "masked_account_display": "0000-01",
        },
    )
    monkeypatch.setattr(main_module, "load_runtime_state", lambda: {})
    monkeypatch.setattr(
        main_module,
        "save_runtime_state",
        lambda state: saved_states.append(dict(state)) or True,
    )
    monkeypatch.setattr(main_module, "_write_slack_runtime_status_snapshot", lambda **_kwargs: True)
    monkeypatch.setattr(main_module, "get_runtime_state_path", lambda settings=None: "runtime.json")
    monkeypatch.setattr(main_module, "get_cycle_snapshots_path", lambda settings=None: "cycle.jsonl")
    monkeypatch.setattr(main_module, "build_cycle_snapshot", lambda **kwargs: {})
    monkeypatch.setattr(main_module, "persist_cycle_snapshot", lambda snapshot: True)
    # Reporting-only boundary: the legacy path builds a realized-PnL summary that
    # reads the on-disk order log via the real account signature; the fake
    # ``_settings`` has no recognized base URL and this call is NOT part of the
    # order decision, so stub it (same category as the other summary stubs).
    monkeypatch.setattr(
        main_module,
        "_build_today_realized_summary",
        lambda settings: {"realized_gross_pnl_krw": 0, "realized_net_pnl_krw": 0},
    )
    monkeypatch.setattr(main_module, "replace", _settings_replace_shim)
    # Backoff / guard drains sleep in real time on a rate limit; no-op them so the
    # characterization run is fast and deterministic (does not affect the order
    # decision, only wall-clock waits).
    monkeypatch.setattr(main_module.time, "sleep", lambda _seconds: None)


def _patch_lane_main_scaffold(monkeypatch) -> None:
    """Extra ``main_module`` boundaries the LANE path exercises during cycle
    finalization (the legacy path stubs above already cover the shared set).
    Mirrors ``test_main_run_cycle_no_hook_scheduler_persists_normal_state``."""
    monkeypatch.setattr(main_module, "_run_cycle_market_data_quality_sentinel", lambda *_a, **_k: None)
    monkeypatch.setattr(main_module, "_resolve_benchmark_snapshot", lambda **_k: None)
    monkeypatch.setattr(main_module, "_build_buy_cycle_funnel_stats", lambda **_k: {})
    monkeypatch.setattr(main_module, "_build_buy_candidate_outcome_records", lambda **_k: [])
    monkeypatch.setattr(main_module, "append_candidate_outcomes", lambda *_a, **_k: True)
    monkeypatch.setattr(main_module, "append_cycle_stats", lambda *_a, **_k: True)
    monkeypatch.setattr(main_module, "build_daily_summary", lambda: {})
    monkeypatch.setattr(main_module, "build_daily_summary_console_lines", lambda _s: [])
    monkeypatch.setattr(main_module, "build_cycle_stats_daily_summary", lambda: {})
    monkeypatch.setattr(main_module, "build_cycle_stats_console_lines", lambda _s: [])
    monkeypatch.setattr(main_module, "_print_runtime_mode", lambda settings: None)
    monkeypatch.setattr(main_module, "_print_applied_settings", lambda settings: None)
    monkeypatch.setattr(main_module, "_print_test_mode", lambda settings: None)
    monkeypatch.setattr(main_module, "_print_cycle_header", lambda settings: None)
    monkeypatch.setattr(main_module, "_print_engine_schedule_state", lambda **_k: None)
    monkeypatch.setattr(main_module, "_print_cycle_timing", lambda **_k: None)
    monkeypatch.setattr(main_module, "_print_api_usage", lambda *_a, **_k: None)
    monkeypatch.setattr(main_module, "_print_sell_metrics", lambda **_k: None)
    monkeypatch.setattr(main_module, "_print_buy_scan_metrics", lambda **_k: None)
    monkeypatch.setattr(main_module, "_print_runtime_state_summary", lambda *_a, **_k: None)


def _drive_run_cycle(
    monkeypatch,
    *,
    lane_enabled: bool,
    sell_check_due: bool,
    buy_scan_due: bool,
    settings_overrides: dict[str, object] | None = None,
    patch_boundaries_kwargs: dict[str, object] | None = None,
    extra_boundary_patches=None,
    api_budget_state: dict[str, object] | None = None,
) -> dict[str, object]:
    """Drive ``main_module.run_cycle`` once through the requested path and return
    the observable decision outputs (recorded orders + persisted state)."""
    orders: list[str] = []
    saved_states: list[dict[str, object]] = []
    _patch_boundaries(monkeypatch, orders=orders, **(patch_boundaries_kwargs or {}))
    _patch_legacy_main_boundaries(
        monkeypatch,
        buy_candidate=bool((patch_boundaries_kwargs or {}).get("buy_candidate", True)),
    )
    _patch_main_runtime_scaffold(monkeypatch, saved_states)
    _patch_lane_main_scaffold(monkeypatch)
    if extra_boundary_patches is not None:
        extra_boundary_patches(monkeypatch)

    settings = _settings(
        lane_scheduler_enabled=lane_enabled, **(settings_overrides or {})
    )
    main_module.run_cycle(
        settings,
        sell_check_due=sell_check_due,
        buy_scan_due=buy_scan_due,
        scheduler_state={"decision": "EQUIV_TEST"},
        api_budget_state=(
            _healthy_api_budget_state() if api_budget_state is None else api_budget_state
        ),
    )
    return {"orders": orders, "saved_states": saved_states}


def test_scenario1_sell_trigger_decision_matches_across_paths(monkeypatch) -> None:
    """Scenario 1 — SELL trigger. A held position (SK하이닉스 000660, -10% PnL vs a
    5% stop-loss) triggers a SELL on BOTH runtime paths. The observable order
    decision (which symbols get a SELL order, in order) must be identical.

    Compared: the ordered list of recorded order-flow calls, filtered to SELL
    entries. Both paths record through the SAME shared ``run_sell_order_flow``
    stub, so this is an apples-to-apples decision comparison, independent of the
    two paths' differing telemetry shapes."""
    legacy = _drive_run_cycle(
        monkeypatch,
        lane_enabled=False,
        sell_check_due=True,
        buy_scan_due=False,
    )
    lane = _drive_run_cycle(
        monkeypatch,
        lane_enabled=True,
        sell_check_due=True,
        buy_scan_due=False,
    )

    legacy_sells = [order for order in legacy["orders"] if order.startswith("SELL:")]
    lane_sells = [order for order in lane["orders"] if order.startswith("SELL:")]

    assert legacy_sells == ["SELL:000660"]
    assert legacy_sells == lane_sells


# --- Scenario 2 (BUY candidate) — NOT PINNABLE, deliberately omitted ----------
#
# The BUY decision is NOT characterizable across the flag boundary with this
# harness because the two paths run structurally different BUY pipelines:
#
#   * Lane path  — resolves candidates through the SINGLE ``scan_target_symbols``
#     seam, which the shared harness mocks wholesale to return the ``005930``
#     candidate directly. A BUY intent for 005930 is produced.
#   * Legacy path — runs a multi-STAGE scan the harness does not mock:
#     ``resolve_buy_universe_symbols`` (live-snapshot universe, ~30 symbols) →
#     ``build_buy_scan_layered_universe`` → ``build_buy_scan_shallow_plan``
#     (shallow ranking) → ``build_buy_scan_deep_eval_symbols`` → only THEN
#     ``scan_target_symbols`` on the surviving shortlist. With the harness mocks,
#     the shallow stage yields an empty shortlist ("shallow ranking 결과 deep
#     evaluation 대상이 없어 ... BUY scan을 생략") so ``scan_target_symbols`` is
#     called with ``()`` and no BUY is produced.
#
# Forcing legacy → BUY:005930 would require mocking the universe resolver plus the
# layered/shallow/deep-eval planners — a large, brittle surface that fakes the
# very ranking machinery whose behaviour legitimately differs between the paths,
# so the test would pin the mocks rather than a real shared decision. Per the S6
# plan §7, this scenario is reported as unpinnable rather than force-passed. The
# BUY *execution/gate* decision itself is already covered where inputs converge:
# ``tests/test_lane_scheduler_no_hooks.py`` exercises the lane BUY gate, and the
# legacy staged scan is covered by the buy-scan scanner tests.


def _rate_limited_quote(symbol, token=None):
    """A quote call that raises the KIS 초당 거래건수 초과 (EGW00201) rate-limit
    error — the same shape the lane rate-limit test uses."""
    raise ApiHttpError(
        "현재가 조회 HTTP 500: {'msg_cd': 'EGW00201', 'msg1': '초당 거래건수를 초과하였습니다.'}",
        500,
        {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."},
    )


def _patch_sell_watch_rate_limit(monkeypatch) -> None:
    """Make the SELL-watch quote fetch hit a rate limit on BOTH paths' quote
    seams (lane reads ``adapter_module.inquire_price``; legacy reads
    ``main_module.inquire_price``)."""
    monkeypatch.setattr(adapter_module, "inquire_price", _rate_limited_quote)
    monkeypatch.setattr(main_module, "inquire_price", _rate_limited_quote)


def test_scenario3_sell_watch_rate_limit_outcome_matches_across_paths(monkeypatch) -> None:
    """Scenario 3 — rate-limit partial. A quote rate-limit (EGW00201) mid
    SELL-watch must produce a CONSISTENT outcome on both paths: NO order is
    executed, and the rate limit is recorded in the shared ``api_budget_state``
    (the object both paths mutate).

    Compared, on the shared decision-relevant surface (not each path's differing
    telemetry shape):
      * recorded orders — empty on both paths (no order escapes a rate limit);
      * ``api_budget_state['rate_limit_hits']`` >= 1 on both — the rate limit was
        noted;
      * ``api_budget_state['last_rate_limit_source'] == 'sell_watch'`` on both —
        both attribute the limit to the sell-watch quote lane.
    ``buy_scan_due`` is False so the rate limit is unambiguously the sell-watch
    quote fetch on both paths."""
    legacy_budget = _healthy_api_budget_state()
    legacy = _drive_run_cycle(
        monkeypatch,
        lane_enabled=False,
        sell_check_due=True,
        buy_scan_due=False,
        extra_boundary_patches=_patch_sell_watch_rate_limit,
        api_budget_state=legacy_budget,
    )
    lane_budget = _healthy_api_budget_state()
    lane = _drive_run_cycle(
        monkeypatch,
        lane_enabled=True,
        sell_check_due=True,
        buy_scan_due=False,
        extra_boundary_patches=_patch_sell_watch_rate_limit,
        api_budget_state=lane_budget,
    )

    # No order escapes a rate limit on either path.
    assert legacy["orders"] == []
    assert lane["orders"] == []

    # Both paths note the rate limit against the shared budget, sourced to the
    # sell-watch quote lane.
    assert int(legacy_budget.get("rate_limit_hits", 0)) >= 1
    assert int(lane_budget.get("rate_limit_hits", 0)) >= 1
    assert legacy_budget.get("last_rate_limit_source") == "sell_watch"
    assert lane_budget.get("last_rate_limit_source") == "sell_watch"
