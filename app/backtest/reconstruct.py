"""Build backtest-ready signals from existing candidate and cycle logs."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any

from app.auth.settings import PROJECT_ROOT
from app.backtest.schema import BacktestSignal

_PROJECT_ROOT = PROJECT_ROOT


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
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
                rows.append(payload)
    return rows


def _candidate_outcomes_path(account: str, date: str) -> Path:
    account_path = _PROJECT_ROOT / "logs" / f"candidate_outcomes_{account}_{date}.jsonl"
    if account_path.exists():
        return account_path
    return _PROJECT_ROOT / "logs" / f"candidate_outcomes_{date}.jsonl"


def _cycle_snapshots_path(account: str) -> Path:
    account_path = _PROJECT_ROOT / "data" / f"cycle_snapshots_{account}.jsonl"
    if account_path.exists():
        return account_path
    return _PROJECT_ROOT / "data" / "cycle_snapshots.jsonl"


def _backtest_signals_path(account: str, date: str) -> Path:
    return _PROJECT_ROOT / "logs" / f"backtest_signals_{account}_{date}.jsonl"


def _parse_timestamp(raw: Any) -> datetime | None:
    ts = str(raw or "").strip()
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _safe_price(value: Any) -> int | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _build_market_data_index(snapshots: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    index: dict[str, dict[str, dict[str, Any]]] = {}
    for snapshot in snapshots:
        cycle_id = str(snapshot.get("cycle_id") or "").strip()
        if not cycle_id:
            continue
        by_symbol = index.setdefault(cycle_id, {})

        selected = snapshot.get("selected_buy_candidate")
        if isinstance(selected, dict):
            symbol = str(selected.get("symbol") or "").strip()
            market_snapshot = selected.get("market_snapshot")
            if symbol and isinstance(market_snapshot, dict):
                by_symbol[symbol] = {
                    "market_snapshot": market_snapshot,
                    "score_components": dict(selected.get("score_components") or {}),
                    "score": selected.get("score"),
                    "net_profit_buffer_bps": selected.get("net_profit_buffer_bps"),
                    "passed_count": selected.get("passed_count"),
                    "passed_pattern": selected.get("passed_pattern"),
                    "passes_profit_buffer": selected.get("passes_profit_buffer"),
                }

        for candidate in snapshot.get("scanner_candidates_top") or []:
            if not isinstance(candidate, dict):
                continue
            symbol = str(candidate.get("symbol") or "").strip()
            market_snapshot = candidate.get("market_snapshot")
            if not symbol or not isinstance(market_snapshot, dict) or symbol in by_symbol:
                continue
            by_symbol[symbol] = {
                "market_snapshot": market_snapshot,
                "score_components": dict(candidate.get("score_components") or {}),
                "score": candidate.get("score"),
                "net_profit_buffer_bps": candidate.get("net_profit_buffer_bps"),
                "passed_count": candidate.get("passed_count"),
                "passed_pattern": candidate.get("passed_pattern"),
                "passes_profit_buffer": candidate.get("passes_profit_buffer"),
            }
    return index


def build_price_observation_index(
    snapshots: list[dict[str, Any]],
) -> dict[str, tuple[tuple[datetime, str, int], ...]]:
    by_symbol: dict[str, dict[str, tuple[datetime, str, int]]] = {}

    def _record(symbol: Any, timestamp: Any, price: Any) -> None:
        parsed_ts = _parse_timestamp(timestamp)
        parsed_price = _safe_price(price)
        normalized_symbol = str(symbol or "").strip()
        if parsed_ts is None or parsed_price is None or not normalized_symbol:
            return
        by_ts = by_symbol.setdefault(normalized_symbol, {})
        raw_ts = str(timestamp or "").strip()
        if raw_ts:
            by_ts.setdefault(raw_ts, (parsed_ts, raw_ts, parsed_price))

    for snapshot in snapshots:
        timestamp = str(snapshot.get("timestamp") or snapshot.get("ts") or "").strip()
        if not timestamp:
            continue

        selected_buy = snapshot.get("selected_buy_candidate")
        if isinstance(selected_buy, dict):
            market_snapshot = selected_buy.get("market_snapshot") or {}
            _record(
                selected_buy.get("symbol"),
                timestamp,
                market_snapshot.get("current_price"),
            )

        for candidate in snapshot.get("scanner_candidates_top") or []:
            if not isinstance(candidate, dict):
                continue
            market_snapshot = candidate.get("market_snapshot") or {}
            _record(
                candidate.get("symbol"),
                timestamp,
                market_snapshot.get("current_price"),
            )

        selected_sell = snapshot.get("selected_sell_candidate")
        if isinstance(selected_sell, dict):
            market_snapshot = selected_sell.get("market_snapshot") or {}
            _record(
                selected_sell.get("symbol"),
                timestamp,
                market_snapshot.get("current_price") or selected_sell.get("current_price"),
            )

    return {
        symbol: tuple(sorted(values.values(), key=lambda item: item[0]))
        for symbol, values in by_symbol.items()
    }


def build_budget_rescue_index(
    snapshots: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for snapshot in snapshots:
        cycle_id = str(snapshot.get("cycle_id") or "").strip()
        selected_buy = snapshot.get("selected_buy_candidate")
        buy_position_sizing = snapshot.get("buy_position_sizing")
        if not cycle_id or not isinstance(selected_buy, dict) or not isinstance(buy_position_sizing, dict):
            continue

        symbol = str(selected_buy.get("symbol") or "").strip()
        if not symbol:
            continue

        details = buy_position_sizing.get("details")
        details = details if isinstance(details, dict) else {}

        def _pick(key: str) -> Any:
            if key in details:
                return details.get(key)
            return buy_position_sizing.get(key)

        index[(cycle_id, symbol)] = {
            "budget_rescue_enabled": _pick("budget_rescue_enabled"),
            "budget_rescue_applied": _pick("budget_rescue_applied"),
            "budget_rescue_qty": _pick("budget_rescue_qty"),
            "budget_rescue_reason": _pick("budget_rescue_reason"),
            "budget_rescue_triggered_from_block_reason": _pick(
                "budget_rescue_triggered_from_block_reason"
            ),
        }
    return index


def build_core_rescue_index(
    snapshots: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for snapshot in snapshots:
        cycle_id = str(snapshot.get("cycle_id") or "").strip()
        if not cycle_id:
            continue

        staged = ((snapshot.get("selection_details") or {}).get("staged_scan") or {})
        if not isinstance(staged, dict):
            continue
        if not staged.get("core_rescue_applied"):
            continue

        selected_symbol = str(staged.get("core_rescue_selected_symbol") or "").strip()
        if not selected_symbol:
            continue

        index[(cycle_id, selected_symbol)] = {
            "core_rescue_applied": staged.get("core_rescue_applied"),
            "core_rescue_selected_symbol": selected_symbol,
            "core_rescue_selected_score": staged.get("core_rescue_selected_score"),
            "core_rescue_replaced_symbol": staged.get("core_rescue_replaced_symbol"),
            "core_rescue_reason": staged.get("core_rescue_reason"),
        }
    return index


def enrich_signal_rows_with_post_entry_outcomes(
    signal_rows: list[dict[str, Any]],
    cycle_snapshots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    market_index = _build_market_data_index(cycle_snapshots)
    observation_index = build_price_observation_index(cycle_snapshots)
    budget_rescue_index = build_budget_rescue_index(cycle_snapshots)
    core_rescue_index = build_core_rescue_index(cycle_snapshots)

    def _first_observation_on_or_after(
        series: tuple[tuple[datetime, str, int], ...],
        *,
        threshold: datetime,
        same_day_only: bool,
    ) -> tuple[str | None, int | None]:
        for observed_at, raw_ts, price in series:
            if same_day_only and observed_at.date() != threshold.date():
                continue
            if observed_at >= threshold:
                return raw_ts, price
        return None, None

    def _last_same_day_observation_after(
        series: tuple[tuple[datetime, str, int], ...],
        *,
        entry_dt: datetime,
    ) -> tuple[str | None, int | None]:
        observed_ts: str | None = None
        observed_price: int | None = None
        for observed_at, raw_ts, price in series:
            if observed_at.date() != entry_dt.date():
                continue
            if observed_at <= entry_dt:
                continue
            observed_ts = raw_ts
            observed_price = price
        return observed_ts, observed_price

    def _ret_bps(entry_price: int | None, future_price: int | None) -> float | None:
        if entry_price is None or future_price is None or entry_price <= 0:
            return None
        return round((future_price - entry_price) / entry_price * 10_000, 2)

    enriched_rows: list[dict[str, Any]] = []
    for row in signal_rows:
        updated = dict(row)
        cycle_id = str(row.get("cycle_id") or "").strip()
        symbol = str(row.get("symbol") or "").strip()
        entry_ts = str(row.get("ts") or row.get("timestamp") or "").strip()
        entry_dt = _parse_timestamp(entry_ts)
        budget_rescue_info = budget_rescue_index.get((cycle_id, symbol)) or {}
        core_rescue_info = core_rescue_index.get((cycle_id, symbol)) or {}
        if not cycle_id or not symbol or entry_dt is None:
            updated.update(
                {
                    "budget_rescue_enabled": budget_rescue_info.get("budget_rescue_enabled"),
                    "budget_rescue_applied": budget_rescue_info.get("budget_rescue_applied"),
                    "budget_rescue_qty": budget_rescue_info.get("budget_rescue_qty"),
                    "budget_rescue_reason": budget_rescue_info.get("budget_rescue_reason"),
                    "budget_rescue_triggered_from_block_reason": budget_rescue_info.get(
                        "budget_rescue_triggered_from_block_reason"
                    ),
                    "core_rescue_applied": core_rescue_info.get("core_rescue_applied"),
                    "core_rescue_selected_symbol": core_rescue_info.get("core_rescue_selected_symbol"),
                    "core_rescue_selected_score": core_rescue_info.get("core_rescue_selected_score"),
                    "core_rescue_replaced_symbol": core_rescue_info.get("core_rescue_replaced_symbol"),
                    "core_rescue_reason": core_rescue_info.get("core_rescue_reason"),
                    "entry_ts": entry_ts or None,
                    "entry_price": None,
                    "obs_ts_5m": None,
                    "price_5m": None,
                    "ret_5m_bps": None,
                    "obs_ts_30m": None,
                    "price_30m": None,
                    "ret_30m_bps": None,
                    "obs_ts_eod": None,
                    "price_eod": None,
                    "ret_eod_bps": None,
                }
            )
            enriched_rows.append(updated)
            continue

        snapshot_info = market_index.get(cycle_id, {}).get(symbol) or {}
        market_snapshot = snapshot_info.get("market_snapshot") or {}
        entry_price = _safe_price(market_snapshot.get("current_price"))
        series = observation_index.get(symbol, ())

        if entry_price is None:
            updated.update(
                {
                    "budget_rescue_enabled": budget_rescue_info.get("budget_rescue_enabled"),
                    "budget_rescue_applied": budget_rescue_info.get("budget_rescue_applied"),
                    "budget_rescue_qty": budget_rescue_info.get("budget_rescue_qty"),
                    "budget_rescue_reason": budget_rescue_info.get("budget_rescue_reason"),
                    "budget_rescue_triggered_from_block_reason": budget_rescue_info.get(
                        "budget_rescue_triggered_from_block_reason"
                    ),
                    "core_rescue_applied": core_rescue_info.get("core_rescue_applied"),
                    "core_rescue_selected_symbol": core_rescue_info.get("core_rescue_selected_symbol"),
                    "core_rescue_selected_score": core_rescue_info.get("core_rescue_selected_score"),
                    "core_rescue_replaced_symbol": core_rescue_info.get("core_rescue_replaced_symbol"),
                    "core_rescue_reason": core_rescue_info.get("core_rescue_reason"),
                    "entry_ts": entry_ts,
                    "entry_price": None,
                    "obs_ts_5m": None,
                    "price_5m": None,
                    "ret_5m_bps": None,
                    "obs_ts_30m": None,
                    "price_30m": None,
                    "ret_30m_bps": None,
                    "obs_ts_eod": None,
                    "price_eod": None,
                    "ret_eod_bps": None,
                }
            )
            enriched_rows.append(updated)
            continue

        obs_ts_5m, price_5m = _first_observation_on_or_after(
            series,
            threshold=entry_dt + timedelta(minutes=5),
            same_day_only=True,
        )
        obs_ts_30m, price_30m = _first_observation_on_or_after(
            series,
            threshold=entry_dt + timedelta(minutes=30),
            same_day_only=True,
        )
        obs_ts_eod, price_eod = _last_same_day_observation_after(
            series,
            entry_dt=entry_dt,
        )

        updated.update(
            {
                "budget_rescue_enabled": budget_rescue_info.get("budget_rescue_enabled"),
                "budget_rescue_applied": budget_rescue_info.get("budget_rescue_applied"),
                "budget_rescue_qty": budget_rescue_info.get("budget_rescue_qty"),
                "budget_rescue_reason": budget_rescue_info.get("budget_rescue_reason"),
                "budget_rescue_triggered_from_block_reason": budget_rescue_info.get(
                    "budget_rescue_triggered_from_block_reason"
                ),
                "core_rescue_applied": core_rescue_info.get("core_rescue_applied"),
                "core_rescue_selected_symbol": core_rescue_info.get("core_rescue_selected_symbol"),
                "core_rescue_selected_score": core_rescue_info.get("core_rescue_selected_score"),
                "core_rescue_replaced_symbol": core_rescue_info.get("core_rescue_replaced_symbol"),
                "core_rescue_reason": core_rescue_info.get("core_rescue_reason"),
                "entry_ts": entry_ts,
                "entry_price": entry_price,
                "obs_ts_5m": obs_ts_5m,
                "price_5m": price_5m,
                "ret_5m_bps": _ret_bps(entry_price, price_5m),
                "obs_ts_30m": obs_ts_30m,
                "price_30m": price_30m,
                "ret_30m_bps": _ret_bps(entry_price, price_30m),
                "obs_ts_eod": obs_ts_eod,
                "price_eod": price_eod,
                "ret_eod_bps": _ret_bps(entry_price, price_eod),
            }
        )
        enriched_rows.append(updated)

    return enriched_rows


def _build_exit_index(snapshots: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    exits: dict[str, dict[str, Any]] = {}
    for snapshot in sorted(snapshots, key=lambda item: str(item.get("timestamp") or "")):
        timestamp = str(snapshot.get("timestamp") or "").strip()

        exit_price_map = snapshot.get("last_exit_price_by_symbol")
        exit_at_map = snapshot.get("last_exit_at_by_symbol")
        exit_reason_map = snapshot.get("last_exit_reason_by_symbol_runtime") or snapshot.get("last_exit_reason_by_symbol")
        if isinstance(exit_price_map, dict):
            for symbol, price in exit_price_map.items():
                if not symbol or price is None:
                    continue
                exits[str(symbol)] = {
                    "exit_price": int(price),
                    "exit_timestamp": str((exit_at_map or {}).get(symbol) or timestamp).strip(),
                    "exit_reason": str((exit_reason_map or {}).get(symbol) or "").strip() or None,
                    "net_pnl_pct": None,
                }

        sell = snapshot.get("selected_sell_candidate")
        if isinstance(sell, dict):
            symbol = str(sell.get("symbol") or "").strip()
            price = sell.get("current_price")
            if symbol and price is not None:
                exits[symbol] = {
                    "exit_price": int(price),
                    "exit_timestamp": timestamp,
                    "exit_reason": str(sell.get("triggered_rule_name") or sell.get("reason") or "").strip() or None,
                    "net_pnl_pct": (
                        float(sell.get("net_pnl_pct"))
                        if sell.get("net_pnl_pct") is not None
                        else None
                    ),
                }
    return exits


def _gross_pnl_bps(entry_price: int, exit_price: int) -> float | None:
    if not entry_price or not exit_price:
        return None
    return round((exit_price - entry_price) / entry_price * 10_000, 2)


def build_backtest_signals(
    *,
    account: str,
    date: str,
    session: str = "",
    deep_eval_only: bool = True,
) -> list[BacktestSignal]:
    outcome_rows = _read_jsonl(_candidate_outcomes_path(account, date))
    cycle_snapshots = _read_jsonl(_cycle_snapshots_path(account))

    date_prefix = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    cycle_snapshots_for_date = [
        snapshot
        for snapshot in cycle_snapshots
        if str(snapshot.get("timestamp") or "").startswith(date_prefix)
    ]

    market_index = _build_market_data_index(cycle_snapshots_for_date)
    exit_index = _build_exit_index(cycle_snapshots)

    signals: list[BacktestSignal] = []
    for row in outcome_rows:
        if session and str(row.get("session") or "").upper() != session.upper():
            continue
        if deep_eval_only and not row.get("deep_evaluated"):
            continue

        symbol = str(row.get("symbol") or "").strip()
        cycle_id = str(row.get("cycle_id") or "").strip()
        timestamp = str(row.get("ts") or row.get("timestamp") or "").strip()
        if not symbol or not cycle_id:
            continue

        snapshot_info = market_index.get(cycle_id, {}).get(symbol)
        market_data_available = snapshot_info is not None
        market_snapshot = (snapshot_info or {}).get("market_snapshot") or {}

        current_price = int(market_snapshot.get("current_price") or 0)
        open_price = int(market_snapshot.get("open_price") or 0)
        low_price = int(market_snapshot.get("low_price") or 0)
        prev_day_change_pct = float(market_snapshot.get("prev_day_change_pct") or 0.0)

        score = float((snapshot_info or {}).get("score") or row.get("score_deep") or 0.0)
        score_components = dict((snapshot_info or {}).get("score_components") or {})
        passed_count = int((snapshot_info or {}).get("passed_count") or row.get("passed_count_deep") or 0)
        strategy_pass_pattern = str(
            (snapshot_info or {}).get("passed_pattern")
            or row.get("strategy_pass_pattern")
            or ""
        )
        if snapshot_info is not None and (snapshot_info or {}).get("passes_profit_buffer") is not None:
            passes_profit_buffer = bool((snapshot_info or {}).get("passes_profit_buffer"))
        else:
            passes_profit_buffer = bool(row.get("passes_profit_buffer") or False)
        net_profit_buffer_bps = float(
            (snapshot_info or {}).get("net_profit_buffer_bps")
            or row.get("net_profit_buffer_bps")
            or 0.0
        )

        candidate = bool(row.get("final_candidate"))
        executed = bool(row.get("executed"))
        rejection_reason = row.get("rejection_reason") or None

        exit_info = exit_index.get(symbol) if executed else None
        exit_price = exit_info.get("exit_price") if exit_info else None
        exit_timestamp = exit_info.get("exit_timestamp") if exit_info else None
        exit_reason = exit_info.get("exit_reason") if exit_info else None
        net_pnl_pct = exit_info.get("net_pnl_pct") if exit_info else None
        gross_pnl_bps = _gross_pnl_bps(current_price, exit_price) if exit_price and current_price else None

        signals.append(
            BacktestSignal(
                signal_id=f"{cycle_id}:{symbol}",
                cycle_id=cycle_id,
                timestamp=timestamp,
                account=account,
                session=str(row.get("session") or "").strip(),
                symbol=symbol,
                selection_bucket=str(row.get("selection_bucket") or "").strip(),
                current_price=current_price,
                open_price=open_price,
                low_price=low_price,
                prev_day_change_pct=prev_day_change_pct,
                market_data_available=market_data_available,
                passed_count=passed_count,
                strategy_pass_pattern=strategy_pass_pattern,
                passes_profit_buffer=passes_profit_buffer,
                net_profit_buffer_bps=net_profit_buffer_bps,
                score=score,
                score_components=score_components,
                candidate=candidate,
                executed=executed,
                rejection_reason=rejection_reason,
                exit_price=exit_price,
                exit_timestamp=exit_timestamp,
                exit_reason=exit_reason,
                gross_pnl_bps=gross_pnl_bps,
                net_pnl_pct=net_pnl_pct,
                source="reconstructed",
            )
        )

    signals.sort(key=lambda signal: signal.timestamp)
    return signals


def write_backtest_signals(
    signals: list[BacktestSignal],
    *,
    account: str,
    date: str,
) -> Path:
    out_path = _backtest_signals_path(account, date)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for signal in signals:
            handle.write(signal.to_jsonl_line())
            handle.write("\n")
    return out_path


def summarize_signals(signals: list[BacktestSignal]) -> dict[str, Any]:
    total = len(signals)
    with_market_data = sum(1 for signal in signals if signal.market_data_available)
    candidates = sum(1 for signal in signals if signal.candidate)
    executed = sum(1 for signal in signals if signal.executed)
    with_exit = sum(1 for signal in signals if signal.has_outcome)
    by_bucket: dict[str, int] = {}
    for signal in signals:
        by_bucket[signal.selection_bucket] = by_bucket.get(signal.selection_bucket, 0) + 1
    return {
        "total_signals": total,
        "market_data_available": with_market_data,
        "market_data_coverage_pct": round(with_market_data / total * 100, 1) if total else 0.0,
        "candidates": candidates,
        "executed": executed,
        "with_exit_outcome": with_exit,
        "by_bucket": by_bucket,
    }
