from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = PROJECT_ROOT / "scripts" / "eod_wrapper.sh"


def _write_fake_check_eod(path: Path, *, exit_code: int) -> None:
    path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "printf 'fake check_eod account=%s date=%s\\n' \"$1\" \"$2\"",
                f"exit {exit_code}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)


def _run_wrapper(tmp_path: Path, *, exit_code: int) -> subprocess.CompletedProcess[str]:
    fake_check = tmp_path / "check_eod.sh"
    _write_fake_check_eod(fake_check, exit_code=exit_code)
    reports_dir = tmp_path / "reports"
    env = os.environ.copy()
    env.update(
        {
            "CHECK_EOD_SCRIPT_OVERRIDE": str(fake_check),
            "REPORTS_DIR_OVERRIDE": str(reports_dir),
            "PYTHON_OVERRIDE": sys.executable,
            "SLACK_ALERTS_ENABLED": "false",
            # Keep the subprocess integration tests hermetic: the best-effort
            # orchestrator audit reads real local account state, so disable it
            # here. Its script contract is pinned by the text tests below.
            "EOD_WRAPPER_POSTRUN_AUDIT": "false",
        }
    )
    return subprocess.run(
        ["bash", str(WRAPPER), "mock_12345678_01", "20260608"],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )


def test_eod_wrapper_captures_success_report(tmp_path: Path) -> None:
    result = _run_wrapper(tmp_path, exit_code=0)

    report = tmp_path / "reports" / "eod_20260608.txt"
    assert result.returncode == 0
    assert report.exists()
    text = report.read_text(encoding="utf-8")
    assert "fake check_eod account=mock_12345678_01 date=20260608" in text
    assert "Exit code   : 0" in text
    assert "Slack notification: skipped reason=disabled" in text
    assert f"EOD report: {report}" in result.stdout


def test_eod_wrapper_preserves_failed_exit_code(tmp_path: Path) -> None:
    result = _run_wrapper(tmp_path, exit_code=7)

    report = tmp_path / "reports" / "eod_20260608.txt"
    assert result.returncode == 7
    text = report.read_text(encoding="utf-8")
    assert "Exit code   : 7" in text
    assert "Slack notification: skipped reason=disabled" in text


def test_eod_wrapper_invokes_postrun_audit_best_effort() -> None:
    text = WRAPPER.read_text(encoding="utf-8")
    audit_lines = [ln for ln in text.splitlines() if "orchestrate postrun_audit" in ln]
    assert audit_lines, "eod_wrapper must invoke app.tools.orchestrate postrun_audit"
    assert any("|| true" in ln for ln in audit_lines), (
        "postrun_audit call must be best-effort (|| true) so an audit failure "
        "never escalates the EOD exit code"
    )


def test_eod_wrapper_preserves_exit_code_contract() -> None:
    text = WRAPPER.read_text(encoding="utf-8")
    assert 'exit "${exit_code}"' in text, (
        "eod_wrapper must preserve check_eod's exit code as its final exit — the "
        "best-effort audit must never replace this contract"
    )
