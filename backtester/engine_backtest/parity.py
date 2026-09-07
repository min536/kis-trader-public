"""Parity checks for engine_backtest against historical live-ish logs."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.auth.settings import PROJECT_ROOT
from app.backtest.reconstruct import build_backtest_signals

from backtester.engine_backtest.parity_summary import (
    _bool,
    _build_buy_diagnostics,
    _build_rule_failure_summary,
    _classify_parity,
    _collect_unique_list_values,
    _enrich_engine_summary,
    _float,
    _int,
    _last_dict_value,
    _max_count_entry,
    _normalize_date,
    _overlap,
    _parse_ts,
    _sum_count_dicts,
    _summarize_engine_report,
    _summarize_live_reference,
    _unique_symbols,
)
from backtester.engine_backtest.parity_proxy import (
    _build_provider_from_signal_rows,
    _estimate_prev_close,
    _pick_latest_symbol_rows,
    _resolve_initial_cash,
    _run_proxy_replay,
    _seed_portfolio_from_snapshot,
    _select_seed_snapshot,
)

_DEFAULT_SESSION = "REGULAR"

_KNOWN_APPROXIMATIONS = [
    "engine_backtest v0는 일중 다회 cycle 대신 날짜당 단일 snapshot으로 replay합니다.",
    "daily close/current_price는 해당 날짜의 마지막 관측 price로 근사합니다.",
    "매도 1회 + 매수 1회만 허용하는 일일 루프를 사용합니다.",
    "포지션 수 상한은 exposure 설정으로 역산한 추정치입니다.",
    "proxy replay는 첫 REGULAR snapshot의 보유 포지션/현금을 seed로 사용합니다.",
]

_NOT_YET_MODELED = [
    "live 엔진의 layered universe, shallow/deep 단계 분리, exploration quota",
    "reentry/cooldown/daily pnl brake/same-symbol guard의 full state machine",
    "cycle 단위 throttle, partial completion, API budget/rate-limit side effect",
    "budget rescue/core rescue/quality rebalance 같은 운영 보조 로직",
    "백테스트 종료 강제 청산(eod forced close)을 포함한 다일자 parity 검증",
]


























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


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _candidate_outcomes_path(account: str, date_text: str) -> Path:
    account_path = PROJECT_ROOT / "logs" / f"candidate_outcomes_{account}_{date_text}.jsonl"
    if account_path.exists():
        return account_path
    return PROJECT_ROOT / "logs" / f"candidate_outcomes_{date_text}.jsonl"


def _cycle_stats_path(account: str, date_text: str) -> Path:
    account_path = PROJECT_ROOT / "logs" / f"cycle_stats_{account}_{date_text}.jsonl"
    if account_path.exists():
        return account_path
    return PROJECT_ROOT / "logs" / f"cycle_stats_{date_text}.jsonl"


def _cycle_snapshots_path(account: str) -> Path:
    account_path = PROJECT_ROOT / "data" / f"cycle_snapshots_{account}.jsonl"
    if account_path.exists():
        return account_path
    return PROJECT_ROOT / "data" / "cycle_snapshots.jsonl"


def _orders_path(account: str) -> Path:
    account_path = PROJECT_ROOT / "logs" / f"orders_{account}.jsonl"
    if account_path.exists():
        return account_path
    return PROJECT_ROOT / "logs" / "orders.jsonl"


def _signal_dataset_path(account: str, date_text: str) -> Path | None:
    for suffix in ("csv", "jsonl", "parquet"):
        path = PROJECT_ROOT / "logs" / f"signal_dataset_{account}_{date_text}.{suffix}"
        if path.exists():
            return path
    return None


def _session_matches(row: dict[str, Any], session: str, *, snapshot: bool = False) -> bool:
    if not session:
        return True
    if snapshot:
        found = str(((row.get("market_session") or {}).get("session")) or "").upper()
    else:
        found = str(row.get("session") or "").upper()
    return found == session.upper()


def _filter_snapshots_for_day(
    snapshots: list[dict[str, Any]],
    *,
    iso_date: str,
    session: str,
) -> list[dict[str, Any]]:
    return [
        row
        for row in snapshots
        if str(row.get("timestamp") or row.get("ts") or "").startswith(iso_date)
        and _session_matches(row, session, snapshot=True)
    ]






def _load_signal_rows(
    *,
    account: str,
    date_text: str,
    session: str,
) -> tuple[list[dict[str, Any]], str | None, str]:
    required_market_fields = {"current_price", "open_price", "low_price"}
    signal_path = _signal_dataset_path(account, date_text)
    if signal_path is not None and signal_path.suffix == ".csv":
        rows = [
            row
            for row in _read_csv(signal_path)
            if _session_matches(row, session)
        ]
        if rows and required_market_fields.issubset(rows[0].keys()):
            return rows, str(signal_path), "signal_dataset_csv"
    if signal_path is not None and signal_path.suffix == ".jsonl":
        rows = [
            row
            for row in _read_jsonl(signal_path)
            if _session_matches(row, session)
        ]
        if rows and required_market_fields.issubset(rows[0].keys()):
            return rows, str(signal_path), "signal_dataset_jsonl"

    signals = build_backtest_signals(
        account=account,
        date=date_text,
        session=session,
        deep_eval_only=False,
    )
    rows = [signal.to_dict() for signal in signals]
    return rows, str(signal_path) if signal_path is not None else None, "reconstructed_backtest_signals"






















def build_parity_report(
    *,
    account: str,
    date: str,
    session: str = _DEFAULT_SESSION,
    output_file: str | Path | None = None,
    engine_report: str | Path | None = None,
    initial_cash: int | None = None,
) -> dict[str, Any]:
    date_text, iso_date, trading_date = _normalize_date(date)
    session = str(session or _DEFAULT_SESSION).upper()
    warnings: list[str] = []

    candidate_path = _candidate_outcomes_path(account, date_text)
    stats_path = _cycle_stats_path(account, date_text)
    snapshots_path = _cycle_snapshots_path(account)
    orders_path = _orders_path(account)

    candidate_rows = [
        row
        for row in _read_jsonl(candidate_path)
        if _session_matches(row, session)
    ]
    if not candidate_rows:
        raise FileNotFoundError(
            f"No candidate_outcomes rows found for account={account} date={date_text} session={session}"
        )

    stats_rows = [
        row
        for row in _read_jsonl(stats_path)
        if str(row.get("ts") or "").startswith(iso_date) and _session_matches(row, session)
    ]
    snapshot_rows = _filter_snapshots_for_day(
        _read_jsonl(snapshots_path),
        iso_date=iso_date,
        session=session,
    )
    order_rows = _read_jsonl(orders_path)

    live_summary = _summarize_live_reference(
        account=account,
        date_text=date_text,
        iso_date=iso_date,
        session=session,
        candidate_rows=candidate_rows,
        stats_rows=stats_rows,
        snapshot_rows=snapshot_rows,
        order_rows=order_rows,
    )

    signal_rows, signal_source_path, signal_source_kind = _load_signal_rows(
        account=account,
        date_text=date_text,
        session=session,
    )
    latest_signal_rows = _pick_latest_symbol_rows(signal_rows)
    if not latest_signal_rows:
        raise FileNotFoundError(
            f"No signal rows with market data found for account={account} date={date_text} session={session}"
        )

    if engine_report:
        engine_summary = _summarize_engine_report(report_path=str(engine_report), iso_date=iso_date)
        engine_source: dict[str, Any] = {
            "mode": "engine_report",
            "report_path": str(engine_report),
        }
    else:
        engine_summary = _run_proxy_replay(
            trading_date=trading_date,
            latest_signal_rows=latest_signal_rows,
            snapshot_rows=snapshot_rows,
            initial_cash=initial_cash,
        )
        engine_source = {
            "mode": engine_summary["mode"],
            "signal_source": signal_source_kind,
            "signal_source_path": signal_source_path,
            "selection_basis": "latest_per_symbol_regular_snapshot",
        }
        if signal_source_kind == "reconstructed_backtest_signals":
            if signal_source_path:
                warnings.append(
                    "signal_dataset에 시장 snapshot 필드가 부족해 candidate_outcomes + cycle_snapshots 재구성값을 사용했습니다."
                )
            else:
                warnings.append(
                    "signal_dataset 파일이 없어 candidate_outcomes + cycle_snapshots 재구성값을 사용했습니다."
                )

    engine_summary = _enrich_engine_summary(engine_summary)

    live_symbols = live_summary["day_unique_symbols"]
    buy_final_overlap = _overlap(engine_summary["selected_buy_symbols"], live_symbols["final_candidate"])
    buy_executed_overlap = _overlap(engine_summary["selected_buy_symbols"], live_symbols["successful_buy_orders"])
    sell_overlap = _overlap(engine_summary["selected_sell_symbols"], live_symbols["successful_sell_orders"])

    parity_level, matched_signals, divergence_notes = _classify_parity(
        source_mode=str(engine_source["mode"]),
        buy_executed_overlap=buy_executed_overlap,
        buy_final_overlap=buy_final_overlap,
        sell_overlap=sell_overlap,
        live_sell_reasons=live_summary["sell_reason_distribution"],
        engine_sell_reasons=engine_summary["sell_reason_distribution"],
        warnings=warnings,
    )

    if live_summary["buy_success_count"] > engine_summary["executed_buy_count"]:
        divergence_notes.append(
            f"live buy success={live_summary['buy_success_count']}건, engine_backtest v0는 일일 최대 buy {engine_summary['executed_buy_count']}건으로 비교됩니다."
        )
    if live_summary["sell_success_count"] > engine_summary["executed_sell_count"]:
        divergence_notes.append(
            f"live sell success={live_summary['sell_success_count']}건, engine_backtest v0는 일일 최대 sell {engine_summary['executed_sell_count']}건으로 비교됩니다."
        )

    report = {
        "generated_at": datetime.now().isoformat(),
        "account": account,
        "session": session,
        "parity_level": parity_level,
        "date_range": {
            "start": iso_date,
            "end": iso_date,
        },
        "live_log_source": {
            "candidate_outcomes": str(candidate_path),
            "cycle_stats": str(stats_path),
            "cycle_snapshots": str(snapshots_path),
            "orders": str(orders_path),
            "signal_dataset": signal_source_path,
        },
        "engine_backtest_source": engine_source,
        "summary_counts": {
            "live": live_summary,
            "engine_backtest": engine_summary,
        },
        "overlap_metrics": {
            "buy_selected_vs_live_final_candidate": buy_final_overlap,
            "buy_selected_vs_live_executed_buy": buy_executed_overlap,
            "sell_selected_vs_live_successful_sell": sell_overlap,
        },
        "matched_signals": matched_signals,
        "divergence_notes": divergence_notes,
        "known_approximations": list(_KNOWN_APPROXIMATIONS),
        "not_yet_modeled": list(_NOT_YET_MODELED),
        "warnings": warnings,
    }

    if output_file:
        from backtester.engine_backtest.parity_report import write_parity_outputs

        write_parity_outputs(report, output_file=output_file)

    return report
