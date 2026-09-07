import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.auth.account_scope import (
    dashboard_paths_for_signature,
    get_cycle_snapshots_path,
    get_order_log_path,
    get_performance_snapshots_path,
    get_performance_summary_path,
    get_runtime_state_path,
)
from app.auth.settings import PROJECT_ROOT
from app.dashboard.normalizers import (
    _apply_account_weights,
    _build_account_view,
    _build_engine_state_view,
    _build_trade_journal,
    _infer_order_side,
    _is_closed_session_summary_without_support,
    _latest_action_record,
    _normalize_candidates,
    _normalize_cycles,
    _normalize_orders,
    _normalize_positions,
    _normalize_positions_from_cycle,
    _parse_iso_datetime,
    _resolve_symbol_name,
    _select_trusted_performance_summary,
    _successful_sell_orders_since,
    _summary_equity,
    _summary_has_cycle_support,
)
from app.core.file_read_limits import (
    LocalReadLimitError,
    iter_lines_bounded,
    iter_tail_lines_bounded,
    iter_tail_lines_window,
    read_text_bounded,
)
from app.core.jsonl import SNAPSHOT_READ_LINE_MAX_BYTES
from app.core.order_log import ORDER_LOG_READER_LINE_MAX_BYTES
from app.core.market_session import get_korean_market_session
from app.runtime_state import runtime_state_summary

_PROJECT_ROOT = PROJECT_ROOT

_DASHBOARD_CYCLE_SNAPSHOTS_MAX_LINES = int(os.environ.get("DASHBOARD_CYCLE_SNAPSHOTS_MAX_LINES", "1000"))
_DASHBOARD_ORDERS_MAX_LINES = int(os.environ.get("DASHBOARD_ORDERS_MAX_LINES", "2000"))
_DASHBOARD_PERF_SUMMARY_MAX_LINES = int(os.environ.get("DASHBOARD_PERF_SUMMARY_MAX_LINES", "1000"))
_DASHBOARD_PERF_SNAPSHOTS_MAX_LINES = int(os.environ.get("DASHBOARD_PERF_SNAPSHOTS_MAX_LINES", "1000"))
_DASHBOARD_BASELINE_STALE_DAYS = 7


def _safe_read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, "파일 없음"
    try:
        raw = json.loads(read_text_bounded(path, encoding="utf-8"))
    except LocalReadLimitError as exc:
        return None, f"읽기 제한 초과: {exc}"
    except UnicodeDecodeError as exc:
        return None, f"디코딩 실패: {exc}"
    except OSError as exc:
        return None, f"읽기 실패: {exc}"
    except json.JSONDecodeError as exc:
        return None, f"파싱 실패: {exc}"
    if not isinstance(raw, dict):
        return None, "JSON 객체가 아닙니다."
    return raw, None


