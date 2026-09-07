"""Backtest signal logger."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from app.auth.account_scope import get_partitioned_log_path
from app.backtest.schema import BacktestSignal
from app.core.time_utils import get_korean_now

if TYPE_CHECKING:
    from app.scanner.service import SymbolAnalysisResult


def _log_path(ts: object = None) -> Path:
    text = str(ts or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        suffix = text[:10].replace("-", "")
    else:
        suffix = get_korean_now().strftime("%Y%m%d")
    return get_partitioned_log_path("backtest_signals", suffix=suffix)


def build_signal_from_result(
    *,
    result: "SymbolAnalysisResult",
    cycle_id: str,
    account: str,
    session: str,
    selection_bucket: str,
    executed: bool = False,
    ts: object = None,
) -> BacktestSignal:
    snapshot = result.market_snapshot
    timestamp = str(ts or get_korean_now().isoformat())
    return BacktestSignal(
        signal_id=f"{cycle_id}:{result.symbol}",
        cycle_id=cycle_id,
        timestamp=timestamp,
        account=account,
        session=session,
        symbol=result.symbol,
        selection_bucket=selection_bucket,
        current_price=int(snapshot.current_price),
        open_price=int(snapshot.open_price),
        low_price=int(snapshot.low_price),
        prev_day_change_pct=float(snapshot.prev_day_change_pct),
        market_data_available=True,
        passed_count=int(result.passed_count),
        strategy_pass_pattern=str(result.passed_pattern or ""),
        passes_profit_buffer=bool(result.passes_profit_buffer),
        net_profit_buffer_bps=float(result.net_profit_buffer_bps),
        score=float(result.score),
        score_components=dict(result.score_components),
        candidate=bool(result.candidate),
        executed=executed,
        rejection_reason=None if result.candidate else (str(result.cost_block_reason or result.final_reason or "") or None),
        source="live_logger",
    )


def append_backtest_signals(
    signals: Iterable[BacktestSignal],
    *,
    ts: object = None,
) -> bool:
    rows = [signal for signal in signals if isinstance(signal, BacktestSignal)]
    if not rows:
        return True
    path = _log_path(ts)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for signal in rows:
                handle.write(signal.to_jsonl_line())
                handle.write("\n")
        return True
    except OSError:
        return False


def load_backtest_signals(path: Path) -> list[BacktestSignal]:
    if not path.exists():
        return []
    signals: list[BacktestSignal] = []
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                signals.append(BacktestSignal.from_dict(payload))
    return signals
