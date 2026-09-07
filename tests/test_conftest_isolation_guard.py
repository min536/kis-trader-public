"""G1/G2 conftest isolation-guard proofs.

Design: docs/test_isolation_leak_design_20260707.md §5 (verification strategy).

Proves the guard is not a silent no-op:
  * the redirect fixture makes account-scope state resolve under a tmp root,
  * the watcher sensor actually detects new/modified real state files,
  * in fail mode a deliberate real-path write fails the offending test
    (subprocess positive detection).
"""

import subprocess
import sys
from pathlib import Path

import pytest

from app.auth import account_scope
from app.auth.settings import PROJECT_ROOT

import conftest as isolation_conftest


# ── redirect is live for a normal (unmarked) test ───────────────────────────

def test_redirect_makes_state_root_tmp(monkeypatch):
    monkeypatch.setattr(account_scope, "get_account_signature", lambda settings=None: "mock_demo")
    root = account_scope.state_root()
    assert root != PROJECT_ROOT, "autouse redirect should move state_root off the repo"
    order_log = account_scope.get_order_log_path()
    assert PROJECT_ROOT not in order_log.parents, "order log must not resolve into the repo"


@pytest.mark.uses_real_state_paths
def test_marker_opts_out_of_redirect():
    # Marked tests keep the real repo root.
    assert account_scope.state_root() == PROJECT_ROOT


# ── watcher sensor detects real writes (pure, no real-dir pollution) ─────────

def test_state_fingerprint_and_diff_detect_new_and_modified(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    globs = ((logs_dir, "*.jsonl"),)

    before = isolation_conftest._state_fingerprint(globs)
    assert before == {}

    probe = logs_dir / "orders_mock_acct_probe.jsonl"
    probe.write_text("a\n", encoding="utf-8")
    after_new = isolation_conftest._state_fingerprint(globs)
    changed_new = isolation_conftest._diff_fingerprints(before, after_new)
    assert any("orders_mock_acct_probe.jsonl" in c for c in changed_new)

    # bump mtime deterministically
    import os

    stamp = after_new[str(probe)] + 10_000_000
    os.utime(probe, ns=(stamp, stamp))
    after_mod = isolation_conftest._state_fingerprint(globs)
    changed_mod = isolation_conftest._diff_fingerprints(after_new, after_mod)
    assert any("orders_mock_acct_probe.jsonl" in c for c in changed_mod)


def test_diff_fingerprints_empty_when_unchanged():
    snap = {"a": 1, "b": 2}
    assert isolation_conftest._diff_fingerprints(snap, dict(snap)) == []


def test_live_snapshot_global_file_excluded_from_leak_detection():
    # 2026-07-17: an intraday live session rewrites data/live_snapshot.json every
    # cycle. It is a *global* (non-signature) path, so the signature-based exclude
    # never catches it — the guard false-flagged it as an isolation leak 3x in one
    # full-suite run. It must live in the global-exclusion set.
    assert (
        "data/live_snapshot.json"
        in isolation_conftest._LIVE_SESSION_GLOBAL_FILES
    )

    # Replicates the fixture's exclusion predicate (_watch_real_state_writes):
    # once a live session is detected the global file is dropped, while an
    # unrelated write is still flagged.
    changed = ["data/live_snapshot.json", "logs/orders_mock_other.jsonl"]
    live_signatures = frozenset({"acct_x"})
    filtered = [
        rel
        for rel in changed
        if rel not in isolation_conftest._LIVE_SESSION_GLOBAL_FILES
        and not any(sig in rel for sig in live_signatures)
    ]
    assert "data/live_snapshot.json" not in filtered
    assert "logs/orders_mock_other.jsonl" in filtered


# ── fail mode catches a real-path escape end-to-end (subprocess) ─────────────

@pytest.mark.uses_real_state_paths
def test_watcher_fails_on_real_state_write():
    import os

    token = f"guard{os.getpid()}"
    target = PROJECT_ROOT / "logs" / f"_leak_probe_{token}.jsonl"
    # A public checkout has no operator-created runtime directories yet.
    target.parent.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "KIS_LEAK_PROBE_TOKEN": token,
        "KIS_TEST_STATE_WATCH": "fail",
    }
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/test_state_leak_probe.py",
                "-p",
                "no:randomly",
                "-q",
            ],
            env=env,
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),
        )
        combined = result.stdout + result.stderr
        assert result.returncode != 0, f"watcher should have failed the leak:\n{combined}"
        assert "isolation leak" in combined, combined
    finally:
        target.unlink(missing_ok=True)
