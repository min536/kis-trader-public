"""Regression tests for the shared JSONL tail helper backing the reporting CLIs.

Root incident (2026-07-19): the briefing/attribution CLIs read cycle-snapshot
tails via the full-scan ``iter_lines_bounded``, which enforces a total-file
limit — the live account's 988MB ``cycle_snapshots_*.jsonl`` raised
``LocalReadLimitError`` and the account silently vanished from the briefing.
The shared helper must tail-read via a bounded EOF window instead.
"""

from __future__ import annotations

import json

from app.core.jsonl import read_jsonl_tail_window


def _write_jsonl(path, count: int) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for i in range(count):
            fh.write(json.dumps({"cycle_id": i}) + "\n")


def test_tail_window_returns_last_n_records(tmp_path) -> None:
    path = tmp_path / "cycle_snapshots_sig.jsonl"
    _write_jsonl(path, 50)

    records = read_jsonl_tail_window(path, max_lines=5)

    assert [r["cycle_id"] for r in records] == [45, 46, 47, 48, 49]


def test_tail_window_reads_file_above_local_read_limit(tmp_path, monkeypatch) -> None:
    """Files above KIS_LOCAL_READ_MAX_BYTES must still be tail-readable (the
    full-scan reader raises LocalReadLimitError here — the original bug)."""
    path = tmp_path / "cycle_snapshots_big.jsonl"
    _write_jsonl(path, 200)
    # Force the total-read limit far below the file size; the EOF-window tail
    # reader never consults it, so the read must still succeed.
    monkeypatch.setenv("KIS_LOCAL_READ_MAX_BYTES", "64")

    records = read_jsonl_tail_window(path, max_lines=3)

    assert [r["cycle_id"] for r in records] == [197, 198, 199]


def test_tail_window_missing_or_broken_degrades_to_empty(tmp_path) -> None:
    assert read_jsonl_tail_window(tmp_path / "absent.jsonl", max_lines=5) == []

    broken = tmp_path / "broken.jsonl"
    broken.write_text("{not json}\n", encoding="utf-8")
    assert read_jsonl_tail_window(broken, max_lines=5) == []
