"""P0: eod_health_check's cycle_snapshot readers must use the tolerant tail
window (not a whole-file bounded read that raises on the 381MB active file).
"""
from __future__ import annotations

from pathlib import Path

from app.tools import eod_health_check


def _write_snapshots(path: Path, rows: list[str]) -> None:
    path.write_text("".join(line + "\n" for line in rows), encoding="utf-8")


def test_core_rescue_builders_tolerate_oversized_snapshots(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    data = tmp_path / "data"
    logs.mkdir()
    data.mkdir()
    monkeypatch.setattr(eod_health_check, "_PROJECT_ROOT", tmp_path)

    account = "mock_acct_test"
    date = "20260703"
    (logs / f"candidate_outcomes_{account}_{date}.jsonl").write_text("", encoding="utf-8")
    _write_snapshots(
        data / f"cycle_snapshots_{account}.jsonl",
        [
            '{"timestamp":"2026-07-03T09:01:00+09:00",'
            '"market_session":{"session":"REGULAR"},"cycle_id":"c1"}',
        ],
    )

    # Force the whole-file bounded reader to raise if it were used: a tiny read
    # limit makes iter_lines_bounded fail on any nonempty file. The tolerant tail
    # window reader must be used instead, so these builders complete.
    monkeypatch.setenv("KIS_LOCAL_READ_MAX_BYTES", "10")

    join = eod_health_check._build_core_rescue_join_summary(
        account=account, date=date, session="REGULAR"
    )
    stall = eod_health_check._build_core_rescue_stall_diagnosis(
        account=account, date=date, session="REGULAR"
    )
    assert isinstance(join, dict)
    assert isinstance(stall, dict)
    assert join["core_rescue_cycles"] == 0
    assert stall["rescued_cycles"] == 0
