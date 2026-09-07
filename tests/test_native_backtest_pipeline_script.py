"""S5a-2 (docs/slack_backtest_native_pipeline_design_20260707.md §4.2): the
native pipeline script honors the same argv contract the Slack launcher emits.
Exercised in --dry-run so no replay runs and no parquet store is needed."""

from __future__ import annotations

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "native_backtest_pipeline.sh"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_dry_run_echoes_plan_and_exits_zero() -> None:
    result = _run(["--account", "mock_acct_x", "--date", "20260701", "--dry-run"])
    assert result.returncode == 0, result.stderr
    assert "DRY-RUN: not executing" in result.stdout
    # S1: --account is accepted+ignored (backward compat) — the banner no
    # longer echoes the account value.
    assert "mock_acct_x" not in result.stdout
    assert "run_native_backtest" in result.stdout
    # --date maps to the native --end window bound
    assert "--end 20260701" in result.stdout


def test_skip_run_maps_to_plan_only() -> None:
    result = _run(["--account", "mock_acct_x", "--skip-run", "--dry-run"])
    assert result.returncode == 0, result.stderr
    assert "--plan-only" in result.stdout


def test_days_flag_overrides_max_days() -> None:
    # S3: `--days N` overrides the NATIVE_BACKTEST_MAX_DAYS env default and
    # maps to the native runner's `--max-days N`.
    result = _run(["--days", "10", "--dry-run"])
    assert result.returncode == 0, result.stderr
    assert "max-days:  10" in result.stdout
    assert "--max-days 10" in result.stdout


def test_stop_after_is_accepted_as_noop() -> None:
    result = _run(["--account", "mock_acct_x", "--stop-after", "--dry-run"])
    assert result.returncode == 0, result.stderr
    assert "DRY-RUN" in result.stdout


def test_unknown_option_is_rejected() -> None:
    result = _run(["--account", "x", "--bogus", "--dry-run"])
    assert result.returncode != 0
    assert "Unknown option" in result.stderr
