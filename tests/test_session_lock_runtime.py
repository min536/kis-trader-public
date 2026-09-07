from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import session_lock


class SessionLockRuntimeTests(unittest.TestCase):
    def test_summarize_lock_status_reports_active_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "session.lock"
            with mock.patch.object(
                session_lock,
                "inspect_lock",
                return_value={
                    "lock_held": True,
                    "same_program": True,
                    "app_pid": 123,
                    "pid": 122,
                    "status": "running",
                    "heartbeat_at": "2026-04-18T12:00:00+00:00",
                    "stale": False,
                    "metadata_corrupted": False,
                },
            ):
                summary = session_lock.summarize_lock_status(lock_path, expected_command="run_session.sh")

            self.assertIn("active lock pid=123", summary)
            self.assertIn("status=running", summary)

    def test_summarize_lock_status_reports_stale_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "session.lock"
            with mock.patch.object(
                session_lock,
                "inspect_lock",
                return_value={
                    "lock_held": False,
                    "same_program": False,
                    "app_pid": None,
                    "pid": 456,
                    "status": "running",
                    "heartbeat_at": None,
                    "stale": True,
                    "stale_reason": "stale_metadata_without_lock",
                    "metadata_corrupted": False,
                },
            ):
                summary = session_lock.summarize_lock_status(lock_path, expected_command="run_session.sh")

            self.assertEqual(summary, "stale lock pid=456 reason=stale_metadata_without_lock")

    def test_second_hold_is_blocked_while_valid_lock_is_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "session.lock"
            ready_file = Path(temp_dir) / "ready"
            project_root = Path(__file__).resolve().parents[1]
            helper_script = project_root / "scripts" / "session_lock.py"

            holder = subprocess.Popen(
                [
                    "python3",
                    str(helper_script),
                    "hold",
                    "--lock-path",
                    str(lock_path),
                    "--owner-pid",
                    str(os.getpid()),
                    "--command",
                    "scripts/run_session.sh -> python3 -m app.main",
                    "--ready-file",
                    str(ready_file),
                    "--heartbeat-interval",
                    "1",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                for _ in range(20):
                    if ready_file.exists():
                        break
                    import time

                    time.sleep(0.1)

                self.assertTrue(ready_file.exists(), "lock holder did not signal readiness")

                second = subprocess.run(
                    [
                        "python3",
                        str(helper_script),
                        "hold",
                        "--lock-path",
                        str(lock_path),
                        "--owner-pid",
                        str(os.getpid()),
                        "--command",
                        "scripts/run_session.sh -> python3 -m app.main",
                        "--heartbeat-interval",
                        "1",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )

                self.assertEqual(second.returncode, 1)
                self.assertIn("active_lock_detected", second.stdout)
            finally:
                holder.terminate()
                holder.wait(timeout=5)
                if holder.stdout is not None:
                    holder.stdout.close()
                if holder.stderr is not None:
                    holder.stderr.close()


if __name__ == "__main__":
    unittest.main()
