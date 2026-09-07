from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core import session_lock


class SessionLockTests(unittest.TestCase):
    def test_inspect_lock_reports_corrupted_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "session.lock"
            metadata_path = session_lock.metadata_path_for(lock_path)
            metadata_path.write_text("{not-json", encoding="utf-8")

            status = session_lock.inspect_lock(lock_path, expected_command="run_session.sh")

            self.assertFalse(status["lock_held"])
            self.assertTrue(status["metadata_corrupted"])
            self.assertTrue(status["stale"])
            self.assertEqual(status["stale_reason"], "corrupted_metadata")

    def test_inspect_lock_reports_stale_metadata_without_live_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "session.lock"
            session_lock.update_lock_metadata(
                lock_path,
                pid=999999,
                app_pid=999998,
                holder_pid=999997,
                command="scripts/run_session.sh -> python3 -m app.main",
                status="running",
            )

            status = session_lock.inspect_lock(lock_path, expected_command="run_session.sh")

            self.assertFalse(status["lock_held"])
            self.assertTrue(status["stale"])
            self.assertEqual(status["stale_reason"], "stale_metadata_without_lock")

    def test_update_lock_metadata_preserves_started_at(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "session.lock"
            first = session_lock.update_lock_metadata(
                lock_path,
                pid=os.getpid(),
                command="scripts/run_session.sh -> python3 -m app.main",
                status="starting",
            )
            second = session_lock.update_lock_metadata(
                lock_path,
                pid=os.getpid(),
                app_pid=12345,
                status="running",
            )

            self.assertEqual(first["started_at"], second["started_at"])
            self.assertEqual(second["app_pid"], 12345)
            self.assertEqual(second["status"], "running")

    def test_same_program_running_matches_current_process_command(self) -> None:
        with mock.patch.object(
            session_lock,
            "read_process_command",
            return_value="/bin/zsh ./scripts/run_session.sh",
        ):
            self.assertTrue(
                session_lock.same_program_running(os.getpid(), "scripts/run_session.sh -> python3 -m app.main")
            )

    def test_same_program_running_rejects_unrelated_process_command(self) -> None:
        with mock.patch.object(
            session_lock,
            "read_process_command",
            return_value="/usr/bin/python3 -m http.server",
        ):
            self.assertFalse(
                session_lock.same_program_running(os.getpid(), "scripts/run_session.sh -> python3 -m app.main")
            )


class AppMainLockTests(unittest.TestCase):
    def test_first_acquire_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "app_main_mock_12345_01.lock"
            handle = session_lock.acquire_app_main_lock(
                lock_path, account_signature="mock_12345_01"
            )
            self.assertIsNotNone(handle)
            handle.close()

    def test_duplicate_acquire_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "app_main_mock_12345_01.lock"
            handle1 = session_lock.acquire_app_main_lock(
                lock_path, account_signature="mock_12345_01"
            )
            try:
                with self.assertRaises(session_lock.AppMainAlreadyRunningError):
                    session_lock.acquire_app_main_lock(
                        lock_path, account_signature="mock_12345_01"
                    )
            finally:
                handle1.close()

    def test_reacquirable_after_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "app_main_mock_12345_01.lock"
            handle1 = session_lock.acquire_app_main_lock(
                lock_path, account_signature="mock_12345_01"
            )
            handle1.close()
            handle2 = session_lock.acquire_app_main_lock(
                lock_path, account_signature="mock_12345_01"
            )
            self.assertIsNotNone(handle2)
            handle2.close()

    def test_metadata_written_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "app_main_mock_12345_01.lock"
            handle = session_lock.acquire_app_main_lock(
                lock_path, account_signature="mock_12345_01"
            )
            try:
                metadata = session_lock.read_lock_metadata(lock_path)
                self.assertIsNotNone(metadata)
                self.assertEqual(metadata["account_signature"], "mock_12345_01")
                self.assertEqual(metadata["pid"], os.getpid())
                self.assertIn("started_at", metadata)
                self.assertIn("heartbeat_at", metadata)
            finally:
                handle.close()

    def test_failure_does_not_overwrite_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "app_main_mock_12345_01.lock"
            handle1 = session_lock.acquire_app_main_lock(
                lock_path, account_signature="mock_12345_01"
            )
            try:
                original = session_lock.read_lock_metadata(lock_path)
                with self.assertRaises(session_lock.AppMainAlreadyRunningError):
                    session_lock.acquire_app_main_lock(
                        lock_path, account_signature="mock_12345_01"
                    )
                after = session_lock.read_lock_metadata(lock_path)
                self.assertEqual(original, after)
            finally:
                handle1.close()

    def test_error_message_includes_existing_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "app_main_mock_12345_01.lock"
            handle1 = session_lock.acquire_app_main_lock(
                lock_path, account_signature="mock_12345_01"
            )
            try:
                with self.assertRaises(session_lock.AppMainAlreadyRunningError) as ctx:
                    session_lock.acquire_app_main_lock(
                        lock_path, account_signature="mock_12345_01"
                    )
                self.assertIn(str(os.getpid()), str(ctx.exception))
            finally:
                handle1.close()


if __name__ == "__main__":
    unittest.main()
