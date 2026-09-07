"""Tests for the C-3 read-only multi-account console overview.

Fixtures write per-account state files under tmp ``data/``/``logs/`` and assert
the aggregation composes discovery + per-signature load + the C-2 console
overview. No network, no broker, no active-Settings coupling.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.dashboard.multi_account_overview import build_multi_account_overview

SIG_A = "mock_acct_0123456789abcdef"
SIG_B = "mock_acct_fe5860362b4612cc"


@pytest.fixture()
def disk(tmp_path: Path):
    data = tmp_path / "data"
    logs = tmp_path / "logs"
    data.mkdir()
    logs.mkdir()
    return data, logs


def _seed_account(data: Path, logs: Path, sig: str, *, main_pid: int) -> None:
    (data / f"runtime_state_{sig}.json").write_text(
        json.dumps({"account_signature": sig, "main_pid": main_pid}),
        encoding="utf-8",
    )
    (data / f"cycle_snapshots_{sig}.jsonl").write_text(
        json.dumps({"timestamp": "2026-06-20T10:00:00", "phase": "REGULAR"}) + "\n",
        encoding="utf-8",
    )
    (logs / f"orders_{sig}.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-06-20T10:01:00",
                "action": "BUY",
                "symbol": "005930",
                "result": "submitted",
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_empty_disk_returns_no_accounts(disk):
    data, logs = disk
    report = build_multi_account_overview(data_dir=data, logs_dir=logs)
    assert report == {"count": 0, "accounts": []}


def test_single_account_overview_shape(disk):
    data, logs = disk
    _seed_account(data, logs, SIG_A, main_pid=4242)
    report = build_multi_account_overview(
        data_dir=data, logs_dir=logs, now_epoch=1_750_000_000.0
    )
    assert report["count"] == 1
    row = report["accounts"][0]
    assert row["signature"] == SIG_A
    assert row["environment"] == "mock"
    assert "runtime_state" in row["sources"]
    overview = row["overview"]
    # C-2 console overview keys are present and scoped to this account
    assert set(overview) >= {"session", "api_health", "orders_today", "account_signature"}
    assert overview["account_signature"] == SIG_A
    assert overview["session"]["main_pid"] == 4242


def test_two_accounts_are_independently_scoped(disk):
    data, logs = disk
    _seed_account(data, logs, SIG_A, main_pid=111)
    _seed_account(data, logs, SIG_B, main_pid=222)
    report = build_multi_account_overview(
        data_dir=data, logs_dir=logs, now_epoch=1_750_000_000.0
    )
    assert report["count"] == 2
    by_sig = {row["signature"]: row for row in report["accounts"]}
    assert set(by_sig) == {SIG_A, SIG_B}
    # each account's overview reflects its own runtime_state, not a shared/global one
    assert by_sig[SIG_A]["overview"]["session"]["main_pid"] == 111
    assert by_sig[SIG_B]["overview"]["session"]["main_pid"] == 222
    assert by_sig[SIG_A]["overview"]["account_signature"] == SIG_A
    assert by_sig[SIG_B]["overview"]["account_signature"] == SIG_B


def test_cli_json_lists_accounts(disk):
    data, logs = disk
    _seed_account(data, logs, SIG_A, main_pid=999)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.dashboard.multi_account_overview",
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
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["count"] == 1
    assert payload["accounts"][0]["signature"] == SIG_A
    assert payload["accounts"][0]["overview"]["account_signature"] == SIG_A


def test_cli_text_output_when_empty(disk):
    data, logs = disk
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.dashboard.multi_account_overview",
            "--data-dir",
            str(data),
            "--logs-dir",
            str(logs),
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert result.returncode == 0, result.stderr
    assert "no account state found" in result.stdout
