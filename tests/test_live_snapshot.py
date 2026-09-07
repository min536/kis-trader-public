from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from app.market_data import live_snapshot as live_snapshot_module


class LiveSnapshotPathSeparationTests(unittest.TestCase):
    def test_snapshot_dirs_default_to_repo_paths(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(live_snapshot_module.LIVE_SNAPSHOT_DIR_ENV, None)
            os.environ.pop(live_snapshot_module.LIVE_SNAPSHOT_LOG_DIR_ENV, None)
            self.assertEqual(
                live_snapshot_module.resolve_snapshot_data_dir(),
                live_snapshot_module.PROJECT_ROOT / "data",
            )
            self.assertEqual(
                live_snapshot_module.resolve_snapshot_log_dir(),
                live_snapshot_module.PROJECT_ROOT / "logs",
            )
            # Module constants keep the legacy single-account paths (no migration).
            self.assertEqual(
                live_snapshot_module.SNAPSHOT_PATH,
                live_snapshot_module.PROJECT_ROOT / "data" / "live_snapshot.json",
            )

    def test_env_overrides_redirect_snapshot_paths_per_account(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir, tempfile.TemporaryDirectory() as log_dir:
            with mock.patch.dict(
                os.environ,
                {
                    live_snapshot_module.LIVE_SNAPSHOT_DIR_ENV: data_dir,
                    live_snapshot_module.LIVE_SNAPSHOT_LOG_DIR_ENV: log_dir,
                },
            ):
                self.assertEqual(
                    live_snapshot_module.resolve_snapshot_data_dir(), Path(data_dir)
                )
                self.assertEqual(
                    live_snapshot_module.resolve_snapshot_log_dir(), Path(log_dir)
                )


class LiveSnapshotLoaderTests(unittest.TestCase):
    def test_load_live_snapshot_symbols_returns_symbols_when_snapshot_is_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "live_snapshot.json"
            now_epoch = 1_710_000_000.0
            snapshot_path.write_text(
                json.dumps(
                    {
                        "updated_at": datetime.fromtimestamp(
                            now_epoch - 30,
                            tz=timezone.utc,
                        ).isoformat(),
                        "ttl_seconds": 300,
                        "top_symbols": ["005930", "000660", "0091M0", "035420"],
                        "errors": [],
                    }
                ),
                encoding="utf-8",
            )

            with (
                mock.patch.object(live_snapshot_module, "SNAPSHOT_PATH", snapshot_path),
                mock.patch("app.market_data.live_snapshot.time.time", return_value=now_epoch),
            ):
                symbols = live_snapshot_module.load_live_snapshot_symbols()
                status = live_snapshot_module.live_snapshot_status()

            self.assertEqual(symbols, ("005930", "000660", "035420"))
            self.assertTrue(status["available"])
            self.assertEqual(status["reason"], "ok")
            self.assertEqual(status["detail"], "fresh_within_refresh_window")
            self.assertEqual(status["symbol_count"], 3)

    def test_get_live_snapshot_signal_returns_source_ranks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "live_snapshot.json"
            now_epoch = 1_710_000_000.0
            snapshot_path.write_text(
                json.dumps(
                    {
                        "updated_at": datetime.fromtimestamp(
                            now_epoch - 30,
                            tz=timezone.utc,
                        ).isoformat(),
                        "ttl_seconds": 300,
                        "top_symbols": ["005930", "000660", "035420"],
                        "source_symbols": {
                            "volume_rank": ["000660", "005930"],
                            "fluctuation_rank": ["035420"],
                            "volume_power_rank": ["005930"],
                        },
                        "errors": [],
                    }
                ),
                encoding="utf-8",
            )

            with (
                mock.patch.object(live_snapshot_module, "SNAPSHOT_PATH", snapshot_path),
                mock.patch("app.market_data.live_snapshot.time.time", return_value=now_epoch),
            ):
                signal = live_snapshot_module.get_live_snapshot_signal("005930")

            assert signal is not None
            self.assertEqual(signal.combined_rank, 1)
            self.assertEqual(signal.volume_rank, 2)
            self.assertIsNone(signal.fluctuation_rank)
            self.assertEqual(signal.volume_power_rank, 1)
            self.assertEqual(signal.ranked_source_count, 2)

    def test_load_live_snapshot_symbols_returns_none_when_snapshot_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "live_snapshot.json"
            now_epoch = 1_710_000_000.0
            snapshot_path.write_text(
                json.dumps(
                    {
                        "updated_at": datetime.fromtimestamp(
                            now_epoch - 601,
                            tz=timezone.utc,
                        ).isoformat(),
                        "ttl_seconds": 300,
                        "top_symbols": ["005930"],
                        "errors": [],
                    }
                ),
                encoding="utf-8",
            )

            with (
                mock.patch.object(live_snapshot_module, "SNAPSHOT_PATH", snapshot_path),
                mock.patch("app.market_data.live_snapshot.time.time", return_value=now_epoch),
            ):
                symbols = live_snapshot_module.load_live_snapshot_symbols()
                status = live_snapshot_module.live_snapshot_status()

            self.assertIsNone(symbols)
            self.assertFalse(status["available"])
            self.assertTrue(status["stale"])
            self.assertEqual(status["reason"], "stale_or_invalid")
            self.assertEqual(status["detail"], "refresh_stopped_beyond_ttl")

    def test_live_snapshot_status_marks_refresh_delay_before_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "live_snapshot.json"
            now_epoch = 1_710_000_000.0
            snapshot_path.write_text(
                json.dumps(
                    {
                        "updated_at": datetime.fromtimestamp(
                            now_epoch - 220,
                            tz=timezone.utc,
                        ).isoformat(),
                        "ttl_seconds": 420,
                        "refresh_interval_seconds": 180,
                        "top_symbols": ["005930", "000660"],
                        "errors": [],
                    }
                ),
                encoding="utf-8",
            )

            with (
                mock.patch.object(live_snapshot_module, "SNAPSHOT_PATH", snapshot_path),
                mock.patch("app.market_data.live_snapshot.time.time", return_value=now_epoch),
            ):
                symbols = live_snapshot_module.load_live_snapshot_symbols()
                status = live_snapshot_module.live_snapshot_status()

            self.assertEqual(symbols, ("005930", "000660"))
            self.assertTrue(status["available"])
            self.assertFalse(status["stale"])
            self.assertEqual(status["reason"], "ok")
            self.assertEqual(status["detail"], "refresh_delayed_but_within_ttl")
            self.assertEqual(status["age_seconds"], 220.0)

    def test_live_snapshot_uses_ttl_grace_when_worker_is_healthy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "live_snapshot.json"
            pid_path = Path(temp_dir) / "worker.pid"
            heartbeat_path = Path(temp_dir) / "worker.heartbeat"
            now_epoch = 1_710_000_000.0
            snapshot_path.write_text(
                json.dumps(
                    {
                        "updated_at": datetime.fromtimestamp(
                            now_epoch - 438,
                            tz=timezone.utc,
                        ).isoformat(),
                        "ttl_seconds": 420,
                        "refresh_interval_seconds": 180,
                        "top_symbols": ["005930", "000660"],
                        "errors": [],
                    }
                ),
                encoding="utf-8",
            )
            pid_path.write_text("4242\n", encoding="utf-8")
            heartbeat_path.write_text(
                "\n".join(
                    [
                        f"timestamp={datetime.fromtimestamp(now_epoch - 70, tz=timezone.utc).isoformat()}",
                        "status=success",
                        "consecutive_failures=0",
                        f"last_success_at={datetime.fromtimestamp(now_epoch - 70, tz=timezone.utc).isoformat()}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with (
                mock.patch.object(live_snapshot_module, "SNAPSHOT_PATH", snapshot_path),
                mock.patch.object(live_snapshot_module, "WORKER_PID_PATH", pid_path),
                mock.patch.object(live_snapshot_module, "WORKER_HEARTBEAT_PATH", heartbeat_path),
                mock.patch.object(live_snapshot_module, "_pid_alive", return_value=True),
                mock.patch("app.market_data.live_snapshot.time.time", return_value=now_epoch),
            ):
                symbols = live_snapshot_module.load_live_snapshot_symbols()
                status = live_snapshot_module.live_snapshot_status()

            self.assertEqual(symbols, ("005930", "000660"))
            self.assertTrue(status["available"])
            self.assertFalse(status["stale"])
            self.assertEqual(status["reason"], "ok")
            self.assertEqual(status["detail"], "refresh_delayed_beyond_ttl_worker_healthy")
            self.assertEqual(status["ttl_grace_seconds"], 120)
            self.assertTrue(status["within_worker_grace"])

    def test_live_snapshot_status_handles_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "missing.json"
            with mock.patch.object(live_snapshot_module, "SNAPSHOT_PATH", snapshot_path):
                status = live_snapshot_module.live_snapshot_status()

            self.assertEqual(status["available"], False)
            self.assertEqual(status["reason"], "file_missing_or_parse_error")
            self.assertEqual(status["detail"], "snapshot_file_missing_or_unreadable")

    def test_live_snapshot_status_handles_read_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot_path = Path(temp_dir) / "live_snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "updated_at": datetime.fromtimestamp(
                            1_710_000_000,
                            tz=timezone.utc,
                        ).isoformat(),
                        "ttl_seconds": 300,
                        "top_symbols": ["005930"],
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.dict(os.environ, {"KIS_LOCAL_READ_MAX_BYTES": "10"}),
                mock.patch.object(live_snapshot_module, "SNAPSHOT_PATH", snapshot_path),
            ):
                symbols = live_snapshot_module.load_live_snapshot_symbols()
                status = live_snapshot_module.live_snapshot_status()

            self.assertIsNone(symbols)
            self.assertFalse(status["available"])
            self.assertEqual(status["reason"], "file_missing_or_parse_error")

    def test_live_snapshot_worker_health_warns_on_stale_heartbeat_and_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            pid_path = Path(temp_dir) / "worker.pid"
            heartbeat_path = Path(temp_dir) / "worker.heartbeat"
            now_epoch = 1_710_000_000.0
            pid_path.write_text("4242\n", encoding="utf-8")
            heartbeat_path.write_text(
                "\n".join(
                    [
                        f"timestamp={datetime.fromtimestamp(now_epoch - 400, tz=timezone.utc).isoformat()}",
                        "status=failure(1)",
                        "consecutive_failures=3",
                        f"last_success_at={datetime.fromtimestamp(now_epoch - 900, tz=timezone.utc).isoformat()}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            with (
                mock.patch.object(live_snapshot_module, "WORKER_PID_PATH", pid_path),
                mock.patch.object(live_snapshot_module, "WORKER_HEARTBEAT_PATH", heartbeat_path),
                mock.patch.object(live_snapshot_module, "_pid_alive", return_value=False),
                mock.patch("app.market_data.live_snapshot.time.time", return_value=now_epoch),
            ):
                health = live_snapshot_module.live_snapshot_worker_health(
                    refresh_interval_seconds=180,
                )

            self.assertEqual(health["worker_pid"], 4242)
            self.assertFalse(health["worker_alive"])
            self.assertEqual(health["consecutive_failures"], 3)
            self.assertEqual(health["heartbeat_age_seconds"], 400.0)
            self.assertTrue(
                any("heartbeat stale" in warning for warning in health["warnings"])
            )
            self.assertTrue(
                any("consecutive=3" in warning for warning in health["warnings"])
            )
            self.assertTrue(
                any("missing or unhealthy" in warning for warning in health["warnings"])
            )


if __name__ == "__main__":
    unittest.main()
