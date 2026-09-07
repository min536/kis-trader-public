from __future__ import annotations

from datetime import date, datetime

import gzip
import io
import json
import os
from contextlib import redirect_stdout

from app.tools.retention_gzip import (
    Candidate,
    FileEntry,
    LockRecord,
    RetentionPolicy,
    RotationPlan,
    apply_candidates,
    apply_guard_decision,
    classify_retention_candidates,
    main,
    plan_active_rotations,
    rotate_active_files,
    scan_file_entries,
)


_TODAY = date(2026, 7, 3)
_POLICY = RetentionPolicy(gzip_after_days=14)


def _entry(path: str, *, days_old: int, size: int = 1024) -> FileEntry:
    mtime = datetime(2026, 7, 3, 12, 0, 0).timestamp() - days_old * 86400
    return FileEntry(path=path, size=size, mtime=mtime)


def test_boundary_13_14_15_days():
    entries = [
        _entry("logs/candidate_outcomes_x_20260601.jsonl", days_old=13),
        _entry("logs/candidate_outcomes_x_20260602.jsonl", days_old=14),
        _entry("logs/candidate_outcomes_x_20260603.jsonl", days_old=15),
    ]
    result = classify_retention_candidates(entries, today=_TODAY, policy=_POLICY)
    kept = {c.path for c in result.keep}
    candidates = {c.path for c in result.candidates}

    assert "logs/candidate_outcomes_x_20260601.jsonl" in kept
    assert "logs/candidate_outcomes_x_20260602.jsonl" in kept
    assert "logs/candidate_outcomes_x_20260603.jsonl" in candidates


def test_active_patterns_excluded_even_when_old():
    # §4 active files: must never become candidates regardless of age.
    entries = [
        _entry("data/runtime_state.json", days_old=90),
        _entry("data/runtime_state_mock_12345678_01.json", days_old=90),
        _entry("logs/orders_mock_12345678_01.jsonl", days_old=90),
        _entry("data/cycle_snapshots_mock_12345678_01.jsonl", days_old=90),
        _entry("logs/performance_summary_mock_12345678_01.jsonl", days_old=90),
        _entry("data/universe_batches/batch_001.json", days_old=90),
    ]
    result = classify_retention_candidates(entries, today=_TODAY, policy=_POLICY)
    candidate_paths = {c.path for c in result.candidates}
    kept_paths = {c.path for c in result.keep}

    for entry in entries:
        assert entry.path not in candidate_paths, entry.path
        assert entry.path in kept_paths, entry.path


def test_session_lock_files_are_never_candidates():
    # app.main session locks + their metadata must never be gzip'd/moved.
    entries = [
        _entry("logs/app_main_mock_12345678_01.lock", days_old=90),
        _entry("logs/app_main_mock_12345678_01.lock.json", days_old=90),
    ]
    result = classify_retention_candidates(entries, today=_TODAY, policy=_POLICY)
    candidate_paths = {c.path for c in result.candidates}
    kept_paths = {c.path for c in result.keep}
    for entry in entries:
        assert entry.path not in candidate_paths, entry.path
        assert entry.path in kept_paths, entry.path


def test_dated_orders_and_snapshots_are_candidates_when_old():
    # Dated (rotated) variants are NOT the current active file → eligible.
    entries = [
        _entry("logs/orders_mock_12345678_01_20260601.jsonl", days_old=90),
        _entry("data/cycle_snapshots_mock_12345678_01_20260601.jsonl", days_old=90),
        _entry("logs/performance_summary_mock_12345678_01_20260601.jsonl", days_old=90),
    ]
    result = classify_retention_candidates(entries, today=_TODAY, policy=_POLICY)
    candidate_paths = {c.path for c in result.candidates}
    for entry in entries:
        assert entry.path in candidate_paths, entry.path


