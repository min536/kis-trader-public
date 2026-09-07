"""Tests for the per-account env generator (C-4 Phase 1 ergonomics).

Verifies generated env values are distinct per label and pass the isolation
preflight by construction.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from app.tools.multi_account_env import (
    detect_slug_collisions,
    recommend_account_env,
    render_export_block,
    slugify_label,
)
from app.tools.multi_account_preflight import AccountEnvProfile, check_isolation


def test_slugify_sanitizes():
    assert slugify_label("Account A!") == "account_a"
    assert slugify_label("  ") == "account"
    assert slugify_label("core-01") == "core_01"


def test_recommend_account_env_keys_and_root():
    env = recommend_account_env("core", base_dir="data/accounts")
    assert env == {
        "KIS_LIVE_SNAPSHOT_DIR": "data/accounts/core/data",
        "KIS_LIVE_SNAPSHOT_LOG_DIR": "data/accounts/core/logs",
        "KIS_CREDENTIAL_CACHE_DIR": "data/accounts/core/cache",
    }


def test_distinct_labels_yield_distinct_dirs():
    a = recommend_account_env("A")
    b = recommend_account_env("B")
    assert set(a.values()).isdisjoint(b.values())


def test_recommendations_pass_preflight():
    profiles = []
    for label in ("core", "extended"):
        env = recommend_account_env(label)
        profiles.append(
            AccountEnvProfile(
                label=label,
                snapshot_dir=env["KIS_LIVE_SNAPSHOT_DIR"],
                snapshot_log_dir=env["KIS_LIVE_SNAPSHOT_LOG_DIR"],
                credential_cache_dir=env["KIS_CREDENTIAL_CACHE_DIR"],
            )
        )
    report = check_isolation(profiles)
    assert report.ok is True


def test_render_export_block_format():
    block = render_export_block("core")
    assert block.startswith("# account: core")
    # shlex.quote leaves shell-safe values unquoted
    assert "export KIS_LIVE_SNAPSHOT_DIR=data/accounts/core/data" in block


def test_empty_base_dir_raises():
    with pytest.raises(ValueError):
        recommend_account_env("core", base_dir="")
    with pytest.raises(ValueError):
        recommend_account_env("core", base_dir="/")


def test_detect_slug_collisions():
    assert detect_slug_collisions(["A!", "A?", "B"]) == {"a": ["A!", "A?"]}
    assert detect_slug_collisions(["core", "extended"]) == {}


def test_render_export_block_base_dir_injection_is_shell_safe():
    malicious = 'data/x" && echo HACKED #'
    block = render_export_block("acct", base_dir=malicious)
    for line in block.splitlines():
        if not line.startswith("export "):
            continue
        # a properly quoted export tokenizes to exactly ["export", "KEY=value"]
        tokens = shlex.split(line)
        assert len(tokens) == 2 and tokens[0] == "export"


def test_render_export_block_label_newline_is_flattened():
    block = render_export_block("acct\nrm -rf /tmp/x", base_dir="data/accounts")
    lines = block.splitlines()
    assert lines[0].startswith("# account:")
    assert "rm -rf" in lines[0]  # folded INTO the comment, not a new command line
    assert lines[1].startswith("export ")


def test_cli_slug_collision_exits_one():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.tools.multi_account_env",
            "--label",
            "Core-A",
            "--label",
            "core_a",
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert result.returncode == 1
    assert "slug" in result.stderr.lower()


def test_cli_json_output():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app.tools.multi_account_env",
            "--label",
            "A",
            "--label",
            "B",
            "--json",
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert set(payload) == {"A", "B"}
    assert payload["A"]["KIS_LIVE_SNAPSHOT_DIR"] == "data/accounts/a/data"
