from __future__ import annotations

import os
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path


class RestartSessionScriptTests(unittest.TestCase):
    def _write_executable(self, path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def _base_env(self, temp_dir: str) -> dict[str, str]:
        root = Path(__file__).resolve().parents[1]
        log_dir = Path(temp_dir) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        helper = root / "scripts" / "session_lock.py"
        return {
            "PROJECT_DIR_OVERRIDE": temp_dir,
            "PYTHON_OVERRIDE": "python3",
            "LOG_DIR_OVERRIDE": str(log_dir),
            "SESSION_LOG_OVERRIDE": str(log_dir / "session_scheduler.log"),
            "PID_FILE_OVERRIDE": str(log_dir / "kis_trader.pid"),
            "LOCK_FILE_OVERRIDE": str(log_dir / "kis_trader.session.lock"),
            "LOCK_HELPER_OVERRIDE": str(helper),
            "STOP_TIMEOUT_SECS_OVERRIDE": "1",
        }

    def _write_helper_wrapper(self, path: Path, *, same_program_value: str) -> None:
        real_helper = Path(__file__).resolve().parents[1] / "scripts" / "session_lock.py"
        self._write_executable(
            path,
            textwrap.dedent(
                f"""\
                #!/usr/bin/env python3
                import subprocess
                import sys

                REAL_HELPER = {str(real_helper)!r}

                argv = sys.argv[1:]
                if "status" in argv and "--field" in argv:
                    field = argv[argv.index("--field") + 1]
                    if field == "same_program":
                        print({same_program_value!r})
                        raise SystemExit(0)
                result = subprocess.run(["{sys.executable}", REAL_HELPER, *argv], check=False)
                raise SystemExit(result.returncode)
                """
            ),
        )

    def _start_waiting_wrapper(
        self,
        *,
        command: str,
        pid_path: Path,
    ) -> subprocess.Popen[str]:
        return subprocess.Popen(
            [
                "bash",
                "-lc",
                f"{command} & child=$!; echo $child > {pid_path}; wait $child",
            ],
            text=True,
        )

    def test_restart_session_starts_new_instance_after_clean_stop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = os.environ.copy()
            env.update(self._base_env(temp_dir))
            helper_wrapper = Path(temp_dir) / "session_lock_wrapper.py"
            self._write_helper_wrapper(helper_wrapper, same_program_value="true")
            env["LOCK_HELPER_OVERRIDE"] = str(helper_wrapper)
            run_marker = Path(temp_dir) / "run_started.txt"
            run_script = Path(temp_dir) / "run_session.sh"
            self._write_executable(
                run_script,
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env bash
                    set -euo pipefail
                    echo "started" > "{run_marker}"
                    """
                ),
            )
            env["RUN_SCRIPT_OVERRIDE"] = str(run_script)
            refresh_script = Path(temp_dir) / "live_snapshot.py"
            refresh_script.write_text(
                "import signal\n"
                "import sys\n"
                "import time\n"
                "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
                "signal.signal(signal.SIGINT, lambda *_: sys.exit(0))\n"
                "time.sleep(30)\n",
                encoding="utf-8",
            )
            refresh_proc = subprocess.Popen(
                ["python3", str(refresh_script)]
            )
            env["LIVE_SNAPSHOT_REFRESH_PID_FILE_OVERRIDE"] = str(
                Path(temp_dir) / "logs" / "kis_trader_live_snapshot.pid"
            )
            Path(env["LIVE_SNAPSHOT_REFRESH_PID_FILE_OVERRIDE"]).write_text(
                f"{refresh_proc.pid}\n",
                encoding="utf-8",
            )

            child_pid_path = Path(temp_dir) / "child.pid"
            proc = self._start_waiting_wrapper(
                command="python3 -c 'import time; time.sleep(30)' app.main",
                pid_path=child_pid_path,
            )
            try:
                for _ in range(20):
                    if child_pid_path.exists():
                        break
                    time.sleep(0.1)
                self.assertTrue(child_pid_path.exists(), "child pid was not recorded")
                child_pid = int(child_pid_path.read_text(encoding="utf-8").strip())
                subprocess.run(
                    [
                        "python3",
                        env["LOCK_HELPER_OVERRIDE"],
                        "update",
                        "--lock-path",
                        env["LOCK_FILE_OVERRIDE"],
                        "--pid",
                        str(os.getpid()),
                        "--app-pid",
                        str(child_pid),
                        "--status",
                        "running",
                        "--command",
                        "scripts/run_session.sh -> python3 -m app.main",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                Path(env["PID_FILE_OVERRIDE"]).write_text(f"{child_pid}\n", encoding="utf-8")

                restart_script = Path(__file__).resolve().parents[1] / "scripts" / "restart_session.sh"
                result = subprocess.run(
                    [str(restart_script)],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=env,
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(run_marker.exists(), "restart script did not launch replacement session")
                session_log = Path(env["SESSION_LOG_OVERRIDE"]).read_text(encoding="utf-8")
                self.assertTrue(
                    (
                        "Stopping live snapshot refresh worker pid=" in session_log
                        or "Removing stale live snapshot refresh worker reference pid=" in session_log
                    ),
                    session_log,
                )
                self.assertIn("Restart success: launching new regular session.", session_log)
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                if refresh_proc.poll() is None:
                    refresh_proc.terminate()
                    try:
                        refresh_proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        refresh_proc.kill()
                        refresh_proc.wait(timeout=5)

    def test_restart_session_blocks_when_process_ignores_term(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = os.environ.copy()
            env.update(self._base_env(temp_dir))
            helper_wrapper = Path(temp_dir) / "session_lock_wrapper.py"
            self._write_helper_wrapper(helper_wrapper, same_program_value="true")
            env["LOCK_HELPER_OVERRIDE"] = str(helper_wrapper)
            run_script = Path(temp_dir) / "run_session.sh"
            self._write_executable(
                run_script,
                "#!/usr/bin/env bash\nset -euo pipefail\necho should-not-run\n",
            )
            env["RUN_SCRIPT_OVERRIDE"] = str(run_script)

            child_pid_path = Path(temp_dir) / "child.pid"
            child_ready_path = Path(temp_dir) / "child.ready"
            proc = self._start_waiting_wrapper(
                command=(
                    "python3 -c \"import pathlib,signal,time;"
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN);"
                    f"pathlib.Path({str(child_ready_path)!r}).write_text('ready', encoding='utf-8');"
                    "time.sleep(30)\" app.main"
                ),
                pid_path=child_pid_path,
            )
            try:
                for _ in range(20):
                    if child_pid_path.exists():
                        break
                    time.sleep(0.1)
                self.assertTrue(child_pid_path.exists(), "child pid was not recorded")
                for _ in range(20):
                    if child_ready_path.exists():
                        break
                    time.sleep(0.1)
                self.assertTrue(child_ready_path.exists(), "child signal handler was not ready")
                child_pid = int(child_pid_path.read_text(encoding="utf-8").strip())
                subprocess.run(
                    [
                        "python3",
                        env["LOCK_HELPER_OVERRIDE"],
                        "update",
                        "--lock-path",
                        env["LOCK_FILE_OVERRIDE"],
                        "--pid",
                        str(os.getpid()),
                        "--app-pid",
                        str(child_pid),
                        "--status",
                        "running",
                        "--command",
                        "scripts/run_session.sh -> python3 -m app.main",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                Path(env["PID_FILE_OVERRIDE"]).write_text(f"{child_pid}\n", encoding="utf-8")

                restart_script = Path(__file__).resolve().parents[1] / "scripts" / "restart_session.sh"
                result = subprocess.run(
                    [str(restart_script)],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=env,
                )

                self.assertEqual(result.returncode, 1)
                session_log = Path(env["SESSION_LOG_OVERRIDE"]).read_text(encoding="utf-8")
                self.assertIn("ERROR: Restart blocked.", session_log)
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)

    def test_restart_session_blocks_for_unrelated_running_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = os.environ.copy()
            env.update(self._base_env(temp_dir))
            helper_wrapper = Path(temp_dir) / "session_lock_wrapper.py"
            self._write_helper_wrapper(helper_wrapper, same_program_value="false")
            env["LOCK_HELPER_OVERRIDE"] = str(helper_wrapper)
            run_script = Path(temp_dir) / "run_session.sh"
            self._write_executable(
                run_script,
                "#!/usr/bin/env bash\nset -euo pipefail\necho should-not-run\n",
            )
            env["RUN_SCRIPT_OVERRIDE"] = str(run_script)

            proc = subprocess.Popen(
                ["python3", "-c", "import time; time.sleep(30)", "unrelated.process"]
            )
            try:
                subprocess.run(
                    [
                        "python3",
                        env["LOCK_HELPER_OVERRIDE"],
                        "update",
                        "--lock-path",
                        env["LOCK_FILE_OVERRIDE"],
                        "--pid",
                        str(os.getpid()),
                        "--app-pid",
                        str(proc.pid),
                        "--status",
                        "running",
                        "--command",
                        "scripts/run_session.sh -> python3 -m app.main",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                Path(env["PID_FILE_OVERRIDE"]).write_text(f"{proc.pid}\n", encoding="utf-8")

                restart_script = Path(__file__).resolve().parents[1] / "scripts" / "restart_session.sh"
                result = subprocess.run(
                    [str(restart_script)],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=env,
                )

                self.assertEqual(result.returncode, 1)
                session_log = Path(env["SESSION_LOG_OVERRIDE"]).read_text(encoding="utf-8")
                self.assertIn("does not match kis-trader session command", session_log)
            finally:
                proc.terminate()
                proc.wait(timeout=5)
