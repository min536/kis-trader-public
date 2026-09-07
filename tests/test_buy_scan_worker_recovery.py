from __future__ import annotations

from tests.test_lane_scheduler_no_hooks import (
    test_run_lane_scheduler_cycle_stale_worker_telemetry_without_hooks as _run_stale_worker,
)


def test_stale_buy_scan_worker_reports_recovery_telemetry(monkeypatch) -> None:
    _run_stale_worker(monkeypatch)
