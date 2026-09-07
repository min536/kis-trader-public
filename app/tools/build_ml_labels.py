"""CLI: build_ml_labels

Derive cautious offline labels from an exported ML candidate dataset.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any

from app.tools.ml_research_common import (
    LABEL_DEFINITIONS,
    ML_DATASET_COLUMNS,
    ML_LABEL_COLS,
    load_rows,
    non_missing_count,
    normalize_null,
    parse_bool,
    parse_float,
    resolve_output_path,
    write_json,
    write_rows,
)


def _default_output_path(input_path: Path, fmt: str) -> Path:
    return input_path.with_name(f"{input_path.stem}_labeled.{fmt}")


def _default_manifest_path(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.stem}.manifest.json")


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = int(round((len(ordered) - 1) * q))
    index = max(0, min(index, len(ordered) - 1))
    return ordered[index]


def _effective_cost_bps(
    row: dict[str, Any],
    *,
    cost_bps_column: str,
    default_cost_bps: float,
) -> float:
    parsed = parse_float(row.get(cost_bps_column))
    if parsed is not None:
        return parsed
    return default_cost_bps


def build_labeled_rows(
    rows: list[dict[str, Any]],
    *,
    cost_bps_column: str,
    default_cost_bps: float,
    margin_bps: float,
    top_quantile: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    working = [dict(row) for row in rows]
    net_eod_values: list[float] = []
    raw_eod_values: list[float] = []

    for row in working:
        effective_cost_bps = _effective_cost_bps(
            row,
            cost_bps_column=cost_bps_column,
            default_cost_bps=default_cost_bps,
        )
        row["effective_cost_bps_used"] = round(effective_cost_bps, 2)

        ret_30m = parse_float(row.get("ret_30m_bps"))
        ret_eod = parse_float(row.get("ret_eod_bps"))

        net_30m = round(ret_30m - effective_cost_bps, 2) if ret_30m is not None else None
        net_eod = round(ret_eod - effective_cost_bps, 2) if ret_eod is not None else None
        row["net_ret_30m_after_cost_bps"] = net_30m
        row["net_ret_eod_after_cost_bps"] = net_eod

        row["label_positive_30m_net_cost"] = (
            net_30m > margin_bps if net_30m is not None else None
        )
        row["label_positive_eod_net_cost"] = (
            net_eod > margin_bps if net_eod is not None else None
        )

        final_candidate = parse_bool(row.get("final_candidate"))
        row["label_final_candidate_vs_reject"] = final_candidate

        if ret_eod is not None:
            raw_eod_values.append(ret_eod)
        if net_eod is not None:
            net_eod_values.append(net_eod)

    raw_threshold = _quantile(raw_eod_values, top_quantile)
    net_threshold = _quantile(net_eod_values, top_quantile)

    for row in working:
        ret_eod = parse_float(row.get("ret_eod_bps"))
        net_eod = parse_float(row.get("net_ret_eod_after_cost_bps"))
        row["label_top_decile_eod"] = (
            ret_eod >= raw_threshold if ret_eod is not None and raw_threshold is not None else None
        )
        row["label_top_decile_eod_net_cost"] = (
            net_eod >= net_threshold if net_eod is not None and net_threshold is not None else None
        )

    summary = {
        "generated_at": datetime.now().isoformat(),
        "row_count": len(working),
        "definitions": dict(LABEL_DEFINITIONS),
        "parameters": {
            "cost_bps_column": cost_bps_column,
            "default_cost_bps": default_cost_bps,
            "margin_bps": margin_bps,
            "top_quantile": top_quantile,
            "top_decile_eod_threshold_bps": raw_threshold,
            "top_decile_eod_net_cost_threshold_bps": net_threshold,
        },
        "coverage": {
            "ret_eod_bps": non_missing_count(working, "ret_eod_bps"),
            "ret_30m_bps": non_missing_count(working, "ret_30m_bps"),
            "label_positive_eod_net_cost": non_missing_count(working, "label_positive_eod_net_cost"),
            "label_positive_30m_net_cost": non_missing_count(working, "label_positive_30m_net_cost"),
            "label_top_decile_eod": non_missing_count(working, "label_top_decile_eod"),
            "label_final_candidate_vs_reject": non_missing_count(
                working, "label_final_candidate_vs_reject"
            ),
        },
        "class_balance": {},
        "notes": [
            "Economic labels are cost-aware when expected_cost_bps is present.",
            "Policy-imitation labels should be analyzed separately from return labels.",
            "Use time-ordered validation only; random CV is not supported.",
        ],
    }

    for column in (
        "label_positive_eod_net_cost",
        "label_positive_30m_net_cost",
        "label_top_decile_eod",
        "label_top_decile_eod_net_cost",
        "label_final_candidate_vs_reject",
    ):
        available = [
            parse_bool(row.get(column))
            for row in working
            if normalize_null(row.get(column)) is not None
        ]
        positives = sum(1 for value in available if value)
        summary["class_balance"][column] = {
            "available": len(available),
            "positive": positives,
            "positive_rate": round((positives / len(available)) * 100, 2) if available else None,
        }

    return working, summary


def _print_report(output_path: Path, manifest_path: Path, summary: dict[str, Any]) -> None:
    print()
    print("build_ml_labels")
    print(f"  rows        : {summary['row_count']}")
    print(
        "  eod_label   : "
        f"{summary['class_balance']['label_positive_eod_net_cost']['positive']}/"
        f"{summary['class_balance']['label_positive_eod_net_cost']['available']}"
    )
    print(
        "  30m_label   : "
        f"{summary['class_balance']['label_positive_30m_net_cost']['positive']}/"
        f"{summary['class_balance']['label_positive_30m_net_cost']['available']}"
    )
    print(f"  output      : {output_path}")
    print(f"  manifest    : {manifest_path}")
    print("  read        : cost-aware offline labels only, not live-policy targets")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Derive cost-aware offline labels from an ML candidate dataset."
    )
    parser.add_argument("--input", required=True, help="Input ML candidate dataset")
    parser.add_argument(
        "--format",
        choices=("csv", "jsonl", "parquet"),
        default="jsonl",
        help="Output format for the labeled dataset.",
    )
    parser.add_argument("--output", default="", help="Output labeled dataset path")
    parser.add_argument("--manifest-output", default="", help="Optional manifest JSON path")
    parser.add_argument(
        "--cost-bps-column",
        default="expected_cost_bps",
        help="Column to use for transaction-cost-aware labels.",
    )
    parser.add_argument(
        "--default-cost-bps",
        type=float,
        default=0.0,
        help="Fallback cost in bps when the dataset lacks the selected cost column.",
    )
    parser.add_argument(
        "--margin-bps",
        type=float,
        default=0.0,
        help="Minimum positive margin after cost for binary-positive labels.",
    )
    parser.add_argument(
        "--top-quantile",
        type=float,
        default=0.9,
        help="Quantile threshold for top-decile-style labels.",
    )
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    rows = load_rows(input_path)
    labeled_rows, summary = build_labeled_rows(
        rows,
        cost_bps_column=args.cost_bps_column,
        default_cost_bps=args.default_cost_bps,
        margin_bps=args.margin_bps,
        top_quantile=args.top_quantile,
    )

    output_path = resolve_output_path(
        args.output,
        args.format,
        default_path=_default_output_path(input_path, args.format),
    )
    manifest_path = Path(args.manifest_output) if args.manifest_output else _default_manifest_path(output_path)

    write_rows(
        rows=labeled_rows,
        path=output_path,
        fmt=args.format,
        ordered_columns=ML_DATASET_COLUMNS + ML_LABEL_COLS,
    )
    write_json(manifest_path, summary)
    _print_report(output_path, manifest_path, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
