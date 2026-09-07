"""Pure generator for autotuner proposal artifacts (Phase 1).

Given a baseline and a desired parameter change, emit a schema-valid
``mode=mock`` proposal. NO runtime injection: nothing in app.main or the
session loop reads this. Every emitted proposal must be accepted by
``app.autotuner.validator.validate_proposal``.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from uuid import uuid4

from app.autotuner.validator import load_whitelist, validate_proposal

_PROPOSAL_ID_RE = re.compile(r"^atp_\d{8}_[a-z_]+_\d{4,}$")


def write_proposal(proposal: dict, directory) -> Path:
    """Serialise a proposal to ``{proposal_id}.json`` under ``directory``."""
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    proposal_id = str(proposal["proposal_id"])
    if not _PROPOSAL_ID_RE.fullmatch(proposal_id):
        raise ValueError("proposal_id is not a safe autotuner proposal id")
    base = target_dir.resolve()
    path = (base / f"{proposal_id}.json").resolve()
    if path.parent != base:
        raise ValueError("proposal path escapes the target directory")
    tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(proposal, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return path


def generate_mock_proposal(
    *,
    parameter: str,
    to_value,
    baseline_values: dict,
    proposal_id: str,
    created_at: str,
    reason: str,
) -> dict:
    """Build a schema-valid ``mode=mock`` proposal for a single parameter change."""
    spec = load_whitelist().get("parameters", {}).get(parameter, {})
    change = {
        "parameter": parameter,
        "from_value": baseline_values.get(parameter),
        "to_value": to_value,
        "type": spec.get("type"),
        "risk_tier": spec.get("tier"),
        "eligible_phase": spec.get("eligible_phase"),
        "bound_min": spec.get("bound_min"),
        "bound_max": spec.get("bound_max"),
        "max_step": spec.get("max_step"),
        "validation_status": "not_validated",
        "rationale": reason,
        "notes": "mock only",
    }
    proposal = {
        "schema_version": "0.1.0",
        "proposal_id": proposal_id,
        "created_at": created_at,
        "generated_by": {
            "kind": "script",
            "name": "generate_mock_proposal",
            "version": "0.1.0",
        },
        "mode": "mock",
        "status": "draft",
        "reason": reason,
        "evidence": [],
        "baseline": {"baseline_id": "baseline", "values": dict(baseline_values)},
        "changes": [change],
        "constraints_checked": {},
        "risk_review": None,
        "approval": None,
        "ttl": None,
        "rollback": {"rollback_baseline_id": "baseline"},
        "audit": {
            "events": [
                {"at": created_at, "actor": "generate_mock_proposal", "event": "created"}
            ]
        },
    }

    result = validate_proposal(proposal)
    if not result.accepted:
        raise ValueError(
            "generator refused to emit an invalid proposal: " + "; ".join(result.rejections)
        )
    return proposal
