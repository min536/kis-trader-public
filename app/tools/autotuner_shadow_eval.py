"""CLI: autotuner_shadow_eval -- produce a DRAFT shadow proposal + evidence bundle.

Composes a proposed cadence change with real bridged evidence (a
run_proposal_backtest evaluation and/or a session summary) into a schema-valid
``mode=shadow`` draft written under ``_workspace/autotuner/proposals/``.
Read-only / proposal generation only: it never approves and never applies
anything to the runtime; no broker calls. The draft must still pass human
approval (app.tools.autotuner_approve) before any runtime reader could consume it.

Usage:
    python -m app.tools.autotuner_shadow_eval \\
        --parameter buy_scan_shallow_top_k --from-value 10 --to-value 12 \\
        --reason "shadow eval" \\
        --backtest-eval research/evaluations/eval_atp_...json \\
        --live-log-summary research/sessions/session_20260603.json
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from app.autotuner.evidence_sources import (
    load_backtest_evidence,
    load_live_log_evidence,
)
from app.autotuner.generator import write_proposal
from app.autotuner.shadow import build_shadow_proposal


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="autotuner_shadow_eval")
    parser.add_argument("--parameter", required=True)
    parser.add_argument("--to-value", type=int, required=True)
    parser.add_argument("--from-value", type=int, required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--proposal-id", default=None)
    parser.add_argument("--backtest-eval", action="append", default=[])
    parser.add_argument("--live-log-summary", action="append", default=[])
    parser.add_argument("--out-dir", default="_workspace/autotuner/proposals")
    args = parser.parse_args(argv)

    evidence = [load_backtest_evidence(path) for path in args.backtest_eval]
    evidence += [load_live_log_evidence(path) for path in args.live_log_summary]
    if not evidence:
        print("REFUSED: a shadow proposal requires at least one evidence source "
              "(--backtest-eval and/or --live-log-summary).")
        return 1
    proposal = build_shadow_proposal(
        parameter=args.parameter,
        to_value=args.to_value,
        baseline_values={args.parameter: args.from_value},
        proposal_id=args.proposal_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        reason=args.reason,
        evidence=evidence,
    )
    path = write_proposal(proposal, args.out_dir)
    print(f"draft shadow proposal written: {path} ({len(evidence)} evidence)")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
