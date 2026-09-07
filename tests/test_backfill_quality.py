"""Tests for app.research.coverage.quality and the backfill parquet CLI (D1).

All file-based fixtures are built under tmp_path. Real data/ paths are never
touched. No broker/KIS/Toss network calls. Python 3.13.
"""

import os
import unittest

from app.research.coverage.quality import (
    detect_price_jumps,
    halt_spans,
    intraday_gap_ratio,
)


class DetectPriceJumpsBoundaryTest(unittest.TestCase):
    def test_positive_gap_boundary_299_not_flagged_300_flagged(self):
        # +29.9% is below the 30.0% threshold -> not flagged.
        below = detect_price_jumps([("2026-06-11", 100.0), ("2026-06-12", 129.9)])
        self.assertEqual(below, [])

        # +30.0% is at the threshold -> flagged.
        at = detect_price_jumps([("2026-06-11", 100.0), ("2026-06-12", 130.0)])
        self.assertEqual(len(at), 1)
        self.assertEqual(at[0]["date"], "2026-06-12")
        self.assertEqual(at[0]["prev_close"], 100.0)
        self.assertEqual(at[0]["close"], 130.0)
        self.assertAlmostEqual(at[0]["change_pct"], 30.0)

    def test_negative_gap_boundary_299_not_flagged_300_flagged(self):
        # -29.9% is below the threshold -> not flagged.
        below = detect_price_jumps([("2026-06-11", 100.0), ("2026-06-12", 70.1)])
        self.assertEqual(below, [])

        # -30.0% is at the threshold -> flagged.
        at = detect_price_jumps([("2026-06-11", 100.0), ("2026-06-12", 70.0)])
        self.assertEqual(len(at), 1)
        self.assertEqual(at[0]["date"], "2026-06-12")
        self.assertAlmostEqual(at[0]["change_pct"], -30.0)


def _minute_stamps(start_hhmm, count, step_min=1):
    """Return ``count`` "HH:MM" strings starting at start_hhmm, step apart."""
    h, m = (int(x) for x in start_hhmm.split(":"))
    total = h * 60 + m
    out = []
    for _ in range(count):
        out.append(f"{total // 60:02d}:{total % 60:02d}")
        total += step_min
    return out


class IntradayGapRatioTest(unittest.TestCase):
    def test_full_391_minute_day_has_zero_gap(self):
        # 09:00..15:30 inclusive at 1-min cadence = 391 stamps.
        bars = _minute_stamps("09:00", 391)
        self.assertEqual(len(bars), 391)
        self.assertEqual(bars[0], "09:00")
        self.assertEqual(bars[-1], "15:30")
        self.assertAlmostEqual(intraday_gap_ratio(bars), 0.0)

    def test_half_missing_day_is_about_one_half(self):
        # Keep every other minute -> ~half the 391 expected minutes present.
        full = _minute_stamps("09:00", 391)
        half = full[::2]  # 196 present of 391 expected
        ratio = intraday_gap_ratio(half)
        self.assertAlmostEqual(ratio, (391 - len(half)) / 391)
        self.assertAlmostEqual(ratio, 0.5, places=1)


def _full_day_rows(date_str, close_base):
    """391 one-minute rows 09:00..15:30 with nonzero volume.

    Every bar's close is ``close_base``; the day's last bar close equals
    ``close_base`` (used as the daily close for jump detection).
    """
    stamps = _minute_stamps("09:00", 391)
    rows = []
    for s in stamps:
        rows.append(
            (
                f"{date_str} {s}:00",
                close_base,
                close_base + 1.0,
                close_base - 1.0,
                close_base,
                10,
            )
        )
    return rows


class HaltSpansTest(unittest.TestCase):
    def _bars_with_zero_run(self, run_len):
        """One nonzero bar, then run_len zero-volume bars, then one nonzero."""
        stamps = _minute_stamps("09:00", run_len + 2)
        bars = [(stamps[0], 100)]
        for s in stamps[1 : 1 + run_len]:
            bars.append((s, 0))
        bars.append((stamps[-1], 100))
        return bars, stamps

    def test_29_minute_zero_run_not_detected(self):
        bars, _ = self._bars_with_zero_run(29)
        self.assertEqual(halt_spans(bars), [])

    def test_30_minute_zero_run_detected(self):
        bars, stamps = self._bars_with_zero_run(30)
        spans = halt_spans(bars)
        self.assertEqual(len(spans), 1)
        # Span covers the 30 zero-volume bars: stamps[1]..stamps[30].
        self.assertEqual(spans[0], (stamps[1], stamps[30]))


