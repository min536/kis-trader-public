"""Minute CSV to parquet converter with a per-file quality report (R4).

Leaf research module under app/research/ingest/. This is the ONLY research
module permitted to import pandas/pyarrow. No imports from app.*.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DayFileQuality:
    """Quality summary for one symbol's day file.

    ``flags`` is a sorted tuple drawn from: ``late_open`` (first bar after
    09:05), ``early_close`` (last bar before 15:30), ``duplicate_ts`` (a
    repeated datetime was dropped), ``non_monotonic`` (rows out of order
    before sorting). The 381-row invariant is NOT used.
    """

    date: str
    symbol: str
    rows: int
    first_time: str
    last_time: str
    flags: tuple[str, ...]


def convert_minute_csv_dir(raw_dir, out_dir):
    import pandas as pd
    from datetime import time
    from pathlib import Path

    raw_dir = Path(raw_dir)
    out_dir = Path(out_dir)
    reports = []
    for date_dir in sorted(p for p in raw_dir.glob("date=*") if p.is_dir()):
        date_str = date_dir.name[len("date=") :]
        frames = []
        for csv_path in sorted(date_dir.glob("symbol=*.csv")):
            stem = csv_path.name[len("symbol=") : -len(".csv")]
            symbol = stem.split("_", 1)[0]
            df = pd.read_csv(csv_path)
            df.columns = [str(c).strip().lower() for c in df.columns]
            df["datetime"] = pd.to_datetime(df["datetime"])
            df["symbol"] = symbol
            flags = set()
            order = df["datetime"].tolist()
            if any(order[i] > order[i + 1] for i in range(len(order) - 1)):
                flags.add("non_monotonic")
            df = df.sort_values("datetime", kind="stable").reset_index(drop=True)
            before = len(df)
            df = df.drop_duplicates(subset="datetime", keep="first").reset_index(
                drop=True
            )
            if len(df) < before:
                flags.add("duplicate_ts")
            first_ts = df["datetime"].iloc[0]
            last_ts = df["datetime"].iloc[-1]
            if first_ts.time() > time(9, 5):
                flags.add("late_open")
            if last_ts.time() < time(15, 30):
                flags.add("early_close")
            reports.append(
                DayFileQuality(
                    date=date_str,
                    symbol=symbol,
                    rows=len(df),
                    first_time=first_ts.strftime("%H:%M:%S"),
                    last_time=last_ts.strftime("%H:%M:%S"),
                    flags=tuple(sorted(flags)),
                )
            )
            frames.append(df)

        if not frames:
            continue
        combined = pd.concat(frames, ignore_index=True)
        date_out = out_dir / f"date={date_str}"
        date_out.mkdir(parents=True, exist_ok=True)
        combined.to_parquet(
            date_out / "part.parquet", engine="pyarrow", index=False
        )

    return reports


def build_quality_console_lines(reports):
    """Render quality reports to console lines.

    ``flags=OK`` is shown when a report has no flags; otherwise the sorted
    flags are joined with commas.
    """
    lines = []
    for report in reports:
        flags = ",".join(report.flags) if report.flags else "OK"
        lines.append(
            f"date={report.date} symbol={report.symbol} rows={report.rows} "
            f"first={report.first_time} last={report.last_time} flags={flags}"
        )
    return lines
