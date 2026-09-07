from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stdout
from datetime import date, datetime

from app.tools.retention_gzip import FileEntry, RetentionPolicy
from app.tools.retention_check import (
    build_violations_report,
    main,
)


_TODAY = date(2026, 7, 3)
_POLICY = RetentionPolicy(gzip_after_days=14)


def _entry(path: str, *, days_old: int, size: int = 1024) -> FileEntry:
    mtime = datetime(2026, 7, 3, 12, 0, 0).timestamp() - days_old * 86400
    return FileEntry(path=path, size=size, mtime=mtime)


def test_report_flags_old_plain_files_as_violations():
    entries = [
        _entry("logs/candidate_outcomes_x_20260601.jsonl", days_old=90),
        _entry("logs/recent_x_20260702.jsonl", days_old=1),
    ]
    report = build_violations_report(entries, today=_TODAY, policy=_POLICY)
    paths = {v.path for v in report.stale_plain_files}
    assert "logs/candidate_outcomes_x_20260601.jsonl" in paths
    assert "logs/recent_x_20260702.jsonl" not in paths


def test_report_warns_on_oversized_active_files():
    big = 150 * 1024 * 1024
    entries = [
        # Active file over 100MB → warning (active never gets rotated, but flag size).
        _entry("logs/orders_mock_12345678_01.jsonl", days_old=90, size=big),
        # Active but under 100MB → no warning.
        _entry("data/runtime_state_mock_12345678_01.json", days_old=90, size=1024),
    ]
    report = build_violations_report(entries, today=_TODAY, policy=_POLICY)
    warned = {w.path for w in report.oversized_active_files}
    assert "logs/orders_mock_12345678_01.jsonl" in warned
    assert "data/runtime_state_mock_12345678_01.json" not in warned
    # These are active files, so they are NOT stale-plain violations.
    assert not report.stale_plain_files


def _make_tree(tmp_path):
    (tmp_path / "logs").mkdir()
    old = tmp_path / "logs" / "candidate_outcomes_x_20260601.jsonl"
    old.write_text("x\n", encoding="utf-8")
    os.utime(old, (old.stat().st_atime, old.stat().st_mtime - 90 * 86400))
    return old


def test_cli_prints_report_and_is_read_only(tmp_path):
    old = _make_tree(tmp_path)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["--root", str(tmp_path)])
    out = buf.getvalue()
    assert rc == 0
    assert "candidate_outcomes_x_20260601.jsonl" in out
    # Read-only: the source file is untouched and no archive created.
    assert old.exists()
    assert not (tmp_path / "archive").exists()


def test_cli_writes_json_report_only(tmp_path):
    _make_tree(tmp_path)
    json_path = tmp_path / "out" / "report.json"
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(["--root", str(tmp_path), "--json", str(json_path)])
    assert rc == 0
    assert json_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    stale = {v["path"] for v in payload["stale_plain_files"]}
    assert "logs/candidate_outcomes_x_20260601.jsonl" in stale
