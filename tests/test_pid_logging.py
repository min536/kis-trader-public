"""Tests that pid / main_pid fields are recorded in all key log structures.

Covers:
- log_order_event → orders.jsonl record contains pid == os.getpid()
- add_recent_order → recent_orders entry contains pid == os.getpid()
- save_runtime_state → persisted JSON contains main_pid == os.getpid()
- build_cycle_snapshot → snapshot dict contains pid == os.getpid()
- build_slack_status_snapshot → snapshot dict contains pid == os.getpid()
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class OrderLogPidTests(unittest.TestCase):
    def test_log_order_event_record_contains_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "orders.jsonl"

            with (
                patch("app.core.order_log.get_order_log_path", return_value=log_path),
            ):
                from app.core.order_log import log_order_event
                log_order_event(
                    symbol="005930",
                    qty=1,
                    order_type="market_buy",
                    confirm_buy="Y",
                    market_open=True,
                    action="order_submitted",
                    result="ordered",
                    reason="test",
                    raw_response=None,
                    environment="mock",
                    cycle_id="c001",
                )

            lines = [l for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
            self.assertTrue(lines, "record이 기록되지 않았습니다")
            record = json.loads(lines[0])
            self.assertIn("pid", record)
            self.assertEqual(record["pid"], os.getpid())
            self.assertIsInstance(record["pid"], int)
            # 기존 필드가 그대로 있는지 확인
            for field in ("timestamp", "cycle_id", "symbol", "qty", "action", "result"):
                self.assertIn(field, record)


class RuntimeStatePidTests(unittest.TestCase):
    def test_add_recent_order_entry_contains_pid(self) -> None:
        from unittest.mock import patch

        with patch("app.runtime_state.get_korean_now") as mock_now, \
             patch("app.runtime_state._today_text", return_value="2026-05-11"):
            from datetime import datetime
            from app.core.time_utils import KOREA_TZ
            mock_now.return_value = datetime(2026, 5, 11, 9, 30, tzinfo=KOREA_TZ)

            from app.runtime_state import add_recent_order
            state: dict = {"recent_orders": []}
            add_recent_order(state, side="BUY", symbol="005930", qty=1, action="order_submitted")

        self.assertEqual(len(state["recent_orders"]), 1)
        entry = state["recent_orders"][0]
        self.assertIn("pid", entry)
        self.assertEqual(entry["pid"], os.getpid())
        self.assertIsInstance(entry["pid"], int)
        # 기존 필드가 그대로 있는지 확인
        for field in ("timestamp", "date", "side", "symbol", "qty", "action"):
            self.assertIn(field, entry)

    def test_save_runtime_state_writes_main_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "runtime_state.json"

            with patch("app.runtime_state.get_runtime_state_path", return_value=state_path), \
                 patch("app.runtime_state.get_account_scope_context", return_value={
                     "account_signature": "mock_test_01",
                     "account_environment": "mock",
                     "masked_account_display": "mock_****_01",
                 }):
                from app.runtime_state import save_runtime_state
                state: dict = {"last_action": "idle"}
                result = save_runtime_state(state)

            self.assertTrue(result)
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertIn("main_pid", saved)
            self.assertEqual(saved["main_pid"], os.getpid())
            self.assertIsInstance(saved["main_pid"], int)


class CycleSnapshotPidTests(unittest.TestCase):
    def _build(self, **overrides) -> dict:
        from app.reporting.cycle_snapshots import build_cycle_snapshot
        kwargs = {
            "cycle_id": 1,
            "timestamp": "2026-05-11T09:30:00+09:00",
            "environment": "mock",
            "market_session": None,
            "portfolio_snapshot": None,
            "scan_results": [],
            "selected_buy_candidate": None,
            "selected_sell_candidate": None,
            "sell_watch_final_review": None,
            "sell_analysis_results": [],
            "selection_details": {},
            "buy_execution_snapshot": None,
            "buy_position_sizing": None,
            "sell_position_sizing": None,
            "buy_risk_guard": None,
            "sell_risk_guard": None,
            "rebalance_preview": None,
            "scheduler_state": None,
            "daily_pnl_brake_state": None,
            "regime_state": None,
            "runtime_state": {},
            "observed_market_snapshots": {},
            "error": None,
        }
        kwargs.update(overrides)
        return build_cycle_snapshot(**kwargs)

    def test_snapshot_contains_pid(self) -> None:
        snapshot = self._build()
        self.assertIn("pid", snapshot)
        self.assertEqual(snapshot["pid"], os.getpid())
        self.assertIsInstance(snapshot["pid"], int)

    def test_pid_position_is_near_top_level_identity_fields(self) -> None:
        # pid는 cycle_id, timestamp, environment 바로 다음에 있어야 한다.
        keys = list(self._build().keys())
        pid_idx = keys.index("pid")
        env_idx = keys.index("environment")
        self.assertLess(pid_idx, env_idx + 3)


class SlackStatusSnapshotPidTests(unittest.TestCase):
    def test_snapshot_contains_pid(self) -> None:
        from app.notifications.runtime_status_snapshot import build_slack_status_snapshot
        snapshot = build_slack_status_snapshot(
            runtime_state={"last_error_count": 0, "rate_limit_hits": 0},
            timestamp="2026-05-11T09:30:00+09:00",
        )
        self.assertIn("pid", snapshot)
        self.assertEqual(snapshot["pid"], os.getpid())
        self.assertIsInstance(snapshot["pid"], int)

    def test_pid_does_not_shadow_existing_fields(self) -> None:
        from app.notifications.runtime_status_snapshot import build_slack_status_snapshot
        snapshot = build_slack_status_snapshot(
            runtime_state={"last_error_count": 0, "rate_limit_hits": 0},
            timestamp="2026-05-11T09:30:00+09:00",
        )
        for key in ("timestamp", "session_status", "order_counters_today", "health"):
            self.assertIn(key, snapshot)


if __name__ == "__main__":
    unittest.main()
