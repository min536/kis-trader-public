"""Shared helpers for offline supervised-learning research tooling."""

from __future__ import annotations

import csv
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from app.tools.parquet_utils import read_flat_parquet, write_flat_parquet

PROJECT_ROOT = Path(__file__).resolve().parents[2]

ML_IDENTIFIER_COLS: tuple[str, ...] = (
    "row_id",
    "source_dataset",
    "trade_date",
    "ts",
    "cycle_id",
    "symbol",
    "symbol_name",
    "session",
    "regime",
    "selection_bucket",
    "selection_profile",
    "stage_reached",
    "time_of_day_bucket",
)

ML_DECISION_FEATURE_COLS: tuple[str, ...] = (
    "pre_gate_passed",
    "shallow_selected",
    "deep_evaluated",
    "score_shallow",
    "score_deep",
    "passed_count_deep",
    "strategy_pass_pattern",
    "passes_profit_buffer",
    "trend_alignment_score",
    "macd_momentum_score",
    "trend_quality_score",
    "momentum_quality_score",
    "price_efficiency_score",
    "pullback_pct",
    "rebound_pct",
    "gap_up_open_pct",
    "gap_down_open_pct",
    "range_recovery_ratio",
    "current_price",
    "open_price",
    "low_price",
    "prev_day_change_pct",
    "core_shadow_evaluated",
    "core_shadow_trend_gate_passed",
    "core_shadow_passed_count",
    "core_shadow_pattern",
    "core_shadow_passed",
    "core_rescue_applied",
    "core_rescue_selected_score",
    "core_rescue_replaced_symbol",
    "core_rescue_reason",
    "budget_rescue_enabled",
    "budget_rescue_applied",
    "budget_rescue_qty",
    "budget_rescue_reason",
)

ML_COST_COLS: tuple[str, ...] = (
    "expected_total_cost_krw",
    "expected_cost_bps",
    "net_edge_bps",
    "net_profit_buffer_bps",
    "cost_quality_score",
    "expected_cost_penalty",
    "cost_block_reason",
)

ML_POLICY_CONTEXT_COLS: tuple[str, ...] = (
    "rejection_reason",
    "selection_outcome",
    "daily_pnl_brake_state",
    "already_holding",
    "cooldown_blocked",
)

ML_OUTCOME_COLS: tuple[str, ...] = (
    "final_candidate",
    "executed",
    "entry_ts",
    "entry_price",
    "ret_5m_bps",
    "ret_30m_bps",
    "ret_eod_bps",
)

ML_LABEL_COLS: tuple[str, ...] = (
    "effective_cost_bps_used",
    "net_ret_30m_after_cost_bps",
    "net_ret_eod_after_cost_bps",
    "label_positive_30m_net_cost",
    "label_positive_eod_net_cost",
    "label_top_decile_eod",
    "label_top_decile_eod_net_cost",
    "label_final_candidate_vs_reject",
)

ML_SAFE_NUMERIC_FEATURE_COLS: tuple[str, ...] = (
    "pre_gate_passed",
    "shallow_selected",
    "deep_evaluated",
    "score_shallow",
    "score_deep",
    "passed_count_deep",
    "passes_profit_buffer",
    "trend_alignment_score",
    "macd_momentum_score",
    "trend_quality_score",
    "momentum_quality_score",
    "price_efficiency_score",
    "pullback_pct",
    "rebound_pct",
    "gap_up_open_pct",
    "gap_down_open_pct",
    "range_recovery_ratio",
    "current_price",
    "open_price",
    "low_price",
    "prev_day_change_pct",
    "core_shadow_evaluated",
    "core_shadow_trend_gate_passed",
    "core_shadow_passed_count",
    "core_shadow_passed",
    "core_rescue_applied",
    "core_rescue_selected_score",
    "budget_rescue_enabled",
    "budget_rescue_applied",
    "budget_rescue_qty",
    "expected_total_cost_krw",
    "expected_cost_bps",
    "net_edge_bps",
    "net_profit_buffer_bps",
    "cost_quality_score",
    "expected_cost_penalty",
)

ML_SAFE_CATEGORICAL_FEATURE_COLS: tuple[str, ...] = (
    "selection_bucket",
    "selection_profile",
    "stage_reached",
    "time_of_day_bucket",
    "session",
    "regime",
    "strategy_pass_pattern",
    "core_shadow_pattern",
    "core_rescue_reason",
    "budget_rescue_reason",
)

ML_DATASET_COLUMNS: tuple[str, ...] = (
    ML_IDENTIFIER_COLS
    + ML_DECISION_FEATURE_COLS
    + ML_COST_COLS
    + ML_POLICY_CONTEXT_COLS
    + ML_OUTCOME_COLS
)

BOOL_COLS: set[str] = {
    "pre_gate_passed",
    "shallow_selected",
    "deep_evaluated",
    "passes_profit_buffer",
    "core_shadow_evaluated",
    "core_shadow_trend_gate_passed",
    "core_shadow_passed",
    "core_rescue_applied",
    "budget_rescue_enabled",
    "budget_rescue_applied",
    "already_holding",
    "cooldown_blocked",
    "final_candidate",
    "executed",
    "label_positive_30m_net_cost",
    "label_positive_eod_net_cost",
    "label_top_decile_eod",
    "label_top_decile_eod_net_cost",
    "label_final_candidate_vs_reject",
}

