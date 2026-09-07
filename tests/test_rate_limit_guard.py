from __future__ import annotations

import tempfile
import unittest
import os
from datetime import datetime, timedelta
from pathlib import Path

from app.core.market_session import MarketSessionStatus
from app.core.time_utils import KOREA_TZ
from app.tools.rate_limit_guard import GuardPaths, decide_guard_action


def _paths(temp_dir: str) -> GuardPaths:
    root = Path(temp_dir)
    log_dir = root / "logs"
    return GuardPaths(
        project_dir=root,
        log_dir=log_dir,
        pid_file=log_dir / "kis_trader.pid",
        lock_file=log_dir / "kis_trader.session.lock",
        run_script=root / "scripts" / "run_session.sh",
        restart_script=root / "scripts" / "restart_session.sh",
        guard_log_file=log_dir / "rate_limit_guard.log",
        guard_state_file=log_dir / "rate_limit_guard_state.json",
    )


def _regular_open() -> MarketSessionStatus:
    return MarketSessionStatus(
        session="REGULAR",
        order_allowed=True,
        reason="한국 정규장 시간입니다.",
        buy_block_action=None,
        sell_block_action=None,
    )


def _closed() -> MarketSessionStatus:
    return MarketSessionStatus(
        session="CLOSED",
        order_allowed=False,
        reason="정규장 및 시간외 거래 시간이 아닙니다.",
        buy_block_action="blocked_holiday_or_closed",
        sell_block_action="blocked_sell_holiday_or_closed",
    )


class RateLimitGuardDecisionTests(unittest.TestCase):
    def test_blocks_healing_for_non_mock_account(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            decision = decide_guard_action(
                paths=_paths(temp_dir),
                account_context={"account_environment": "live"},
                market_status=_regular_open(),
                runtime_state={},
                lock_status={"lock_held": False},
                now=datetime(2026, 4, 29, 11, 0, tzinfo=KOREA_TZ),
            )

        self.assertEqual(decision.action, "blocked_non_mock_account")
        self.assertFalse(decision.should_execute)

    def test_does_not_start_session_when_market_is_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            decision = decide_guard_action(
                paths=_paths(temp_dir),
                account_context={"account_environment": "mock"},
                market_status=_closed(),
                runtime_state={},
                lock_status={"lock_held": False},
                now=datetime(2026, 4, 29, 18, 30, tzinfo=KOREA_TZ),
            )

        self.assertEqual(decision.action, "observe_market_closed")
        self.assertFalse(decision.should_execute)

    def test_starts_missing_mock_session_during_regular_market(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            decision = decide_guard_action(
                paths=_paths(temp_dir),
                account_context={"account_environment": "mock"},
                market_status=_regular_open(),
                runtime_state={},
                lock_status={"lock_held": False},
                now=datetime(2026, 4, 29, 11, 0, tzinfo=KOREA_TZ),
            )

        self.assertEqual(decision.action, "start_session")
        self.assertTrue(decision.should_execute)
        self.assertIn("run_session.sh", decision.command[0])

    def test_observes_fresh_rate_limit_recovery_without_restart(self) -> None:
        now = datetime(2026, 4, 29, 11, 0, tzinfo=KOREA_TZ)
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = _paths(temp_dir)
            decision = decide_guard_action(
                paths=paths,
                account_context={"account_environment": "mock"},
                market_status=_regular_open(),
                runtime_state={
                    "last_cycle_started_at": (now - timedelta(seconds=30)).isoformat(),
                    "last_budget_status": {
                        "recent_rate_limit_hits_10m": 2,
                        "backoff_remaining_seconds": 30,
                    },
                },
                lock_status={
                    "lock_held": True,
                    "same_program": True,
                    "app_pid": os.getpid(),
                },
                now=now,
            )

        self.assertEqual(decision.action, "observe_rate_limit_recovery")
        self.assertFalse(decision.should_execute)

    def test_restarts_stale_rate_limited_session(self) -> None:
        now = datetime(2026, 4, 29, 11, 0, tzinfo=KOREA_TZ)
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = _paths(temp_dir)
            decision = decide_guard_action(
                paths=paths,
                account_context={"account_environment": "mock"},
                market_status=_regular_open(),
                runtime_state={
                    "last_cycle_started_at": (now - timedelta(minutes=7)).isoformat(),
                    "rate_limit_triggered": True,
                    "last_budget_status": {
                        "recent_rate_limit_hits_10m": 3,
                        "backoff_remaining_seconds": 0,
                    },
                },
                lock_status={
                    "lock_held": True,
                    "same_program": True,
                    "app_pid": os.getpid(),
                },
                now=now,
                stale_cycle_seconds=300,
            )

        self.assertEqual(decision.action, "restart_session")
        self.assertTrue(decision.should_execute)
        self.assertIn("restart_session.sh", decision.command[0])

    def test_blocks_when_lock_belongs_to_unrelated_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            decision = decide_guard_action(
                paths=_paths(temp_dir),
                account_context={"account_environment": "mock"},
                market_status=_regular_open(),
                runtime_state={},
                lock_status={"lock_held": True, "same_program": False},
                now=datetime(2026, 4, 29, 11, 0, tzinfo=KOREA_TZ),
            )

        self.assertEqual(decision.action, "blocked_unrelated_lock")
        self.assertFalse(decision.should_execute)


if __name__ == "__main__":
    unittest.main()
