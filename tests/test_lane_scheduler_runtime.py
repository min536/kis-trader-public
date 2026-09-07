from __future__ import annotations

import os
import time
from contextlib import ExitStack
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

_IMPORT_ENV = {
    "KIS_TRADER_DISABLE_DOTENV": "1",
    "KIS_APP_KEY": "test-key",
    "KIS_APP_SECRET": "test-secret",
    "KIS_BASE_URL": "https://openapivts.koreainvestment.com:9443",
    "KIS_CANO": "00000000",
    "KIS_ACNT_PRDT_CD": "01",
}
_ORIGINAL_IMPORT_ENV = {key: os.environ.get(key) for key in _IMPORT_ENV}
os.environ.update(_IMPORT_ENV)

from app import main as main_module
from app.pipeline import BuyIntent, BuyScanLaneController, SellIntent
from app.pipeline.order_gate import reset_detached_handlers_for_tests
from app.scanner.quote_account import BuyScanQuotePrefetchResult

for _key, _value in _ORIGINAL_IMPORT_ENV.items():
    if _key == "KIS_TRADER_DISABLE_DOTENV" and _value is None:
        os.environ.pop(_key, None)
    elif _value is not None:
        os.environ[_key] = _value


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        run_mode="mock",
        run_once=False,
        lane_scheduler_enabled=True,
        order_gate_enabled=True,
        enable_sell_guard_selftest=False,
        enable_sell_test_scenarios=False,
        sell_test_mode="off",
        session_cycle_hard_budget_seconds=60.0,
        sell_check_interval_seconds=30,
        buy_scan_interval_seconds=60,
        buy_scan_min_remaining_budget_seconds=5.0,
        buy_scan_total_budget_seconds=25.0,
        buy_scan_prefetch_deadline_enabled=True,
        buy_scan_quote_prefetch_deadline_seconds=18.0,
        buy_scan_quote_request_timeout_seconds=2.0,
        buy_scan_quote_max_attempts=1,
        scan_symbols_max_per_cycle=200,
        target_symbols=(),
        buy_scan_top_k_candidates=5,
        enable_daily_pnl_brake=False,
        buy_max_budget_per_trade_krw=0,
        buy_max_account_exposure_pct=0.0,
        buy_max_qty_per_trade=0,
        rebuy_cooldown_minutes=0,
        same_symbol_max_buys_per_day=0,
        buy_daily_max_order_submissions=0,
    )


def _runtime_patches():
    return (
        mock.patch.object(main_module, "sync_account_scope_meta", return_value={}),
        mock.patch.object(
            main_module,
            "get_account_scope_context",
            return_value={
                "account_signature": "mock-test",
                "account_environment": "mock",
                "masked_account_display": "0000-01",
            },
        ),
        mock.patch.object(main_module, "load_runtime_state", return_value={}),
        mock.patch.object(main_module, "save_runtime_state", return_value=True),
        mock.patch.object(
            main_module,
            "_write_slack_runtime_status_snapshot",
            return_value=True,
        ),
        mock.patch.object(
            main_module,
            "_run_cycle_market_data_quality_sentinel",
            return_value=None,
        ),
        mock.patch.object(main_module, "_build_buy_cycle_funnel_stats", return_value={}),
        mock.patch.object(
            main_module,
            "_build_buy_candidate_outcome_records",
            return_value=[],
        ),
        mock.patch.object(main_module, "append_candidate_outcomes", return_value=True),
        mock.patch.object(main_module, "append_cycle_stats", return_value=True),
        mock.patch.object(main_module, "build_daily_summary", return_value={}),
        mock.patch.object(main_module, "build_daily_summary_console_lines", return_value=[]),
        mock.patch.object(main_module, "build_cycle_stats_daily_summary", return_value={}),
        mock.patch.object(main_module, "build_cycle_stats_console_lines", return_value=[]),
        mock.patch.object(main_module, "build_cycle_snapshot", return_value={}),
        mock.patch.object(main_module, "persist_cycle_snapshot", return_value=True),
        mock.patch.object(main_module, "_print_runtime_mode", return_value=None),
        mock.patch.object(main_module, "_print_applied_settings", return_value=None),
        mock.patch.object(main_module, "_print_test_mode", return_value=None),
        mock.patch.object(main_module, "_print_cycle_header", return_value=None),
        mock.patch.object(main_module, "_print_engine_schedule_state", return_value=None),
        mock.patch.object(main_module, "_print_cycle_timing", return_value=None),
        mock.patch.object(main_module, "_print_api_usage", return_value=None),
        mock.patch.object(main_module, "_print_sell_metrics", return_value=None),
        mock.patch.object(main_module, "_print_buy_scan_metrics", return_value=None),
        mock.patch.object(main_module, "_print_runtime_state_summary", return_value=None),
    )


