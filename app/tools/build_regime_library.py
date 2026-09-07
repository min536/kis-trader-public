"""CLI: build the US-regime library (M-C1, offline). See plan §4.

Assembles the regime library from V1 artifacts, read-only:

1. Load US index daily parquet per asset (``--us-root`` / ``--assets``) and
   build the session feature frame (``app.research.us_regime.features``).
2. Load the KR←US calendar parquet (``--calendar``).
3. For each regime's conditional KR days, load its records from the records
   parquet (``--records``) at the pyarrow level (scattered day set).
4. Cluster US days, run the conditional gate2 weight search per regime, and
   judge each against ``--base-artifact`` (candidates =
   ``generate_random_candidates`` with the default frozen keys — 13 search dims,
   plan review F-3).
5. Write the library JSON (``--library-out``) + a human report markdown
   (``--report-out``) with per-regime enrolled/rejected reasons and a
   baseline-comparison table (required by plan §4).

Safety: no broker/KIS/Toss network calls; consumes gate2/US artifacts read-only
and writes only to the given output paths. Like the sibling weight-search CLI,
it **refuses to run when ``BUY_SCAN_QUOTE_KIS_ENV`` is set** (defence in depth —
this CLI never reaches the scanner, but the refusal keeps the offline-only
contract uniform across the research tools). Real-data runs are operator work.
"""

from __future__ import annotations

import argparse
import os


def main(argv=None) -> dict:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if os.environ.get("BUY_SCAN_QUOTE_KIS_ENV"):
        raise RuntimeError(
            "build_regime_library requires BUY_SCAN_QUOTE_KIS_ENV unset "
            "(offline research tool — no live quote token)"
        )

    assets = tuple(a.strip() for a in args.assets.split(",") if a.strip())
    return run(
        us_root=args.us_root,
        assets=assets,
        calendar_path=args.calendar,
        records_path=args.records,
        base_artifact_path=args.base_artifact,
        library_out=args.library_out,
        report_out=args.report_out,
        k=args.k,
        seed=args.seed,
        random_candidates=args.random_candidates,
        min_days=args.min_days,
        min_selected_train=args.min_selected_train,
    )


def run(
    *,
    us_root,
    assets,
    calendar_path,
    records_path,
    base_artifact_path,
    library_out,
    report_out,
    k=None,
    seed: int = 20260702,
    random_candidates: int = 64,
    min_days: int = 40,
    min_selected_train: int = 30,
) -> dict:
    """Build the regime library from the input parquets/artifact and write the
    JSON + human report. Returns ``{"library": <dict>, ...paths}``."""
    import json
    from pathlib import Path

    import pandas as pd

    from app.gate2.schema import load_artifact
    from app.research.gate2.weight_search import generate_random_candidates
    from app.research.us_regime.features import build_feature_frame
    from app.research.us_regime.ingest import load_us_daily
    from app.research.us_regime.regimes import (
        build_regime_library,
        load_records_for_days,
    )

    us_daily_by_asset = {asset: load_us_daily(us_root, asset) for asset in assets}
    feature_frame = build_feature_frame(us_daily_by_asset, tuple(assets))

    kr_us_map = pd.read_parquet(calendar_path)
    for col in ("kr_day", "us_day"):
        kr_us_map[col] = pd.to_datetime(kr_us_map[col]).dt.date

    base_artifact = load_artifact(base_artifact_path)
    candidates = generate_random_candidates(base_artifact, random_candidates, seed)

    def records_for_kr_days(kr_days):
        if not kr_days:
            return []
        return load_records_for_days(records_path, list(kr_days))

    library = build_regime_library(
        feature_frame,
        kr_us_map,
        records_for_kr_days,
        base_artifact,
        candidates,
        k=k,
        seed=seed,
        assets=list(assets),
        min_days=min_days,
        min_selected_train=min_selected_train,
    )

    library_out = Path(library_out)
    library_out.parent.mkdir(parents=True, exist_ok=True)
    library_out.write_text(json.dumps(library, indent=2, sort_keys=True))

    report_out = Path(report_out)
    report_out.parent.mkdir(parents=True, exist_ok=True)
    report_out.write_text(_render_report(library))

    return {
        "library": library,
        "library_out": str(library_out),
        "report_out": str(report_out),
    }


