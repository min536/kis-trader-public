"""Autotuner periodic propose-cycle (post-Phase-4 Step 4).

One tick of the human-in-the-loop tuning loop, designed to be invoked
periodically (every N minutes during market hours) by an external scheduler.
For each operator-curated candidate it collects bridged evidence
(``evidence_sources``), shadow-evaluates it into a DRAFT proposal
(``build_shadow_proposal``), optionally persists the draft under
``_workspace/autotuner/proposals/`` (write-gated via ``assert_write_allowed``),
and renders a human notification report.

Hard boundaries: it NEVER approves and NEVER applies anything to the runtime, and
it makes no broker calls. Candidates come from a curated plan — auto-generating
parameter moves is a deliberate non-goal (a human decides what to evaluate). A bad
candidate is recorded as ``refused``; it never aborts the tick.
"""

from __future__ import annotations

from app.autotuner.evidence_sources import (
    load_backtest_evidence,
    load_live_log_evidence,
)
from app.autotuner.generator import write_proposal
from app.autotuner.persist import workspace_paths
from app.autotuner.shadow import build_shadow_proposal


def _collect_evidence(candidate: dict) -> list:
    evidence: list = []
    if candidate.get("backtest_eval"):
        evidence.append(load_backtest_evidence(candidate["backtest_eval"]))
    if candidate.get("live_log_summary"):
        evidence.append(load_live_log_evidence(candidate["live_log_summary"]))
    return evidence


def run_propose_cycle(plan, *, project_root, now, read_only=True, out_dir=None):
    if out_dir is None:
        out_dir = workspace_paths(project_root)["proposals"]
    outcomes = []
    written_paths = []
    for candidate in plan:
        outcome = {"proposal_id": candidate.get("proposal_id"), "status": "built"}
        try:
            evidence = _collect_evidence(candidate)
            proposal = build_shadow_proposal(
                parameter=candidate["parameter"],
                to_value=candidate["to_value"],
                baseline_values={candidate["parameter"]: candidate["from_value"]},
                proposal_id=candidate["proposal_id"],
                created_at=now.isoformat(),
                reason=candidate["reason"],
                evidence=evidence,
            )
        except (ValueError, KeyError, OSError) as exc:
            # A bad candidate is recorded as refused; it never aborts the tick.
            outcome["status"] = "refused"
            outcome["reason"] = str(exc)
            outcomes.append(outcome)
            continue
        if not read_only:
            path = write_proposal(proposal, out_dir)
            written_paths.append(str(path))
            outcome["written_path"] = str(path)
        outcomes.append(outcome)
    report = _render_report(outcomes, now=now, read_only=read_only)
    return {"outcomes": outcomes, "written_paths": written_paths, "report": report}


def _render_report(outcomes, *, now, read_only) -> str:
    built = [o for o in outcomes if o["status"] == "built"]
    refused = [o for o in outcomes if o["status"] == "refused"]
    mode = "read-only (dry-run)" if read_only else "write (drafts persisted)"
    lines = [
        f"# Autotuner Propose Cycle — {now.isoformat()}",
        "",
        f"- **Mode:** {mode}",
        f"- **Drafts built:** {len(built)}  |  **Refused:** {len(refused)}",
        "",
        "## Drafts (awaiting human approval)",
    ]
    if built:
        for o in built:
            loc = o.get("written_path", "(not written — dry-run)")
            lines.append(f"- `{o['proposal_id']}` → {loc}")
    else:
        lines.append("- none")
    if refused:
        lines.append("")
        lines.append("## Refused (not written)")
        for o in refused:
            lines.append(f"- `{o['proposal_id']}`: {o.get('reason', '')}")
    lines += [
        "",
        "> These are DRAFTS only. Nothing is approved or applied automatically. "
        "A human must review and approve via `app.tools.autotuner_approve` before "
        "any runtime reader (mock, default-OFF) could consume a proposal.",
    ]
    return "\n".join(lines) + "\n"
