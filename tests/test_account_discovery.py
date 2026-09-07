"""Tests for filename-based account discovery (C-3 multi-account foundation).

Fixtures create empty marker files (filenames only) — discovery never reads
file contents.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.dashboard.account_discovery import (
    discover_account_signatures,
    discover_accounts,
)

SIG_A = "mock_acct_0123456789abcdef"
SIG_B = "mock_acct_fe5860362b4612cc"
SIG_LEGACY = "mock_12345678_01"


@pytest.fixture()
def disk(tmp_path: Path):
    data = tmp_path / "data"
    logs = tmp_path / "logs"
    data.mkdir()
    logs.mkdir()
    return data, logs


def _touch(path: Path) -> None:
    path.write_text("", encoding="utf-8")


def test_empty_dirs_return_nothing(disk):
    data, logs = disk
    assert discover_account_signatures(data_dir=data, logs_dir=logs) == ()
    assert discover_accounts(data_dir=data, logs_dir=logs) == ()


def test_discovers_signatures_across_data_and_logs(disk):
    data, logs = disk
    _touch(data / f"runtime_state_{SIG_A}.json")
    _touch(logs / f"orders_{SIG_B}.jsonl")
    assert discover_account_signatures(data_dir=data, logs_dir=logs) == (SIG_A, SIG_B)


def test_dedupes_signature_present_in_multiple_artifacts(disk):
    data, logs = disk
    _touch(data / f"runtime_state_{SIG_A}.json")
    _touch(data / f"cycle_snapshots_{SIG_A}.jsonl")
    _touch(logs / f"orders_{SIG_A}.jsonl")
    sigs = discover_account_signatures(data_dir=data, logs_dir=logs)
    assert sigs == (SIG_A,)


def test_legacy_and_new_signatures_both_found(disk):
    data, logs = disk
    _touch(data / f"runtime_state_{SIG_A}.json")
    _touch(data / f"runtime_state_{SIG_LEGACY}.json")
    sigs = discover_account_signatures(data_dir=data, logs_dir=logs)
    assert set(sigs) == {SIG_A, SIG_LEGACY}


def test_account_presence_environment_and_sources(disk):
    data, logs = disk
    _touch(data / f"runtime_state_{SIG_A}.json")
    _touch(logs / f"orders_{SIG_A}.jsonl")
    accounts = discover_accounts(data_dir=data, logs_dir=logs)
    assert len(accounts) == 1
    account = accounts[0]
    assert account.signature == SIG_A
    assert account.environment == "mock"
    assert account.sources == ("orders", "runtime_state")


def test_ignores_unrelated_files(disk):
    data, logs = disk
    _touch(data / "account_scope_meta.json")
    _touch(data / "live_snapshot.json")
    _touch(logs / "kis_trader.session.lock")
    assert discover_account_signatures(data_dir=data, logs_dir=logs) == ()


def test_rotated_backup_files_are_not_pseudo_signatures(disk):
    """Incident backups like runtime_state_<sig>_rotated_<ts>.json must not
    surface `<sig>_rotated_<ts>` as its own account in the selector (observed
    polluting the v2 account options on 2026-07-06)."""
    data, logs = disk
    _touch(data / f"runtime_state_{SIG_A}.json")
    _touch(data / f"runtime_state_{SIG_A}_rotated_20260704_155506.json")
    _touch(data / f"cycle_snapshots_{SIG_LEGACY}_rotated_20260704_155506.jsonl")

    signatures = discover_account_signatures(data_dir=data, logs_dir=logs)

    assert signatures == (SIG_A,)


def test_missing_dirs_are_safe(tmp_path: Path):
    # neither dir exists
    sigs = discover_account_signatures(
        data_dir=tmp_path / "nope_data", logs_dir=tmp_path / "nope_logs"
    )
    assert sigs == ()


def test_partitioned_log_strips_date_suffix(disk):
    data, logs = disk
    _touch(logs / f"cycle_stats_{SIG_A}_20260622.jsonl")
    sigs = discover_account_signatures(data_dir=data, logs_dir=logs)
    assert sigs == (SIG_A,)


def test_partitioned_logs_dedup_across_dates(disk):
    data, logs = disk
    _touch(logs / f"candidate_outcomes_{SIG_A}_20260101.jsonl")
    _touch(logs / f"candidate_outcomes_{SIG_A}_20260102.jsonl")
    _touch(logs / f"backtest_signals_{SIG_A}_20260103.jsonl")
    accounts = discover_accounts(data_dir=data, logs_dir=logs)
    assert len(accounts) == 1
    assert accounts[0].signature == SIG_A
    assert accounts[0].sources == ("backtest_signals", "candidate_outcomes")


def test_partitioned_only_account_is_discovered(disk):
    data, logs = disk
    _touch(logs / f"cycle_stats_{SIG_B}_20260622.jsonl")
    assert discover_account_signatures(data_dir=data, logs_dir=logs) == (SIG_B,)


def test_legacy_signature_date_suffix_not_overstripped(disk):
    # legacy sig ends in 2-digit _01; only the 8-digit date must be stripped
    data, logs = disk
    _touch(logs / f"cycle_stats_{SIG_LEGACY}_20260512.jsonl")
    sigs = discover_account_signatures(data_dir=data, logs_dir=logs)
    assert sigs == (SIG_LEGACY,)


def test_cli_json_lists_accounts(disk):
    data, logs = disk
    _touch(data / f"runtime_state_{SIG_A}.json")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.dashboard.account_discovery",
            "--data-dir",
            str(data),
            "--logs-dir",
            str(logs),
            "--json",
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload == [
        {"signature": SIG_A, "environment": "mock", "sources": ["runtime_state"]}
    ]
