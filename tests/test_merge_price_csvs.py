import csv
from pathlib import Path

from app.tools.merge_price_csvs import FIELDNAMES, main, merge_price_csvs


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_merge_price_csvs_deduplicates_and_sorts_by_date_symbol(tmp_path: Path) -> None:
    batch_a = tmp_path / "batch_a.csv"
    batch_b = tmp_path / "batch_b.csv"
    output = tmp_path / "merged" / "prices.csv"
    _write_csv(
        batch_a,
        [
            {
                "date": "2024-01-02",
                "symbol": "005930",
                "open": "1",
                "high": "2",
                "low": "1",
                "close": "2",
                "volume": "100",
                "prev_close": "1",
            },
            {
                "date": "2024-01-01",
                "symbol": "000660",
                "open": "10",
                "high": "20",
                "low": "10",
                "close": "20",
                "volume": "200",
                "prev_close": "10",
            },
        ],
    )
    _write_csv(
        batch_b,
        [
            {
                "date": "2024-01-02",
                "symbol": "005930",
                "open": "999",
                "high": "999",
                "low": "999",
                "close": "999",
                "volume": "999",
                "prev_close": "999",
            },
            {
                "date": "2024-01-01",
                "symbol": "005930",
                "open": "3",
                "high": "4",
                "low": "3",
                "close": "4",
                "volume": "300",
                "prev_close": "3",
            },
        ],
    )

    row_count = merge_price_csvs([batch_a, batch_b], output)

    assert row_count == 3
    assert _read_csv(output) == [
        {
            "date": "2024-01-01",
            "symbol": "000660",
            "open": "10",
            "high": "20",
            "low": "10",
            "close": "20",
            "volume": "200",
            "prev_close": "10",
        },
        {
            "date": "2024-01-01",
            "symbol": "005930",
            "open": "3",
            "high": "4",
            "low": "3",
            "close": "4",
            "volume": "300",
            "prev_close": "3",
        },
        {
            "date": "2024-01-02",
            "symbol": "005930",
            "open": "1",
            "high": "2",
            "low": "1",
            "close": "2",
            "volume": "100",
            "prev_close": "1",
        },
    ]


def test_merge_price_csvs_cli_writes_output(tmp_path: Path, capsys) -> None:
    batch = tmp_path / "batch.csv"
    output = tmp_path / "out.csv"
    _write_csv(
        batch,
        [
            {
                "date": "2024-01-01",
                "symbol": "005930",
                "open": "1",
                "high": "2",
                "low": "1",
                "close": "2",
                "volume": "100",
                "prev_close": "1",
            }
        ],
    )

    exit_code = main([str(batch), "--output", str(output)])

    assert exit_code == 0
    assert _read_csv(output)[0]["symbol"] == "005930"
    assert "Merged 1 files / 1 rows" in capsys.readouterr().out
