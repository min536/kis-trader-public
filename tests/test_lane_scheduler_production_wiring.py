from __future__ import annotations

from tests.test_lane_scheduler_no_hooks import (
    test_run_lane_scheduler_cycle_low_budget_skips_buy_without_prefetch as _run_low_budget,
    test_run_lane_scheduler_cycle_uses_real_default_adapters_without_hooks as _run_no_hook_wiring,
)


def test_no_hook_default_adapters_delegate_sell_and_buy(monkeypatch) -> None:
    _run_no_hook_wiring(monkeypatch)


def test_no_hook_low_budget_policy_keeps_sell_priority(monkeypatch) -> None:
    _run_low_budget(monkeypatch)
