from __future__ import annotations

from pathlib import Path

from app.tools import live_health_check


def _write_snapshot_lines(path: Path, rows: list[str]) -> None:
    path.write_text("".join(line + "\n" for line in rows), encoding="utf-8")


def test_load_recent_snapshots_returns_rows_skips_and_truncation(tmp_path):
    path = tmp_path / "cycle_snapshots.jsonl"
    _write_snapshot_lines(
        path,
        [
            '{"timestamp":"2026-06-03T09:01:00+09:00",'
            '"market_session":{"session":"REGULAR"},"cycle_id":"c1"}',
            '{"timestamp":"2026-06-02T09:01:00+09:00",'
            '"market_session":{"session":"REGULAR"},"cycle_id":"old"}',
        ],
    )
    result = live_health_check._load_recent_cycle_snapshots_for_date(
        path,
        date="20260603",
        session="REGULAR",
    )
    assert [row["cycle_id"] for row in result.rows] == ["c1"]
    assert result.skipped_oversized == 0
    assert result.tail_window_truncated is False


def test_load_recent_snapshots_skips_oversized_line(tmp_path):
    path = tmp_path / "cycle_snapshots.jsonl"
    fat = '{"timestamp":"2026-06-03T09:02:00+09:00",' \
          '"market_session":{"session":"REGULAR"},"cycle_id":"fat","pad":"' + "x" * 200 + '"}'
    _write_snapshot_lines(
        path,
        [
            '{"timestamp":"2026-06-03T09:01:00+09:00",'
            '"market_session":{"session":"REGULAR"},"cycle_id":"c1"}',
            fat,
        ],
    )
    result = live_health_check._load_recent_cycle_snapshots_for_date(
        path,
        date="20260603",
        session="REGULAR",
        max_line_bytes=150,
    )
    assert [row["cycle_id"] for row in result.rows] == ["c1"]
    assert result.skipped_oversized == 1


def test_load_recent_snapshots_flags_window_truncation(tmp_path):
    path = tmp_path / "cycle_snapshots.jsonl"
    _write_snapshot_lines(
        path,
        [
            '{"timestamp":"2026-06-03T09:01:00+09:00",'
            '"market_session":{"session":"REGULAR"},"cycle_id":"c1"}',
            '{"timestamp":"2026-06-03T09:02:00+09:00",'
            '"market_session":{"session":"REGULAR"},"cycle_id":"c2"}',
        ],
    )
    # window smaller than the file → oldest content dropped → truncated flag set.
    result = live_health_check._load_recent_cycle_snapshots_for_date(
        path,
        date="20260603",
        session="REGULAR",
        window_bytes=40,
    )
    assert result.tail_window_truncated is True
