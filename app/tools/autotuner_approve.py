"""CLI: autotuner_approve — the human approval gate for autotuner artifacts.

Reviews a reviewed draft/shadow autotuner proposal and, only on the explicit
``--approve`` flag, performs the validated ``draft -> approved`` transition and
writes the artifact into the proposals dir the runtime reader consumes. Without
``--approve`` it is a read-only review (dry-run) and writes nothing. Local-file
only; never touches the broker, runtime state, or the trading-critical surface.

Usage:
    # Review only (writes nothing)
    python -m app.tools.autotuner_approve --proposal-file _workspace/autotuner/proposals/atp_...json \\
        --approved-by human:dan

    # Approve (writes the approved artifact)
    python -m app.tools.autotuner_approve --proposal-file ...json \\
        --approved-by human:dan --ttl-hours 24 --approve
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.autotuner.approval import approve_proposal
from app.autotuner.generator import write_proposal
from app.autotuner.persist import supersede_active_bundles


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autotuner_approve")
    parser.add_argument("--proposal-file", required=True)
    parser.add_argument("--approved-by", required=True)
    parser.add_argument("--out-dir", default="_workspace/autotuner/proposals")
    parser.add_argument("--ttl-hours", type=float, default=24.0)
    parser.add_argument("--expires-at", default=None)
    parser.add_argument(
        "--high-risk",
        action="store_true",
        help="approve as approved_high_risk; requires Tier B, risk_review, and >=2 live_log evidence",
    )
    parser.add_argument("--approve", action="store_true")
    return parser


def _print_review(proposal: dict) -> None:
    changes = proposal.get("changes") or []
    evidence = proposal.get("evidence") or []
    live_logs = sum(
        1 for e in evidence if isinstance(e, dict) and e.get("source_type") == "live_log"
    )
    print(f"proposal_id : {proposal.get('proposal_id')}")
    print(f"mode/status : {proposal.get('mode')} / {proposal.get('status')}")
    print(f"evidence    : {len(evidence)} ({live_logs} live_log)")
    for change in changes:
        if isinstance(change, dict):
            print(
                f"  change    : {change.get('parameter')} "
                f"{change.get('from_value')} -> {change.get('to_value')} "
                f"(Tier {change.get('risk_tier')})"
            )


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    proposal = json.loads(Path(args.proposal_file).read_text(encoding="utf-8"))
    _print_review(proposal)

    if not args.approve:
        print("dry-run: pass --approve to perform the validated approval (writes nothing).")
        return 0

    now = datetime.now(timezone.utc)
    expires_at = args.expires_at or (now + timedelta(hours=args.ttl_hours)).isoformat()
    try:
        approved = approve_proposal(
            proposal,
            approved_by=args.approved_by,
            approved_at=now.isoformat(),
            expires_at=expires_at,
            high_risk=args.high_risk,
        )
    except ValueError as exc:
        print(f"REFUSED: {exc}")
        return 1

    path = write_proposal(approved, args.out_dir)
    print(f"approved and written: {path}")

    # D6: a new approval supersedes the previously-active same-mode bundle so the
    # runtime reader keeps seeing exactly one active bundle (else fail-closed {}).
    superseded = supersede_active_bundles(
        args.out_dir,
        keep_proposal_id=approved["proposal_id"],
        mode=approved["mode"],
        now=now,
    )
    for prior in superseded:
        print(f"superseded prior active bundle: {prior}")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
