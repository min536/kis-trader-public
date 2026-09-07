"""Human-gated approval transition for autotuner artifacts (post-Phase-4 Step 2).

``approve_proposal`` performs the ``draft -> approved`` transition on an autotuner
proposal: it stamps the human ``approval`` + ``ttl``, sets the approved mode, and
re-runs ``validate_proposal`` so an invalid or out-of-scope change can never be
approved. It is pure (returns a new dict, never writes) and refuses (raises
``ValueError``) on any doubt. The thin CLI in ``app/tools/autotuner_approve.py``
persists the result; this module owns the gate logic.
See docs/live_autotuner_pipeline_reconciliation.md.
"""

from __future__ import annotations

import copy

from app.autotuner.validator import load_whitelist, validate_proposal


def approve_proposal(
    proposal, *, approved_by, approved_at, expires_at, high_risk: bool = False
):
    approved = copy.deepcopy(proposal)
    approved["status"] = "approved"
    approved["mode"] = "approved_high_risk" if high_risk else "approved_low_risk"
    approved["approval"] = {"approved_by": approved_by, "approved_at": approved_at}
    approved["ttl"] = {"expires_at": expires_at}

    if high_risk:
        _validate_high_risk_requirements(approved)

    result = validate_proposal(approved, allow_high_risk=high_risk)
    if not result.accepted:
        raise ValueError(
            "refusing to approve an invalid proposal: " + "; ".join(result.rejections)
        )
    return approved


def _validate_high_risk_requirements(proposal: dict) -> None:
    """Producer-side Stage 3 guard for Tier B approvals."""
    rejections: list[str] = []
    evidence = (
        proposal.get("evidence") if isinstance(proposal.get("evidence"), list) else []
    )
    live_logs = sum(
        1
        for entry in evidence
        if isinstance(entry, dict) and entry.get("source_type") == "live_log"
    )
    if live_logs < 2:
        rejections.append(
            "approved_high_risk requires at least two live_log evidence entries"
        )
    risk_review = proposal.get("risk_review")
    if not isinstance(risk_review, dict) or not risk_review:
        rejections.append("approved_high_risk requires a non-empty risk_review block")

    known = load_whitelist().get("parameters", {})
    changes = (
        proposal.get("changes") if isinstance(proposal.get("changes"), list) else []
    )
    for change in changes:
        if not isinstance(change, dict):
            continue
        name = change.get("parameter")
        spec = known.get(name)
        if spec and spec.get("tier") != "B":
            rejections.append(
                f"approved_high_risk allows Tier B only; '{name}' is Tier {spec.get('tier')}"
            )

    if rejections:
        raise ValueError(
            "refusing to approve a high-risk proposal: " + "; ".join(rejections)
        )
