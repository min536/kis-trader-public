from __future__ import annotations

from pathlib import Path

import pytest

from app.core.file_read_limits import iter_tail_lines_window


def _write_lines(path: Path, count: int, *, prefix: str = "line") -> None:
    path.write_text(
        "".join(f"{prefix}-{i}\n" for i in range(count)),
        encoding="utf-8",
    )


def test_yields_last_max_lines_within_window(tmp_path):
    path = tmp_path / "snap.jsonl"
    _write_lines(path, 10)
    rows = list(iter_tail_lines_window(path, max_lines=3))
    assert [text for _lineno, text in rows] == ["line-7", "line-8", "line-9"]


def test_window_exactly_at_file_boundary_keeps_first_line(tmp_path):
    # window_bytes sized to exactly cover the whole file → first line is complete,
    # so it must NOT be dropped as a partial fragment.
    path = tmp_path / "snap.jsonl"
    _write_lines(path, 4)  # "line-0\n".."line-3\n"
    exact = path.stat().st_size
    rows = list(iter_tail_lines_window(path, max_lines=10, window_bytes=exact))
    assert [text for _lineno, text in rows] == ["line-0", "line-1", "line-2", "line-3"]


def test_window_truncated_drops_partial_first_line(tmp_path):
    path = tmp_path / "snap.jsonl"
    _write_lines(path, 4)  # each record is "line-N\n" == 7 bytes
    # A window that starts mid-first-record must drop that partial fragment.
    rows = list(iter_tail_lines_window(path, max_lines=10, window_bytes=20))
    texts = [text for _lineno, text in rows]
    assert "line-1" not in texts  # partial fragment dropped
    assert texts == ["line-2", "line-3"]


def test_oversized_line_is_skipped_not_raised(tmp_path):
    path = tmp_path / "snap.jsonl"
    big = "x" * 100
    path.write_text(f"small-a\n{big}\nsmall-b\n", encoding="utf-8")
    skipped: list[tuple[int, int]] = []
    rows = list(
        iter_tail_lines_window(
            path,
            max_lines=10,
            max_line_bytes=20,
            on_skip=lambda lineno, byte_len: skipped.append((lineno, byte_len)),
        )
    )
    texts = [text for _lineno, text in rows]
    assert texts == ["small-a", "small-b"]  # oversized line dropped, never raised
    assert len(skipped) == 1
    assert skipped[0][1] == len(big)  # byte length reported to on_skip


def test_empty_file_yields_nothing(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    assert list(iter_tail_lines_window(path, max_lines=5)) == []
