"""CLI tests for the minute-data gap audit tool."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from app.tools.audit_minute_data_gaps import main


class AuditMinuteDataGapsCliTest(unittest.TestCase):
    def test_prints_gap_report_and_plan_only_commands(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "cache"
            (root / "date=2026-06-24").mkdir(parents=True)
            calendar_csv = Path(temp_dir) / "calendar.csv"
            calendar_csv.write_text(
                "date,symbol\n2026-06-24,A\n2026-06-25,A\n",
                encoding="utf-8",
            )
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--root",
                        str(root),
                        "--expected-start",
                        "2026-06-24",
                        "--expected-end",
                        "2026-06-30",
                        "--calendar-csv",
                        str(calendar_csv),
                        "--symbols-file",
                        "active.txt",
                        "--raw-dir",
                        "recovery",
                    ]
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(
                payload["gap_report"]["confirmed_missing_trading_dates"],
                ["2026-06-25"],
            )
            self.assertEqual(payload["fetch_plan"]["window_count"], 1)
            self.assertIn("--plan-only", payload["fetch_plan"]["commands"][0])
            self.assertEqual(payload["safety"]["broker_api_called"], False)

    def test_optional_output_file_matches_stdout_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "cache"
            (root / "date=2026-06-24").mkdir(parents=True)
            output_path = Path(temp_dir) / "audit.json"
            output = io.StringIO()

            with redirect_stdout(output):
                main(
                    [
                        "--root",
                        str(root),
                        "--expected-end",
                        "2026-06-25",
                        "--output",
                        str(output_path),
                    ]
                )

            self.assertEqual(
                json.loads(output_path.read_text(encoding="utf-8")),
                json.loads(output.getvalue()),
            )


if __name__ == "__main__":
    unittest.main()
