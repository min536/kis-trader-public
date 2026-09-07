"""Behavior-lock unit test for ``app.runtime.cycle_phases.finalize.finalize_cycle``.

Stage B-3 slice F (``docs/main_run_cycle_slimming_plan_20260703.md`` §3): the
``finally:`` body of ``run_cycle`` is extracted verbatim into ``finalize_cycle``.
This test wires the extracted function with a minimal stub ``CycleContext`` and
all-no-op injected collaborators, then asserts the two load-bearing side effects
of the finalize path — the runtime-state recorder fires exactly once and the
cycle-result summary lands on ``state`` — without touching any file or network.
"""

from __future__ import annotations

import unittest
from unittest import mock

from app.runtime.cycle_phases.context import CycleContext
from app.runtime.cycle_phases.finalize import finalize_cycle


class _StubCycleBudget:
    exceeded = False
    budget_ms = 0.0


def _noop(*args, **kwargs):
    return None


def _finalize_kwargs(state: dict, save_runtime_state) -> dict:
    """All-no-op collaborator set that walks finalize's main path to save_runtime_state.

    Injected collaborators must not touch files/network; anything returning a
    structured value gets a permissive default.
    """
    return dict(
        settings=mock.Mock(buy_scan_top_k_candidates=5, run_mode="scan_only"),
        api_budget_state={},
        scheduler_state={},
        sell_check_due=False,
        state=state,
        cycle_id="20260704T000000-deadbeef",
        cycle_started_at="2026-07-04T00:00:00",
        cycle_started_perf=0.0,
        cycle_budget=_StubCycleBudget(),
        note=_noop,
        _api_budget_note_rate_limit=_noop,
        _api_budget_backoff_remaining_seconds=lambda *a, **kw: 0,
        BUY_SCAN_LANE_CONTROLLER=mock.Mock(running=False),
        _run_cycle_market_data_quality_sentinel=_noop,
        _resolve_benchmark_snapshot=_noop,
        build_performance_report=lambda **kw: {"integrity_warnings": []},
        persist_performance_report=lambda *a, **kw: {
            "snapshot_saved": True,
            "report_saved": True,
        },
        build_performance_console_lines=lambda *a, **kw: [],
        _summarize_api_budget_state=lambda *a, **kw: {},
        _api_budget_update_rate_limit_recovery_state=_noop,
        _api_budget_update_transient_recovery_state=_noop,
        build_cycle_snapshot=lambda **kw: {"integrity_warnings": []},
        _build_buy_cycle_funnel_stats=lambda **kw: {},
        _build_buy_candidate_outcome_records=lambda **kw: [],
        persist_cycle_snapshot=lambda *a, **kw: True,
        append_candidate_outcomes=lambda *a, **kw: True,
        append_cycle_stats=lambda *a, **kw: True,
        get_cycle_snapshots_path=lambda *a, **kw: "/dev/null",
        _format_cycle_summary=lambda **kw: "CYCLE_RESULT_LINE",
        _update_recent_market_snapshots=_noop,
        save_runtime_state=save_runtime_state,
        get_runtime_state_path=lambda *a, **kw: "/dev/null",
        get_korean_now=mock.Mock(
            return_value=mock.Mock(isoformat=lambda: "2026-07-04T00:00:00")
        ),
        _print_cycle_timing=_noop,
        _print_api_usage=_noop,
        _print_sell_metrics=_noop,
        _print_buy_scan_metrics=_noop,
        build_daily_summary=lambda *a, **kw: {},
        _write_slack_runtime_status_snapshot=lambda *a, **kw: True,
        build_daily_summary_console_lines=lambda *a, **kw: [],
        build_cycle_stats_daily_summary=lambda *a, **kw: {},
        build_cycle_stats_console_lines=lambda *a, **kw: [],
        _print_runtime_state_summary=_noop,
        _get_slack_notifier=lambda: mock.Mock(),
    )


