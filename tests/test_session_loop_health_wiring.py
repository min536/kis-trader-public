from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from unittest import mock

import pytest

from app.runtime.session_loop import run_session_loop


class _StopLoop(RuntimeError):
    pass


_NOW = datetime(2026, 7, 17, 10, 0, 0)

_MISSING = object()


@dataclass
class _FakeSettings:
    run_interval_seconds: int = 30
    sell_check_interval_seconds: int = 30
    buy_scan_interval_seconds: int = 30
    scan_symbols_max_per_cycle: int = 10
    buy_scan_shallow_top_k: int = 5
    buy_scan_deep_eval_limit: int = 3


def _rate_control(**_kwargs):
    return {
        "effective_sell_check_interval_seconds": 30,
        "effective_buy_scan_interval_seconds": 30,
        "effective_scan_symbols_max_per_cycle": 10,
        "effective_buy_scan_shallow_top_k": 5,
        "effective_buy_scan_deep_eval_limit": 3,
        "effective_sell_watch_max_holdings_per_tick": 5,
    }


def _run_one_cycle(*, run_cycle, record_cycle_health=_MISSING):
    sleep = mock.Mock(side_effect=_StopLoop)
    emit_status = mock.Mock()
    record_main_loop_exception_if_needed = mock.Mock()
    kwargs = dict(
        settings=_FakeSettings(),
        build_api_budget_state=lambda settings: {},
        build_runtime_rate_control=_rate_control,
        compute_effective_sell_check_interval_seconds=lambda **_k: 30,
        is_due_callback=lambda **_k: True,
        build_scheduler_tick_decision=lambda **_k: {
            "decision": "RUN",
            "sell_check_due": True,
            "buy_scan_due": True,
        },
        api_budget_transient_backoff_remaining_seconds=lambda *a, **k: 0.0,
        api_budget_backoff_remaining_seconds=lambda *a, **k: 0.0,
        run_cycle=run_cycle,
        emit_status=emit_status,
        record_main_loop_exception_if_needed=record_main_loop_exception_if_needed,
        get_now=lambda: _NOW,
        sleep=sleep,
        print_message=lambda *a, **k: None,
    )
    if record_cycle_health is not _MISSING:
        kwargs["record_cycle_health"] = record_cycle_health
    with pytest.raises(_StopLoop):
        run_session_loop(**kwargs)
    return {
        "sleep": sleep,
        "emit_status": emit_status,
        "record_main_loop_exception_if_needed": record_main_loop_exception_if_needed,
    }


def test_successful_cycle_records_health_with_none() -> None:
    record_cycle_health = mock.Mock()
    _run_one_cycle(run_cycle=mock.Mock(), record_cycle_health=record_cycle_health)
    record_cycle_health.assert_called_once_with(None)


def test_cycle_exception_records_health_and_loop_continues() -> None:
    boom = ValueError("OPSQ0008 잔고 조회 실패")
    record_cycle_health = mock.Mock()
    handles = _run_one_cycle(
        run_cycle=mock.Mock(side_effect=boom),
        record_cycle_health=record_cycle_health,
    )
    # Existing bottleneck hook still fires with the same exception.
    handles["record_main_loop_exception_if_needed"].assert_called_once_with(boom)
    # New watchdog hook is fed the same exception.
    record_cycle_health.assert_called_once_with(boom)
    # Loop swallowed the cycle error and reached the tick sleep (which stopped it).
    handles["sleep"].assert_called_once()


def test_default_none_health_hook_preserves_behavior() -> None:
    run_cycle = mock.Mock()
    handles = _run_one_cycle(run_cycle=run_cycle)  # record_cycle_health omitted
    run_cycle.assert_called_once()
    handles["sleep"].assert_called_once()
    handles["record_main_loop_exception_if_needed"].assert_not_called()
    handles["emit_status"].assert_not_called()
