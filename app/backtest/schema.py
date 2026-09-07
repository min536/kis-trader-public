"""Canonical schema for a backtestable trade signal."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class BacktestSignal:
    signal_id: str
    cycle_id: str
    timestamp: str
    account: str
    session: str
    symbol: str
    selection_bucket: str
    current_price: int
    open_price: int
    low_price: int
    prev_day_change_pct: float
    market_data_available: bool
    passed_count: int
    strategy_pass_pattern: str
    passes_profit_buffer: bool
    net_profit_buffer_bps: float
    score: float
    score_components: dict[str, float] = field(default_factory=dict)
    candidate: bool = False
    executed: bool = False
    rejection_reason: str | None = None
    exit_price: int | None = None
    exit_timestamp: str | None = None
    exit_reason: str | None = None
    hold_cycles: int | None = None
    gross_pnl_bps: float | None = None
    net_pnl_pct: float | None = None
    source: str = "reconstructed"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_jsonl_line(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)

    @staticmethod
    def from_dict(payload: dict[str, Any]) -> "BacktestSignal":
        known = set(BacktestSignal.__dataclass_fields__)
        return BacktestSignal(**{key: value for key, value in payload.items() if key in known})

    @property
    def has_outcome(self) -> bool:
        return self.exit_price is not None

    @property
    def entry_price(self) -> int:
        return self.current_price
