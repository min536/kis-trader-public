from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def w3_trade_records() -> tuple[dict[str, Any], ...]:
    return (
        {
            "trade_id": "w3_001",
            "symbol": "005930",
            "name": "Samsung Electronics",
            "sell_trigger": "take_profit",
            "entry_at": "2026-06-08T09:30:00+09:00",
            "exit_at": "2026-06-09T10:00:00+09:00",
            "hold_days": 1.0,
            "qty": 10,
            "entry_price_krw": 70_000,
            "exit_price_krw": 73_000,
            "buy_notional_krw": 700_000,
            "sell_notional_krw": 730_000,
            "gross_pnl_krw": 30_000,
            "net_pnl_krw": 25_000,
        },
        {
            "trade_id": "w3_002",
            "symbol": "005930",
            "name": "Samsung Electronics",
            "sell_trigger": "stop_loss",
            "entry_at": "2026-06-09T10:10:00+09:00",
            "exit_at": "2026-06-10T09:55:00+09:00",
            "hold_days": 1.0,
            "qty": 10,
            "entry_price_krw": 72_000,
            "exit_price_krw": 70_500,
            "buy_notional_krw": 720_000,
            "sell_notional_krw": 705_000,
            "gross_pnl_krw": -15_000,
            "net_pnl_krw": -18_000,
        },
        {
            "trade_id": "w3_003",
            "symbol": "000660",
            "name": "SK Hynix",
            "sell_trigger": "take_profit",
            "entry_at": "2026-06-08T10:20:00+09:00",
            "exit_at": "2026-06-11T14:40:00+09:00",
            "hold_days": 3.0,
            "qty": 5,
            "entry_price_krw": 130_000,
            "exit_price_krw": 136_000,
            "buy_notional_krw": 650_000,
            "sell_notional_krw": 680_000,
            "gross_pnl_krw": 30_000,
            "net_pnl_krw": 24_000,
        },
        {
            "trade_id": "w3_004",
            "symbol": "035420",
            "name": "NAVER",
            "sell_trigger": "time_exit",
            "entry_at": "2026-06-09T09:40:00+09:00",
            "exit_at": "2026-06-11T13:15:00+09:00",
            "hold_days": 2.0,
            "qty": 4,
            "entry_price_krw": 180_000,
            "exit_price_krw": 179_000,
            "buy_notional_krw": 720_000,
            "sell_notional_krw": 716_000,
            "gross_pnl_krw": -4_000,
            "net_pnl_krw": -7_000,
        },
    )


def w3_expected_analytics_values() -> dict[str, Any]:
    return {
        "total": {
            "trade_count": 4,
            "win_count": 2,
            "loss_count": 2,
            "win_rate_pct": 50.0,
            "buy_notional_krw": 2_790_000,
            "sell_notional_krw": 2_831_000,
            "gross_pnl_krw": 41_000,
            "net_pnl_krw": 24_000,
            "avg_hold_days": 1.75,
        },
        "by_symbol": {
            "000660": {
                "trade_count": 1,
                "win_rate_pct": 100.0,
                "net_pnl_krw": 24_000,
                "avg_hold_days": 3.0,
            },
            "005930": {
                "trade_count": 2,
                "win_rate_pct": 50.0,
                "net_pnl_krw": 7_000,
                "avg_hold_days": 1.0,
            },
            "035420": {
                "trade_count": 1,
                "win_rate_pct": 0.0,
                "net_pnl_krw": -7_000,
                "avg_hold_days": 2.0,
            },
        },
        "by_trigger": {
            "stop_loss": {
                "trade_count": 1,
                "win_rate_pct": 0.0,
                "net_pnl_krw": -18_000,
                "avg_hold_days": 1.0,
            },
            "take_profit": {
                "trade_count": 2,
                "win_rate_pct": 100.0,
                "net_pnl_krw": 49_000,
                "avg_hold_days": 2.0,
            },
            "time_exit": {
                "trade_count": 1,
                "win_rate_pct": 0.0,
                "net_pnl_krw": -7_000,
                "avg_hold_days": 2.0,
            },
        },
    }


def write_trade_records_jsonl(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(record, ensure_ascii=False, sort_keys=True)
        for record in w3_trade_records()
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
