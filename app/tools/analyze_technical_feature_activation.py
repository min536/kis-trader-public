"""CLI: analyze_technical_feature_activation

Diagnose whether technical overlay features are actually activating for
deep-evaluated buy rows, or staying near zero due to insufficient history.

Usage:
    python3 -m app.tools.analyze_technical_feature_activation --date 20260403 --account mock_12345678_01
    python3 -m app.tools.analyze_technical_feature_activation --date 20260403 --account mock_12345678_01 --session REGULAR
    python3 -m app.tools.analyze_technical_feature_activation --date 20260403 --account mock_12345678_01 --session REGULAR --last-n-cycles 5
    python3 -m app.tools.analyze_technical_feature_activation --date 20260403 --account mock_12345678_01 --json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.core.jsonl import read_jsonl_objects
from app.math_models.technical import (
    FULL_TECHNICAL_HISTORY_OBSERVATIONS,
    MIN_TECHNICAL_HISTORY_OBSERVATIONS,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
KNOWN_BUCKETS = ("core", "rotating", "exploration")
DEEP_EVAL_STAGES = {"deep_eval", "final_candidate", "executed"}

_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _c(color: str, text: str) -> str:
    return f"{color}{text}{_RESET}"


def _ok(text: str) -> str:
    return _c(_GREEN, text)


def _bad(text: str) -> str:
    return _c(_RED, text)


def _warn(text: str) -> str:
    return _c(_YELLOW, text)


def _header(text: str) -> str:
    return _c(_BOLD + _CYAN, text)


def _dim(text: str) -> str:
    return _c(_DIM, text)


def _candidate_log_path(account: str, date: str) -> Path:
    return PROJECT_ROOT / "logs" / f"candidate_outcomes_{account}_{date}.jsonl"


def _cycle_stats_path(account: str, date: str) -> Path:
    return PROJECT_ROOT / "logs" / f"cycle_stats_{account}_{date}.jsonl"


def _cycle_snapshots_path(account: str) -> Path:
    return PROJECT_ROOT / "data" / f"cycle_snapshots_{account}.jsonl"


def _load_rows(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    return read_jsonl_objects(path)


def _filter_session(rows: list[dict[str, Any]], session: str) -> list[dict[str, Any]]:
    if not session:
        return rows
    target = session.upper()
    return [row for row in rows if str(row.get("session") or "").upper() == target]


def _recent_cycle_ids(rows: list[dict[str, Any]], n: int) -> list[str]:
    if n <= 0:
        return []
    cycle_ids: list[str] = []
    seen: set[str] = set()
    for row in reversed(rows):
        cycle_id = str(row.get("cycle_id") or "").strip()
        if not cycle_id or cycle_id in seen:
            continue
        seen.add(cycle_id)
        cycle_ids.append(cycle_id)
        if len(cycle_ids) == n:
            break
    cycle_ids.reverse()
    return cycle_ids


def _filter_cycle_ids(rows: list[dict[str, Any]], cycle_ids: set[str]) -> list[dict[str, Any]]:
    if not cycle_ids:
        return rows
    return [row for row in rows if str(row.get("cycle_id") or "").strip() in cycle_ids]


def _filter_bucket(rows: list[dict[str, Any]], bucket: str) -> list[dict[str, Any]]:
    if not bucket:
        return rows
    return [row for row in rows if str(row.get("selection_bucket") or "") == bucket]


def _is_deep_eval_row(row: dict[str, Any]) -> bool:
    stage = str(row.get("stage_reached") or "").strip()
    if stage in DEEP_EVAL_STAGES:
        return True
    if row.get("deep_evaluated") is True:
        return True
    return row.get("score_deep") is not None


def _calc_ema(prices: list[float], window: int) -> list[float]:
    if not prices or window <= 0:
        return []
    alpha = 2.0 / (window + 1.0)
    values = [prices[0]]
    for price in prices[1:]:
        values.append(price * alpha + values[-1] * (1.0 - alpha))
    return values


def _compute_technical_metrics(prices: list[float]) -> dict[str, Any]:
    observation_count = len(prices)
    if observation_count < MIN_TECHNICAL_HISTORY_OBSERVATIONS:
        return {
            "observation_count": observation_count,
            "history_usable": False,
            "history_full_confidence": False,
            "trend_alignment_score": 0.0,
            "macd_momentum_score": 0.0,
            "technical_total_score": 0.0,
        }

    ema9 = _calc_ema(prices, 9)
    ema21 = _calc_ema(prices, 21)
    current_price = prices[-1]
    trend_score = 0.0
    if current_price > ema21[-1]:
        trend_score += 0.10
    if ema9[-1] > ema21[-1]:
        trend_score += 0.15
    if len(ema21) > 1 and ema21[-1] > ema21[-2]:
        trend_score += 0.10

    ema12 = _calc_ema(prices, 12)
    ema26 = _calc_ema(prices, 26)
    macd_line = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
    signal_line = _calc_ema(macd_line, 9)
    current_hist = macd_line[-1] - signal_line[-1]
    prev_hist = (macd_line[-2] - signal_line[-2]) if len(macd_line) > 1 else 0.0

    macd_score = 0.0
    if current_hist > 0:
        macd_score += 0.10
        if current_hist > prev_hist:
            macd_score += 0.10
    elif current_hist < 0 and current_hist > prev_hist:
        macd_score += 0.05

    confidence = min(
        max(float(observation_count), float(MIN_TECHNICAL_HISTORY_OBSERVATIONS))
        / float(FULL_TECHNICAL_HISTORY_OBSERVATIONS),
        1.0,
    )
    trend_score *= confidence
    macd_score *= confidence

    trend_score = round(trend_score, 2)
    macd_score = round(macd_score, 2)
    return {
        "observation_count": observation_count,
        "history_usable": True,
        "history_full_confidence": observation_count >= FULL_TECHNICAL_HISTORY_OBSERVATIONS,
        "trend_alignment_score": trend_score,
        "macd_momentum_score": macd_score,
        "technical_total_score": round(trend_score + macd_score, 2),
    }


def _extract_observations(record: dict[str, Any]) -> list[dict[str, Any]]:
    timestamp = str(record.get("timestamp") or "").strip()
    if not timestamp:
        return []

    observations: list[dict[str, Any]] = []

    selected_buy = record.get("selected_buy_candidate")
    selected_buy = selected_buy if isinstance(selected_buy, dict) else {}
    selected_snapshot = selected_buy.get("market_snapshot")
    selected_snapshot = selected_snapshot if isinstance(selected_snapshot, dict) else {}
    selected_symbol = str(
        selected_buy.get("symbol", "") or selected_snapshot.get("symbol", "")
    ).strip()
    selected_price = selected_snapshot.get("current_price")
    if selected_symbol and selected_price is not None:
        observations.append(
            {
                "symbol": selected_symbol,
                "timestamp": timestamp,
                "price": int(selected_price),
                "source": "selected_buy",
            }
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
        if symbol and price is not None:
            observations.append(
                {
                    "symbol": symbol,
                    "timestamp": timestamp,
                    "price": int(price),
                    "source": "scanner_top",
                }
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
        if symbol and price is not None:
            observations.append(
                {
                    "symbol": symbol,
                    "timestamp": timestamp,
                    "price": int(price),
                    "source": "selection_candidate",
                }
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
        if symbol and price is not None:
            observations.append(
                {
                    "symbol": symbol,
                    "timestamp": timestamp,
                    "price": int(price),
                    "source": "holding",
                }
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
        if symbol and price is not None:
            observations.append(
                {
                    "symbol": symbol,
                    "timestamp": timestamp,
                    "price": int(price),
                    "source": "observed_cycle",
                }
            )

    return observations


def _safe_mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def _safe_median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def _history_bucket(observation_count: int) -> str:
    if observation_count <= 0:
        return "0"
    if observation_count <= 5:
        return "1-5"
    if observation_count <= 10:
        return "6-10"
    if observation_count <= 25:
        return "11-25"
    return "26+"


def _sample_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "ts": row.get("ts"),
        "cycle_id": row.get("cycle_id"),
        "symbol": row.get("symbol"),
        "symbol_name": row.get("symbol_name"),
        "bucket": row.get("selection_bucket"),
        "stage_reached": row.get("stage_reached"),
        "score_deep": row.get("score_deep"),
        "observation_count": row.get("observation_count"),
        "trend_alignment_score": row.get("trend_alignment_score"),
        "macd_momentum_score": row.get("macd_momentum_score"),
        "technical_total_score": row.get("technical_total_score"),
    }


def run_analysis(
    *,
    account: str,
    date: str,
    session: str,
    bucket: str,
    last_n: int,
) -> dict[str, Any]:
    candidate_path = _candidate_log_path(account, date)
    cycle_stats_path = _cycle_stats_path(account, date)
    snapshots_path = _cycle_snapshots_path(account)

    all_candidate_rows, candidate_errors = _load_rows(candidate_path)
    all_cycle_stats_rows, cycle_stats_errors = _load_rows(cycle_stats_path)
    all_snapshot_rows, snapshot_errors = _load_rows(snapshots_path)

    candidate_rows = _filter_session(all_candidate_rows, session)
    cycle_stats_rows = _filter_session(all_cycle_stats_rows, session)

    selected_cycle_ids: list[str] = []
    if last_n > 0:
        selected_cycle_ids = _recent_cycle_ids(cycle_stats_rows, last_n)
        if not selected_cycle_ids:
            selected_cycle_ids = _recent_cycle_ids(candidate_rows, last_n)
        keep_cycle_ids = set(selected_cycle_ids)
        candidate_rows = _filter_cycle_ids(candidate_rows, keep_cycle_ids)
        cycle_stats_rows = _filter_cycle_ids(cycle_stats_rows, keep_cycle_ids)

    candidate_rows = _filter_bucket(candidate_rows, bucket)
    deep_rows = [row for row in candidate_rows if _is_deep_eval_row(row)]

    rows_by_cycle_symbol: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    symbols_by_cycle: dict[str, set[str]] = defaultdict(set)
    for row in deep_rows:
        cycle_id = str(row.get("cycle_id") or "").strip()
        symbol = str(row.get("symbol") or "").strip()
        if not cycle_id or not symbol:
            continue
        rows_by_cycle_symbol[(cycle_id, symbol)].append(row)
        symbols_by_cycle[cycle_id].add(symbol)

    history_by_symbol: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    analyzed_rows: list[dict[str, Any]] = []
    seen_cycles: set[str] = set()

    sorted_snapshot_rows = sorted(
        (row for row in all_snapshot_rows if isinstance(row, dict)),
        key=lambda row: str(row.get("timestamp") or ""),
    )

    for snapshot_row in sorted_snapshot_rows:
        cycle_id = str(snapshot_row.get("cycle_id") or "").strip()
        if cycle_id and cycle_id in symbols_by_cycle:
            seen_cycles.add(cycle_id)
            for symbol in sorted(symbols_by_cycle[cycle_id]):
                observations = history_by_symbol.get(symbol) or {}
                prices = [
                    float(item.get("price", 0) or 0)
                    for _, item in sorted(observations.items(), key=lambda pair: pair[0])
                    if float(item.get("price", 0) or 0) > 0
                ]
                metrics = _compute_technical_metrics(prices)
                for row in rows_by_cycle_symbol.get((cycle_id, symbol), []):
                    analyzed_rows.append(
                        {
                            **row,
                            **metrics,
                        }
                    )

        for observation in _extract_observations(snapshot_row):
            symbol = str(observation.get("symbol") or "").strip()
            timestamp = str(observation.get("timestamp") or "").strip()
            if not symbol or not timestamp:
                continue
            history_by_symbol[symbol][timestamp] = observation

    missing_cycle_ids = sorted(set(symbols_by_cycle) - seen_cycles)

    trend_nonzero = [row for row in analyzed_rows if float(row["trend_alignment_score"]) > 0.0]
    macd_nonzero = [row for row in analyzed_rows if float(row["macd_momentum_score"]) > 0.0]
    either_nonzero = [row for row in analyzed_rows if float(row["technical_total_score"]) > 0.0]
    usable_history_rows = [row for row in analyzed_rows if bool(row["history_usable"])]
    full_history_rows = [row for row in analyzed_rows if bool(row["history_full_confidence"])]
    observation_counts = [int(row["observation_count"]) for row in analyzed_rows]
    history_buckets = Counter(_history_bucket(int(row["observation_count"])) for row in analyzed_rows)

    bucket_breakdown: dict[str, dict[str, Any]] = {}
    for bucket_name in KNOWN_BUCKETS:
        bucket_rows = [
            row for row in analyzed_rows
            if str(row.get("selection_bucket") or "") == bucket_name
        ]
        bucket_obs = [int(row["observation_count"]) for row in bucket_rows]
        bucket_breakdown[bucket_name] = {
            "rows": len(bucket_rows),
            "history_usable_rows": sum(1 for row in bucket_rows if row["history_usable"]),
            "history_full_confidence_rows": sum(
                1 for row in bucket_rows if row["history_full_confidence"]
            ),
            "trend_nonzero_rows": sum(
                1 for row in bucket_rows if float(row["trend_alignment_score"]) > 0.0
            ),
            "macd_nonzero_rows": sum(
                1 for row in bucket_rows if float(row["macd_momentum_score"]) > 0.0
            ),
            "either_nonzero_rows": sum(
                1 for row in bucket_rows if float(row["technical_total_score"]) > 0.0
            ),
            "avg_observation_count": round(_safe_mean(bucket_obs), 1),
        }

    trend_samples = [
        _sample_payload(row)
        for row in sorted(
            trend_nonzero,
            key=lambda row: (
                -float(row["trend_alignment_score"]),
                -int(row["observation_count"]),
                str(row.get("symbol") or ""),
            ),
        )[:3]
    ]
    macd_samples = [
        _sample_payload(row)
        for row in sorted(
            macd_nonzero,
            key=lambda row: (
                -float(row["macd_momentum_score"]),
                -int(row["observation_count"]),
                str(row.get("symbol") or ""),
            ),
        )[:3]
    ]
    combined_samples = [
        _sample_payload(row)
        for row in sorted(
            either_nonzero,
            key=lambda row: (
                -float(row["technical_total_score"]),
                -int(row["observation_count"]),
                str(row.get("symbol") or ""),
            ),
        )[:5]
    ]

    return {
        "meta": {
            "account": account,
            "date": date,
            "session": session or "ALL",
            "bucket_filter": bucket or "ALL",
            "last_n_cycles": last_n,
            "candidate_log_file": str(candidate_path),
            "cycle_stats_file": str(cycle_stats_path),
            "cycle_snapshots_file": str(snapshots_path),
            "candidate_log_exists": candidate_path.exists(),
            "cycle_stats_file_exists": cycle_stats_path.exists(),
            "cycle_snapshots_file_exists": snapshots_path.exists(),
            "selected_cycle_ids": selected_cycle_ids,
            "total_candidate_rows": len(all_candidate_rows),
            "filtered_candidate_rows": len(candidate_rows),
            "deep_eval_rows": len(deep_rows),
            "analyzed_rows": len(analyzed_rows),
            "active_cycle_rows": len(cycle_stats_rows),
            "missing_cycle_ids": missing_cycle_ids,
            "errors": list(candidate_errors)
            + (cycle_stats_errors if (last_n > 0 or cycle_stats_path.exists()) else [])
            + snapshot_errors,
        },
        "activation_summary": {
            "trend_nonzero_count": len(trend_nonzero),
            "macd_nonzero_count": len(macd_nonzero),
            "either_nonzero_count": len(either_nonzero),
            "history_usable_count": len(usable_history_rows),
            "history_full_confidence_count": len(full_history_rows),
            "history_insufficient_count": max(0, len(analyzed_rows) - len(usable_history_rows)),
        },
        "bucket_breakdown": bucket_breakdown,
        "history_summary": {
            "min_observation_count": min(observation_counts) if observation_counts else 0,
            "avg_observation_count": round(_safe_mean(observation_counts), 1),
            "median_observation_count": round(_safe_median(observation_counts), 1),
            "max_observation_count": max(observation_counts) if observation_counts else 0,
            "observation_count_buckets": dict(history_buckets),
        },
        "samples": {
            "trend_alignment_active": trend_samples,
            "macd_momentum_active": macd_samples,
            "technical_any_active": combined_samples,
        },
    }


def print_terminal_report(result: dict[str, Any]) -> None:
    meta = result["meta"]
    print()
    print(_header("═" * 76))
    print(_header("  analyze_technical_feature_activation"))
    print(_header("═" * 76))
    print(f"  account : {meta['account']}")
    print(f"  date    : {meta['date']}")
    print(f"  session : {meta['session']}")
    print(f"  bucket  : {meta['bucket_filter']}")
    print(f"  last_n  : {meta['last_n_cycles'] if meta['last_n_cycles'] > 0 else 'ALL'}")
    print(
        f"  rows    : {meta['total_candidate_rows']} candidate rows -> "
        f"{meta['filtered_candidate_rows']} filtered -> {meta['deep_eval_rows']} deep-eval rows"
    )
    print(f"  analyzed: {meta['analyzed_rows']}")
    if meta["active_cycle_rows"]:
        print(f"  cycles  : {meta['active_cycle_rows']} matching cycle stats rows")
    if meta["selected_cycle_ids"]:
        print(f"  cycle_ids: {', '.join(meta['selected_cycle_ids'])}")
    if meta["missing_cycle_ids"]:
        print(_warn(f"  missing cycle snapshots: {len(meta['missing_cycle_ids'])}"))
    if meta["errors"]:
        print(_warn(f"  load warnings: {len(meta['errors'])}"))
    print()

    summary = result["activation_summary"]
    print(_header("  ── Activation Summary ─────────────────────────────────────────"))
    print(f"  trend_alignment_score != 0   : {summary['trend_nonzero_count']}")
    print(f"  macd_momentum_score != 0     : {summary['macd_nonzero_count']}")
    print(f"  any technical score != 0     : {summary['either_nonzero_count']}")
    print(
        f"  history usable (>={MIN_TECHNICAL_HISTORY_OBSERVATIONS})    : "
        f"{summary['history_usable_count']}"
    )
    print(
        f"  history full (>=26)          : "
        f"{summary['history_full_confidence_count']}"
    )
    print(
        f"  history insufficient (<{MIN_TECHNICAL_HISTORY_OBSERVATIONS}) : "
        f"{summary['history_insufficient_count']}"
    )
    print()

    history_summary = result["history_summary"]
    print(_header("  ── History Summary (Proxy) ──────────────────────────────────"))
    print(
        "  obs count : "
        f"min={history_summary['min_observation_count']} "
        f"avg={history_summary['avg_observation_count']:.1f} "
        f"median={history_summary['median_observation_count']:.1f} "
        f"max={history_summary['max_observation_count']}"
    )
    print("  buckets   :")
    for label in ("0", "1-5", "6-10", "11-25", "26+"):
        print(
            f"    {label:<5} "
            f"{int(history_summary['observation_count_buckets'].get(label, 0)):>5}"
        )
    print()

    print(_header("  ── Bucket Breakdown ─────────────────────────────────────────"))
    print(
        f"  {'bucket':<12} {'rows':>6} {'hist12+':>8} {'hist26+':>8} {'trend>0':>8} "
        f"{'macd>0':>8} {'any>0':>8} {'avg_obs':>8}"
    )
    print(_dim("  " + "─" * 72))
    for bucket_name in KNOWN_BUCKETS:
        payload = result["bucket_breakdown"].get(bucket_name) or {}
        print(
            f"  {bucket_name:<12} "
            f"{int(payload.get('rows', 0)):>6} "
            f"{int(payload.get('history_usable_rows', 0)):>8} "
            f"{int(payload.get('history_full_confidence_rows', 0)):>8} "
            f"{int(payload.get('trend_nonzero_rows', 0)):>8} "
            f"{int(payload.get('macd_nonzero_rows', 0)):>8} "
            f"{int(payload.get('either_nonzero_rows', 0)):>8} "
            f"{float(payload.get('avg_observation_count', 0.0)):>8.1f}"
        )
    print()

    print(_header("  ── Sample Active Rows ───────────────────────────────────────"))
    samples = result["samples"]
    for label, key in (
        ("trend_alignment_active", "trend_alignment_active"),
        ("macd_momentum_active", "macd_momentum_active"),
        ("technical_any_active", "technical_any_active"),
    ):
        print(f"  {label}:")
        rows = samples.get(key) or []
        if not rows:
            print(_dim("    None"))
            continue
        for row in rows:
            print(
                "    "
                f"{row.get('symbol')}({row.get('symbol_name') or ''}) | "
                f"bucket={row.get('bucket')} | "
                f"cycle={row.get('cycle_id')} | "
                f"obs={row.get('observation_count')} | "
                f"trend={row.get('trend_alignment_score')} | "
                f"macd={row.get('macd_momentum_score')} | "
                f"score_deep={row.get('score_deep')}"
            )
    print()


def print_json_report(result: dict[str, Any]) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze whether technical overlay features are actually activating."
    )
    parser.add_argument("--date", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--session", default="REGULAR")
    parser.add_argument("--bucket", default="")
    parser.add_argument("--last-n-cycles", type=int, default=0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = run_analysis(
        account=args.account,
        date=args.date,
        session=args.session,
        bucket=args.bucket,
        last_n=args.last_n_cycles,
    )

    if args.json:
        print_json_report(result)
    else:
        print_terminal_report(result)
    sys.exit(0)


if __name__ == "__main__":
    main()
