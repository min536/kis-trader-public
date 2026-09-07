from __future__ import annotations

import io
from contextlib import redirect_stderr

import pytest

from app.tools import eod_health_check


_ACCOUNT = "mock_acct_0123456789abcdef"


def _mk_logs(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    monkeypatch.setattr(eod_health_check, "_PROJECT_ROOT", tmp_path)
    return logs


def test_outcomes_only_returns_max_outcomes_date(tmp_path, monkeypatch):
    logs = _mk_logs(tmp_path, monkeypatch)
    (logs / f"candidate_outcomes_{_ACCOUNT}_20260628.jsonl").write_text("x\n")
    (logs / f"candidate_outcomes_{_ACCOUNT}_20260630.jsonl").write_text("x\n")
    assert eod_health_check.detect_latest_market_date(_ACCOUNT) == "20260630"


def test_stats_only_returns_max_stats_date(tmp_path, monkeypatch):
    logs = _mk_logs(tmp_path, monkeypatch)
    (logs / f"cycle_stats_{_ACCOUNT}_20260702.jsonl").write_text("x\n")
    (logs / f"cycle_stats_{_ACCOUNT}_20260703.jsonl").write_text("x\n")
    buf = io.StringIO()
    with redirect_stderr(buf):
        result = eod_health_check.detect_latest_market_date(_ACCOUNT)
    assert result == "20260703"
    # outcomes missing for the winning date → WARN on stderr
    assert "candidate_outcomes missing" in buf.getvalue()
    assert "20260703" in buf.getvalue()


def test_both_returns_max_across_both_sources(tmp_path, monkeypatch):
    logs = _mk_logs(tmp_path, monkeypatch)
    (logs / f"candidate_outcomes_{_ACCOUNT}_20260630.jsonl").write_text("x\n")
    (logs / f"cycle_stats_{_ACCOUNT}_20260703.jsonl").write_text("x\n")
    buf = io.StringIO()
    with redirect_stderr(buf):
        result = eod_health_check.detect_latest_market_date(_ACCOUNT)
    # cycle_stats has a newer date than the stuck outcomes date
    assert result == "20260703"
    assert "candidate_outcomes missing" in buf.getvalue()


def test_both_no_warn_when_outcomes_cover_winning_date(tmp_path, monkeypatch):
    logs = _mk_logs(tmp_path, monkeypatch)
    (logs / f"candidate_outcomes_{_ACCOUNT}_20260703.jsonl").write_text("x\n")
    (logs / f"cycle_stats_{_ACCOUNT}_20260703.jsonl").write_text("x\n")
    buf = io.StringIO()
    with redirect_stderr(buf):
        result = eod_health_check.detect_latest_market_date(_ACCOUNT)
    assert result == "20260703"
    assert "candidate_outcomes missing" not in buf.getvalue()


def test_neither_raises_filenotfound(tmp_path, monkeypatch):
    _mk_logs(tmp_path, monkeypatch)
    with pytest.raises(FileNotFoundError):
        eod_health_check.detect_latest_market_date(_ACCOUNT)