def test_non_logs_data_subtree_is_skipped_with_reason():
    # Only logs/ and data/ are in scope for gzip+move. Others → skip(reason).
    entries = [
        _entry("results/full154_research_round2/x_backtest.json", days_old=90),
        _entry("archive/logs/app_stdout_20260415.log", days_old=90),
    ]
    result = classify_retention_candidates(entries, today=_TODAY, policy=_POLICY)
    candidate_paths = {c.path for c in result.candidates}
    skips = {s.path: s.reason for s in result.skips}

    for entry in entries:
        assert entry.path not in candidate_paths, entry.path
        assert entry.path in skips, entry.path
        assert skips[entry.path]  # non-empty reason


def test_missing_mtime_is_skipped_with_reason():
    entries = [FileEntry(path="logs/mystery.jsonl", size=10, mtime=None)]
    result = classify_retention_candidates(entries, today=_TODAY, policy=_POLICY)
    assert not result.candidates
    assert result.skips[0].path == "logs/mystery.jsonl"
    assert result.skips[0].reason


def test_apply_guard_allows_when_no_locks():
    decision = apply_guard_decision([])
    assert decision.allowed is True
    assert decision.reason


def test_apply_guard_blocks_on_live_app_main():
    records = [
        LockRecord(
            lock_path="logs/app_main_mock.lock",
            metadata={"pid": 4242, "command": "python -m app.main"},
            metadata_present=True,
            owner_alive=True,
            same_program=True,
        )
    ]
    decision = apply_guard_decision(records)
    assert decision.allowed is False
    assert "app.main" in decision.reason or "live" in decision.reason.lower()


def test_apply_guard_fail_closed_on_unparseable_metadata():
    # Lock file present but metadata unparseable/absent → unknown → refuse.
    records = [
        LockRecord(
            lock_path="logs/app_main_mock.lock",
            metadata=None,
            metadata_present=True,
            owner_alive=False,
            same_program=False,
        )
    ]
    decision = apply_guard_decision(records)
    assert decision.allowed is False
    assert "unknown" in decision.reason.lower() or "unparse" in decision.reason.lower()


def test_apply_guard_fail_closed_on_lock_without_metadata():
    records = [
        LockRecord(
            lock_path="logs/app_main_mock.lock",
            metadata=None,
            metadata_present=False,
            owner_alive=False,
            same_program=False,
        )
    ]
    decision = apply_guard_decision(records)
    assert decision.allowed is False
    assert "metadata" in decision.reason.lower()


def test_apply_guard_allows_when_lock_stale_dead_owner():
    # Metadata parsed, owner not alive → not a live session → allowed.
    records = [
        LockRecord(
            lock_path="logs/app_main_mock.lock",
            metadata={"pid": 999999, "command": "python -m app.main"},
            metadata_present=True,
            owner_alive=False,
            same_program=False,
        )
    ]
    decision = apply_guard_decision(records)
    assert decision.allowed is True


def test_apply_candidates_partial_failure_isolation(tmp_path):
    # tmp_path ONLY — never touches real data/ or logs/.
    (tmp_path / "logs").mkdir()
    good_src = tmp_path / "logs" / "old_good.jsonl"
    good_src.write_text("line-a\nline-b\n", encoding="utf-8")
    good = Candidate(
        path="logs/old_good.jsonl",
        size=good_src.stat().st_size,
        age_days=90,
        dest="archive/logs/old_good.jsonl.gz",
    )
    # Missing source → this candidate must fail in isolation.
    bad = Candidate(
        path="logs/does_not_exist.jsonl",
        size=10,
        age_days=90,
        dest="archive/logs/does_not_exist.jsonl.gz",
    )

    outcome = apply_candidates([good, bad], project_root=tmp_path)

    # Good candidate compressed + moved.
    dest_path = tmp_path / "archive" / "logs" / "old_good.jsonl.gz"
    assert dest_path.exists()
    with gzip.open(dest_path, "rt", encoding="utf-8") as fh:
        assert fh.read() == "line-a\nline-b\n"
    assert not good_src.exists()  # original removed after integrity check
    assert "logs/old_good.jsonl" in {c.path for c in outcome.compressed}

    # Bad candidate failed but did NOT abort the run.
    failed = {f.path: f.reason for f in outcome.failed}
    assert "logs/does_not_exist.jsonl" in failed
    assert failed["logs/does_not_exist.jsonl"]