def _prefetch_result(
    *,
    symbols: tuple[str, ...],
    completed: int,
    failed: int = 0,
    skipped: int = 0,
    deadline_hit: bool = False,
    elapsed_ms: float = 0.0,
    timeout_count: int = 0,
) -> BuyScanQuotePrefetchResult:
    completed_symbols = symbols[:completed]
    failed_symbols = symbols[completed : completed + failed]
    skipped_symbols = symbols[completed + failed : completed + failed + skipped]
    return BuyScanQuotePrefetchResult(
        price_data_by_symbol={
            symbol: {"rt_cd": "0", "output": {"stck_shrn_iscd": symbol}}
            for symbol in completed_symbols
        },
        requested_symbols=symbols,
        completed_symbols=completed_symbols,
        failed_symbols=failed_symbols,
        quote_account_mode="read_only_quote_account",
        quote_account_env="live",
        elapsed_ms=elapsed_ms,
        quote_response_ms=elapsed_ms,
        throttle_sleep_ms=0.0,
        skipped_deadline_symbols=skipped_symbols,
        budget_skipped_symbols=skipped_symbols,
        deadline_hit=deadline_hit,
        deadline_seconds=18.0,
        success_ratio=round(completed / len(symbols), 4) if symbols else 0.0,
        request_timeout_seconds=2.0,
        max_attempts=1,
        timeout_count=timeout_count,
    )


def _run_enabled_cycle(
    scenario: str,
    *,
    prefetch_func,
    buy_controller: BuyScanLaneController | None = None,
    buy_intent: bool = True,
    sell_intent: bool = True,
    sell_intent_advance_seconds: float = 0.0,
    sell_order_advance_seconds: float = 1.0,
    buy_order_advance_seconds: float = 1.0,
) -> tuple[dict[str, object], list[str], FakeClock]:
    clock = FakeClock()
    symbols = tuple(f"{index:06d}" for index in range(200))
    orders: list[str] = []
    controller = buy_controller or BuyScanLaneController(clock=clock)
    created_at = datetime(2026, 6, 27, 9, 0, tzinfo=timezone.utc)

    def sell_intent_factory(*, cycle_id, **_kwargs):
        if not sell_intent:
            return None
        clock.advance(sell_intent_advance_seconds)
        return SellIntent(
            intent_id=f"{cycle_id}:sell",
            source_cycle_id=cycle_id,
            source_lane="sell_watch",
            symbol="000660",
            reason="risk",
            created_at=created_at,
        )

    def buy_intent_factory(*, cycle_id, prefetch_result, **_kwargs):
        if not buy_intent or not prefetch_result.price_data_by_symbol:
            return None
        return BuyIntent(
            intent_id=f"{cycle_id}:buy",
            source_cycle_id=cycle_id,
            source_lane="buy_scan",
            symbol="005930",
            reason="candidate",
            created_at=created_at,
        )

    def sell_order_handler(intent):
        orders.append(f"SELL:{intent.symbol}")
        clock.advance(sell_order_advance_seconds)
        return True

    def buy_order_handler(intent):
        orders.append(f"BUY:{intent.symbol}")
        clock.advance(buy_order_advance_seconds)
        return True

    scheduler_state: dict[str, object] = {
        "cycle_id": scenario,
        "lane_scheduler_hooks": {
            "clock": clock,
            "now_func": lambda: created_at,
            "buy_controller": controller,
            "buy_symbols": symbols,
            "execution_token": "dry-run-token",
            "prefetch_func": prefetch_func,
            "sell_intent_factory": sell_intent_factory,
            "buy_intent_factory": buy_intent_factory,
            "sell_order_handler": sell_order_handler,
            "buy_order_handler": buy_order_handler,
        },
    }
    with ExitStack() as stack:
        for patch in _runtime_patches():
            stack.enter_context(patch)
        main_module.run_cycle(
            _settings(),
            sell_check_due=True,
            buy_scan_due=True,
            scheduler_state=scheduler_state,
            api_budget_state={"dry_run": True},
        )
    return scheduler_state["lane_scheduler_result"], orders, clock


def test_main_run_cycle_lane_scheduler_normal_200_symbol_buy_sell_due_cycle() -> None:
    def prefetch_func(*, symbols, **_kwargs):
        _kwargs["lane_budget"].clock.advance(10.0)
        return _prefetch_result(symbols=symbols, completed=200, elapsed_ms=10_000.0)

    result, orders, clock = _run_enabled_cycle("normal", prefetch_func=prefetch_func)

    assert clock.now * 1000 <= 60_000
    assert result["telemetry"]["buy_quote_prefetch_completed"] == 200
    assert not result["telemetry"]["buy_quote_prefetch_deadline_hit"]
    assert orders == ["SELL:000660", "BUY:005930"]