def _render_report(library: dict) -> str:
    """Human-readable markdown report: per-regime enrolled/rejected reasons + a
    candidate-vs-baseline comparison table (required by plan §4)."""
    lines: list[str] = []
    lines.append("# US-regime library report")
    lines.append("")
    lines.append(f"- version: `{library['version']}`")
    lines.append(f"- created_utc: `{library['created_utc']}`")
    lines.append(f"- assets: {library['assets']}")
    lines.append(f"- k: {library['k']}")
    lines.append(f"- feature_columns: {library['feature_columns']}")
    lines.append(f"- max_assign_distance (p95): {library['max_assign_distance']:.6f}")
    lines.append(f"- scaler.as_of: `{library['scaler'].get('as_of')}`")
    lines.append(f"- fallback: `{library['fallback']}`")
    lines.append("")

    enrolled = sum(1 for r in library["regimes"] if r["enrolled"])
    lines.append(
        f"## Regimes — {enrolled}/{len(library['regimes'])} enrolled"
    )
    lines.append("")
    lines.append(
        "| regime_id | n_us_days | n_kr_days | enrolled | reason | "
        "candidate_total_bps | baseline_total_bps | candidate_sel | baseline_sel |"
    )
    lines.append(
        "|---|---|---|---|---|---|---|---|---|"
    )
    for regime in library["regimes"]:
        cand = regime.get("candidate_test") or {}
        base = regime.get("baseline_test") or {}
        lines.append(
            "| {rid} | {nus} | {nkr} | {enr} | {reason} | {ctot} | {btot} | "
            "{csel} | {bsel} |".format(
                rid=regime["regime_id"],
                nus=regime["n_us_days"],
                nkr=regime["n_kr_days"],
                enr=regime["enrolled"],
                reason=regime["reason"],
                ctot=_fmt(cand.get("total_return_bps")),
                btot=_fmt(base.get("total_return_bps")),
                csel=cand.get("selected_count", "-"),
                bsel=base.get("selected_count", "-"),
            )
        )
    lines.append("")
    lines.append(
        "> Judgment (plan §7): a regime enrols ONLY when its candidate strictly "
        "beats the baseline on the held-out test slice under the dd_adjusted "
        "objective. If NO regime improves over baseline, Track M is not adopted "
        "— that null result is itself a valid conclusion."
    )
    lines.append("")
    return "\n".join(lines)


def _fmt(value) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the US-regime library (M-C1, offline)."
    )
    parser.add_argument("--us-root", required=True, help="US daily parquet root")
    parser.add_argument(
        "--assets",
        default="SPX,NDX,VIX,SOX",
        help="comma-separated asset labels (default SPX,NDX,VIX,SOX)",
    )
    parser.add_argument("--calendar", required=True, help="KR<-US calendar parquet")
    parser.add_argument("--records", required=True, help="per-horizon records parquet")
    parser.add_argument("--base-artifact", required=True, help="baseline ScoreV2 JSON")
    parser.add_argument("--library-out", required=True, help="library JSON output path")
    parser.add_argument("--report-out", required=True, help="human report .md output")
    parser.add_argument("--k", type=int, default=None, help="fixed k (else choose_k)")
    parser.add_argument("--seed", type=int, default=20260702)
    parser.add_argument("--random-candidates", type=int, default=64)
    parser.add_argument("--min-days", type=int, default=40)
    parser.add_argument("--min-selected-train", type=int, default=30)
    return parser


if __name__ == "__main__":  # pragma: no cover
    main()
