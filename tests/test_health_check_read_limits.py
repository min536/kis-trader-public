from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core.file_read_limits import (
    LocalReadLimitError,
    read_csv_dicts_bounded,
    read_csv_dicts_with_fieldnames_bounded,
)
from app.tools import eod_health_check, live_health_check


class HealthCheckReadLimitTests(unittest.TestCase):
    def test_eod_health_check_jsonl_uses_bounded_reader(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "candidate_outcomes.jsonl"
            path.write_text("sentinel that should not be scanned directly\n", encoding="utf-8")
            with mock.patch.object(
                eod_health_check,
                "iter_lines_bounded",
                return_value=[(1, '{"symbol":"005930"}\n')],
            ) as mocked_iter:
                rows = eod_health_check._load_jsonl(path)

        self.assertEqual(rows, [{"symbol": "005930"}])
        mocked_iter.assert_called_once_with(path, encoding="utf-8")

    def test_live_health_check_jsonl_uses_bounded_reader(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "candidate_outcomes.jsonl"
            path.write_text("sentinel that should not be scanned directly\n", encoding="utf-8")
            with mock.patch.object(
                live_health_check,
                "iter_lines_bounded",
                return_value=[(1, '{"cycle_id":"c1"}\n')],
            ) as mocked_iter:
                rows = live_health_check._load_jsonl(path)

        self.assertEqual(rows, [{"cycle_id": "c1"}])
        mocked_iter.assert_called_once_with(path, encoding="utf-8")

    def test_live_health_check_jsonl_raises_readable_error_on_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "candidate_outcomes.jsonl"
            path.write_text("sentinel\n", encoding="utf-8")
            with mock.patch.object(
                live_health_check,
                "iter_lines_bounded",
                side_effect=live_health_check.LocalReadLimitError("too large"),
            ):
                with self.assertRaisesRegex(RuntimeError, "exceeds local read limit"):
                    live_health_check._load_jsonl(path)

    def test_live_health_check_cycle_snapshots_use_tail_window_reader(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cycle_snapshots.jsonl"
            path.write_text("sentinel that should not be scanned directly\n", encoding="utf-8")
            with mock.patch.object(
                live_health_check,
                "iter_tail_lines_window",
                return_value=[
                    (
                        1,
                        '{"timestamp":"2026-06-03T09:01:00+09:00",'
                        '"market_session":{"session":"REGULAR"},"cycle_id":"c1"}',
                    ),
                    (
                        2,
                        '{"timestamp":"2026-06-02T09:01:00+09:00",'
                        '"market_session":{"session":"REGULAR"},"cycle_id":"old"}',
                    ),
                ],
            ) as mocked_tail:
                result = live_health_check._load_recent_cycle_snapshots_for_date(
                    path,
                    date="20260603",
                    session="REGULAR",
                )

        self.assertEqual([row["cycle_id"] for row in result.rows], ["c1"])
        _args, kwargs = mocked_tail.call_args
        self.assertEqual(kwargs["max_lines"], 5000)
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertIn("on_skip", kwargs)

    def test_read_csv_dicts_bounded_reads_rows_and_enforces_size_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "rows.csv"
            path.write_text("symbol,score\n005930,1.5\n", encoding="utf-8")

            rows = read_csv_dicts_bounded(path)
            self.assertEqual(rows, [{"symbol": "005930", "score": "1.5"}])

            with self.assertRaises(LocalReadLimitError):
                read_csv_dicts_bounded(path, max_bytes=1)

    def test_read_csv_dicts_with_fieldnames_preserves_header_for_empty_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "empty.csv"
            path.write_text("symbol,name,market\n", encoding="utf-8")

            rows, fieldnames = read_csv_dicts_with_fieldnames_bounded(path)

        self.assertEqual(rows, [])
        self.assertEqual(fieldnames, ("symbol", "name", "market"))


if __name__ == "__main__":
    unittest.main()