def test_main_run_cycle_lane_scheduler_slow_deadline_cycle_is_bounded() -> None:
    def prefetch_func(*, symbols, **_kwargs):
        _kwargs["lane_budget"].clock.advance(18.0)
        return _prefetch_result(
            symbols=symbols,
            completed=80,
            skipped=120,
            deadline_hit=True,
            elapsed_ms=18_000.0,
        )

    result, _orders, clock = _run_enabled_cycle("slow_deadline", prefetch_func=prefetch_func)

    assert clock.now * 1000 <= 60_000
    assert result["telemetry"]["buy_quote_prefetch_deadline_hit"]
    assert result["telemetry"]["buy_quote_prefetch_skipped_deadline"] == 120


def test_main_run_cycle_lane_scheduler_previous_buy_running_skips_new_prefetch() -> None:
    controller = BuyScanLaneController()
    controller.guard.try_start(
        scan_id="previous",
        now=datetime(2026, 6, 27, 8, 59, tzinfo=timezone.utc),
    )

    def prefetch_func(**_kwargs):
        raise AssertionError("new quote prefetch must not start")

    result, orders, clock = _run_enabled_cycle(
        "previous_running",
        prefetch_func=prefetch_func,
        buy_controller=controller,
    )

    assert clock.now * 1000 <= 60_000
    assert result["buy_scan_skipped_reason"] == "previous_scan_running"
    assert orders == ["SELL:000660"]


def test_main_run_cycle_lane_scheduler_sell_buy_conflict_uses_shared_gate_order() -> None:
    def prefetch_func(*, symbols, **_kwargs):
        _kwargs["lane_budget"].clock.advance(8.0)
        return _prefetch_result(symbols=symbols, completed=200, elapsed_ms=8_000.0)

    result, orders, _clock = _run_enabled_cycle("conflict", prefetch_func=prefetch_func)

    assert result["order_gate_order"] == ("SELL:000660", "BUY:005930")
    assert orders == ["SELL:000660", "BUY:005930"]


def test_main_run_cycle_lane_scheduler_hanging_quote_is_bounded() -> None:
    def prefetch_func(*, symbols, **_kwargs):
        _kwargs["lane_budget"].clock.advance(2.0)
        return _prefetch_result(
            symbols=symbols,
            completed=0,
            failed=1,
            skipped=199,
            deadline_hit=True,
            elapsed_ms=2_000.0,
            timeout_count=1,
        )

    result, orders, clock = _run_enabled_cycle(
        "hanging_quote",
        prefetch_func=prefetch_func,
    )

    assert clock.now * 1000 <= 60_000
    assert result["telemetry"]["buy_quote_prefetch_timeout_count"] == 1
    assert result["buy_scan_skipped_reason"] == "quote_prefetch_deadline"
    assert orders == ["SELL:000660"]


def test_main_run_cycle_lane_scheduler_exception_after_prefetch_start_releases_guard() -> None:
    def prefetch_func(**_kwargs):
        _kwargs["lane_budget"].clock.advance(1.0)
        raise RuntimeError("prefetch exploded")

    result, orders, clock = _run_enabled_cycle(
        "prefetch_exception",
        prefetch_func=prefetch_func,
    )

    assert clock.now * 1000 <= 60_000
    assert result["buy_scan_skipped_reason"] == "prefetch_exception"
    assert result["telemetry"]["buy_scan_guard_released"]
    assert result["telemetry"]["buy_scan_guard_release_reason"] == "prefetch_exception"
    assert orders == ["SELL:000660"]


def test_main_run_cycle_lane_scheduler_missing_prefetch_payloads_skip_buy() -> None:
    def prefetch_func(*, symbols, **_kwargs):
        _kwargs["lane_budget"].clock.advance(3.0)
        return _prefetch_result(
            symbols=symbols,
            completed=0,
            skipped=200,
            deadline_hit=True,
            elapsed_ms=3_000.0,
        )

    result, orders, clock = _run_enabled_cycle("missing_prefetch", prefetch_func=prefetch_func)

    assert clock.now * 1000 <= 60_000
    assert result["buy_scan_skipped_reason"] == "quote_prefetch_deadline"
    assert result["telemetry"]["buy_quote_prefetch_budget_skipped"] == 200
    assert orders == ["SELL:000660"]


