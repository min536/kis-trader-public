from __future__ import annotations

import json

from tests.fixtures.portfolio_trade_records import (
    w3_expected_analytics_values,
    w3_trade_records,
    write_trade_records_jsonl,
)


def test_w3_trade_records_cover_symbol_and_trigger_attribution_cases() -> None:
    records = w3_trade_records()

    assert len(records) == 4
    assert {record["symbol"] for record in records} == {"000660", "005930", "035420"}
    assert {record["sell_trigger"] for record in records} == {
        "stop_loss",
        "take_profit",
        "time_exit",
    }
    assert all(record["net_pnl_krw"] != record["gross_pnl_krw"] for record in records)


def test_w3_expected_analytics_values_match_fixture_arithmetic() -> None:
    records = w3_trade_records()
    expected = w3_expected_analytics_values()

    assert expected["total"]["trade_count"] == len(records)
    assert expected["total"]["gross_pnl_krw"] == sum(
        record["gross_pnl_krw"] for record in records
    )
    assert expected["total"]["net_pnl_krw"] == sum(
        record["net_pnl_krw"] for record in records
    )
    assert expected["total"]["win_rate_pct"] == 50.0
    assert expected["total"]["avg_hold_days"] == 1.75
    assert expected["by_symbol"]["005930"]["net_pnl_krw"] == 7_000
    assert expected["by_trigger"]["take_profit"]["net_pnl_krw"] == 49_000


def test_write_trade_records_jsonl_preserves_order(tmp_path) -> None:
    path = write_trade_records_jsonl(tmp_path / "trades.jsonl")

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    assert [row["trade_id"] for row in rows] == [
        "w3_001",
        "w3_002",
        "w3_003",
        "w3_004",
    ]
