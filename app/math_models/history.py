from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.auth.account_scope import get_cycle_snapshots_path
from app.core.jsonl import SNAPSHOT_READ_LINE_MAX_BYTES, read_jsonl_objects

_OBSERVATION_SOURCE_PRIORITY = {
    "holding": 0,
    "selection_candidate": 1,
    "observed_cycle": 2,
    "scanner_top": 3,
    "selected_buy": 4,
}


def _file_marker(path: Path) -> int:
    if not path.exists():
        return 0
    stat = path.stat()
    return int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)))


def _cycle_snapshots_file() -> Path:
    return get_cycle_snapshots_path()


def _build_symbol_histories_from_records(
    records: list[dict[str, Any]],
    limit: int,
) -> dict[str, list[dict[str, Any]]]:
    histories: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)

    def _store_observation(
        *,
        symbol: str,
        timestamp: str,
        price: int | None,
        open_price: int | None,
        low_price: int | None,
        prev_day_change_pct: float | None,
        source: str,
    ) -> None:
        normalized_symbol = str(symbol or "").strip()
        if not normalized_symbol or not timestamp or price is None:
            return
        priority = int(_OBSERVATION_SOURCE_PRIORITY.get(source, 0))
        existing = histories[normalized_symbol].get(timestamp)
        if existing is not None and int(existing.get("_priority", -1)) > priority:
            return
        histories[normalized_symbol][timestamp] = {
            "timestamp": timestamp,
            "price": int(price),
            "open_price": int(open_price) if open_price is not None else None,
            "low_price": int(low_price) if low_price is not None else None,
            "prev_day_change_pct": (
                float(prev_day_change_pct) if prev_day_change_pct is not None else None
            ),
            "source": source,
            "_priority": priority,
        }

    for record in records[-max(limit, 1):]:
        timestamp = str(record.get("timestamp", "")).strip()
        if not timestamp:
            continue

        selected_buy = record.get("selected_buy_candidate")
        selected_buy = selected_buy if isinstance(selected_buy, dict) else {}
        selected_snapshot = selected_buy.get("market_snapshot")
        selected_snapshot = selected_snapshot if isinstance(selected_snapshot, dict) else {}
        selected_symbol = str(selected_buy.get("symbol", "") or selected_snapshot.get("symbol", "")).strip()
        selected_price = selected_snapshot.get("current_price")
        _store_observation(
            symbol=selected_symbol,
            timestamp=timestamp,
            price=(int(selected_price) if selected_price is not None else None),
            open_price=(
                int(selected_snapshot.get("open_price", 0) or 0)
                if selected_snapshot.get("open_price") is not None
                else None
            ),
            low_price=(
                int(selected_snapshot.get("low_price", 0) or 0)
                if selected_snapshot.get("low_price") is not None
                else None
            ),
            prev_day_change_pct=(
                float(selected_snapshot.get("prev_day_change_pct", 0.0) or 0.0)
                if selected_snapshot.get("prev_day_change_pct") is not None
                else None
            ),
            source="selected_buy",
        )

        scanner_candidates = record.get("scanner_candidates_top")
        scanner_candidates = scanner_candidates if isinstance(scanner_candidates, list) else []
        for candidate in scanner_candidates:
            if not isinstance(candidate, dict):
                continue
            snapshot = candidate.get("market_snapshot")
            snapshot = snapshot if isinstance(snapshot, dict) else {}
            symbol = str(candidate.get("symbol", "") or snapshot.get("symbol", "")).strip()
            price = snapshot.get("current_price")
            _store_observation(
                symbol=symbol,
                timestamp=timestamp,
                price=(int(price) if price is not None else None),
                open_price=(
                    int(snapshot.get("open_price", 0) or 0)
                    if snapshot.get("open_price") is not None
                    else None
                ),
                low_price=(
                    int(snapshot.get("low_price", 0) or 0)
                    if snapshot.get("low_price") is not None
                    else None
                ),
                prev_day_change_pct=(
                    float(snapshot.get("prev_day_change_pct", 0.0) or 0.0)
                    if snapshot.get("prev_day_change_pct") is not None
                    else None
                ),
                source="scanner_top",
            )

        selection_details = record.get("selection_details")
        selection_details = selection_details if isinstance(selection_details, dict) else {}
        candidates = selection_details.get("candidates")
        candidates = candidates if isinstance(candidates, list) else []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            snapshot = candidate.get("market_snapshot")
            snapshot = snapshot if isinstance(snapshot, dict) else {}
            symbol = str(candidate.get("symbol", "") or snapshot.get("symbol", "")).strip()
            price = snapshot.get("current_price")
            _store_observation(
                symbol=symbol,
                timestamp=timestamp,
                price=(int(price) if price is not None else None),
                open_price=(
                    int(snapshot.get("open_price", 0) or 0)
                    if snapshot.get("open_price") is not None
                    else None
                ),
                low_price=(
                    int(snapshot.get("low_price", 0) or 0)
                    if snapshot.get("low_price") is not None
                    else None
                ),
                prev_day_change_pct=(
                    float(snapshot.get("prev_day_change_pct", 0.0) or 0.0)
                    if snapshot.get("prev_day_change_pct") is not None
                    else None
                ),
                source="selection_candidate",
            )

        holdings_summary = record.get("holdings_summary")
        holdings_summary = holdings_summary if isinstance(holdings_summary, dict) else {}
        positions = holdings_summary.get("positions")
        positions = positions if isinstance(positions, list) else []
        for position in positions:
            if not isinstance(position, dict):
                continue
            symbol = str(position.get("symbol", "")).strip()
            price = position.get("current_price")
            _store_observation(
                symbol=symbol,
                timestamp=timestamp,
                price=(int(price) if price is not None else None),
                open_price=None,
                low_price=None,
                prev_day_change_pct=None,
                source="holding",
            )

        observed_market_snapshots = record.get("observed_market_snapshots")
        observed_market_snapshots = (
            observed_market_snapshots if isinstance(observed_market_snapshots, list) else []
        )
        for snapshot in observed_market_snapshots:
            if not isinstance(snapshot, dict):
                continue
            symbol = str(snapshot.get("symbol", "")).strip()
            price = snapshot.get("current_price")
            _store_observation(
                symbol=symbol,
                timestamp=timestamp,
                price=(int(price) if price is not None else None),
                open_price=(
                    int(snapshot.get("open_price", 0) or 0)
                    if snapshot.get("open_price") is not None
                    else None
                ),
                low_price=(
                    int(snapshot.get("low_price", 0) or 0)
                    if snapshot.get("low_price") is not None
                    else None
                ),
                prev_day_change_pct=(
                    float(snapshot.get("prev_day_change_pct", 0.0) or 0.0)
                    if snapshot.get("prev_day_change_pct") is not None
                    else None
                ),
                source="observed_cycle",
            )

    ordered_histories: dict[str, list[dict[str, Any]]] = {}
    for symbol, observations_by_ts in histories.items():
        ordered_histories[symbol] = [
            {
                key: value
                for key, value in item.items()
                if key != "_priority"
            }
            for item in sorted(
                observations_by_ts.values(),
                key=lambda entry: str(entry.get("timestamp", "")),
            )[-limit:]
        ]
    return ordered_histories


