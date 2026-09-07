"""Global test isolation — real ``logs/``·``data/`` state-file leak guard.

Design: ``docs/test_isolation_leak_design_20260707.md`` (G1/G2).

Two autouse guards run for every test:

1. **State-root redirect (G1)** — sets ``KIS_STATE_ROOT`` to a per-test temp
   dir so every signature state writer (order log, performance, cycle
   snapshots, runtime state) resolves under tmp instead of the repo. Relies on
   the ``state_root()`` seam (G3). Tests that genuinely need the real repo
   state files opt out with ``@pytest.mark.uses_real_state_paths`` (they are
   then not redirected and not failed by the watcher, but still recorded).

2. **Credential env restore (G1)** — snapshots ``KIS_*`` env vars before each
   test and restores them after, neutralising the ~8 tests that mutate
   ``os.environ`` directly (bypassing monkeypatch) and thereby bled a foreign
   account signature into later tests' real-path writers.

3. **Real-path write watcher (G2)** — snapshots the repo's ``logs/``·``data/``
   state files (mtime) before/after each unmarked test. In ``report`` mode it
   collects offenders and prints a session-end summary; in ``fail`` mode any
   new real-path write fails the offending test immediately.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

import pytest

from app.auth.account_scope import STATE_ROOT_ENV
from app.auth.settings import PROJECT_ROOT

# ── G2 watcher mode ─────────────────────────────────────────────────────────
# "report": collect + print offenders at session end (does not fail tests).
# "fail":   any real-path state write fails the offending test immediately.
# Env override (``KIS_TEST_STATE_WATCH``) lets the positive-detection test force
# "fail" regardless of the default. Default flipped to "fail" in G004.
WATCH_MODE = os.environ.get("KIS_TEST_STATE_WATCH", "fail").strip() or "fail"

_REAL_LOGS_DIR = PROJECT_ROOT / "logs"
_REAL_DATA_DIR = PROJECT_ROOT / "data"

# Glob patterns for the account-scope state-file surface tests can leak into.
_STATE_GLOBS: tuple[tuple[Path, str], ...] = (
    (_REAL_LOGS_DIR, "*.jsonl"),
    (_REAL_DATA_DIR, "*.jsonl"),
    (_REAL_DATA_DIR, "*.json"),
)

# Populated by the watcher; drained by the session-finish reporter.
_LEAK_REPORT: list[tuple[str, list[str]]] = []

# Global (non-signature) state files a concurrent live session also writes.
# live_snapshot.json is rewritten every cycle by an intraday live session and is
# a global path (not a per-account signature), so it must be excluded here
# (2026-07-17: the guard false-flagged it as an isolation leak 3x in one run).
_LIVE_SESSION_GLOBAL_FILES = frozenset(
    {
        "data/account_scope_meta.json",
        "data/account_scope_history.jsonl",
        "data/live_snapshot.json",
    }
)

# Signatures owned by a concurrent live `app.main` session — computed once at
# session start (the operator's live session is stable across a suite run).
_LIVE_OWNED_SIGNATURES: frozenset[str] = frozenset()


def _live_owned_signatures() -> frozenset[str]:
    """Signatures whose per-account ``app_main`` lock is held by a live session.

    A concurrent operator-run ``app.main`` (e.g. during market hours) writes its
    active account's real state files every cycle — those are NOT test leaks, and
    the watcher must not misattribute them to whatever test is running when a
    cycle lands. Detect them by probing the fcntl lock non-blocking: a held lock
    raises immediately (we never acquire or remove the live lock); a stale lock
    is acquired and released at once. Never raises.
    """
    live: set[str] = set()
    try:
        lock_files = list(_REAL_LOGS_DIR.glob("app_main_*.lock"))
    except OSError:
        return frozenset()
    prefix, suffix = "app_main_", ".lock"
    for lock_path in lock_files:
        name = lock_path.name
        signature = name[len(prefix) : -len(suffix)]
        if not signature:
            continue
        try:
            with open(lock_path, "a", encoding="utf-8") as handle:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # stale — not live
                except OSError:
                    live.add(signature)  # held — a live session owns this account
        except OSError:
            continue
    return frozenset(live)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "uses_real_state_paths: opt out of the tmp state-root redirect; the "
        "test intentionally reads the repo's real logs/·data/ state files.",
    )
    global _LIVE_OWNED_SIGNATURES
    _LIVE_OWNED_SIGNATURES = _live_owned_signatures()
    if _LIVE_OWNED_SIGNATURES:
        reporter = config.pluginmanager.get_plugin("terminalreporter")
        msg = (
            "[state-watch] concurrent live session detected for signature(s): "
            + ", ".join(sorted(_LIVE_OWNED_SIGNATURES))
            + " — their real state-file writes are excluded from the leak watcher."
        )
        if reporter is not None:
            reporter.write_line(msg)


def _state_fingerprint(
    globs: tuple[tuple[Path, str], ...] = _STATE_GLOBS,
) -> dict[str, int]:
    """Map real state-file path → mtime_ns. Stat-only; never opens files."""
    fingerprint: dict[str, int] = {}
    for directory, pattern in globs:
        try:
            entries = directory.glob(pattern)
        except OSError:
            continue
        for path in entries:
            try:
                fingerprint[str(path)] = path.stat().st_mtime_ns
            except OSError:
                continue
    return fingerprint


def _diff_fingerprints(before: dict[str, int], after: dict[str, int]) -> list[str]:
    changed: list[str] = []
    for path, mtime in after.items():
        if before.get(path) != mtime:
            changed.append(os.path.relpath(path, PROJECT_ROOT))
    return sorted(changed)


@pytest.fixture(autouse=True)
def _isolate_account_state(request: pytest.FixtureRequest, monkeypatch, tmp_path):
    """Redirect signature state writes to tmp + restore KIS_* credential env."""
    marked = request.node.get_closest_marker("uses_real_state_paths") is not None

    # Snapshot raw KIS_* env (guards tests that bypass monkeypatch).
    cred_snapshot = {k: v for k, v in os.environ.items() if k.startswith("KIS_")}

    if not marked:
        monkeypatch.setenv(STATE_ROOT_ENV, str(tmp_path))

    try:
        yield
    finally:
        # Restore raw credential env — neutralise direct os.environ mutations.
        # Leave KIS_STATE_ROOT to monkeypatch's own teardown.
        live_keys = {k for k in os.environ if k.startswith("KIS_")}
        for key in live_keys - set(cred_snapshot):
            if key == STATE_ROOT_ENV:
                continue
            os.environ.pop(key, None)
        for key, value in cred_snapshot.items():
            if key == STATE_ROOT_ENV:
                continue
            os.environ[key] = value


@pytest.fixture(autouse=True)
def _watch_real_state_writes(request: pytest.FixtureRequest, _isolate_account_state):
    """Detect writes that escaped the redirect and hit the repo's real state files."""
    if request.node.get_closest_marker("uses_real_state_paths") is not None:
        # Marked tests intentionally touch real paths — do not watch/fail them.
        yield
        return

    before = _state_fingerprint()
    yield
    after = _state_fingerprint()
    changed = _diff_fingerprints(before, after)
    # Exclude files a concurrent live session owns — its per-cycle writes to the
    # active account's real files are not test leaks (misattribution guard).
    if _LIVE_OWNED_SIGNATURES:
        changed = [
            rel
            for rel in changed
            if rel not in _LIVE_SESSION_GLOBAL_FILES
            and not any(sig in rel for sig in _LIVE_OWNED_SIGNATURES)
        ]
    if not changed:
        return
    nodeid = request.node.nodeid
    if WATCH_MODE == "fail":
        pytest.fail(
            "test wrote to real repo state files (isolation leak): "
            + ", ".join(changed),
            pytrace=False,
        )
    _LEAK_REPORT.append((nodeid, changed))


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if not _LEAK_REPORT:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    lines = [
        "",
        "=" * 72,
        f"REAL-STATE-WRITE LEAK REPORT ({WATCH_MODE} mode) — "
        f"{len(_LEAK_REPORT)} test(s) wrote to repo logs/·data/:",
    ]
    for nodeid, changed in _LEAK_REPORT:
        lines.append(f"  {nodeid}")
        for rel in changed:
            lines.append(f"      → {rel}")
    lines.append("=" * 72)
    text = "\n".join(lines)
    if reporter is not None:
        reporter.write_line(text)
    else:  # pragma: no cover - fallback when terminal plugin is absent
        print(text)
