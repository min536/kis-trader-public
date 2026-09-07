"""Tests for the log/data retention tool (C-1).

The tool is metadata-only (it stats files, never reads their contents) and
fail-safe: dry-run by default, refuses to rotate while ``app.main`` holds a
lock, and never touches active (rolling, undated, non-archive) files.
"""
from __future__ import annotations

import gzip
import os

from app.maintenance import retention


def test_account_number_is_not_a_date_stamp():
    assert retention.has_valid_date_stamp("orders_mock_12345678_01.jsonl") is False


def test_real_date_stamp_is_detected_even_next_to_account_number():
    name = "candidate_outcomes_mock_12345678_01_20260420.jsonl"
    assert retention.has_valid_date_stamp(name) is True


def test_date_stamp_rejects_out_of_range_year():
    assert retention.has_valid_date_stamp("dump_19990101.log") is False
    assert retention.has_valid_date_stamp("dump_20260101.log") is True


def test_long_digit_run_is_not_a_date_stamp():
    assert retention.has_valid_date_stamp("orders_1234567801.jsonl") is False


_DAY = 86_400.0
NOW = 1_760_000_000.0  # fixed reference epoch for deterministic age math


def _age(days: float) -> float:
    return NOW - days * _DAY


def _classify(name, *, under_archive=False, under_results=False, age_days=1.0):
    return retention.classify(
        name=name,
        under_archive=under_archive,
        under_results=under_results,
        size_bytes=100,
        mtime_epoch=_age(age_days),
        now_epoch=NOW,
    )


def test_compressed_file_is_kept():
    c = _classify("archive_app_stdout_20260415.log.gz", under_archive=True)
    assert c.category == retention.COMPRESSED
    assert c.action == retention.KEEP


def test_archive_plain_file_is_gzip_candidate():
    c = _classify("orders_mock_12345678_01.jsonl", under_archive=True, age_days=2.0)
    assert c.category == retention.GZIP_CANDIDATE
    assert c.action == retention.GZIP


def test_active_undated_nonarchive_file_is_kept_even_when_old():
    c = _classify("orders_mock_12345678_01.jsonl", age_days=90.0)
    assert c.category == retention.ACTIVE
    assert c.action == retention.KEEP


def test_recent_dated_log_is_kept():
    c = _classify("cycle_stats_mock_12345678_01_20260601.jsonl", age_days=3.0)
    assert c.category == retention.RECENT
    assert c.action == retention.KEEP


def test_old_dated_log_is_gzip_candidate():
    c = _classify("cycle_stats_mock_12345678_01_20260419.jsonl", age_days=40.0)
    assert c.category == retention.GZIP_CANDIDATE
    assert c.action == retention.GZIP


def test_results_file_is_research_bundle_not_gzip():
    c = _classify("summary.json", under_results=True, age_days=120.0)
    assert c.category == retention.RESEARCH_RESULT
    assert c.action != retention.GZIP


