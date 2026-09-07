"""Tests for app.research.ingest.minute_csv_to_parquet (R4).

All file-based fixtures are built under tmp_path. The tests/fixtures/ directory
is owned by another work stream and is never used here.
"""

import unittest

import pandas as pd

from app.research.ingest.minute_csv_to_parquet import (
    DayFileQuality,
    build_quality_console_lines,
    convert_minute_csv_dir,
)


def _write_csv(raw_dir, date_str, symbol, rows, header=None):
    """Write a CSV at raw_dir/date=DATE/symbol=SYMBOL_YYYYMMDD.csv.

    rows is a list of (datetime, open, high, low, close, volume) tuples.
    """
    day_dir = raw_dir / f"date={date_str}"
    day_dir.mkdir(parents=True, exist_ok=True)
    compact = date_str.replace("-", "")
    path = day_dir / f"symbol={symbol}_{compact}.csv"
    cols = header or ["datetime", "open", "high", "low", "close", "volume"]
    lines = [",".join(cols)]
    for r in rows:
        lines.append(",".join(str(x) for x in r))
    path.write_text("\n".join(lines) + "\n")
    return path


class CleanRoundTripTest(unittest.TestCase):
    def test_clean_csv_roundtrip_and_quality(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw = tmp_path / "raw"
            out = tmp_path / "out"
            rows = [
                ("2026-06-12 09:00:00", 100.0, 101.0, 99.0, 100.5, 10),
                ("2026-06-12 09:01:00", 100.5, 103.0, 100.0, 102.0, 20),
                ("2026-06-12 15:30:00", 102.0, 102.5, 98.0, 99.0, 5),
            ]
            _write_csv(raw, "2026-06-12", "005930", rows)
            reports = convert_minute_csv_dir(raw, out)
            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0].rows, 3)
            self.assertEqual(reports[0].date, "2026-06-12")
            self.assertEqual(reports[0].symbol, "005930")
            self.assertEqual(reports[0].first_time, "09:00:00")
            self.assertEqual(reports[0].last_time, "15:30:00")
            self.assertEqual(reports[0].flags, ())