@lru_cache(maxsize=8)
def _build_symbol_histories(limit: int, file_marker: int) -> dict[str, list[dict[str, Any]]]:
    cycle_snapshots_file = _cycle_snapshots_file()
    if not cycle_snapshots_file.exists():
        return {}

    records, _errors = read_jsonl_objects(
        cycle_snapshots_file, max_line_bytes=SNAPSHOT_READ_LINE_MAX_BYTES
    )
    return _build_symbol_histories_from_records(records, limit)


def get_recent_symbol_price_history(symbol: str, *, limit: int = 60) -> list[dict[str, Any]]:
    symbol = str(symbol or "").strip()
    if not symbol:
        return []
    cycle_snapshots_file = _cycle_snapshots_file()
    histories = _build_symbol_histories(
        limit=max(limit, 1),
        file_marker=_file_marker(cycle_snapshots_file),
    )
    return list(histories.get(symbol, []))


def append_current_snapshot_observation(
    history: list[dict[str, Any]],
    *,
    symbol: str,
    snapshot: object | None,
) -> list[dict[str, Any]]:
    normalized_symbol = str(symbol or "").strip()
    snapshot_symbol = str(getattr(snapshot, "symbol", "") or "").strip()
    if not normalized_symbol or snapshot is None or snapshot_symbol != normalized_symbol:
        return list(history)
    try:
        current_price = int(getattr(snapshot, "current_price", 0) or 0)
    except (TypeError, ValueError):
        current_price = 0
    if current_price <= 0:
        return list(history)

    rows = list(history)
    if rows and str(rows[-1].get("source") or "") == "current_cycle":
        rows = rows[:-1]
    rows.append(
        {
            "timestamp": "__current_cycle__",
            "price": current_price,
            "open_price": int(getattr(snapshot, "open_price", 0) or 0),
            "low_price": int(getattr(snapshot, "low_price", 0) or 0),
            "prev_day_change_pct": float(
                getattr(snapshot, "prev_day_change_pct", 0.0) or 0.0
            ),
            "source": "current_cycle",
        }
    )
    return rows
