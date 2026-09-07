"""G3 — state_root() 이음새 회귀·리다이렉트 핀.

설계: docs/test_isolation_leak_design_20260707.md §4-1 P1 / G3.

기본값(KIS_STATE_ROOT 미설정)에서 경로가 현행과 바이트 동일해야 하고(무회귀),
KIS_STATE_ROOT 설정 시 signature 상태 파일 라이터가 그 루트로 리다이렉트되어야 한다.
"""

from pathlib import Path

import pytest

from app.auth import account_scope
from app.auth.account_scope import (
    _data_path_for_signature,
    _log_path_for_signature,
    dashboard_paths_for_signature,
    get_order_log_path,
    get_partitioned_log_path,
    state_root,
)
from app.auth.settings import PROJECT_ROOT

STATE_ROOT_ENV = "KIS_STATE_ROOT"


def test_state_root_defaults_to_project_root(monkeypatch):
    monkeypatch.delenv(STATE_ROOT_ENV, raising=False)
    assert state_root() == PROJECT_ROOT


def test_state_root_honors_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path))
    assert state_root() == tmp_path


def test_state_root_blank_env_falls_back_to_project_root(monkeypatch):
    monkeypatch.setenv(STATE_ROOT_ENV, "   ")
    assert state_root() == PROJECT_ROOT


def test_data_path_default_bytes_identical(monkeypatch):
    monkeypatch.delenv(STATE_ROOT_ENV, raising=False)
    resolved = _data_path_for_signature(
        "performance_snapshots", ".jsonl", signature="mock_acct_deadbeef"
    )
    assert resolved == PROJECT_ROOT / "data" / "performance_snapshots_mock_acct_deadbeef.jsonl"


def test_log_path_default_bytes_identical(monkeypatch):
    monkeypatch.delenv(STATE_ROOT_ENV, raising=False)
    resolved = _log_path_for_signature("orders", ".jsonl", signature="mock_acct_deadbeef")
    assert resolved == PROJECT_ROOT / "logs" / "orders_mock_acct_deadbeef.jsonl"


def test_data_path_redirects_under_state_root(monkeypatch, tmp_path):
    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path))
    resolved = _data_path_for_signature(
        "performance_snapshots", ".jsonl", signature="mock_acct_deadbeef"
    )
    assert resolved == tmp_path / "data" / "performance_snapshots_mock_acct_deadbeef.jsonl"


def test_log_path_redirects_under_state_root(monkeypatch, tmp_path):
    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path))
    resolved = _log_path_for_signature("orders", ".jsonl", signature="mock_acct_deadbeef")
    assert resolved == tmp_path / "logs" / "orders_mock_acct_deadbeef.jsonl"


def test_get_order_log_path_redirects_under_state_root(monkeypatch, tmp_path):
    """결정적 이음새: get_order_log_path()가 KIS_STATE_ROOT로 리다이렉트."""
    monkeypatch.setattr(account_scope, "get_account_signature", lambda settings=None: "mock_acct_deadbeef")
    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path))
    resolved = get_order_log_path()
    assert resolved == tmp_path / "logs" / "orders_mock_acct_deadbeef.jsonl"
    # 기본값에서는 현행 경로
    monkeypatch.delenv(STATE_ROOT_ENV, raising=False)
    assert get_order_log_path() == PROJECT_ROOT / "logs" / "orders_mock_acct_deadbeef.jsonl"


def test_partitioned_log_path_redirects_under_state_root(monkeypatch, tmp_path):
    monkeypatch.setattr(account_scope, "get_account_signature", lambda settings=None: "mock_acct_deadbeef")
    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path))
    resolved = get_partitioned_log_path("orders", suffix="rotated_20260101")
    assert resolved == tmp_path / "logs" / "orders_mock_acct_deadbeef_rotated_20260101.jsonl"


def test_dashboard_paths_default_bytes_identical(monkeypatch):
    monkeypatch.delenv(STATE_ROOT_ENV, raising=False)
    paths = dashboard_paths_for_signature("mock_acct_deadbeef")
    assert paths["orders"] == PROJECT_ROOT / "logs" / "orders_mock_acct_deadbeef.jsonl"
    assert paths["cycle_snapshots"] == PROJECT_ROOT / "data" / "cycle_snapshots_mock_acct_deadbeef.jsonl"


def test_dashboard_paths_redirect_under_state_root(monkeypatch, tmp_path):
    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path))
    paths = dashboard_paths_for_signature("mock_acct_deadbeef")
    assert paths["orders"] == tmp_path / "logs" / "orders_mock_acct_deadbeef.jsonl"
    assert paths["performance_snapshots"] == tmp_path / "data" / "performance_snapshots_mock_acct_deadbeef.jsonl"


def test_dashboard_paths_explicit_dirs_override_state_root(monkeypatch, tmp_path):
    """명시 data_dir/logs_dir는 state_root보다 우선(하네틱 테스트 계약 보존)."""
    monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path / "ignored"))
    explicit_data = tmp_path / "explicit_data"
    explicit_logs = tmp_path / "explicit_logs"
    paths = dashboard_paths_for_signature(
        "mock_acct_deadbeef", data_dir=explicit_data, logs_dir=explicit_logs
    )
    assert paths["orders"] == explicit_logs / "orders_mock_acct_deadbeef.jsonl"
    assert paths["cycle_snapshots"] == explicit_data / "cycle_snapshots_mock_acct_deadbeef.jsonl"