def test_main_run_cycle_lane_scheduler_low_remaining_budget_skips_buy_before_prefetch() -> None:
    prefetch_started = False

    def prefetch_func(**_kwargs):
        nonlocal prefetch_started
        prefetch_started = True
        raise AssertionError("BUY quote prefetch must not start when cycle budget is low")

    result, orders, clock = _run_enabled_cycle(
        "low_budget_before_buy",
        prefetch_func=prefetch_func,
        sell_intent_advance_seconds=59.95,
        sell_order_advance_seconds=0.0,
    )

    assert clock.now * 1000 <= 60_000
    assert not prefetch_started
    assert result["buy_scan_skipped_reason"] == "cycle_budget_low"
    assert result["telemetry"]["budget_skip_stage"] == "before_buy_scan"
    assert result["telemetry"]["buy_quote_prefetch_request_count"] == 0
    assert orders == ["SELL:000660"]


def test_main_run_cycle_lane_scheduler_slow_sell_stops_buy_order_gate() -> None:
    def prefetch_func(*, symbols, **_kwargs):
        _kwargs["lane_budget"].clock.advance(1.0)
        return _prefetch_result(symbols=symbols, completed=200, elapsed_ms=1_000.0)

    result, orders, _clock = _run_enabled_cycle(
        "slow_sell_order_gate",
        prefetch_func=prefetch_func,
        sell_order_advance_seconds=61.0,
        buy_order_advance_seconds=1.0,
    )

    assert orders == ["SELL:000660"]
    assert result["order_gate_order"] == ("SELL:000660", "BUY:005930")
    assert result["telemetry"]["order_gate_processed_count"] == 1
    assert result["telemetry"]["order_gate_last_decision"] == "budget_exceeded"
    assert result["telemetry"]["budget_exceeded_stage"] == "order_gate_after_sell"


def test_main_run_cycle_lane_scheduler_order_gate_handler_timeout_is_bounded() -> None:
    orders: list[str] = []
    created_at = datetime(2026, 6, 27, 9, 0, tzinfo=timezone.utc)
    symbols = ("005930",)

    def prefetch_func(*, symbols, **_kwargs):
        return _prefetch_result(symbols=symbols, completed=1, elapsed_ms=0.0)

    def sell_intent_factory(*, cycle_id, **_kwargs):
        return SellIntent(
            intent_id=f"{cycle_id}:sell",
            source_cycle_id=cycle_id,
            source_lane="sell_watch",
            symbol="000660",
            reason="risk",
            created_at=created_at,
        )

    def buy_intent_factory(*, cycle_id, **_kwargs):
        return BuyIntent(
            intent_id=f"{cycle_id}:buy",
            source_cycle_id=cycle_id,
            source_lane="buy_scan",
            symbol="005930",
            reason="candidate",
            created_at=created_at,
        )

    def slow_sell_order_handler(intent):
        orders.append(f"SELL:{intent.symbol}")
        time.sleep(0.05)
        return True

    started = time.perf_counter()
    scheduler_state = {
        "cycle_id": "handler_timeout",
        "lane_scheduler_hooks": {
            "clock": time.perf_counter,
            "now_func": lambda: created_at,
            "buy_controller": BuyScanLaneController(clock=time.perf_counter),
            "buy_symbols": symbols,
            "execution_token": "dry-run-token",
            "prefetch_func": prefetch_func,
            "sell_intent_factory": sell_intent_factory,
            "buy_intent_factory": buy_intent_factory,
            "sell_order_handler": slow_sell_order_handler,
            "buy_order_handler": lambda intent: orders.append(f"BUY:{intent.symbol}"),
            "order_gate_handler_timeout_enabled": True,
            "buy_min_remaining_budget_seconds": 0.0,
        },
    }

    try:
        with ExitStack() as stack:
            for patch in _runtime_patches():
                stack.enter_context(patch)
            main_module.run_cycle(
                SimpleNamespace(
                        **{
                            **_settings().__dict__,
                            "session_cycle_hard_budget_seconds": 0.01,
                            "buy_scan_min_remaining_budget_seconds": 0.0,
                        }
                ),
                sell_check_due=True,
                buy_scan_due=True,
                scheduler_state=scheduler_state,
                api_budget_state={"dry_run": True},
            )

        result = scheduler_state["lane_scheduler_result"]
        assert (time.perf_counter() - started) < 0.04
        assert orders == ["SELL:000660"]
        # S1 Change B: the trailing BUY intent is now recorded as budget_exceeded
        # (previously silently dropped), so the literal last decision is
        # budget_exceeded; the timeout is preserved in budget_exceeded_stage below.
        assert result["telemetry"]["order_gate_last_decision"] == "budget_exceeded"
        assert result["telemetry"]["order_gate_processed_count"] == 0
        assert result["telemetry"]["budget_exceeded_stage"] == "order_gate_handler_timeout"
    finally:
        # This test deliberately detaches a slow sell handler (sleep 0.05s) that
        # OrderGate registers in its process-wide detached-handler guard. Wait for
        # it to drain and clear the registry so it cannot block later cycles/tests.
        time.sleep(0.06)
        reset_detached_handlers_for_tests()
