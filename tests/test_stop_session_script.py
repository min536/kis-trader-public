from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


class StopSessionScriptTests(unittest.TestCase):
    def test_stop_session_stops_refresh_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            log_dir = temp_path / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            pid_file = log_dir / "kis_trader.pid"
            refresh_pid_file = log_dir / "kis_trader_live_snapshot.pid"
            app_script = temp_path / "app_main.py"
            app_script.write_text(
                "import signal\n"
                "import sys\n"
                "import time\n"
                "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
                "signal.signal(signal.SIGINT, lambda *_: sys.exit(0))\n"
                "time.sleep(30)\n",
                encoding="utf-8",
            )
            refresh_script = temp_path / "live_snapshot.py"
            refresh_script.write_text(
                "import signal\n"
                "import sys\n"
                "import time\n"
                "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
                "signal.signal(signal.SIGINT, lambda *_: sys.exit(0))\n"
                "time.sleep(30)\n",
                encoding="utf-8",
            )

            app_proc = subprocess.Popen(
                [
                    sys.executable,
                    str(app_script),
                ]
            )
            refresh_proc = subprocess.Popen(
                [
                    sys.executable,
                    str(refresh_script),
                ]
            )
            try:
                pid_file.write_text(f"{app_proc.pid}\n", encoding="utf-8")
                refresh_pid_file.write_text(f"{refresh_proc.pid}\n", encoding="utf-8")

                env = os.environ.copy()
                env.update(
                    {
                        "PROJECT_DIR_OVERRIDE": temp_dir,
                        "PYTHON_OVERRIDE": sys.executable,
                        "LOG_DIR_OVERRIDE": str(log_dir),
                        "LOCK_HELPER_OVERRIDE": str(
                            Path(__file__).resolve().parents[1] / "scripts" / "session_lock.py"
                        ),
                    }
                )

                stop_script = Path(__file__).resolve().parents[1] / "scripts" / "stop_session.sh"
                result = subprocess.run(
                    [str(stop_script)],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=env,
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                session_log = (log_dir / "session_scheduler.log").read_text(encoding="utf-8")
                self.assertTrue(
                    (
                        "Stopping live snapshot refresh worker pid=" in session_log
                        or "Removing stale live snapshot refresh worker reference pid=" in session_log
                    ),
                    session_log,
                )

                app_proc.wait(timeout=5)
                self.assertFalse(refresh_pid_file.exists())
            finally:
                for proc in (app_proc, refresh_proc):
                    if proc.poll() is None:
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
