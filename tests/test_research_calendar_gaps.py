"""Tests for date-level research cache gap auditing and fetch planning."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.research.coverage.calendar_gaps import (
    build_calendar_gap_report,
    build_monthly_fetch_windows,
    build_plan_only_commands,
    calendar_gap_report_to_dict,
    scan_partition_dates,
)


def _partition(root: str, day: str) -> None:
    target = Path(root) / f"date={day}"
    target.mkdir(parents=True, exist_ok=True)
    (target / "part.parquet").write_text("", encoding="utf-8")


class PartitionDateScanTest(unittest.TestCase):
    def test_combines_roots_and_ignores_malformed_directories(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            _partition(first, "2025-12-31")
            _partition(second, "2026-01-02")
            _partition(second, "2025-12-31")
            (Path(first) / "date=2026-99-99").mkdir()
            (Path(first) / "not-a-partition").mkdir()

            self.assertEqual(
                scan_partition_dates([first, second]),
                ("2025-12-31", "2026-01-02"),
            )


class CalendarGapReportTest(unittest.TestCase):
    def test_reports_full_missing_year_month_and_weekday_candidates(self):
        with tempfile.TemporaryDirectory() as root:
            _partition(root, "2024-12-31")
            _partition(root, "2026-02-02")

            report = build_calendar_gap_report(
                [root],
                expected_start=date(2024, 12, 31),
                expected_end=date(2026, 2, 2),
            )

            self.assertEqual(report.missing_years, (2025,))
            self.assertIn("2025-01", report.fully_missing_months)
            self.assertIn("2026-01", report.fully_missing_months)
            self.assertNotIn("2024-12", report.fully_missing_months)
            self.assertNotIn("2026-02", report.fully_missing_months)
            self.assertIn("2025-01-01", report.weekday_absence_candidates)

    def test_weekends_are_not_weekday_candidates(self):
        with tempfile.TemporaryDirectory() as root:
            _partition(root, "2026-06-05")  # Friday
            _partition(root, "2026-06-09")  # Tuesday

            report = build_calendar_gap_report(
                [root],
                expected_start=date(2026, 6, 5),
                expected_end=date(2026, 6, 9),
            )

            self.assertEqual(report.weekday_absence_candidates, ("2026-06-08",))

    def test_authoritative_calendar_separates_confirmed_gaps(self):
        with tempfile.TemporaryDirectory() as root:
            _partition(root, "2026-06-05")
            _partition(root, "2026-06-09")

            report = build_calendar_gap_report(
                [root],
                expected_start=date(2026, 6, 5),
                expected_end=date(2026, 6, 9),
                expected_trading_dates=("2026-06-05", "2026-06-08", "2026-06-09"),
            )

            self.assertEqual(report.confirmed_missing_trading_dates, ("2026-06-08",))

    def test_reports_freshness_tail(self):
        with tempfile.TemporaryDirectory() as root:
            _partition(root, "2026-06-24")

            report = build_calendar_gap_report(
                [root],
                expected_start=date(2026, 6, 24),
                expected_end=date(2026, 7, 2),
            )

            self.assertEqual(report.latest_available_date, "2026-06-24")
            self.assertEqual(report.freshness_tail_start, "2026-06-25")
            self.assertEqual(report.stale_calendar_days, 8)

    def test_empty_roots_require_explicit_expected_range(self):
        with tempfile.TemporaryDirectory() as root:
            report = build_calendar_gap_report(
                [root],
                expected_start=date(2026, 1, 1),
                expected_end=date(2026, 1, 31),
            )

            self.assertEqual(report.available_dates, ())
            self.assertEqual(report.fully_missing_months, ("2026-01",))
            self.assertEqual(report.latest_available_date, None)


class FetchPlanTest(unittest.TestCase):
    def test_tail_is_split_into_resumable_month_windows(self):
        with tempfile.TemporaryDirectory() as root:
            _partition(root, "2026-06-24")
            report = build_calendar_gap_report(
                [root],
                expected_start=date(2026, 6, 1),
                expected_end=date(2026, 9, 2),
            )

            windows = build_monthly_fetch_windows(report)

            self.assertEqual(
                [(window.start, window.end) for window in windows],
                [
                    ("2026-06-25", "2026-06-30"),
                    ("2026-07-01", "2026-07-31"),
                    ("2026-08-01", "2026-08-31"),
                    ("2026-09-01", "2026-09-02"),
                ],
            )

    def test_commands_are_plan_only_and_shell_quoted(self):
        with tempfile.TemporaryDirectory() as root:
            _partition(root, "2026-06-24")
            report = build_calendar_gap_report(
                [root],
                expected_start=date(2026, 6, 24),
                expected_end=date(2026, 6, 30),
            )
            windows = build_monthly_fetch_windows(report)

            commands = build_plan_only_commands(
                windows,
                symbols_file="data/minute universe/active.txt",
                raw_dir="data/minute_raw_gap_recovery",
            )

            self.assertEqual(len(commands), 1)
            self.assertIn("app.tools.fetch_kis_minute_bars", commands[0])
            self.assertIn("--plan-only", commands[0])
            self.assertIn("'data/minute universe/active.txt'", commands[0])
            self.assertNotIn("--overwrite", commands[0])

    def test_report_dict_is_json_serializable_shape(self):
        with tempfile.TemporaryDirectory() as root:
            _partition(root, "2026-06-24")
            report = build_calendar_gap_report(
                [root],
                expected_start=date(2026, 6, 24),
                expected_end=date(2026, 6, 25),
            )

            payload = calendar_gap_report_to_dict(report)

            self.assertEqual(payload["available_date_count"], 1)
            self.assertEqual(payload["expected_end"], "2026-06-25")
            self.assertEqual(payload["freshness"]["tail_start"], "2026-06-25")


if __name__ == "__main__":
    unittest.main()
