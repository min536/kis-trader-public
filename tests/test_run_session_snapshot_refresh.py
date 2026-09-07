from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


class RunSessionSnapshotRefreshTests(unittest.TestCase):
    def _write_file(self, path: Path, content: str, *, executable: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if executable:
            path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def test_run_session_starts_snapshot_refresh_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            log_dir = project_dir / "logs"
            config_dir = project_dir / "config"
            marker_path = project_dir / "snapshot_ran.txt"
            wake_guard_marker_path = project_dir / "wake_guard_ran.txt"

            self._write_file(
                project_dir / "app" / "__init__.py",
                "",
            )
            self._write_file(
                project_dir / "app" / "main.py",
                textwrap.dedent(
                    """\
                    import signal
                    import time

                    running = True

                    def _stop(*_args):
                        global running
                        running = False

                    signal.signal(signal.SIGTERM, _stop)
                    signal.signal(signal.SIGINT, _stop)

                    while running:
                        time.sleep(0.1)
                    """
                ),
            )
            self._write_file(
                project_dir / "scripts" / "live_snapshot.py",
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env python3
                    from pathlib import Path

                    marker = Path({str(marker_path)!r})
                    marker.write_text("snapshot-ran\\n", encoding="utf-8")
                    print("[live_snapshot] refresh success: updated_at=2026-04-18T09:00:00+09:00 | age=0s | ttl=420s | refresh=1s")
                    """
                ),
                executable=True,
            )
            self._write_file(
                config_dir / "regular_session.env",
                textwrap.dedent(
                    """\
                    LIVE_SNAPSHOT_REFRESH_INTERVAL_SECONDS=1
                    LIVE_SNAPSHOT_TTL_SECONDS=420
                    """
                ),
            )
            self._write_file(
                project_dir / "fake_caffeinate.py",
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env python3
                    import os
                    import sys
                    import time
                    from pathlib import Path

                    Path({str(wake_guard_marker_path)!r}).write_text("wake-guard-ran\\n", encoding="utf-8")
                    pid = None
                    if "-w" in sys.argv:
                        try:
                            pid = int(sys.argv[sys.argv.index("-w") + 1])
                        except (ValueError, IndexError):
                            pid = None
                    while pid:
                        try:
                            os.kill(pid, 0)
                        except OSError:
                            break
                        time.sleep(0.05)
                    """
                ),
                executable=True,
            )

            env = os.environ.copy()
            env.update(
                {
                    "PROJECT_DIR_OVERRIDE": str(project_dir),
                    "PYTHON_OVERRIDE": sys.executable,
                    "LOG_DIR_OVERRIDE": str(log_dir),
                    "SESSION_ENV_FILE_OVERRIDE": str(config_dir / "regular_session.env"),
                    "LOCK_HELPER_OVERRIDE": str(
                        Path(__file__).resolve().parents[1] / "scripts" / "session_lock.py"
                    ),
                    "STARTUP_WATCH_SECS_OVERRIDE": "1",
                    "STOP_HOUR_OVERRIDE": "0",
                    "STOP_MIN_OVERRIDE": "0",
                    "STOP_SEC_OVERRIDE": "0",
                    "SESSION_DATE_OVERRIDE": "20260418",
                    "CAFFEINATE_BIN_OVERRIDE": str(project_dir / "fake_caffeinate.py"),
                }
            )

            run_script = Path(__file__).resolve().parents[1] / "scripts" / "run_session.sh"
            result = subprocess.run(
                [str(run_script)],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(marker_path.exists(), "snapshot refresh worker did not run")
            self.assertTrue(wake_guard_marker_path.exists(), "session wake guard did not run")
            session_log = (log_dir / "session_scheduler.log").read_text(encoding="utf-8")
            self.assertIn("Live snapshot refresh worker started", session_log)
            self.assertIn("Session wake guard started", session_log)
            self.assertNotIn("unbound variable", session_log)
            self.assertNotIn("${BASHPID}", run_script.read_text(encoding="utf-8"))
            self.assertTrue((log_dir / "app_stdout_20260418.log").exists())
            self.assertTrue((log_dir / "app_stderr_20260418.log").exists())
            self.assertTrue((log_dir / "app_stdout.log").is_symlink())
            self.assertTrue((log_dir / "app_stderr.log").is_symlink())
            self.assertFalse((log_dir / "kis_trader_live_snapshot.pid").exists())
            self.assertFalse((log_dir / "kis_trader_wake_guard.pid").exists())


if __name__ == "__main__":
    unittest.main()