def test_apply_candidates_keeps_original_when_gzip_integrity_fails(tmp_path, monkeypatch):
    (tmp_path / "logs").mkdir()
    src = tmp_path / "logs" / "old.jsonl"
    src.write_text("payload\n", encoding="utf-8")
    cand = Candidate(
        path="logs/old.jsonl",
        size=src.stat().st_size,
        age_days=90,
        dest="archive/logs/old.jsonl.gz",
    )

    import app.tools.retention_gzip as mod

    # Force the integrity re-open to report failure.
    monkeypatch.setattr(mod, "_verify_gzip", lambda _p: False)
    outcome = apply_candidates([cand], project_root=tmp_path)

    assert src.exists()  # original preserved when integrity check fails
    assert "logs/old.jsonl" in {f.path for f in outcome.failed}


def _make_tree(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "data").mkdir()
    old = tmp_path / "logs" / "candidate_outcomes_x_20260601.jsonl"
    old.write_text("x\n", encoding="utf-8")
    os.utime(old, (old.stat().st_atime, old.stat().st_mtime - 90 * 86400))
    active = tmp_path / "logs" / "orders_mock_12345678_01.jsonl"
    active.write_text("y\n", encoding="utf-8")
    os.utime(active, (active.stat().st_atime, active.stat().st_mtime - 90 * 86400))
    return old, active


def test_scan_file_entries_reads_only_metadata(tmp_path):
    old, _active = _make_tree(tmp_path)
    entries = scan_file_entries(tmp_path, subdirs=("logs", "data"))
    by_path = {e.path: e for e in entries}
    assert "logs/candidate_outcomes_x_20260601.jsonl" in by_path
    assert by_path["logs/candidate_outcomes_x_20260601.jsonl"].size == old.stat().st_size
    assert by_path["logs/candidate_outcomes_x_20260601.jsonl"].mtime is not None


def test_cli_dry_run_is_default_and_touches_nothing(tmp_path):
    old, active = _make_tree(tmp_path)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["--root", str(tmp_path)])
    out = buf.getvalue()
    assert rc == 0
    assert "dry-run" in out.lower()
    assert "candidate_outcomes_x_20260601.jsonl" in out
    # Nothing moved/compressed in dry-run.
    assert old.exists()
    assert active.exists()
    assert not (tmp_path / "archive").exists()


_BIG = 100 * 1024 * 1024 + 1  # just over the 100MB oversized-active threshold


def test_plan_active_rotations_lists_oversized_active_no_date_suffix():
    entries = [
        # Oversized active (no date suffix) → rotation target.
        FileEntry(path="data/cycle_snapshots_mock_acct.jsonl", size=_BIG, mtime=1.0),
        # Oversized active in logs → also a target (D3 oversized_active set).
        FileEntry(path="logs/orders_mock_acct.jsonl", size=_BIG, mtime=1.0),
        # Dated (rotated) variant → NOT active → excluded even if huge.
        FileEntry(path="data/cycle_snapshots_mock_acct_20260601.jsonl", size=_BIG, mtime=1.0),
        # Active but under threshold → excluded.
        FileEntry(path="data/cycle_snapshots_small.jsonl", size=1024, mtime=1.0),
    ]
    plans = plan_active_rotations(entries, now=datetime(2026, 7, 4, 13, 5, 9))
    planned = {p.path for p in plans}
    assert "data/cycle_snapshots_mock_acct.jsonl" in planned
    assert "logs/orders_mock_acct.jsonl" in planned
    assert "data/cycle_snapshots_mock_acct_20260601.jsonl" not in planned
    assert "data/cycle_snapshots_small.jsonl" not in planned
    # Planned new name = <stem>_rotated_<YYYYMMDD>_<HHMMSS><ext> in same dir.
    snap = next(p for p in plans if p.path == "data/cycle_snapshots_mock_acct.jsonl")
    assert snap.rotated_path == "data/cycle_snapshots_mock_acct_rotated_20260704_130509.jsonl"


