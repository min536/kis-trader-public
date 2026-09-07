"""CLI: proposal_registry

Manage the lifecycle of research proposals.

Proposal statuses (in order):
  proposed         → generated, not yet evaluated
  invalid          → failed constraint validation
  research_only    → evaluated, results recorded, not promoted
  shadow_candidate → human-approved for shadow observation
  live_candidate   → human-approved for live parameter change (requires explicit --promote-live)
  rejected         → explicitly rejected

This is the single source of truth for proposal state.
Live promotion requires explicit human action via --promote; it cannot happen automatically.

Usage:
    # List all proposals
    python3 -m app.tools.proposal_registry list

    # Show a single proposal
    python3 -m app.tools.proposal_registry show --id proposal_20260412_...

    # Register a new proposal from file
    python3 -m app.tools.proposal_registry register --file research/proposals/proposal_20260412_v1.json

    # Update status after evaluation
    python3 -m app.tools.proposal_registry update --id proposal_20260412_... --status research_only

    # Attach evaluation result
    python3 -m app.tools.proposal_registry attach-eval \\
        --id proposal_20260412_... \\
        --eval-file research/evaluations/eval_proposal_20260412_v1.json

    # Promote to shadow (human approval step)
    python3 -m app.tools.proposal_registry promote --id proposal_20260412_... --to shadow_candidate

    # Promote to live_candidate (requires explicit flag — cannot skip)
    python3 -m app.tools.proposal_registry promote --id proposal_20260412_... --to live_candidate --promote-live

    # Reject a proposal
    python3 -m app.tools.proposal_registry reject --id proposal_20260412_... --reason "MDD worse than baseline"
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_REGISTRY_PATH = _PROJECT_ROOT / "research" / "proposal_registry.json"

# Valid statuses in lifecycle order
_VALID_STATUSES = [
    "proposed",
    "invalid",
    "exhausted",       # all direction candidates already tried — no new change available
    "research_only",
    "shadow_candidate",
    "live_candidate",
    "rejected",
]

# Statuses that require explicit human approval (cannot be set programmatically)
_HUMAN_APPROVAL_REQUIRED = {"live_candidate"}

# Statuses that require --promote-live flag
_LIVE_PROMOTE_FLAG_REQUIRED = {"live_candidate"}


# ---------------------------------------------------------------------------
# Registry I/O
# ---------------------------------------------------------------------------


def _load_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"proposals": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"proposals": {}}
        if "proposals" not in data:
            data["proposals"] = {}
        return data
    except (json.JSONDecodeError, OSError):
        return {"proposals": {}}


def _save_registry(registry: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_registry_path(args_path: str) -> Path:
    if args_path:
        return Path(args_path)
    return _DEFAULT_REGISTRY_PATH


# ---------------------------------------------------------------------------
# Registry operations
# ---------------------------------------------------------------------------


def _change_fingerprint(changes: list[dict[str, Any]]) -> frozenset[tuple]:
    """Canonical fingerprint for a set of changes: {(family, param_id, new_value)}."""
    return frozenset(
        (str(c.get("family", "")), str(c.get("param_id", "")), c.get("new_value"))
        for c in changes
    )


def find_duplicate(
    registry: dict[str, Any],
    changes: list[dict[str, Any]],
    ignore_statuses: frozenset[str] = frozenset({"rejected"}),
) -> str | None:
    """Return the proposal_id of an existing non-rejected proposal with identical changes, or None."""
    fp = _change_fingerprint(changes)
    for pid, entry in (registry.get("proposals") or {}).items():
        if entry.get("status") in ignore_statuses:
            continue
        if _change_fingerprint(entry.get("changes") or []) == fp:
            return pid
    return None


def register_proposal(
    registry: dict[str, Any],
    proposal: dict[str, Any],
) -> str:
    proposal_id = str(proposal.get("proposal_id") or "")
    if not proposal_id:
        raise ValueError("proposal_id is missing")

    if proposal_id in registry["proposals"]:
        raise ValueError(f"Proposal {proposal_id} is already registered")

    # Detect duplicate change sets (same family/param/new_value, non-rejected)
    existing_dup = find_duplicate(registry, proposal.get("changes") or [])

    registry["proposals"][proposal_id] = {
        "proposal_id": proposal_id,
        "status": proposal.get("status", "proposed"),
        "direction": proposal.get("direction", ""),
        "source_snapshot_date": proposal.get("source_snapshot_date", ""),
        "source_account": proposal.get("source_account", ""),
        "changes": proposal.get("changes", []),
        "reasoning_summary": proposal.get("reasoning_summary", []),
        "risk_flags": proposal.get("risk_flags", []),
        "generated_at": proposal.get("generated_at", ""),
        "registered_at": datetime.now().isoformat(),
        "status_history": [
            {
                "status": proposal.get("status", "proposed"),
                "at": datetime.now().isoformat(),
                "note": "registered",
            }
        ],
        "evaluation": None,
        "rejection_reason": None,
        "promotion_notes": [],
        "duplicate_of": existing_dup,
    }
    return proposal_id


def update_status(
    registry: dict[str, Any],
    proposal_id: str,
    new_status: str,
    note: str = "",
    promote_live: bool = False,
) -> None:
    if new_status not in _VALID_STATUSES:
        raise ValueError(f"Invalid status: {new_status}. Must be one of {_VALID_STATUSES}")

    if new_status in _LIVE_PROMOTE_FLAG_REQUIRED and not promote_live:
        raise ValueError(
            f"Status '{new_status}' requires explicit --promote-live flag. "
            "This is a safety gate: live promotion must be intentional."
        )

    entry = registry["proposals"].get(proposal_id)
    if entry is None:
        raise KeyError(f"Proposal {proposal_id} not found in registry")

    old_status = entry["status"]
    entry["status"] = new_status
    entry["status_history"].append({
        "status": new_status,
        "from": old_status,
        "at": datetime.now().isoformat(),
        "note": note or "",
    })


def attach_evaluation(
    registry: dict[str, Any],
    proposal_id: str,
    evaluation: dict[str, Any],
) -> None:
    entry = registry["proposals"].get(proposal_id)
    if entry is None:
        raise KeyError(f"Proposal {proposal_id} not found in registry")

    entry["evaluation"] = {
        "attached_at": datetime.now().isoformat(),
        "overall_verdict": evaluation.get("overall_verdict", ""),
        "evaluations": evaluation.get("evaluations", []),
    }

    # Auto-set status to research_only if currently proposed and evaluation exists
    if entry["status"] == "proposed":
        update_status(registry, proposal_id, "research_only", note="auto after evaluation attached")


def reject_proposal(
    registry: dict[str, Any],
    proposal_id: str,
    reason: str,
) -> None:
    entry = registry["proposals"].get(proposal_id)
    if entry is None:
        raise KeyError(f"Proposal {proposal_id} not found in registry")
    entry["rejection_reason"] = reason
    update_status(registry, proposal_id, "rejected", note=reason)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

_STATUS_ICONS = {
    "proposed": "○",
    "invalid": "✗",
    "exhausted": "⊘",
    "research_only": "◎",
    "shadow_candidate": "◑",
    "live_candidate": "●",
    "rejected": "—",
}


def _fmt_status(status: str) -> str:
    icon = _STATUS_ICONS.get(status, "?")
    return f"{icon} {status}"


def _list_proposals(registry: dict[str, Any], status_filter: str = "") -> None:
    proposals = registry.get("proposals") or {}
    if not proposals:
        print("  (no proposals registered)")
        return

    rows = list(proposals.values())
    if status_filter:
        rows = [r for r in rows if r.get("status") == status_filter]

    rows.sort(key=lambda r: r.get("registered_at", ""), reverse=True)

    print(f"\n  {'ID':<52} {'STATUS':<18} {'VER':<5} {'DIRECTION':<25} {'DATE'}")
    print("  " + "-" * 118)
    for row in rows:
        pid = row.get("proposal_id", "")[:50]
        status = _fmt_status(row.get("status", ""))
        direction = str(row.get("direction", ""))[:23]
        date = row.get("source_snapshot_date", "")
        # Verdict from attached evaluation
        eval_data = row.get("evaluation") or {}
        raw_verdict = eval_data.get("overall_verdict", "")
        verdict = {"pass": "✓", "fail": "✗", "": "—"}.get(raw_verdict, raw_verdict[:3])
        dup = row.get("duplicate_of")
        dup_mark = " (dup)" if dup else ""
        print(f"  {pid:<52} {status:<18} {verdict:<5} {direction:<25} {date}{dup_mark}")
    print()


def _show_proposal(registry: dict[str, Any], proposal_id: str) -> None:
    entry = registry["proposals"].get(proposal_id)
    if entry is None:
        print(f"  ERROR: {proposal_id} not found")
        return

    print(f"\n  proposal_id : {entry['proposal_id']}")
    print(f"  status      : {_fmt_status(entry.get('status', ''))}")
    print(f"  direction   : {entry.get('direction', '')}")
    print(f"  date        : {entry.get('source_snapshot_date', '')}")
    print(f"  account     : {entry.get('source_account', '')}")
    print(f"  registered  : {entry.get('registered_at', '')}")
    print()

    changes = entry.get("changes") or []
    if changes:
        print("  changes:")
        for c in changes:
            delta = c.get("delta", "")
            sign = "+" if isinstance(delta, (int, float)) and delta > 0 else ""
            print(f"    {c.get('family')}/{c.get('param_id')} : {c.get('current_value')} → {c.get('new_value')} ({sign}{delta})")
        print()

    reasoning = entry.get("reasoning_summary") or []
    if reasoning:
        print("  reasoning:")
        for line in reasoning:
            print(f"    - {line}")
        print()

    risk_flags = entry.get("risk_flags") or []
    if risk_flags:
        print("  risk_flags:")
        for f in risk_flags:
            print(f"    ! {f}")
        print()

    eval_data = entry.get("evaluation")
    if eval_data:
        verdict = eval_data.get("overall_verdict", "")
        verdict_icon = {"pass": "✓ pass", "fail": "✗ fail"}.get(verdict, verdict)
        print(f"  evaluation verdict : {verdict_icon}")
        for ev in (eval_data.get("evaluations") or []):
            cid = ev.get("candidate_run_id", "")
            pc = ev.get("pass_criteria") or {}
            deltas = ev.get("deltas") or {}
            cperf = ev.get("candidate_performance") or {}
            bperf = ev.get("baseline_performance") or {}
            backend = ev.get("execution_backend", "rest")
            payload = ev.get("payload_mode", "compact")
            print(f"    candidate : {cid}")
            # Key metrics side-by-side
            def _fmt(v: Any) -> str:
                return f"{v:.3f}" if isinstance(v, float) else str(v) if v is not None else "—"
            print(f"    metrics   : return={_fmt(cperf.get('total_return'))} sharpe={_fmt(cperf.get('sharpe'))} "
                  f"mdd={_fmt(cperf.get('max_drawdown'))} win={_fmt(cperf.get('win_rate'))}% "
                  f"trades={cperf.get('trade_count','—')}")
            print(f"    vs base   : return={_fmt(bperf.get('total_return'))} sharpe={_fmt(bperf.get('sharpe'))} "
                  f"mdd={_fmt(bperf.get('max_drawdown'))} win={_fmt(bperf.get('win_rate'))}% "
                  f"trades={bperf.get('trade_count','—')}")
            def _dsign(v: Any) -> str:
                if not isinstance(v, (int, float)):
                    return "—"
                return f"+{v:.3f}" if v > 0 else f"{v:.3f}"
            print(f"    deltas    : Δreturn={_dsign(deltas.get('total_return'))} Δsharpe={_dsign(deltas.get('sharpe'))} "
                  f"Δmdd={_dsign(deltas.get('max_drawdown'))} Δwin={_dsign(deltas.get('win_rate'))}")
            crit_parts = []
            for k, v in pc.items():
                if k != "overall":
                    crit_parts.append(f"{'✓' if v else '✗'} {k}")
            print(f"    criteria  : {' | '.join(crit_parts)}")
            print(f"    backend   : {backend} | {payload}")
            if ev.get("duplicate_of"):
                print(f"    dup_of    : {ev['duplicate_of']}")
        print()

    dup_of = entry.get("duplicate_of")
    if dup_of:
        print(f"  duplicate_of : {dup_of}")
        print()

    history = entry.get("status_history") or []
    if history:
        print("  status history:")
        for h in history:
            note = h.get("note", "")
            note_str = f" — {note}" if note else ""
            print(f"    {h.get('at', '')[:19]}  {_fmt_status(h.get('status', ''))}{note_str}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the research proposal registry")
    parser.add_argument(
        "--registry",
        default="",
        help=f"Path to registry JSON file (default: {_DEFAULT_REGISTRY_PATH})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = sub.add_parser("list", help="List all proposals")
    p_list.add_argument("--status", default="", help="Filter by status")

    # show
    p_show = sub.add_parser("show", help="Show details of a proposal")
    p_show.add_argument("--id", required=True, help="proposal_id")

    # register
    p_reg = sub.add_parser("register", help="Register a new proposal from file")
    p_reg.add_argument("--file", required=True, help="Path to proposal JSON")

    # update
    p_upd = sub.add_parser("update", help="Update proposal status")
    p_upd.add_argument("--id", required=True)
    p_upd.add_argument("--status", required=True, choices=_VALID_STATUSES)
    p_upd.add_argument("--note", default="")
    p_upd.add_argument(
        "--promote-live",
        action="store_true",
        help="Required when setting status to live_candidate",
    )

    # attach-eval
    p_eval = sub.add_parser("attach-eval", help="Attach an evaluation result to a proposal")
    p_eval.add_argument("--id", required=True)
    p_eval.add_argument("--eval-file", required=True, help="Path to evaluation JSON")

    # promote
    p_promo = sub.add_parser("promote", help="Promote a proposal to a higher status")
    p_promo.add_argument("--id", required=True)
    p_promo.add_argument(
        "--to",
        required=True,
        choices=["shadow_candidate", "live_candidate"],
    )
    p_promo.add_argument("--note", default="")
    p_promo.add_argument(
        "--promote-live",
        action="store_true",
        help="Required when promoting to live_candidate",
    )

    # reject
    p_rej = sub.add_parser("reject", help="Reject a proposal")
    p_rej.add_argument("--id", required=True)
    p_rej.add_argument("--reason", default="")

    args = parser.parse_args()
    registry_path = _get_registry_path(args.registry)
    registry = _load_registry(registry_path)

    try:
        if args.command == "list":
            _list_proposals(registry, status_filter=args.status)
            return

        if args.command == "show":
            _show_proposal(registry, args.id)
            return

        if args.command == "register":
            proposal_file = Path(args.file)
            if not proposal_file.exists():
                print(f"ERROR: file not found: {proposal_file}", file=sys.stderr)
                sys.exit(1)
            proposal = json.loads(proposal_file.read_text(encoding="utf-8"))
            pid = register_proposal(registry, proposal)
            _save_registry(registry, registry_path)
            print(f"registered → {pid}")

        elif args.command == "update":
            update_status(
                registry,
                args.id,
                args.status,
                note=args.note,
                promote_live=args.promote_live,
            )
            _save_registry(registry, registry_path)
            print(f"updated {args.id} → {args.status}")

        elif args.command == "attach-eval":
            eval_file = Path(args.eval_file)
            if not eval_file.exists():
                print(f"ERROR: eval file not found: {eval_file}", file=sys.stderr)
                sys.exit(1)
            evaluation = json.loads(eval_file.read_text(encoding="utf-8"))
            attach_evaluation(registry, args.id, evaluation)
            _save_registry(registry, registry_path)
            print(f"evaluation attached to {args.id}")

        elif args.command == "promote":
            update_status(
                registry,
                args.id,
                args.to,
                note=args.note or f"promoted to {args.to}",
                promote_live=args.promote_live,
            )
            _save_registry(registry, registry_path)
            print(f"promoted {args.id} → {args.to}")

        elif args.command == "reject":
            reject_proposal(registry, args.id, reason=args.reason)
            _save_registry(registry, registry_path)
            print(f"rejected {args.id}")

    except (ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
