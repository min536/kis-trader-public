"""M-D1 tests — US daily CSV ingest + parquet roundtrip (plan §2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.research.us_regime.ingest import import_us_daily_csv, load_us_daily


def _write_csv(path: Path, rows: list[str]) -> None:
    header = "date,open,high,low,close,volume"
    path.write_text("\n".join([header, *rows]) + "\n")


def test_import_rejects_descending_dates(tmp_path):
    csv_path = tmp_path / "spx.csv"
    _write_csv(
        csv_path,
        [
            "2020-01-03,100,101,99,100,0",
            "2020-01-02,100,101,99,100,0",
        ],
    )
    out_root = tmp_path / "out"
    with pytest.raises(ValueError) as exc:
        import_us_daily_csv(csv_path, asset="SPX", out_root=out_root)
    # Row number of the offending (out-of-order) row is reported.
    assert "2" in str(exc.value)


def test_parquet_roundtrip(tmp_path):
    csv_path = tmp_path / "spx.csv"
    _write_csv(
        csv_path,
        [
            "2020-01-02,100,101,99,100.5,0",
            "2020-01-03,100.5,103,100,102,0",
            "2020-01-06,102,102.5,101,101.5,0",
        ],
    )
    out_root = tmp_path / "out"
    n = import_us_daily_csv(csv_path, asset="SPX", out_root=out_root)
    assert n == 3
    assert (out_root / "SPX.parquet").exists()

    frame = load_us_daily(out_root, "SPX")
    assert list(frame.columns) == list(
        ("date", "open", "high", "low", "close", "volume")
    )
    assert len(frame) == 3
    import datetime

    assert frame["date"].iloc[0] == datetime.date(2020, 1, 2)
    assert frame["date"].iloc[-1] == datetime.date(2020, 1, 6)
    assert float(frame["close"].iloc[1]) == 102.0