def test_rotate_apply_renames_and_recreates_empty_active(tmp_path):
    (tmp_path / "data").mkdir()
    src = tmp_path / "data" / "cycle_snapshots_mock_acct.jsonl"
    src.write_text("row-1\nrow-2\n", encoding="utf-8")
    os.chmod(src, 0o640)
    original_mode = src.stat().st_mode & 0o777

    plan = RotationPlan(
        path="data/cycle_snapshots_mock_acct.jsonl",
        size=src.stat().st_size,
        rotated_path="data/cycle_snapshots_mock_acct_rotated_20260704_130509.jsonl",
    )
    outcome = rotate_active_files([plan], project_root=tmp_path)

    rotated = tmp_path / "data" / "cycle_snapshots_mock_acct_rotated_20260704_130509.jsonl"
    # Original content moved to the dated file.
    assert rotated.exists()
    assert rotated.read_text(encoding="utf-8") == "row-1\nrow-2\n"
    # Active file recreated empty with the original name + permissions.
    assert src.exists()
    assert src.read_text(encoding="utf-8") == ""
    assert (src.stat().st_mode & 0o777) == original_mode
    assert "data/cycle_snapshots_mock_acct.jsonl" in {r.path for r in outcome.rotated}


def test_cli_rotate_active_dry_run_lists_candidate_and_touches_nothing(tmp_path):
    (tmp_path / "data").mkdir()
    big = tmp_path / "data" / "cycle_snapshots_mock_acct.jsonl"
    big.write_text("x", encoding="utf-8")
    # Fake an oversized size via truncate so we don't write 100MB.
    with big.open("r+b") as fh:
        fh.truncate(_BIG)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["--root", str(tmp_path), "--rotate-active"])
    out = buf.getvalue()
    assert rc == 0
    assert "dry-run" in out.lower()
    assert "cycle_snapshots_mock_acct.jsonl" in out
    assert "_rotated_" in out  # planned new name shown
    # Operator caution present.
    assert "warmup" in out.lower()
    assert "weekend" in out.lower() or "holiday" in out.lower()
    # Nothing rotated in dry-run.
    assert big.stat().st_size == _BIG
    assert not list((tmp_path / "data").glob("*_rotated_*"))


def test_cli_rotate_active_apply_refused_when_session_lock_held(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "logs").mkdir()
    big = tmp_path / "data" / "cycle_snapshots_mock_acct.jsonl"
    big.write_text("x", encoding="utf-8")
    with big.open("r+b") as fh:
        fh.truncate(_BIG)
    # A live-looking lock: valid metadata for THIS running process (alive +
    # same program). Use the real running command so same_program_running matches.
    import subprocess as _sp

    real_command = _sp.run(
        ["ps", "-p", str(os.getpid()), "-o", "command="],
        capture_output=True, text=True, check=False,
    ).stdout.strip() or "python"
    lock = tmp_path / "logs" / "app_main_mock.lock"
    lock.write_text("", encoding="utf-8")
    (tmp_path / "logs" / "app_main_mock.lock.json").write_text(
        json.dumps({"pid": os.getpid(), "command": real_command}),
        encoding="utf-8",
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["--root", str(tmp_path), "--rotate-active", "--apply"])
    out = buf.getvalue().lower()
    assert rc != 0
    assert "refus" in out
    # Fail-closed: nothing rotated.
    assert big.stat().st_size == _BIG
    assert not list((tmp_path / "data").glob("*_rotated_*"))


def test_cli_apply_refused_when_lock_metadata_unparseable(tmp_path):
    _make_tree(tmp_path)
    # A lock file with unparseable sibling metadata → fail-closed refuse.
    lock = tmp_path / "logs" / "app_main_mock.lock"
    lock.write_text("", encoding="utf-8")
    (tmp_path / "logs" / "app_main_mock.lock.json").write_text(
        "{ not json", encoding="utf-8"
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["--root", str(tmp_path), "--apply"])
    out = buf.getvalue().lower()
    assert rc != 0
    assert "refus" in out or "unknown" in out
    # Nothing compressed.
    assert not (tmp_path / "archive").exists()