INT_COLS: set[str] = {
    "passed_count_deep",
    "core_shadow_passed_count",
    "budget_rescue_qty",
    "current_price",
    "open_price",
    "low_price",
    "entry_price",
}

FLOAT_COLS: set[str] = {
    "score_shallow",
    "score_deep",
    "trend_alignment_score",
    "macd_momentum_score",
    "trend_quality_score",
    "momentum_quality_score",
    "price_efficiency_score",
    "pullback_pct",
    "rebound_pct",
    "gap_up_open_pct",
    "gap_down_open_pct",
    "range_recovery_ratio",
    "prev_day_change_pct",
    "core_rescue_selected_score",
    "expected_total_cost_krw",
    "expected_cost_bps",
    "net_edge_bps",
    "net_profit_buffer_bps",
    "cost_quality_score",
    "expected_cost_penalty",
    "ret_5m_bps",
    "ret_30m_bps",
    "ret_eod_bps",
    "effective_cost_bps_used",
    "net_ret_30m_after_cost_bps",
    "net_ret_eod_after_cost_bps",
}

TIMESTAMP_COLS: set[str] = {
    "ts",
    "entry_ts",
}

LABEL_DEFINITIONS: dict[str, str] = {
    "label_positive_eod_net_cost": (
        "True when ret_eod_bps - effective_cost_bps_used is strictly greater "
        "than the configured margin_bps."
    ),
    "label_positive_30m_net_cost": (
        "True when ret_30m_bps - effective_cost_bps_used is strictly greater "
        "than the configured margin_bps."
    ),
    "label_top_decile_eod": (
        "True when ret_eod_bps is greater than or equal to the in-sample top "
        "decile threshold."
    ),
    "label_top_decile_eod_net_cost": (
        "True when net_ret_eod_after_cost_bps is greater than or equal to the "
        "in-sample top decile threshold after cost."
    ),
    "label_final_candidate_vs_reject": (
        "Policy-imitation label copied from final_candidate. Use separately "
        "from economic outcome labels."
    ),
}


def normalize_null(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and value.strip() in {"", "None", "null", "NULL"}:
        return None
    return value


def parse_bool(value: Any) -> bool | None:
    value = normalize_null(value)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    return None


def parse_int(value: Any) -> int | None:
    value = normalize_null(value)
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            try:
                return int(float(value.strip()))
            except ValueError:
                return None
    return None


def parse_float(value: Any) -> float | None:
    value = normalize_null(value)
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def parse_timestamp(value: Any) -> datetime | None:
    value = normalize_null(value)
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def parse_trade_date(value: Any) -> str | None:
    parsed = parse_timestamp(value)
    if parsed is None:
        return None
    return parsed.date().isoformat()


def classify_time_of_day(value: Any) -> str | None:
    parsed = parse_timestamp(value)
    if parsed is None:
        return None
    minutes = parsed.hour * 60 + parsed.minute
    if minutes < 9 * 60:
        return "pre_open"
    if minutes < 9 * 60 + 30:
        return "open_30m"
    if minutes < 10 * 60 + 30:
        return "morning"
    if minutes < 13 * 60:
        return "midday"
    if minutes < 15 * 60 + 30:
        return "late_day"
    return "after_hours"


def build_row_id(row: dict[str, Any]) -> str:
    parts = (
        str(normalize_null(row.get("ts")) or "-"),
        str(normalize_null(row.get("cycle_id")) or "-"),
        str(normalize_null(row.get("symbol")) or "-"),
    )
    return "|".join(parts)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
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


def load_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return read_jsonl(path)
    if suffix == ".parquet":
        return read_flat_parquet(path)
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _ordered_columns_for_rows(
    ordered_columns: tuple[str, ...] | list[str] | None,
    rows: list[dict[str, Any]],
) -> list[str]:
    result = list(ordered_columns or [])
    seen = set(result)
    for row in rows:
        for key in row.keys():
            if key not in seen:
                result.append(key)
                seen.add(key)
    return result


def write_rows(
    *,
    rows: list[dict[str, Any]],
    path: Path,
    fmt: str,
    ordered_columns: tuple[str, ...] | list[str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "jsonl":
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, default=str))
                handle.write("\n")
        return

    columns = _ordered_columns_for_rows(ordered_columns, rows)
    if fmt == "parquet":
        write_flat_parquet(
            rows=rows,
            path=path,
            ordered_columns=columns,
            bool_cols=BOOL_COLS,
            int_cols=INT_COLS,
            float_cols=FLOAT_COLS,
            timestamp_cols=TIMESTAMP_COLS,
        )
        return

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column) for column in columns})


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def non_missing_count(rows: list[dict[str, Any]], column: str) -> int:
    return sum(1 for row in rows if normalize_null(row.get(column)) is not None)


def resolve_output_path(output: str, fmt: str, *, default_path: Path) -> Path:
    if not output:
        return default_path
    path = Path(output)
    if path.suffix.lower() == f".{fmt}":
        return path
    if path.suffix.lower() in {".csv", ".jsonl", ".parquet"}:
        return path
    return path.with_suffix(f".{fmt}")