class FlagTest(unittest.TestCase):
    def _run(self, rows):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw = tmp_path / "raw"
            out = tmp_path / "out"
            _write_csv(raw, "2026-06-12", "005930", rows)
            return convert_minute_csv_dir(raw, out)

    def test_late_open_flag_when_first_after_0905(self):
        rows = [
            ("2026-06-12 09:06:00", 100.0, 101.0, 99.0, 100.5, 10),
            ("2026-06-12 15:30:00", 102.0, 102.5, 98.0, 99.0, 5),
        ]
        reports = self._run(rows)
        self.assertEqual(reports[0].flags, ("late_open",))

    def test_early_close_flag_when_last_before_1530(self):
        rows = [
            ("2026-06-12 09:00:00", 100.0, 101.0, 99.0, 100.5, 10),
            ("2026-06-12 15:00:00", 102.0, 102.5, 98.0, 99.0, 5),
        ]
        reports = self._run(rows)
        self.assertEqual(reports[0].flags, ("early_close",))

    def test_duplicate_ts_flag_keeps_first_row(self):
        import tempfile
        from pathlib import Path

        rows = [
            ("2026-06-12 09:00:00", 100.0, 101.0, 99.0, 100.5, 10),
            ("2026-06-12 09:01:00", 100.5, 103.0, 100.0, 102.0, 20),
            ("2026-06-12 09:01:00", 999.0, 999.0, 999.0, 999.0, 999),
            ("2026-06-12 15:30:00", 102.0, 102.5, 98.0, 99.0, 5),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw = tmp_path / "raw"
            out = tmp_path / "out"
            _write_csv(raw, "2026-06-12", "005930", rows)
            reports = convert_minute_csv_dir(raw, out)
            self.assertEqual(reports[0].flags, ("duplicate_ts",))
            self.assertEqual(reports[0].rows, 3)
            df = pd.read_parquet(out / "date=2026-06-12" / "part.parquet")
            # the first 09:01 row (close 102.0) is kept, the duplicate dropped
            self.assertEqual(list(df["close"]), [100.5, 102.0, 99.0])

    def test_non_monotonic_flag_when_rows_out_of_order(self):
        import tempfile
        from pathlib import Path

        rows = [
            ("2026-06-12 09:01:00", 100.5, 103.0, 100.0, 102.0, 20),
            ("2026-06-12 09:00:00", 100.0, 101.0, 99.0, 100.5, 10),
            ("2026-06-12 15:30:00", 102.0, 102.5, 98.0, 99.0, 5),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw = tmp_path / "raw"
            out = tmp_path / "out"
            _write_csv(raw, "2026-06-12", "005930", rows)
            reports = convert_minute_csv_dir(raw, out)
            self.assertEqual(reports[0].flags, ("non_monotonic",))
            # output is sorted despite out-of-order input
            df = pd.read_parquet(out / "date=2026-06-12" / "part.parquet")
            self.assertEqual(list(df["close"]), [100.5, 102.0, 99.0])


class ConsoleLinesTest(unittest.TestCase):
    def test_build_quality_console_lines_literal(self):
        reports = [
            DayFileQuality(
                date="2026-06-12",
                symbol="005930",
                rows=381,
                first_time="09:00:00",
                last_time="15:30:00",
                flags=(),
            ),
            DayFileQuality(
                date="2026-06-12",
                symbol="000660",
                rows=300,
                first_time="09:06:00",
                last_time="15:00:00",
                flags=("early_close", "late_open"),
            ),
        ]
        lines = build_quality_console_lines(reports)
        self.assertEqual(
            lines,
            [
                "date=2026-06-12 symbol=005930 rows=381 "
                "first=09:00:00 last=15:30:00 flags=OK",
                "date=2026-06-12 symbol=000660 rows=300 "
                "first=09:06:00 last=15:00:00 flags=early_close,late_open",
            ],
        )


class CaseInsensitiveHeaderTest(unittest.TestCase):
    def test_uppercase_headers_are_normalized(self):
        import tempfile
        from pathlib import Path

        rows = [
            ("2026-06-12 09:00:00", 100.0, 101.0, 99.0, 100.5, 10),
            ("2026-06-12 15:30:00", 102.0, 102.5, 98.0, 99.0, 5),
        ]
        header = ["Datetime", "Open", "High", "Low", "Close", "Volume"]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw = tmp_path / "raw"
            out = tmp_path / "out"
            _write_csv(raw, "2026-06-12", "005930", rows, header=header)
            reports = convert_minute_csv_dir(raw, out)
            self.assertEqual(reports[0].rows, 2)
            self.assertEqual(reports[0].flags, ())
            df = pd.read_parquet(out / "date=2026-06-12" / "part.parquet")
            self.assertEqual(list(df["close"]), [100.5, 99.0])


class ParquetOutputTest(unittest.TestCase):
    def test_writes_date_partitioned_parquet(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw = tmp_path / "raw"
            out = tmp_path / "out"
            rows = [
                ("2026-06-12 09:00:00", 100.0, 101.0, 99.0, 100.5, 10),
                ("2026-06-12 09:01:00", 100.5, 103.0, 100.0, 102.0, 20),
                ("2026-06-12 15:30:00", 102.0, 102.5, 98.0, 99.0, 5),
            ]
            _write_csv(raw, "2026-06-12", "005930", rows)
            convert_minute_csv_dir(raw, out)
            parquet_path = out / "date=2026-06-12" / "part.parquet"
            self.assertTrue(parquet_path.exists())
            df = pd.read_parquet(parquet_path)
            self.assertEqual(list(df["close"]), [100.5, 102.0, 99.0])
            self.assertEqual(list(df["volume"]), [10, 20, 5])
            self.assertEqual(list(df["symbol"]), ["005930", "005930", "005930"])


if __name__ == "__main__":
    unittest.main()