def _touch(path, *, size=10, age_days=1.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    mtime = _age(age_days)
    os.utime(path, (mtime, mtime))


def test_scan_classifies_a_tree(tmp_path):
    _touch(tmp_path / "logs" / "orders_mock_12345678_01.jsonl", age_days=200.0)
    _touch(tmp_path / "logs" / "cycle_stats_mock_12345678_01_20260419.jsonl", age_days=40.0)
    _touch(tmp_path / "logs" / "app_stdout_20260601.log", age_days=2.0)
    _touch(tmp_path / "archive" / "logs" / "old.jsonl", age_days=120.0)
    _touch(tmp_path / "archive" / "logs" / "old.jsonl.gz", age_days=120.0)
    _touch(tmp_path / "results" / "exp" / "r.json", age_days=120.0)

    scanned = retention.scan_retention(tmp_path, now_epoch=NOW)
    by_rel = {sf.relpath: sf.classification.category for sf in scanned}

    assert by_rel["logs/orders_mock_12345678_01.jsonl"] == retention.ACTIVE
    assert by_rel["logs/cycle_stats_mock_12345678_01_20260419.jsonl"] == retention.GZIP_CANDIDATE
    assert by_rel["logs/app_stdout_20260601.log"] == retention.RECENT
    assert by_rel["archive/logs/old.jsonl"] == retention.GZIP_CANDIDATE
    assert by_rel["archive/logs/old.jsonl.gz"] == retention.COMPRESSED
    assert by_rel["results/exp/r.json"] == retention.RESEARCH_RESULT


def test_scan_skips_missing_dirs(tmp_path):
    _touch(tmp_path / "logs" / "x_20260419.log", age_days=40.0)
    scanned = retention.scan_retention(tmp_path, now_epoch=NOW)
    assert len(scanned) == 1


def test_build_report_counts_and_candidates(tmp_path):
    _touch(tmp_path / "logs" / "a_20260419.jsonl", size=1000, age_days=40.0)
    _touch(tmp_path / "logs" / "b_20260418.jsonl", size=2000, age_days=41.0)
    _touch(tmp_path / "logs" / "orders_mock_12345678_01.jsonl", size=500, age_days=300.0)

    scanned = retention.scan_retention(tmp_path, now_epoch=NOW)
    report = retention.build_retention_report(scanned)

    assert report.total_files == 3
    assert report.gzip_candidate_count == 2
    assert report.gzip_candidate_bytes == 3000
    assert report.gzip_candidates[0][0] == "logs/b_20260418.jsonl"
    payload = report.to_dict()
    assert payload["gzip_candidate_count"] == 2
    assert payload["by_category"][retention.ACTIVE]["count"] == 1


def test_gzip_dry_run_makes_no_change(tmp_path):
    f = tmp_path / "a_20260419.log"
    _touch(f, size=50, age_days=40.0)
    result = retention.gzip_file(f, dry_run=True)
    assert result.status == "planned"
    assert f.exists()
    assert not (tmp_path / "a_20260419.log.gz").exists()


def test_gzip_real_compresses_and_removes_original(tmp_path):
    f = tmp_path / "a_20260419.log"
    f.write_bytes(b"hello world\n" * 100)
    gz = tmp_path / "a_20260419.log.gz"

    result = retention.gzip_file(f, dry_run=False)
    assert result.status == "compressed"
    assert not f.exists()
    assert gz.exists()
    with gzip.open(gz, "rb") as handle:
        assert handle.read() == b"hello world\n" * 100


def test_gzip_skips_when_gz_already_exists(tmp_path):
    f = tmp_path / "a_20260419.log"
    f.write_bytes(b"data")
    (tmp_path / "a_20260419.log.gz").write_bytes(b"already")
    result = retention.gzip_file(f, dry_run=False)
    assert result.status == "skipped_exists"
    assert f.exists()


def test_app_main_running_detects_held_lock(tmp_path):
    from app.core.session_lock import acquire_app_main_lock

    logs = tmp_path / "logs"
    logs.mkdir()
    lock_path = logs / "app_main_test.lock"
    handle = acquire_app_main_lock(lock_path, account_signature="test")
    try:
        assert retention.app_main_running(logs) is True
    finally:
        handle.close()
    assert retention.app_main_running(logs) is False


def test_apply_retention_refuses_when_app_main_running(tmp_path):
    _touch(tmp_path / "logs" / "a_20260419.jsonl", age_days=40.0)
    scanned = retention.scan_retention(tmp_path, now_epoch=NOW)
    results = retention.apply_retention(
        scanned, root=tmp_path, dry_run=False, app_main_is_running=True
    )
    assert len(results) == 1
    assert results[0].status == "refused"
    assert (tmp_path / "logs" / "a_20260419.jsonl").exists()


def test_apply_retention_only_acts_on_gzip_candidates(tmp_path):
    _touch(tmp_path / "logs" / "a_20260419.jsonl", age_days=40.0)
    _touch(tmp_path / "logs" / "orders_mock_12345678_01.jsonl", age_days=300.0)
    scanned = retention.scan_retention(tmp_path, now_epoch=NOW)
    results = retention.apply_retention(
        scanned, root=tmp_path, dry_run=True, app_main_is_running=False
    )
    planned = [r for r in results if r.status == "planned"]
    assert len(planned) == 1
    assert planned[0].relpath == "logs/a_20260419.jsonl"
