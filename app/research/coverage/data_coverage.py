"""Data cache coverage reporting (R-coverage).

Leaf research module under app/research/coverage/. Imports only the Python
stdlib. No imports from app.*.

Coverage is derived from the on-disk partitioned cache layout

    <root>/date=YYYY-MM-DD/symbol=<SYM>_<YYYYMMDD>.csv

using DIRECTORY/FILENAME STRUCTURE ONLY. CSV file contents are never opened
or read (the repo forbids bulk-reading data files).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SymbolCoverage:
    """Coverage of one cached symbol over the union of cache dates."""

    symbol: str
    first_date: str
    last_date: str
    present_days: int
    expected_days: int
    missing_dates: tuple[str, ...]

    @property
    def coverage_ratio(self) -> float:
        if self.expected_days == 0:
            return 0.0
        return self.present_days / self.expected_days


@dataclass(frozen=True)
class CoverageReport:
    """Cache coverage across all cached symbols, vs. an optional master list."""

    root: str
    granularity: str
    cache_dates: tuple[str, ...]
    symbols: tuple[SymbolCoverage, ...]
    master_symbol_count: int
    covered_master_count: int
    uncovered_master_symbols: tuple[str, ...]
    extra_symbols: tuple[str, ...]


def _is_valid_date_name(name: str) -> bool:
    """True when ``name`` is 'date=NNNN-NN-NN' (digits except the two dashes)."""
    prefix = "date="
    if not name.startswith(prefix):
        return False
    rest = name[len(prefix):]
    if len(rest) != 10:
        return False
    for i, ch in enumerate(rest):
        if i in (4, 7):
            if ch != "-":
                return False
        elif not ch.isdigit():
            return False
    return True


def scan_partitioned_cache(root) -> dict[str, set[str]]:
    """Scan <root>/date=YYYY-MM-DD/symbol=<SYM>*.csv -> {symbol: {date,...}}.

    Directory/filename listing only; CSV contents are never read. Returns an
    empty dict when ``root`` is missing or not a directory.
    """
    root_path = Path(root)
    if not root_path.is_dir():
        return {}
    by_symbol: dict[str, set[str]] = {}
    for date_dir in root_path.iterdir():
        if not date_dir.is_dir() or not _is_valid_date_name(date_dir.name):
            continue
        date_str = date_dir.name[len("date="):]
        for csv_path in date_dir.iterdir():
            name = csv_path.name
            if not (name.startswith("symbol=") and name.endswith(".csv")):
                continue
            stem = name[len("symbol="):-len(".csv")]
            symbol = stem.split("_", 1)[0]
            by_symbol.setdefault(symbol, set()).add(date_str)
    return by_symbol


def build_coverage_report(
    root, *, granularity: str = "minute", master_symbols=()
) -> CoverageReport:
    """Build a :class:`CoverageReport` from the partitioned cache at ``root``."""
    by_symbol = scan_partitioned_cache(root)

    all_dates: set[str] = set()
    for dates in by_symbol.values():
        all_dates |= dates
    cache_dates = tuple(sorted(all_dates))

    symbols: list[SymbolCoverage] = []
    for symbol in sorted(by_symbol):
        dates = by_symbol[symbol]
        ds = sorted(dates)
        first_date = ds[0]
        last_date = ds[-1]
        in_window = [d for d in cache_dates if first_date <= d <= last_date]
        expected_days = len(in_window)
        missing_dates = tuple(d for d in in_window if d not in dates)
        symbols.append(
            SymbolCoverage(
                symbol=symbol,
                first_date=first_date,
                last_date=last_date,
                present_days=len(dates),
                expected_days=expected_days,
                missing_dates=missing_dates,
            )
        )

    master = set(str(s) for s in master_symbols)
    master_symbol_count = len(master)
    covered_master_count = sum(1 for s in master if s in by_symbol)
    uncovered_master_symbols = tuple(sorted(master - set(by_symbol)))
    if master:
        extra_symbols = tuple(sorted(set(by_symbol) - master))
    else:
        extra_symbols = ()

    return CoverageReport(
        root=str(root),
        granularity=granularity,
        cache_dates=cache_dates,
        symbols=tuple(symbols),
        master_symbol_count=master_symbol_count,
        covered_master_count=covered_master_count,
        uncovered_master_symbols=uncovered_master_symbols,
        extra_symbols=extra_symbols,
    )


def build_coverage_console_lines(report: CoverageReport) -> list[str]:
    """Render a :class:`CoverageReport` to console lines (one str per line)."""
    lines = [
        f"coverage granularity={report.granularity} root={report.root} "
        f"dates={len(report.cache_dates)} symbols={len(report.symbols)}"
    ]
    for c in report.symbols:
        lines.append(
            f"symbol={c.symbol} first={c.first_date} last={c.last_date} "
            f"days={c.present_days}/{c.expected_days} "
            f"missing={len(c.missing_dates)}"
        )
    if report.master_symbol_count > 0:
        lines.append(
            f"master covered={report.covered_master_count}/"
            f"{report.master_symbol_count} "
            f"uncovered={len(report.uncovered_master_symbols)} "
            f"extra={len(report.extra_symbols)}"
        )
    return lines


def coverage_report_to_dict(report: CoverageReport) -> dict:
    """Return a JSON-serializable dict (plain str/int/float/list)."""
    return {
        "root": report.root,
        "granularity": report.granularity,
        "cache_dates": list(report.cache_dates),
        "symbols": [
            {
                "symbol": c.symbol,
                "first_date": c.first_date,
                "last_date": c.last_date,
                "present_days": c.present_days,
                "expected_days": c.expected_days,
                "missing_dates": list(c.missing_dates),
                "coverage_ratio": c.coverage_ratio,
            }
            for c in report.symbols
        ],
        "master_symbol_count": report.master_symbol_count,
        "covered_master_count": report.covered_master_count,
        "uncovered_master_symbols": list(report.uncovered_master_symbols),
        "extra_symbols": list(report.extra_symbols),
    }
