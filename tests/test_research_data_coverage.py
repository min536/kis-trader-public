"""Tests for app.research.coverage.data_coverage (leaf research module)."""

import tempfile
import unittest
from pathlib import Path

from app.research.coverage.data_coverage import (
    CoverageReport,
    SymbolCoverage,
    build_coverage_console_lines,
    build_coverage_report,
    coverage_report_to_dict,
    scan_partitioned_cache,
)


def _touch(root, date, filename):
    d = Path(root) / f"date={date}"
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text("", encoding="utf-8")


class ScanPartitionedCacheTest(unittest.TestCase):
    def test_scan_basic(self):
        with tempfile.TemporaryDirectory() as root:
            _touch(root, "2026-06-10", "symbol=005930_20260610.csv")
            _touch(root, "2026-06-11", "symbol=005930_20260611.csv")
            _touch(root, "2026-06-11", "symbol=000660_20260611.csv")
            self.assertEqual(
                scan_partitioned_cache(root),
                {
                    "005930": {"2026-06-10", "2026-06-11"},
                    "000660": {"2026-06-11"},
                },
            )

    def test_scan_ignores_malformed(self):
        with tempfile.TemporaryDirectory() as root:
            # non date= directory
            (Path(root) / "notdate").mkdir()
            (Path(root) / "notdate" / "symbol=ZZZ.csv").write_text(
                "", encoding="utf-8"
            )
            # malformed date directory name
            bad = Path(root) / "date=badformat"
            bad.mkdir()
            (bad / "symbol=YYY.csv").write_text("", encoding="utf-8")
            # valid date dir with one valid file plus a stray file to ignore
            _touch(root, "2026-06-12", "symbol=AAA.csv")
            _touch(root, "2026-06-12", "readme.txt")
            self.assertEqual(
                scan_partitioned_cache(root),
                {"AAA": {"2026-06-12"}},
            )

    def test_missing_root_returns_empty(self):
        self.assertEqual(scan_partitioned_cache("/no/such/path/xyz"), {})


class MissingRootReportTest(unittest.TestCase):
    def test_report_on_missing_root_with_master(self):
        report = build_coverage_report(
            "/no/such/path/xyz", master_symbols=["A", "B"]
        )
        self.assertEqual(report.cache_dates, ())
        self.assertEqual(report.symbols, ())
        self.assertEqual(report.master_symbol_count, 2)
        self.assertEqual(report.covered_master_count, 0)
        self.assertEqual(report.uncovered_master_symbols, ("A", "B"))
        self.assertEqual(report.extra_symbols, ())


class CoverageReportGapTest(unittest.TestCase):
    def _report(self, root):
        _touch(root, "2026-06-10", "symbol=A_20260610.csv")
        _touch(root, "2026-06-12", "symbol=A_20260612.csv")
        _touch(root, "2026-06-10", "symbol=B_20260610.csv")
        _touch(root, "2026-06-11", "symbol=B_20260611.csv")
        _touch(root, "2026-06-12", "symbol=B_20260612.csv")
        return build_coverage_report(root)

    def test_report_with_internal_gap(self):
        with tempfile.TemporaryDirectory() as root:
            report = self._report(root)
            self.assertEqual(
                report.cache_dates,
                ("2026-06-10", "2026-06-11", "2026-06-12"),
            )
            a = report.symbols[0]
            b = report.symbols[1]
            self.assertEqual(a.symbol, "A")
            self.assertEqual(a.first_date, "2026-06-10")
            self.assertEqual(a.last_date, "2026-06-12")
            self.assertEqual(a.present_days, 2)
            self.assertEqual(a.expected_days, 3)
            self.assertEqual(a.missing_dates, ("2026-06-11",))
            self.assertEqual(b.symbol, "B")
            self.assertEqual(b.present_days, 3)
            self.assertEqual(b.expected_days, 3)
            self.assertEqual(b.missing_dates, ())

    def test_coverage_ratio(self):
        with tempfile.TemporaryDirectory() as root:
            report = self._report(root)
            self.assertEqual(report.symbols[0].coverage_ratio, 2 / 3)

    def test_coverage_ratio_zero_expected_guard(self):
        c = SymbolCoverage(
            symbol="A",
            first_date="2026-06-10",
            last_date="2026-06-10",
            present_days=0,
            expected_days=0,
            missing_dates=(),
        )
        self.assertEqual(c.coverage_ratio, 0.0)


class MasterComparisonTest(unittest.TestCase):
    def test_master_comparison(self):
        with tempfile.TemporaryDirectory() as root:
            _touch(root, "2026-06-10", "symbol=A_20260610.csv")
            _touch(root, "2026-06-10", "symbol=B_20260610.csv")
            report = build_coverage_report(root, master_symbols=["A", "C"])
            self.assertEqual(report.master_symbol_count, 2)
            self.assertEqual(report.covered_master_count, 1)
            self.assertEqual(report.uncovered_master_symbols, ("C",))
            self.assertEqual(report.extra_symbols, ("B",))


class ConsoleAndDictTest(unittest.TestCase):
    def _report(self, root):
        _touch(root, "2026-06-10", "symbol=A_20260610.csv")
        _touch(root, "2026-06-12", "symbol=A_20260612.csv")
        _touch(root, "2026-06-10", "symbol=B_20260610.csv")
        _touch(root, "2026-06-11", "symbol=B_20260611.csv")
        _touch(root, "2026-06-12", "symbol=B_20260612.csv")
        return build_coverage_report(
            root, granularity="minute", master_symbols=["A", "C"]
        )

    def test_console_lines(self):
        with tempfile.TemporaryDirectory() as root:
            report = self._report(root)
            lines = build_coverage_console_lines(report)
            self.assertEqual(
                lines,
                [
                    f"coverage granularity=minute root={root} dates=3 symbols=2",
                    "symbol=A first=2026-06-10 last=2026-06-12 days=2/3 missing=1",
                    "symbol=B first=2026-06-10 last=2026-06-12 days=3/3 missing=0",
                    "master covered=1/2 uncovered=1 extra=1",
                ],
            )

    def test_to_dict(self):
        with tempfile.TemporaryDirectory() as root:
            report = self._report(root)
            self.assertEqual(
                coverage_report_to_dict(report),
                {
                    "root": root,
                    "granularity": "minute",
                    "cache_dates": [
                        "2026-06-10",
                        "2026-06-11",
                        "2026-06-12",
                    ],
                    "symbols": [
                        {
                            "symbol": "A",
                            "first_date": "2026-06-10",
                            "last_date": "2026-06-12",
                            "present_days": 2,
                            "expected_days": 3,
                            "missing_dates": ["2026-06-11"],
                            "coverage_ratio": 2 / 3,
                        },
                        {
                            "symbol": "B",
                            "first_date": "2026-06-10",
                            "last_date": "2026-06-12",
                            "present_days": 3,
                            "expected_days": 3,
                            "missing_dates": [],
                            "coverage_ratio": 1.0,
                        },
                    ],
                    "master_symbol_count": 2,
                    "covered_master_count": 1,
                    "uncovered_master_symbols": ["C"],
                    "extra_symbols": ["B"],
                },
            )


if __name__ == "__main__":
    unittest.main()