def _safe_read_jsonl(
    path: Path,
    *,
    max_lines: int | None = None,
    max_line_bytes: int | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    if not path.exists():
        return [], "파일 없음"
    records: list[dict[str, Any]] = []
    parse_error_count = 0
    skipped_line_count = 0
    decoder = json.JSONDecoder()
    try:
        if max_lines is not None and max_lines > 0:
            def _on_skip(_lineno: int, _byte_len: int) -> None:
                nonlocal skipped_line_count
                skipped_line_count += 1

            lines = iter_tail_lines_window(
                path,
                encoding="utf-8",
                max_lines=max_lines,
                max_line_bytes=max_line_bytes,
                on_skip=_on_skip,
            )
        else:
            lines = iter_lines_bounded(
                path, encoding="utf-8", max_line_bytes=max_line_bytes
            )
        for _lineno, raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            cursor = 0
            line_parsed = False
            while cursor < len(line):
                while cursor < len(line) and line[cursor].isspace():
                    cursor += 1
                if cursor >= len(line):
                    break
                try:
                    record, next_cursor = decoder.raw_decode(line, cursor)
                except json.JSONDecodeError:
                    parse_error_count += 1
                    break
                if isinstance(record, dict):
                    records.append(record)
                    line_parsed = True
                cursor = next_cursor
            if not line_parsed:
                parse_error_count += 1
    except LocalReadLimitError as exc:
        return [], f"읽기 제한 초과: {exc}"
    except UnicodeDecodeError as exc:
        return [], f"디코딩 실패: {exc}"
    except OSError as exc:
        return [], f"읽기 실패: {exc}"
    errors = []
    if parse_error_count:
        errors.append(f"일부 레코드 파싱 실패: {parse_error_count}건")
    if skipped_line_count:
        errors.append(f"읽기 제한 초과 라인 생략: {skipped_line_count}건")
    if errors:
        return records, " / ".join(errors)
    return records, None


def _baseline_age(end_date: object) -> tuple[int | None, bool]:
    try:
        parsed = date.fromisoformat(str(end_date or "").strip())
    except ValueError:
        return None, True
    days_old = max(0, (date.today() - parsed).days)
    return days_old, days_old > _DASHBOARD_BASELINE_STALE_DAYS


def _latest_native_backtest() -> dict[str, Any]:
    report_dir = _PROJECT_ROOT / "reports" / "native_backtest"
    for path in sorted(report_dir.glob("native_backtest_*.json"), reverse=True):
        payload, _error = _safe_read_json(path)
        if not payload or not payload.get("ok") or payload.get("plan_only"):
            continue
        result = dict(payload)
        result["source_file"] = str(path)
        timestamp_text = path.stem.removeprefix("native_backtest_")
        try:
            result["generated_at"] = datetime.strptime(
                timestamp_text, "%Y%m%d_%H%M%S"
            ).isoformat()
        except ValueError:
            result["generated_at"] = ""
        return result
    return {}


def _load_research_data() -> dict[str, Any]:
    """Load research artefacts: proposal registry, ML viability report, latest snapshot."""
    research_dir = _PROJECT_ROOT / "research"
    ml_dir = _PROJECT_ROOT / "logs" / "ml"

    # ── proposal registry ──────────────────────────────────────────────────
    registry_path = research_dir / "proposal_registry.json"
    registry, registry_err = _safe_read_json(registry_path)
    proposals_raw: dict[str, Any] = (registry or {}).get("proposals", {})

    proposals: list[dict[str, Any]] = []
    for pid, p in proposals_raw.items():
        ev = p.get("evaluation") or {}
        evals = ev.get("evaluations") or []
        delta_sharpe: float | None = None
        if evals:
            first = evals[0]
            bl_sharpe = (first.get("baseline_performance") or {}).get("sharpe")
            ca_sharpe = (first.get("candidate_performance") or {}).get("sharpe")
            if bl_sharpe is not None and ca_sharpe is not None:
                delta_sharpe = round(float(ca_sharpe) - float(bl_sharpe), 3)

        changes = p.get("changes") or []
        change_summary = "; ".join(
            f"{c.get('param_id','?')} {c.get('current_value','?')}→{c.get('new_value','?')}"
            for c in changes[:2]
        )
        proposals.append(
            {
                "proposal_id": pid,
                "status": p.get("status", "unknown"),
                "direction": p.get("direction", ""),
                "generated_at": p.get("generated_at", ""),
                "registered_at": p.get("registered_at", ""),
                "changes": changes,
                "change_summary": change_summary,
                "reasoning_summary": p.get("reasoning_summary") or [],
                "risk_flags": p.get("risk_flags") or [],
                "evaluation_verdict": ev.get("overall_verdict"),
                "delta_sharpe": delta_sharpe,
                "status_history": p.get("status_history") or [],
                "source_snapshot_date": p.get("source_snapshot_date", ""),
            }
        )
    proposals.sort(key=lambda x: x.get("registered_at", ""), reverse=True)

    # ── ML viability report ────────────────────────────────────────────────
    viability_path = ml_dir / "ml_label_viability_report.json"
    viability, viability_err = _safe_read_json(viability_path)
    ml_labels: list[dict[str, Any]] = []
    if viability:
        for lbl in viability.get("labels") or []:
            bl = lbl.get("baseline") or {}
            cov = lbl.get("coverage") or {}
            ml_labels.append(
                {
                    "label": lbl.get("label", ""),
                    "kind": lbl.get("kind", ""),
                    "verdict": lbl.get("verdict", ""),
                    "rank_score": lbl.get("rank_score"),
                    "notes": lbl.get("notes") or [],
                    "positive_count": cov.get("positive_count"),
                    "available_count": cov.get("available_count"),
                    "positive_rate": cov.get("positive_rate"),
                    "evaluated_folds": bl.get("evaluated_folds"),
                    "pr_auc": bl.get("pr_auc"),
                    "f1": bl.get("f1"),
                    "precision": bl.get("precision"),
                    "recall": bl.get("recall"),
                    "model_used": bl.get("model_used"),
                    "confidence": bl.get("ml_confidence"),
                }
            )
        ml_labels.sort(key=lambda x: float(x.get("rank_score") or -99), reverse=True)

    # ── latest research snapshot ───────────────────────────────────────────
    snapshot_dir = research_dir / "snapshots"
    latest_snapshot: dict[str, Any] = {}
    if snapshot_dir.exists():
        snap_files = sorted(
            [f for f in snapshot_dir.glob("snapshot_*.json") if "_dry" not in f.name],
            key=lambda f: f.name,
            reverse=True,
        )
        if snap_files:
            snap, _ = _safe_read_json(snap_files[0])
            latest_snapshot = snap or {}

    # ── shadow watch reports ──────────────────────────────────────────────
    shadow_dir = research_dir / "shadow_watch"
    shadow_reports: list[dict[str, Any]] = []
    if shadow_dir.exists():
        for rpt_path in sorted(shadow_dir.glob("shadow_report_*.json"), reverse=True)[:5]:
            rpt, _ = _safe_read_json(rpt_path)
            if rpt:
                shadow_reports.append(rpt)

    # ── baselines from snapshot ────────────────────────────────────────────
    baselines_raw = latest_snapshot.get("backtest_baselines") or {}
    baselines: list[dict[str, Any]] = []
    for family, bl in baselines_raw.items():
        if not isinstance(bl, dict):
            continue
        performance = bl.get("performance") or {}
        staleness = bl.get("staleness") or {}
        end_date = staleness.get("end_date", bl.get("end_date", ""))
        days_old, is_stale = _baseline_age(end_date)
        baselines.append(
            {
                "family": family,
                "sharpe": bl.get("sharpe", performance.get("sharpe")),
                "total_return": bl.get(
                    "total_return", performance.get("total_return")
                ),
                "max_drawdown": bl.get(
                    "max_drawdown", performance.get("max_drawdown")
                ),
                "win_rate": bl.get("win_rate", performance.get("win_rate")),
                "trade_count": bl.get(
                    "trade_count", performance.get("trade_count")
                ),
                "research_read": bl.get("research_read", ""),
                "end_date": end_date,
                "days_old": days_old,
                "is_stale": is_stale,
            }
        )

    snapshot_meta = latest_snapshot.get("snapshot_meta") or {}
    snapshot_meta = dict(snapshot_meta)
    stale_baselines = [row["family"] for row in baselines if row["is_stale"]]
    snapshot_meta["stale_baselines"] = stale_baselines
    snapshot_meta["baselines_need_update"] = bool(stale_baselines)
    ml_status = latest_snapshot.get("ml_status") or {}

    return {
        "proposals": proposals,
        "registry_error": registry_err,
        "ml_labels": ml_labels,
        "viability_error": viability_err,
        "viability_generated_at": (viability or {}).get("generated_at"),
        "baselines": baselines,
        "snapshot_meta": snapshot_meta,
        "snapshot_as_of": latest_snapshot.get("as_of_date", ""),
        "snapshot_generated_at": latest_snapshot.get("generated_at", ""),
        "shadow_reports": shadow_reports,
        "ml_status": ml_status,
        "native_backtest": _latest_native_backtest(),
    }
def load_dashboard_data(
    *,
    signature: str | None = None,
    data_dir: Path | None = None,
    logs_dir: Path | None = None,
) -> dict[str, Any]:
    """Load the single-account dashboard payload.

    With ``signature=None`` (default) the paths resolve from the active
    ``Settings`` exactly as before. Passing an explicit ``signature`` redirects
    the five source files to that account (used by the C-3 multi-account
    overview); ``data_dir``/``logs_dir`` override the roots for tests.
    """
    if signature is None:
        runtime_state_path = get_runtime_state_path()
        cycle_snapshots_path = get_cycle_snapshots_path()
        performance_snapshots_path = get_performance_snapshots_path()
        orders_log_path = get_order_log_path()
        performance_summary_path = get_performance_summary_path()
    else:
        _paths = dashboard_paths_for_signature(
            signature, data_dir=data_dir, logs_dir=logs_dir
        )
        runtime_state_path = _paths["runtime_state"]
        cycle_snapshots_path = _paths["cycle_snapshots"]
        performance_snapshots_path = _paths["performance_snapshots"]
        orders_log_path = _paths["orders"]
        performance_summary_path = _paths["performance_summary"]

    runtime_state, runtime_state_error = _safe_read_json(runtime_state_path)
    cycle_records, cycle_error = _safe_read_jsonl(
        cycle_snapshots_path,
        max_lines=_DASHBOARD_CYCLE_SNAPSHOTS_MAX_LINES,
        max_line_bytes=SNAPSHOT_READ_LINE_MAX_BYTES,
    )
    performance_snapshots, performance_snapshot_error = _safe_read_jsonl(
        performance_snapshots_path,
        max_lines=_DASHBOARD_PERF_SNAPSHOTS_MAX_LINES,
        max_line_bytes=SNAPSHOT_READ_LINE_MAX_BYTES,
    )
    order_records, orders_error = _safe_read_jsonl(
        orders_log_path,
        max_lines=_DASHBOARD_ORDERS_MAX_LINES,
        max_line_bytes=ORDER_LOG_READER_LINE_MAX_BYTES,
    )
    performance_summaries, performance_summary_error = _safe_read_jsonl(
        performance_summary_path,
        max_lines=_DASHBOARD_PERF_SUMMARY_MAX_LINES,
        max_line_bytes=SNAPSHOT_READ_LINE_MAX_BYTES,
    )

    latest_performance_summary, performance_selection_meta = _select_trusted_performance_summary(
        performance_summaries,
        order_records=order_records,
        cycle_records=cycle_records,
    )
    latest_cycle = cycle_records[-1] if cycle_records else None
    market_status = get_korean_market_session()
    runtime_summary = runtime_state_summary(runtime_state or {})
    normalized_cycles = _normalize_cycles(cycle_records)
    normalized_positions = _normalize_positions(latest_performance_summary)
    if not normalized_positions:
        normalized_positions = _normalize_positions_from_cycle(latest_cycle if isinstance(latest_cycle, dict) else None)
    account_view = _build_account_view(
        latest_performance_summary,
        positions=normalized_positions,
        latest_cycle=normalized_cycles[0] if normalized_cycles else None,
    )
    normalized_positions = _apply_account_weights(
        normalized_positions,
        account_view=account_view,
    )
    normalized_orders = _normalize_orders(order_records)

    research = _load_research_data()
    trade_journal = _build_trade_journal(normalized_orders)

    return {
        "market_status": market_status,
        "runtime_state": runtime_state or {},
        "runtime_state_summary": runtime_summary,
        "runtime_state_error": runtime_state_error,
        "orders": normalized_orders,
        "orders_error": orders_error,
        "cycles": normalized_cycles,
        "cycles_error": cycle_error,
        "performance_snapshots": performance_snapshots,
        "performance_snapshots_error": performance_snapshot_error,
        "performance_summaries": performance_summaries,
        "performance_summary": latest_performance_summary or {},
        "performance_summary_error": performance_summary_error,
        "performance_selection_meta": performance_selection_meta,
        "positions": normalized_positions,
        "account_view": account_view,
        "candidate_rows": _normalize_candidates(normalized_cycles),
        "latest_cycle_raw": latest_cycle,
        "engine_state_view": _build_engine_state_view(
            runtime_state=runtime_state or {},
            cycles=normalized_cycles,
            orders=normalized_orders,
            market_status=market_status,
        ),
        "research": research,
        "trade_journal": trade_journal,
    }
