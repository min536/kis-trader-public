"""CLI: build_research_snapshot

Aggregate live diagnostics, backtest baselines, and ML status into a single
JSON snapshot for downstream research tools (proposal_generator, etc.).

This is research-only tooling; it does not touch live trading settings.

Usage:
    python3 -m app.tools.build_research_snapshot --account mock_12345678_01
    python3 -m app.tools.build_research_snapshot --account mock_12345678_01 --date 20260410
    python3 -m app.tools.build_research_snapshot --account mock_12345678_01 --output-file research/snapshots/snapshot_20260412.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Baseline is considered stale when its end_date is older than this many days.
# The research loop uses this to decide whether to auto-refresh before generating.
BASELINE_STALE_DAYS = 7

from app.tools.analyze_core_bucket import run_analysis as run_core_bucket_analysis
from app.tools.core_change_decision_report import (
    _direction_1,
    _direction_2,
    _direction_3,
    build_json_output as build_core_decision_json,
)
from app.tools.eod_health_check import detect_latest_market_date
from app.tools.live_health_check import build_health_summary
from app.tools.overnight_tuning_report import (
    _build_attention_hints,
    _build_outcome_summary,
    _load_outcome_rows,
    _signal_outcomes_path,
    build_json_output as build_overnight_json,
)
from app.tools.postrun_diagnostics import build_report

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_BACKTESTER_REPORTS = _PROJECT_ROOT / "backtester" / "reports"
_ML_DIR = _PROJECT_ROOT / "logs" / "ml"

# Known backtester run IDs — extend as new families are added
_KNOWN_BT_RUN_IDS = [
    "bt_custom_kis_trader_core_family_approx",
    "bt_custom_kis_trader_continuation_family_approx",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def _ml_confidence(evaluated_folds: int, research_read: str) -> str:
    read_lc = str(research_read).lower()
    if "too little" in read_lc or "sparse" in read_lc or evaluated_folds < 2:
        return "low"
    if evaluated_folds < 4:
        return "medium"
    return "high"


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _build_live_diagnostics(
    account: str, date: str, session: str
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    health = build_health_summary(account=account, date=date, session=session)
    report = build_report(account=account, date=date, session=session, last_n_cycles=0)
    core_bucket = run_core_bucket_analysis(account=account, date=date, session=session)

    outcome_path = _signal_outcomes_path(account, date)
    if not outcome_path.exists():
        warnings.append(f"signal_outcomes file missing for {account} {date} — rescue/outcome data absent")
    outcome_rows = _load_outcome_rows(outcome_path)
    if session:
        outcome_rows = [
            row for row in outcome_rows
            if str(row.get("session") or "").upper() == session.upper()
        ]
    outcomes = _build_outcome_summary(outcome_rows)
    hints = _build_attention_hints(
        report=report, health=health, core_bucket=core_bucket, outcomes=outcomes
    )

    overnight = build_overnight_json(
        account=account,
        date=date,
        session=session,
        health=health,
        report=report,
        core_bucket=core_bucket,
        outcomes=outcomes,
        hints=hints,
    )
    directions = [
        _direction_1(report=report, core=core_bucket, outcomes=outcomes),
        _direction_2(core=core_bucket, outcomes=outcomes),
        _direction_3(core=core_bucket, outcomes=outcomes),
    ]
    core_decision = build_core_decision_json(
        account=account,
        date=date,
        session=session,
        health=health,
        report=report,
        core=core_bucket,
        outcomes=outcomes,
        directions=directions,
    )

    return {
        "overnight_tuning": overnight,
        "core_change_decision": core_decision,
    }, warnings


def _baseline_staleness(end_date_str: str) -> tuple[int, bool]:
    """Return (days_old, is_stale) for a baseline end_date string (YYYY-MM-DD)."""
    if not end_date_str:
        return -1, True
    try:
        end_dt = datetime.strptime(end_date_str, "%Y-%m-%d")
        days_old = (datetime.now() - end_dt).days
        return days_old, days_old > BASELINE_STALE_DAYS
    except ValueError:
        return -1, True


def _build_backtest_baselines() -> tuple[dict[str, Any], list[str]]:
    baselines: dict[str, Any] = {}
    warnings: list[str] = []
    for run_id in _KNOWN_BT_RUN_IDS:
        path = _BACKTESTER_REPORTS / f"run_{run_id}_summary.json"
        data = _load_json_file(path)
        if data is None:
            warnings.append(f"backtest summary missing: {path.name}")
            continue
        family_key = run_id.replace("bt_custom_kis_trader_", "")
        end_date = data.get("end_date", "")
        updated_at = data.get("updated_at", "")
        days_old, is_stale = _baseline_staleness(end_date)
        if is_stale:
            warnings.append(
                f"baseline stale: {family_key} end_date={end_date} "
                f"({days_old}d ago > {BASELINE_STALE_DAYS}d threshold) — "
                "run scripts/update_baselines.sh"
            )
        baselines[family_key] = {
            "run_id": data.get("run_id", run_id),
            "strategy_name": data.get("strategy_name", ""),
            "period": {
                "start": data.get("start_date", ""),
                "end": end_date,
            },
            "performance": {
                "total_return": data.get("total_return"),
                "cagr": data.get("cagr"),
                "sharpe": data.get("sharpe"),
                "max_drawdown": data.get("max_drawdown"),
                "win_rate": data.get("win_rate"),
                "profit_factor": data.get("profit_factor"),
                "trade_count": data.get("trade_count"),
            },
            "research_read": data.get("research_read", ""),
            "staleness": {
                "end_date": end_date,
                "updated_at": updated_at,
                "days_old": days_old,
                "is_stale": is_stale,
            },
        }
    return baselines, warnings


def _build_ml_status() -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []

    viability_path = _ML_DIR / "ml_label_viability_report.json"
    viability = _load_json_file(viability_path)
    if viability is None:
        warnings.append("ml_label_viability_report.json missing")
        return {"available": False}, warnings

    labels_raw = viability.get("labels") or []
    label_summaries = []
    best_label: str | None = None
    best_score: float = -999.0

    for entry in labels_raw:
        label = str(entry.get("label") or "")
        verdict = str(entry.get("verdict") or "")
        rank_score = float(entry.get("rank_score") or 0.0)
        baseline = entry.get("baseline") or {}

        summary: dict[str, Any] = {
            "label": label,
            "kind": entry.get("kind", ""),
            "verdict": verdict,
            "rank_score": rank_score,
            "coverage": {
                "available_count": (entry.get("coverage") or {}).get("available_count"),
                "positive_count": (entry.get("coverage") or {}).get("positive_count"),
                "positive_rate": (entry.get("coverage") or {}).get("positive_rate"),
            },
        }
        if baseline:
            evaluated_folds = int(baseline.get("evaluated_folds") or 0)
            research_read = str(baseline.get("research_read") or "")
            summary["baseline"] = {
                "model": baseline.get("model_used", ""),
                "evaluated_folds": evaluated_folds,
                "macro_f1": baseline.get("macro_f1"),
                "macro_pr_auc": baseline.get("macro_pr_auc"),
                "macro_roc_auc": baseline.get("macro_roc_auc"),
                "research_read": research_read,
                "confidence": _ml_confidence(evaluated_folds, research_read),
            }

        label_summaries.append(summary)

        if rank_score > best_score and entry.get("kind") == "economic":
            best_score = rank_score
            best_label = label

    # Pick the best economic label that actually has a baseline
    best_with_baseline = max(
        (e for e in label_summaries if e.get("baseline") and e.get("kind") == "economic"),
        key=lambda e: e["rank_score"],
        default=None,
    )
    if best_with_baseline:
        best_label = best_with_baseline["label"]
        best_confidence = (best_with_baseline.get("baseline") or {}).get("confidence", "low")
    else:
        best_confidence = "low"

    return {
        "available": True,
        "generated_at": viability.get("generated_at", ""),
        "best_label_candidate": best_label,
        "best_label_confidence": best_confidence,
        "label_count": len(label_summaries),
        "labels": label_summaries,
    }, warnings


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_snapshot(
    account: str,
    date: str,
    session: str = "",
) -> dict[str, Any]:
    all_warnings: list[str] = []

    live_diagnostics, w1 = _build_live_diagnostics(account, date, session)
    all_warnings.extend(w1)

    backtest_baselines, w2 = _build_backtest_baselines()
    all_warnings.extend(w2)

    ml_status, w3 = _build_ml_status()
    all_warnings.extend(w3)

    # Compute overall baseline staleness for the loop to check quickly
    stale_families = [
        fk for fk, b in backtest_baselines.items()
        if (b.get("staleness") or {}).get("is_stale")
    ]

    return {
        "snapshot_type": "research_snapshot",
        "generated_at": datetime.now().isoformat(),
        "account": account,
        "as_of_date": date,
        "session": session or "ALL",
        "live_diagnostics": live_diagnostics,
        "backtest_baselines": backtest_baselines,
        "ml_status": ml_status,
        "snapshot_meta": {
            "warnings": all_warnings,
            "backtester_reports_dir": str(_BACKTESTER_REPORTS),
            "ml_dir": str(_ML_DIR),
            "baseline_stale_threshold_days": BASELINE_STALE_DAYS,
            "stale_baselines": stale_families,
            "baselines_need_update": bool(stale_families),
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aggregate live diagnostics + backtest + ML into a single research snapshot JSON"
    )
    parser.add_argument("--account", required=True, help="Account identifier")
    parser.add_argument(
        "--date",
        default="",
        help="Date YYYYMMDD. Defaults to latest available candidate_outcomes date.",
    )
    parser.add_argument("--session", default="", help="Optional session filter")
    parser.add_argument(
        "--output-file",
        default="",
        help="Write snapshot JSON to this path. Defaults to stdout.",
    )
    args = parser.parse_args()

    date = args.date or detect_latest_market_date(args.account)
    snapshot = build_snapshot(account=args.account, date=date, session=args.session)

    serialised = json.dumps(snapshot, ensure_ascii=False, indent=2)

    if args.output_file:
        out_path = Path(args.output_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(serialised, encoding="utf-8")
        print(f"snapshot written → {out_path}", file=sys.stderr)
    else:
        sys.stdout.write(serialised + "\n")


if __name__ == "__main__":
    main()