class BuildBackfillParquetCliTest(unittest.TestCase):
    def _write_full_day(self, raw_dir, date_str, symbol, close_base):
        rows = _full_day_rows(date_str, close_base)
        day_dir = raw_dir / f"date={date_str}"
        day_dir.mkdir(parents=True, exist_ok=True)
        compact = date_str.replace("-", "")
        path = day_dir / f"symbol={symbol}_{compact}.csv"
        header = ["datetime", "open", "high", "low", "close", "volume"]
        lines = [",".join(header)]
        for r in rows:
            lines.append(",".join(str(x) for x in r))
        path.write_text("\n".join(lines) + "\n")

    def test_cli_end_to_end_produces_parquet_and_report(self):
        import json
        import os
        import tempfile
        from pathlib import Path

        import pandas as pd

        from app.tools.build_backfill_parquet import main

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw = tmp_path / "raw"
            out = tmp_path / "out"
            report_path = tmp_path / "report" / "quality_report.json"

            # 2 symbols x 2 days. CLEAN keeps close flat (~100 -> ~101).
            # JUMP has a +40% overnight close move (100 -> 140) -> excluded.
            self._write_full_day(raw, "2026-06-11", "005930", 100.0)
            self._write_full_day(raw, "2026-06-12", "005930", 101.0)
            self._write_full_day(raw, "2026-06-11", "000660", 100.0)
            self._write_full_day(raw, "2026-06-12", "000660", 140.0)

            # Noise that must be skipped by the CLI enumeration.
            (raw / "_state").mkdir(parents=True, exist_ok=True)
            (raw / "_state" / "005930.json").write_text("{}")
            (raw / "fetch_manifest.jsonl").write_text('{"x": 1}\n')

            # Guard: replay/record processes must run with the quote env unset.
            self.assertIsNone(os.environ.get("BUY_SCAN_QUOTE_KIS_ENV"))

            report = main(
                [
                    "--raw-dir",
                    str(raw),
                    "--out-dir",
                    str(out),
                    "--report",
                    str(report_path),
                ]
            )

            # Parquet output exists for both dates.
            self.assertTrue((out / "date=2026-06-11" / "part.parquet").exists())
            self.assertTrue((out / "date=2026-06-12" / "part.parquet").exists())
            df = pd.read_parquet(out / "date=2026-06-12" / "part.parquet")
            self.assertEqual(set(df["symbol"]), {"005930", "000660"})

            # Report JSON written and returned identically.
            self.assertTrue(report_path.exists())
            on_disk = json.loads(report_path.read_text())
            self.assertEqual(on_disk, report)

            # exclude_recommended present; jumped symbol in, clean symbol out.
            self.assertIn("exclude_recommended", report)
            self.assertIn("000660", report["exclude_recommended"])
            self.assertNotIn("005930", report["exclude_recommended"])

            # Per-symbol quality fields exist.
            self.assertIn("symbols", report)
            self.assertIn("000660", report["symbols"])
            jump_sym = report["symbols"]["000660"]
            self.assertGreaterEqual(jump_sym["jump_flags"], 1)
            clean_sym = report["symbols"]["005930"]
            self.assertEqual(clean_sym["jump_flags"], 0)
            self.assertAlmostEqual(clean_sym["gap_ratio_p95"], 0.0)


class QuoteEnvGuardTest(unittest.TestCase):
    def test_main_refuses_when_quote_env_set(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        from app.tools.build_backfill_parquet import main

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            argv = [
                "--raw-dir",
                str(tmp_path / "raw"),
                "--out-dir",
                str(tmp_path / "out"),
                "--report",
                str(tmp_path / "report.json"),
            ]
            with mock.patch.dict(
                os.environ, {"BUY_SCAN_QUOTE_KIS_ENV": "live"}
            ):
                with self.assertRaises(RuntimeError):
                    main(argv)
            # Guard fires before any conversion/output is produced.
            self.assertFalse((tmp_path / "out").exists())


if __name__ == "__main__":
    unittest.main()
