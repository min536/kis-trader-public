from __future__ import annotations

import inspect
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from app import main as main_module
from app.core.time_utils import KOREA_TZ


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        sell_check_interval_seconds=20,
        buy_scan_interval_seconds=180,
        scan_symbols_max_per_cycle=36,
        buy_scan_shallow_top_k=10,
        buy_scan_deep_eval_limit=6,
        adaptive_midday_enabled=True,
        adaptive_midday_window="11:00-13:00",
        adaptive_midday_buy_scan_interval_seconds=240,
        adaptive_midday_sell_check_interval_seconds=25,
        adaptive_midday_scan_symbols_max_per_cycle=24,
        adaptive_midday_buy_scan_deep_eval_limit=4,
        degraded_mode_enabled=True,
        degraded_mode_rate_limit_hits_in_10m=3,
        degraded_mode_consecutive_backoff_cycles=4,
        degraded_mode_duration_seconds=600,
        degraded_mode_buy_scan_interval_seconds=300,
        degraded_mode_sell_check_interval_seconds=30,
        degraded_mode_scan_symbols_max_per_cycle=16,
        degraded_mode_buy_scan_deep_eval_limit=2,
        degraded_mode_sell_watch_max_holdings_per_tick=4,
    )


class RuntimeRateControlTests(unittest.TestCase):
    def test_midday_mode_applies_more_conservative_buy_scan_settings(self) -> None:
        now = datetime(2026, 4, 18, 11, 30, tzinfo=KOREA_TZ)
        api_budget_state = {
            "recent_rate_limit_hit_times": [],
            "consecutive_backoff_cycles": 0,
            "degraded_mode_until": None,
            "degraded_mode_reason": None,
            "backoff_until": None,
        }

        runtime = main_module._build_runtime_rate_control(
            settings=_settings(),
            api_budget_state=api_budget_state,
            now=now,
        )

        self.assertEqual(runtime["mode"], "midday")
        self.assertEqual(runtime["effective_sell_check_interval_seconds"], 25)
        self.assertEqual(runtime["effective_buy_scan_interval_seconds"], 240)
        self.assertEqual(runtime["effective_scan_symbols_max_per_cycle"], 24)
        self.assertEqual(runtime["effective_buy_scan_deep_eval_limit"], 4)
        self.assertEqual(runtime["effective_buy_scan_shallow_top_k"], 10)
        self.assertIsNone(runtime["effective_sell_watch_max_holdings_per_tick"])

    def test_degraded_mode_triggers_on_recent_rate_limits_and_backoff(self) -> None:
        now = datetime(2026, 4, 18, 10, 15, tzinfo=KOREA_TZ)
        api_budget_state = {
            "recent_rate_limit_hit_times": [
                now - timedelta(minutes=1),
                now - timedelta(minutes=3),
                now - timedelta(minutes=8),
            ],
            "consecutive_backoff_cycles": 3,
            "degraded_mode_until": None,
            "degraded_mode_reason": None,
            "backoff_until": now + timedelta(seconds=30),
        }

        runtime = main_module._build_runtime_rate_control(
            settings=_settings(),
            api_budget_state=api_budget_state,
            now=now,
        )

        self.assertEqual(runtime["mode"], "degraded")
        self.assertTrue(runtime["degraded_active"])
        self.assertEqual(runtime["effective_sell_check_interval_seconds"], 30)
        self.assertEqual(runtime["effective_buy_scan_interval_seconds"], 300)
        self.assertEqual(runtime["effective_scan_symbols_max_per_cycle"], 16)
        self.assertEqual(runtime["effective_buy_scan_deep_eval_limit"], 2)
        self.assertEqual(runtime["effective_sell_watch_max_holdings_per_tick"], 4)
        self.assertIn("rate_limit_hits_10m=3", str(runtime["reason"]))
        self.assertIsNotNone(api_budget_state.get("degraded_mode_until"))

    def test_degraded_mode_stays_active_until_timeout_then_returns_to_normal(self) -> None:
        now = datetime(2026, 4, 18, 10, 15, tzinfo=KOREA_TZ)
        api_budget_state = {
            "recent_rate_limit_hit_times": [],
            "consecutive_backoff_cycles": 0,
            "degraded_mode_until": now + timedelta(minutes=5),
            "degraded_mode_reason": "rate_limit_hits_10m=3",
            "backoff_until": None,
        }

        active_runtime = main_module._build_runtime_rate_control(
            settings=_settings(),
            api_budget_state=api_budget_state,
            now=now,
        )
        self.assertEqual(active_runtime["mode"], "degraded")
        self.assertTrue(active_runtime["degraded_active"])

        recovered_runtime = main_module._build_runtime_rate_control(
            settings=_settings(),
            api_budget_state=api_budget_state,
            now=now + timedelta(minutes=11),
        )
        self.assertEqual(recovered_runtime["mode"], "normal")
        self.assertFalse(recovered_runtime["degraded_active"])
        self.assertEqual(recovered_runtime["effective_sell_check_interval_seconds"], 20)
        self.assertEqual(recovered_runtime["effective_buy_scan_interval_seconds"], 180)

    def test_normal_mode_preserves_base_settings_and_resets_backoff_counter(self) -> None:
        now = datetime(2026, 4, 18, 14, 30, tzinfo=KOREA_TZ)
        api_budget_state = {
            "recent_rate_limit_hit_times": [],
            "consecutive_backoff_cycles": 5,
            "degraded_mode_until": None,
            "degraded_mode_reason": None,
            "backoff_until": None,
        }

        runtime = main_module._build_runtime_rate_control(
            settings=_settings(),
            api_budget_state=api_budget_state,
            now=now,
        )

        self.assertEqual(runtime["mode"], "normal")
        self.assertEqual(runtime["reason"], "base_defaults")
        self.assertFalse(runtime["midday_active"])
        self.assertFalse(runtime["degraded_active"])
        self.assertEqual(runtime["effective_sell_check_interval_seconds"], 20)
        self.assertEqual(runtime["effective_buy_scan_interval_seconds"], 180)
        self.assertEqual(runtime["effective_scan_symbols_max_per_cycle"], 36)
        self.assertEqual(runtime["effective_buy_scan_shallow_top_k"], 10)
        self.assertEqual(runtime["effective_buy_scan_deep_eval_limit"], 6)
        self.assertIsNone(runtime["effective_sell_watch_max_holdings_per_tick"])
        self.assertEqual(runtime["consecutive_backoff_cycles"], 0)
        self.assertEqual(api_budget_state["consecutive_backoff_cycles"], 0)
        self.assertIsNone(api_budget_state["degraded_mode_until"])
        self.assertIsNone(api_budget_state["degraded_mode_reason"])

    def test_active_backoff_increments_consecutive_cycles_without_degrading(self) -> None:
        now = datetime(2026, 4, 18, 14, 30, tzinfo=KOREA_TZ)
        api_budget_state = {
            "recent_rate_limit_hit_times": [],
            "consecutive_backoff_cycles": 2,
            "degraded_mode_until": None,
            "degraded_mode_reason": None,
            "backoff_until": now + timedelta(seconds=30),
        }

        runtime = main_module._build_runtime_rate_control(
            settings=_settings(),
            api_budget_state=api_budget_state,
            now=now,
        )

        self.assertEqual(runtime["mode"], "normal")
        self.assertFalse(runtime["degraded_active"])
        self.assertEqual(runtime["consecutive_backoff_cycles"], 3)
        self.assertEqual(api_budget_state["consecutive_backoff_cycles"], 3)
        self.assertEqual(runtime["effective_sell_check_interval_seconds"], 20)
        self.assertEqual(runtime["effective_buy_scan_interval_seconds"], 180)

    def test_degraded_mode_triggers_on_consecutive_backoff_threshold(self) -> None:
        now = datetime(2026, 4, 18, 14, 30, tzinfo=KOREA_TZ)
        api_budget_state = {
            "recent_rate_limit_hit_times": [],
            "consecutive_backoff_cycles": 3,
            "degraded_mode_until": None,
            "degraded_mode_reason": None,
            "backoff_until": now + timedelta(seconds=30),
        }

        runtime = main_module._build_runtime_rate_control(
            settings=_settings(),
            api_budget_state=api_budget_state,
            now=now,
        )

        self.assertEqual(runtime["mode"], "degraded")
        self.assertTrue(runtime["degraded_active"])
        self.assertEqual(runtime["consecutive_backoff_cycles"], 4)
        self.assertIn("consecutive_backoff_cycles=4", str(runtime["reason"]))
        self.assertEqual(
            api_budget_state["degraded_mode_reason"], "consecutive_backoff_cycles=4"
        )
        self.assertIsNotNone(api_budget_state["degraded_mode_until"])

    def test_cap_calculations_clamp_shallow_top_k_and_deep_eval(self) -> None:
        now = datetime(2026, 4, 18, 14, 30, tzinfo=KOREA_TZ)
        settings = _settings()
        settings.scan_symbols_max_per_cycle = 5
        settings.buy_scan_shallow_top_k = 10
        settings.buy_scan_deep_eval_limit = 8
        api_budget_state = {
            "recent_rate_limit_hit_times": [],
            "consecutive_backoff_cycles": 0,
            "degraded_mode_until": None,
            "degraded_mode_reason": None,
            "backoff_until": None,
        }

        runtime = main_module._build_runtime_rate_control(
            settings=settings,
            api_budget_state=api_budget_state,
            now=now,
        )

        self.assertEqual(runtime["mode"], "normal")
        self.assertEqual(runtime["effective_scan_symbols_max_per_cycle"], 5)
        self.assertEqual(runtime["effective_buy_scan_shallow_top_k"], 5)
        self.assertEqual(runtime["effective_buy_scan_deep_eval_limit"], 5)

    def test_measured_balance_extra_requests_are_registered_in_api_budget(self) -> None:
        now = datetime(2026, 4, 18, 10, 15, tzinfo=KOREA_TZ)
        api_budget_state = {"recent_requests": []}
        request_delta = {
            "categories": {
                "balance": {"count": 3, "elapsed_ms": 1200.0},
            },
        }

        extra_count = main_module._api_budget_register_measured_extra_requests(
            api_budget_state,
            request_delta=request_delta,
            category="balance",
            already_registered_count=1,
            now=now,
        )

        self.assertEqual(extra_count, 2)
        self.assertEqual(len(api_budget_state["recent_requests"]), 2)
        self.assertTrue(all(item == now for item in api_budget_state["recent_requests"]))

    def test_measured_request_registration_ignores_already_counted_calls(self) -> None:
        now = datetime(2026, 4, 18, 10, 15, tzinfo=KOREA_TZ)
        api_budget_state = {"recent_requests": []}
        request_delta = {
            "categories": {
                "balance": {"count": 1, "elapsed_ms": 300.0},
            },
        }

        extra_count = main_module._api_budget_register_measured_extra_requests(
            api_budget_state,
            request_delta=request_delta,
            category="balance",
            already_registered_count=1,
            now=now,
        )

        self.assertEqual(extra_count, 0)
        self.assertEqual(api_budget_state["recent_requests"], [])

    def test_rate_limit_source_is_recorded_in_budget_summary_immediately(self) -> None:
        now = datetime(2026, 4, 18, 10, 15, tzinfo=KOREA_TZ)
        api_budget_state = {
            "recent_requests": [],
            "quotes_used_this_tick": 0,
            "backoff_until": None,
            "rate_limit_hits": 0,
            "recent_rate_limit_hit_times": [],
            "consecutive_backoff_cycles": 0,
            "soft_max_requests_per_second": 5,
            "soft_max_quotes_per_tick": 5,
            "backoff_seconds_on_rate_limit": 30,
        }

        main_module._api_budget_note_rate_limit(
            api_budget_state,
            now=now,
            source="sell_watch",
        )
        summary = main_module._summarize_api_budget_state(
            api_budget_state,
            now=now,
        )

        self.assertEqual(api_budget_state["last_rate_limit_source"], "sell_watch")
        self.assertEqual(summary["last_rate_limit_source"], "sell_watch")
        self.assertEqual(summary["rate_limit_hits"], 1)

    def test_rate_limit_hits_are_preserved_while_backoff_is_active(self) -> None:
        now = datetime(2026, 4, 18, 10, 15, tzinfo=KOREA_TZ)
        api_budget_state = {
            "backoff_until": now + timedelta(seconds=20),
            "rate_limit_hits": 2,
            "last_rate_limit_source": "buy_scan",
        }

        main_module._api_budget_update_rate_limit_recovery_state(
            api_budget_state,
            now=now,
            rate_limit_source=None,
        )

        self.assertEqual(api_budget_state["rate_limit_hits"], 2)
        self.assertEqual(api_budget_state["last_rate_limit_source"], "buy_scan")

    def test_rate_limit_hits_reset_after_clean_recovery(self) -> None:
        now = datetime(2026, 4, 18, 10, 15, tzinfo=KOREA_TZ)
        api_budget_state = {
            "backoff_until": now - timedelta(seconds=1),
            "rate_limit_hits": 2,
            "last_rate_limit_source": "buy_scan",
        }

        main_module._api_budget_update_rate_limit_recovery_state(
            api_budget_state,
            now=now,
            rate_limit_source=None,
        )

        self.assertEqual(api_budget_state["rate_limit_hits"], 0)
        self.assertIsNone(api_budget_state["last_rate_limit_source"])

    def test_transient_api_backoff_blocks_scheduler_tick(self) -> None:
        now = datetime(2026, 4, 18, 10, 15, tzinfo=KOREA_TZ)
        api_budget_state = {
            "recent_requests": [],
            "quotes_used_this_tick": 0,
            "backoff_until": None,
            "transient_error_until": None,
            "recent_transient_error_times": [],
            "soft_max_requests_per_second": 5,
            "soft_max_quotes_per_tick": 5,
        }

        main_module._api_budget_note_transient_api_error(
            api_budget_state,
            now=now,
            source="balance",
        )
        scheduler_tick = main_module._build_scheduler_tick_decision(
            sell_check_due=True,
            buy_scan_due=True,
            api_budget_state=api_budget_state,
            now=now,
        )
        summary = main_module._summarize_api_budget_state(
            api_budget_state,
            now=now,
        )

        self.assertEqual(scheduler_tick["decision"], "API_TRANSIENT_BACKOFF_WAIT")
        self.assertFalse(scheduler_tick["sell_check_due"])
        self.assertFalse(scheduler_tick["buy_scan_due"])
        self.assertTrue(scheduler_tick["skip_cycle"])
        self.assertEqual(summary["last_transient_error_source"], "balance")
        self.assertEqual(summary["transient_error_hits"], 1)
        self.assertGreater(summary["transient_backoff_remaining_seconds"], 0)

    def test_transient_api_error_detection_ignores_rate_limit(self) -> None:
        transient_error = RuntimeError(
            "잔고 조회 요청 실패 (일시 재시도 3회 후): "
            "<urlopen error [Errno 8] nodename nor servname provided, or not known>"
        )
        rate_limit_error = RuntimeError("잔고 조회 실패: EGW00201 초당 거래건수")

        self.assertTrue(main_module._looks_like_transient_api_error(transient_error))
        self.assertEqual(
            main_module._transient_api_source_from_exception(transient_error),
            "balance",
        )
        self.assertFalse(main_module._looks_like_transient_api_error(rate_limit_error))

    def test_scheduler_backoff_preserves_due_sell_watch_and_defers_buy(self) -> None:
        now = datetime(2026, 4, 18, 10, 15, tzinfo=KOREA_TZ)
        api_budget_state = {"backoff_until": now + timedelta(seconds=30)}

        decision = main_module._build_scheduler_tick_decision(
            sell_check_due=True,
            buy_scan_due=True,
            api_budget_state=api_budget_state,
            now=now,
        )

        self.assertEqual(decision["decision"], "API_BACKOFF_WAIT")
        self.assertTrue(decision["sell_check_due"])
        self.assertFalse(decision["buy_scan_due"])
        self.assertFalse(decision["skip_cycle"])

    def test_scheduler_balance_backoff_skips_due_sell_watch(self) -> None:
        now = datetime(2026, 4, 18, 10, 15, tzinfo=KOREA_TZ)
        api_budget_state = {
            "backoff_until": now + timedelta(seconds=30),
            "last_rate_limit_source": "balance",
        }

        decision = main_module._build_scheduler_tick_decision(
            sell_check_due=True,
            buy_scan_due=True,
            api_budget_state=api_budget_state,
            now=now,
        )

        self.assertEqual(decision["decision"], "API_BACKOFF_WAIT")
        self.assertFalse(decision["sell_check_due"])
        self.assertFalse(decision["buy_scan_due"])
        self.assertTrue(decision["skip_cycle"])

    def test_scheduler_backoff_skips_cycle_when_only_buy_is_due(self) -> None:
        now = datetime(2026, 4, 18, 10, 15, tzinfo=KOREA_TZ)
        api_budget_state = {"backoff_until": now + timedelta(seconds=30)}

        decision = main_module._build_scheduler_tick_decision(
            sell_check_due=False,
            buy_scan_due=True,
            api_budget_state=api_budget_state,
            now=now,
        )

        self.assertEqual(decision["decision"], "API_BACKOFF_WAIT")
        self.assertFalse(decision["sell_check_due"])
        self.assertFalse(decision["buy_scan_due"])
        self.assertTrue(decision["skip_cycle"])

    def test_backoff_request_budget_can_be_opened_for_sell_watch_only(self) -> None:
        now = datetime(2026, 4, 18, 10, 15, tzinfo=KOREA_TZ)
        api_budget_state = {
            "backoff_until": now + timedelta(seconds=30),
            "recent_requests": [],
            "soft_max_requests_per_second": 1,
        }

        self.assertFalse(
            main_module._api_budget_can_request(api_budget_state, now=now)
        )
        self.assertTrue(
            main_module._api_budget_can_request(
                api_budget_state,
                now=now,
                allow_during_backoff=True,
            )
        )
        self.assertEqual(
            main_module._api_budget_remaining_requests(
                api_budget_state,
                now=now,
                allow_during_backoff=True,
            ),
            1,
        )

    def test_run_cycle_drains_backoff_before_due_sell_watch(self) -> None:
        # Slice G (docs/main_run_cycle_slimming_plan_20260703.md §3, 국면 7): the
        # backoff-drain block moved verbatim into run_budget_gate. The pinned chain
        # is run_cycle → run_budget_gate → drain: run_cycle wires the gate on the
        # cycle path, and the gate body performs the SELL-due backoff drain.
        run_cycle_source = inspect.getsource(main_module.run_cycle)
        self.assertIn("run_budget_gate(", run_cycle_source)

        from app.runtime.cycle_phases import budget_gate as budget_gate_module

        source = inspect.getsource(budget_gate_module.run_budget_gate)
        start = source.index(
            "if _api_budget_backoff_active(api_budget_state, now=budget_now):"
        )
        end = source.index("if not _api_budget_can_request", start)
        backoff_guard_block = source[start:end]

        self.assertIn("sell_check_due", backoff_guard_block)
        self.assertIn('scheduler_decision == "API_BACKOFF_WAIT"', backoff_guard_block)
        self.assertIn(
            "time.sleep(_cycle_start_backoff_drain_seconds)",
            backoff_guard_block,
        )
        self.assertIn("HOLD_API_BACKOFF", backoff_guard_block)

    def test_run_cycle_only_builds_sell_watch_budget_when_sell_check_is_due(self) -> None:
        # Stage B-3 slice K re-aim: the SELL-watch loop moved to
        # run_sell_watch_phase (byte-verbatim); run_cycle still wires it.
        from app.runtime.cycle_phases import sell_watch_phase
        self.assertIn(
            "run_sell_watch_phase(",
            inspect.getsource(main_module.run_cycle),
        )
        source = inspect.getsource(sell_watch_phase.run_sell_watch_phase)
        sell_loop_start = source.index("if held_positions and sell_check_due:")
        sell_loop_end = source.index("ctx.sell_evaluated_count = len(ctx.sell_analysis_results)", sell_loop_start)
        sell_loop_block = source[sell_loop_start:sell_loop_end]

        self.assertIn("build_sell_watch_budget_plan(", sell_loop_block)
        self.assertIn("sell_watch_partial_budget_protection", sell_loop_block)
        self.assertNotIn("if held_positions:", sell_loop_block)

    def test_run_cycle_does_not_double_reserve_execution_budget_during_buy_scan_reserve(self) -> None:
        # Stage B-3 slice K re-aim: same relocation as above — the two rate-limit
        # branches' ordering + execution-reserve arithmetic move verbatim.
        from app.runtime.cycle_phases import sell_watch_phase
        self.assertIn(
            "run_sell_watch_phase(",
            inspect.getsource(main_module.run_cycle),
        )
        source = inspect.getsource(sell_watch_phase.run_sell_watch_phase)
        sell_loop_start = source.index("if held_positions and sell_check_due:")
        sell_loop_end = source.index("ctx.sell_evaluated_count = len(ctx.sell_analysis_results)", sell_loop_start)
        sell_loop_block = source[sell_loop_start:sell_loop_end]

        self.assertIn("execution_request_reserve=(", sell_loop_block)
        self.assertIn("if ctx.buy_scan_budget_reserved", sell_loop_block)
        self.assertIn("else 2 if settings.confirm_buy == \"YES\" and ctx.market_open else 0", sell_loop_block)

    def test_scheduler_defer_buy_when_quote_budget_is_exhausted(self) -> None:
        now = datetime(2026, 4, 18, 10, 15, tzinfo=KOREA_TZ)
        api_budget_state = {
            "backoff_until": None,
            "quotes_used_this_tick": 2,
            "soft_max_quotes_per_tick": 2,
        }

        decision = main_module._build_scheduler_tick_decision(
            sell_check_due=True,
            buy_scan_due=True,
            api_budget_state=api_budget_state,
            now=now,
        )

        self.assertEqual(decision["decision"], "SELL_PRIORITY_DEFER_BUY_BUDGET")
        self.assertTrue(decision["sell_check_due"])
        self.assertFalse(decision["buy_scan_due"])
        self.assertFalse(decision["skip_cycle"])


if __name__ == "__main__":
    unittest.main()
