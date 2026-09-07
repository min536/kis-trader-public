from __future__ import annotations

from tests.test_lane_scheduler_no_hooks import (
    test_main_run_cycle_no_hook_scheduler_persists_normal_state as _run_persistence,
)


def test_enabled_no_hook_scheduler_uses_normal_cycle_persistence(monkeypatch) -> None:
    _run_persistence(monkeypatch)