class FinalizeCycleTest(unittest.TestCase):
    def test_finalize_cycle_saves_state_once_and_writes_cycle_result(self) -> None:
        ctx = CycleContext()
        state: dict[str, object] = {}
        save_runtime_state = mock.Mock(return_value=True)

        # Injected collaborators that must not touch files/network. Anything that
        # returns a structured value is given a permissive default so the verbatim
        # finalize body walks its main path to save_runtime_state without raising.
        finalize_cycle(
            ctx,
            settings=mock.Mock(buy_scan_top_k_candidates=5, run_mode="scan_only"),
            api_budget_state={},
            scheduler_state={},
            sell_check_due=False,
            state=state,
            cycle_id="20260704T000000-deadbeef",
            cycle_started_at="2026-07-04T00:00:00",
            cycle_started_perf=0.0,
            cycle_budget=_StubCycleBudget(),
            note=_noop,
            _api_budget_note_rate_limit=_noop,
            _api_budget_backoff_remaining_seconds=lambda *a, **kw: 0,
            BUY_SCAN_LANE_CONTROLLER=mock.Mock(running=False),
            _run_cycle_market_data_quality_sentinel=_noop,
            _resolve_benchmark_snapshot=_noop,
            build_performance_report=lambda **kw: {"integrity_warnings": []},
            persist_performance_report=lambda *a, **kw: {
                "snapshot_saved": True,
                "report_saved": True,
            },
            build_performance_console_lines=lambda *a, **kw: [],
            _summarize_api_budget_state=lambda *a, **kw: {},
            _api_budget_update_rate_limit_recovery_state=_noop,
            _api_budget_update_transient_recovery_state=_noop,
            build_cycle_snapshot=lambda **kw: {"integrity_warnings": []},
            _build_buy_cycle_funnel_stats=lambda **kw: {},
            _build_buy_candidate_outcome_records=lambda **kw: [],
            persist_cycle_snapshot=lambda *a, **kw: True,
            append_candidate_outcomes=lambda *a, **kw: True,
            append_cycle_stats=lambda *a, **kw: True,
            get_cycle_snapshots_path=lambda *a, **kw: "/dev/null",
            _format_cycle_summary=lambda **kw: "CYCLE_RESULT_LINE",
            _update_recent_market_snapshots=_noop,
            save_runtime_state=save_runtime_state,
            get_runtime_state_path=lambda *a, **kw: "/dev/null",
            get_korean_now=mock.Mock(return_value=mock.Mock(isoformat=lambda: "2026-07-04T00:00:00")),
            _print_cycle_timing=_noop,
            _print_api_usage=_noop,
            _print_sell_metrics=_noop,
            _print_buy_scan_metrics=_noop,
            build_daily_summary=lambda *a, **kw: {},
            _write_slack_runtime_status_snapshot=lambda *a, **kw: True,
            build_daily_summary_console_lines=lambda *a, **kw: [],
            build_cycle_stats_daily_summary=lambda *a, **kw: {},
            build_cycle_stats_console_lines=lambda *a, **kw: [],
            _print_runtime_state_summary=_noop,
            _get_slack_notifier=lambda: mock.Mock(),
        )

        save_runtime_state.assert_called_once_with(state)
        self.assertEqual(state["last_cycle_result"], "CYCLE_RESULT_LINE")
        self.assertIn("last_cycle_elapsed_ms", state)
        self.assertIs(ctx.state_write_ok, True)

    def test_finalize_releases_stale_sell_intents_before_state_save(self) -> None:
        """finalize는 양 런타임 경로(legacy/lane)의 수렴점이다.

        lane-scheduler 경로는 account_snapshot(=reconciliation)을 건너뛰므로,
        stale SELL intent TTL 안전망은 finalize에서도 작동해야 한다
        (docs/todo_20260710.md §A-4b W6). legacy 경로에선 reconciliation이 먼저
        정리해 no-op이 된다.
        """
        from datetime import timedelta

        from app.core.time_utils import get_korean_now

        ctx = CycleContext()
        stale_at = (get_korean_now() - timedelta(minutes=61)).isoformat()
        state: dict[str, object] = {
            "pending_sell_intents_by_symbol": {
                "023530": {"qty": 18, "submitted_at": stale_at, "trigger": "stop_loss"}
            },
            "recent_orders": [],
        }
        save_runtime_state = mock.Mock(return_value=True)

        finalize_cycle(ctx, **_finalize_kwargs(state, save_runtime_state))

        self.assertEqual(state["pending_sell_intents_by_symbol"], {})
        causes = [
            entry.get("cause") for entry in state.get("last_intent_adjustments", [])
        ]
        self.assertIn("intent_ttl_expired", causes)
        # The release must land in the very state snapshot being persisted.
        save_runtime_state.assert_called_once_with(state)

    def test_finalize_emits_order_log_untrusted_alert(self) -> None:
        from app.notifications.account_alerts import reset_order_log_integrity_latch

        reset_order_log_integrity_latch()
        self.addCleanup(reset_order_log_integrity_latch)

        ctx = CycleContext()
        ctx.buy_risk_guard_payload = {
            "guard_results": {
                "order_log_integrity": {
                    "passed": False,
                    "details": {"order_log_error_code": "order_log_too_large"},
                }
            }
        }
        state: dict[str, object] = {"account_signature": "sig-xyz"}
        notifier = mock.Mock()

        finalize_cycle(
            ctx,
            settings=mock.Mock(buy_scan_top_k_candidates=5, run_mode="trade"),
            api_budget_state={},
            scheduler_state={},
            sell_check_due=False,
            state=state,
            cycle_id="20260707T000000-deadbeef",
            cycle_started_at="2026-07-07T00:00:00",
            cycle_started_perf=0.0,
            cycle_budget=_StubCycleBudget(),
            note=_noop,
            _api_budget_note_rate_limit=_noop,
            _api_budget_backoff_remaining_seconds=lambda *a, **kw: 0,
            BUY_SCAN_LANE_CONTROLLER=mock.Mock(running=False),
            _run_cycle_market_data_quality_sentinel=_noop,
            _resolve_benchmark_snapshot=_noop,
            build_performance_report=lambda **kw: {"integrity_warnings": []},
            persist_performance_report=lambda *a, **kw: {
                "snapshot_saved": True,
                "report_saved": True,
            },
            build_performance_console_lines=lambda *a, **kw: [],
            _summarize_api_budget_state=lambda *a, **kw: {},
            _api_budget_update_rate_limit_recovery_state=_noop,
            _api_budget_update_transient_recovery_state=_noop,
            build_cycle_snapshot=lambda **kw: {"integrity_warnings": []},
            _build_buy_cycle_funnel_stats=lambda **kw: {
                "buy_non_execution_reason": "order_log_untrusted"
            },
            _build_buy_candidate_outcome_records=lambda **kw: [],
            persist_cycle_snapshot=lambda *a, **kw: True,
            append_candidate_outcomes=lambda *a, **kw: True,
            append_cycle_stats=lambda *a, **kw: True,
            get_cycle_snapshots_path=lambda *a, **kw: "/dev/null",
            _format_cycle_summary=lambda **kw: "CYCLE_RESULT_LINE",
            _update_recent_market_snapshots=_noop,
            save_runtime_state=mock.Mock(return_value=True),
            get_runtime_state_path=lambda *a, **kw: "/dev/null",
            get_korean_now=mock.Mock(
                return_value=mock.Mock(isoformat=lambda: "2026-07-07T00:00:00")
            ),
            _print_cycle_timing=_noop,
            _print_api_usage=_noop,
            _print_sell_metrics=_noop,
            _print_buy_scan_metrics=_noop,
            build_daily_summary=lambda *a, **kw: {},
            _write_slack_runtime_status_snapshot=lambda *a, **kw: True,
            build_daily_summary_console_lines=lambda *a, **kw: [],
            build_cycle_stats_daily_summary=lambda *a, **kw: {},
            build_cycle_stats_console_lines=lambda *a, **kw: [],
            _print_runtime_state_summary=_noop,
            _get_slack_notifier=lambda: notifier,
        )

        notifier.send.assert_called_once()
        call = notifier.send.call_args
        self.assertEqual(call.args[0], "order_log.untrusted")
        self.assertEqual(
            call.kwargs["details"]["order_log_error_code"], "order_log_too_large"
        )


if __name__ == "__main__":
    unittest.main()
