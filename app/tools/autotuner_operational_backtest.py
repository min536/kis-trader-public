"""CLI: autotuner_operational_backtest -- offline cadence eval producer.

Builds autotuner-domain backtest/eval artifacts for operational cadence params.
The output is consumed by ``autotuner_screen`` and then by ``autotuner_suggest``.
No broker calls, no app.main, no runtime apply.
"""

from __future__ import annotations


def main(argv=None) -> int:
    import argparse
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    from app.autotuner.operational_backtest import (
        build_operational_cadence_eval,
        load_session_summaries,
    )

    parser = argparse.ArgumentParser(prog="autotuner_operational_backtest")
    parser.add_argument(
        "--candidates", required=True, help="JSON list of {parameter,to_value}"
    )
    parser.add_argument("--baseline", required=True, help="JSON object of baseline values")
    parser.add_argument(
        "--session-summary",
        action="append",
        default=[],
        help="Local session summary JSON. Repeat for multiple sessions.",
    )
    parser.add_argument(
        "--sessions-dir",
        default="",
        help="Optional directory of local session summary JSON files.",
    )
    parser.add_argument(
        "--holdout-summary",
        action="append",
        default=[],
        help="Out-of-sample (holdout) session summary JSON for W1 walk-forward. "
        "Repeat for multiple sessions.",
    )
    parser.add_argument(
        "--holdout-dir",
        default="",
        help="Optional directory of out-of-sample (holdout) session summary JSON files.",
    )
    parser.add_argument("--out", required=True, help="Output eval JSON path")
    parser.add_argument(
        "--repo-head",
        default="",
        help="Optional commit id to stamp into provenance for audit parity.",
    )
    args = parser.parse_args(list(argv or []))

    candidates = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    if not isinstance(candidates, list):
        print("error: --candidates must be a JSON list")
        return 2
    if not isinstance(baseline, dict):
        print("error: --baseline must be a JSON object")
        return 2

    session_paths = [Path(raw) for raw in args.session_summary]
    if args.sessions_dir:
        sessions_dir = Path(args.sessions_dir)
        if not sessions_dir.is_dir():
            print(f"error: --sessions-dir is not a directory: {sessions_dir}")
            return 2
        session_paths.extend(sorted(sessions_dir.glob("*.json")))
    summaries, loaded_paths = load_session_summaries(session_paths)
    if not summaries:
        print(
            "error: at least one readable --session-summary "
            "or --sessions-dir JSON is required"
        )
        return 2

    holdout_paths = [Path(raw) for raw in args.holdout_summary]
    if args.holdout_dir:
        holdout_dir = Path(args.holdout_dir)
        if not holdout_dir.is_dir():
            print(f"error: --holdout-dir is not a directory: {holdout_dir}")
            return 2
        holdout_paths.extend(sorted(holdout_dir.glob("*.json")))
    holdout_summaries, holdout_loaded_paths = load_session_summaries(holdout_paths)

    artifact = build_operational_cadence_eval(
        candidates,
        baseline_values=baseline,
        session_summaries=summaries,
        generated_at=datetime.now(timezone.utc),
        source_paths=loaded_paths,
        repo_head=args.repo_head or None,
        holdout_summaries=holdout_summaries or None,
        holdout_source_paths=holdout_loaded_paths,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "operational cadence eval: "
        f"{len(artifact['evaluations'])} candidate(s), "
        f"overall={artifact['overall_verdict']} -> {out}"
    )
    walk_forward = artifact.get("walk_forward")
    if walk_forward:
        # overall= above is in-sample only; surface the holdout reconciliation so the
        # operator sees a contradicted move at the point of use, not just in the JSON.
        print(
            "walk-forward holdout: "
            f"{walk_forward['confirmed']} confirmed, "
            f"{walk_forward['holdout_contradicted']} contradicted (downgraded), "
            f"{walk_forward['in_sample_fail']} in-sample-fail"
        )
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
