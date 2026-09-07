"""Tests for the read-only multi-account path-isolation preflight (C-4 Phase 2).

Secrets-free, network-free: only checks that operator-set isolation dirs
resolve to pairwise-distinct paths.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.tools.multi_account_preflight import (
    AccountEnvProfile,
    check_isolation,
    load_profiles,
    resolve_resources,
)


def _profile(label: str, base: Path, **overrides) -> AccountEnvProfile:
    kwargs = {
        "label": label,
        "snapshot_dir": str(base / "data"),
        "snapshot_log_dir": str(base / "logs"),
        "credential_cache_dir": str(base / "cache"),
    }
    kwargs.update(overrides)
    return AccountEnvProfile(**kwargs)


def test_distinct_profiles_pass(tmp_path: Path):
    a = _profile("A", tmp_path / "A")
    b = _profile("B", tmp_path / "B")
    report = check_isolation([a, b])
    assert report.ok is True
    assert report.issues == ()


def test_both_unset_collide_on_all_three(tmp_path: Path):
    a = AccountEnvProfile(label="A")
    b = AccountEnvProfile(label="B")
    report = check_isolation([a, b])
    assert report.ok is False
    resources = {issue.resource for issue in report.issues}
    assert resources == {"live_snapshot", "snapshot_worker_dir", "token_cache"}


def test_shared_snapshot_dir_only(tmp_path: Path):
    shared = tmp_path / "shared"
    a = AccountEnvProfile(
        label="A",
        snapshot_dir=str(shared),
        snapshot_log_dir=str(tmp_path / "A" / "logs"),
        credential_cache_dir=str(tmp_path / "A" / "cache"),
    )
    b = AccountEnvProfile(
        label="B",
        snapshot_dir=str(shared),
        snapshot_log_dir=str(tmp_path / "B" / "logs"),
        credential_cache_dir=str(tmp_path / "B" / "cache"),
    )
    report = check_isolation([a, b])
    assert report.ok is False
    assert [issue.resource for issue in report.issues] == ["live_snapshot"]
    assert report.issues[0].env_var == "KIS_LIVE_SNAPSHOT_DIR"


def test_same_env_shared_cache_dir_collides(tmp_path: Path):
    shared_cache = str(tmp_path / "cache")
    a = AccountEnvProfile(
        label="A",
        snapshot_dir=str(tmp_path / "A" / "data"),
        snapshot_log_dir=str(tmp_path / "A" / "logs"),
        credential_cache_dir=shared_cache,
        env="mock",
    )
    b = AccountEnvProfile(
        label="B",
        snapshot_dir=str(tmp_path / "B" / "data"),
        snapshot_log_dir=str(tmp_path / "B" / "logs"),
        credential_cache_dir=shared_cache,
        env="mock",
    )
    report = check_isolation([a, b])
    assert report.ok is False
    assert [issue.resource for issue in report.issues] == ["token_cache"]


def test_same_cache_dir_different_env_is_distinct(tmp_path: Path):
    shared_cache = str(tmp_path / "cache")
    a = _profile("A", tmp_path / "A", credential_cache_dir=shared_cache, env="mock")
    b = _profile("B", tmp_path / "B", credential_cache_dir=shared_cache, env="live")
    # kis_auth_mock.json vs kis_auth_live.json -> distinct files
    report = check_isolation([a, b])
    cache_a = resolve_resources(a)["token_cache"].name
    cache_b = resolve_resources(b)["token_cache"].name
    assert cache_a == "kis_auth_mock.json"
    assert cache_b == "kis_auth_live.json"
    assert report.ok is True


def test_dotdot_paths_to_same_dir_collide(tmp_path: Path):
    # "<x>/sub/.." resolves to "<x>" — must be detected as a collision
    shared = tmp_path / "shared"
    a = AccountEnvProfile(
        label="A",
        snapshot_dir=str(shared),
        snapshot_log_dir=str(tmp_path / "A/logs"),
        credential_cache_dir=str(tmp_path / "A/cache"),
    )
    b = AccountEnvProfile(
        label="B",
        snapshot_dir=str(tmp_path / "shared" / "sub" / ".."),
        snapshot_log_dir=str(tmp_path / "B/logs"),
        credential_cache_dir=str(tmp_path / "B/cache"),
    )
    report = check_isolation([a, b])
    assert report.ok is False
    assert [issue.resource for issue in report.issues] == ["live_snapshot"]


def test_relative_and_absolute_same_dir_collide(tmp_path, monkeypatch):
    # a relative path and its absolute equivalent must compare equal
    monkeypatch.chdir(tmp_path)
    a = AccountEnvProfile(
        label="A",
        snapshot_dir=str(tmp_path / "data" / "acct"),
        snapshot_log_dir=str(tmp_path / "A/logs"),
        credential_cache_dir=str(tmp_path / "A/cache"),
    )
    b = AccountEnvProfile(
        label="B",
        snapshot_dir="data/acct",
        snapshot_log_dir=str(tmp_path / "B/logs"),
        credential_cache_dir=str(tmp_path / "B/cache"),
    )
    report = check_isolation([a, b])
    assert report.ok is False
    assert "live_snapshot" in [issue.resource for issue in report.issues]


def test_single_profile_is_ok(tmp_path: Path):
    report = check_isolation([AccountEnvProfile(label="solo")])
    assert report.ok is True
    assert report.issues == ()


def test_duplicate_labels_flagged(tmp_path: Path):
    a = _profile("dup", tmp_path / "A")
    b = _profile("dup", tmp_path / "B")
    report = check_isolation([a, b])
    assert report.ok is False
    assert any(issue.resource == "label" for issue in report.issues)


def test_scoped_env_restored(tmp_path: Path):
    import os

    sentinel = "PREFLIGHT_TEST_ORIGINAL"
    os.environ["KIS_CREDENTIAL_CACHE_DIR"] = sentinel
    try:
        resolve_resources(_profile("A", tmp_path / "A"))
        assert os.environ["KIS_CREDENTIAL_CACHE_DIR"] == sentinel
    finally:
        os.environ.pop("KIS_CREDENTIAL_CACHE_DIR", None)


def test_load_profiles_from_json(tmp_path: Path):
    path = tmp_path / "profiles.json"
    path.write_text(
        json.dumps(
            [
                {"label": "A", "snapshot_dir": "/data/A", "env": "mock"},
                {"label": "B", "KIS_LIVE_SNAPSHOT_DIR": "/data/B"},
            ]
        ),
        encoding="utf-8",
    )
    profiles = load_profiles(path)
    assert [p.label for p in profiles] == ["A", "B"]
    assert profiles[0].snapshot_dir == "/data/A"
    assert profiles[1].snapshot_dir == "/data/B"


def _run_cli(profiles_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "app.tools.multi_account_preflight",
            "--profiles",
            str(profiles_path),
            *args,
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )


def test_cli_distinct_exit_zero(tmp_path: Path):
    path = tmp_path / "profiles.json"
    path.write_text(
        json.dumps(
            [
                {
                    "label": "A",
                    "snapshot_dir": str(tmp_path / "A/data"),
                    "snapshot_log_dir": str(tmp_path / "A/logs"),
                    "credential_cache_dir": str(tmp_path / "A/cache"),
                },
                {
                    "label": "B",
                    "snapshot_dir": str(tmp_path / "B/data"),
                    "snapshot_log_dir": str(tmp_path / "B/logs"),
                    "credential_cache_dir": str(tmp_path / "B/cache"),
                },
            ]
        ),
        encoding="utf-8",
    )
    result = _run_cli(path)
    assert result.returncode == 0
    assert "OK" in result.stdout


def test_cli_collision_exit_one(tmp_path: Path):
    path = tmp_path / "profiles.json"
    path.write_text(
        json.dumps([{"label": "A"}, {"label": "B"}]),
        encoding="utf-8",
    )
    result = _run_cli(path, "--json")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert len(payload["issues"]) >= 1
