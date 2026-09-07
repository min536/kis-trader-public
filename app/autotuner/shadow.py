"""Shadow runner for autotuner proposals (Phase 2).

Compose a candidate change + already-collected evidence into a schema-valid
``mode=shadow`` proposal. Read-only: evaluates against evidence, never applies
to the runtime. Self-validates and refuses to emit an invalid artifact.
"""

from __future__ import annotations

from app.autotuner.generator import generate_mock_proposal
from app.autotuner.validator import validate_proposal


def build_shadow_proposal(
    *,
    parameter: str,
    to_value,
    baseline_values: dict,
    proposal_id: str,
    created_at: str,
    reason: str,
    evidence: list,
) -> dict:
    """Build a schema-valid ``mode=shadow`` proposal from a candidate + evidence."""
    proposal = generate_mock_proposal(
        parameter=parameter,
        to_value=to_value,
        baseline_values=baseline_values,
        proposal_id=proposal_id,
        created_at=created_at,
        reason=reason,
    )
    proposal["mode"] = "shadow"
    proposal["status"] = "pending_review"
    proposal["evidence"] = list(evidence)
    proposal["generated_by"]["name"] = "build_shadow_proposal"

    result = validate_proposal(proposal)
    if not result.accepted:
        raise ValueError(
            "shadow runner refused to emit an invalid proposal: "
            + "; ".join(result.rejections)
        )
    return proposal
