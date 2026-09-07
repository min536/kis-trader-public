"""Pure validator for autotuner proposal artifacts.

Read-only by construction: this module parses a proposal dict and returns an
accept/reject verdict. Runtime readers re-run it before consuming approved
artifacts, but the validator itself has no runtime write path. See
docs/live_autotuner_proposal_schema.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

_WHITELIST_PATH = Path(__file__).resolve().parents[2] / "config" / "autotuner_whitelist.yaml"


@dataclass
class ValidationResult:
    accepted: bool
    rejections: list[str] = field(default_factory=list)


def load_whitelist(path: Path | None = None) -> dict:
    """Load the autotuner whitelist/bounds config (single source of truth)."""
    target = Path(path) if path is not None else _WHITELIST_PATH
    with target.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def validate_proposal(proposal, *, allow_high_risk: bool = False) -> ValidationResult:
    if not isinstance(proposal, dict):
        return ValidationResult(
            accepted=False,
            rejections=["proposal must be an object"],
        )

    whitelist = load_whitelist()
    known = whitelist.get("parameters", {})
    forbidden = set(whitelist.get("forbidden_tier_d", []))

    rejections: list[str] = []

    if proposal.get("mode") == "approved_high_risk" and not allow_high_risk:
        rejections.append(
            "mode 'approved_high_risk' is disabled by default (requires explicit opt-in)"
        )

    baseline_raw = proposal.get("baseline")
    if not baseline_raw:
        rejections.append("missing baseline: a comparison baseline is required")
    elif not isinstance(baseline_raw, dict):
        rejections.append("baseline must be an object")
    baseline = baseline_raw if isinstance(baseline_raw, dict) else {}
    baseline_values = baseline.get("values") if isinstance(baseline, dict) else {}
    if not isinstance(baseline_values, dict):
        if baseline:
            rejections.append("baseline.values must be an object")
        baseline_values = {}

    live_eligible = proposal.get("mode") in {"approved_low_risk", "approved_high_risk"}

    evidence_required = proposal.get("mode") in {"shadow"} or live_eligible
    evidence_raw = proposal.get("evidence") or []
    if isinstance(evidence_raw, list):
        evidence = evidence_raw
    else:
        rejections.append("evidence must be a list")
        evidence = []
    if evidence_required and not evidence:
        rejections.append(f"mode '{proposal.get('mode')}' requires non-empty evidence")
    elif live_eligible and not any(
        isinstance(e, dict) and e.get("source_type") == "live_log" for e in evidence
    ):
        # Trust boundary (D4): proxy backtest/MCP evidence is not enough for a
        # live-affecting change — at least one live_log cross-validation is required.
        rejections.append(
            "live-eligible proposals require at least one live_log cross-validation evidence"
        )

    ttl_raw = proposal.get("ttl")
    if ttl_raw is None:
        ttl = {}
    elif isinstance(ttl_raw, dict):
        ttl = ttl_raw
    else:
        rejections.append("ttl must be an object")
        ttl = {}
    expires_at_raw = ttl.get("expires_at")
    if live_eligible and not expires_at_raw:
        rejections.append("live-eligible proposals require a ttl with expires_at")
    if expires_at_raw:
        try:
            expires_at = datetime.fromisoformat(expires_at_raw)
        except (ValueError, TypeError):
            rejections.append(f"ttl.expires_at is not a valid ISO-8601 datetime: {expires_at_raw}")
        else:
            if expires_at.tzinfo is None:
                rejections.append("ttl.expires_at must include a timezone offset")
            elif datetime.now(timezone.utc) > expires_at:
                rejections.append(f"proposal expired at {expires_at_raw}")

    if proposal.get("status") == "approved":
        approval_raw = proposal.get("approval")
        if isinstance(approval_raw, dict):
            approval = approval_raw
        else:
            rejections.append("approval must be an object")
            approval = {}
        if not approval.get("approved_by") or not approval.get("approved_at"):
            rejections.append(
                "status=approved requires approval.approved_by and approval.approved_at"
            )
        else:
            rejections.extend(_check_self_approval(proposal, approval))

    raw_changes = proposal.get("changes")
    if not isinstance(raw_changes, list) or not raw_changes:
        rejections.append("changes must be a non-empty list of change objects")
        raw_changes = []
    for change in raw_changes:
        if not isinstance(change, dict):
            rejections.append("each change must be an object")
            continue
        name = change.get("parameter")
        if not isinstance(name, str):
            rejections.append(f"change has an invalid parameter name: {name!r}")
            continue
        if name in forbidden or change.get("risk_tier") == "D":
            rejections.append(f"parameter '{name}' is forbidden (Tier D)")
            continue
        if name not in known:
            rejections.append(f"parameter '{name}' is not in the whitelist (unknown)")
            continue
        spec = known[name]
        if spec.get("blocked"):
            rejections.append(
                f"parameter '{name}' is blocked (Tier {spec.get('tier')}); "
                "not eligible until bounds/evidence are established"
            )
            continue
        if proposal.get("mode") == "approved_low_risk" and spec.get("tier") != "A":
            rejections.append(
                f"approved_low_risk allows Tier A only; '{name}' is Tier {spec.get('tier')}"
            )
        to_value = change.get("to_value")
        from_value = change.get("from_value")
        if name in baseline_values and baseline_values[name] != from_value:
            rejections.append(
                f"parameter '{name}' from_value does not match baseline value"
            )
        numeric_param = spec.get("type") in {"int", "float"}
        if numeric_param and (
            not isinstance(from_value, (int, float)) or isinstance(from_value, bool)
        ):
            rejections.append(f"parameter '{name}' requires numeric from_value")
        if numeric_param and (
            not isinstance(to_value, (int, float)) or isinstance(to_value, bool)
        ):
            rejections.append(
                f"parameter '{name}' requires {spec.get('type')} numeric to_value"
            )
        bound_min = spec.get("bound_min")
        bound_max = spec.get("bound_max")
        if isinstance(to_value, (int, float)):
            if bound_min is not None and to_value < bound_min:
                rejections.append(
                    f"parameter '{name}' value {to_value} below bound_min {bound_min}"
                )
            if bound_max is not None and to_value > bound_max:
                rejections.append(
                    f"parameter '{name}' value {to_value} above bound_max {bound_max}"
                )
            max_step = spec.get("max_step")
            if (
                isinstance(from_value, (int, float))
                and max_step is not None
                and abs(to_value - from_value) > max_step
            ):
                rejections.append(
                    f"parameter '{name}' step {abs(to_value - from_value)} exceeds max_step {max_step}"
                )
    rejections.extend(_check_cross_parameter(proposal))

    return ValidationResult(accepted=not rejections, rejections=rejections)


_AUTOMATED_GENERATOR_KINDS = {"script", "bot", "mcp", "automated", "agent"}


def _check_self_approval(proposal, approval) -> list[str]:
    """Schema §8: an automated generator may not approve its own proposal.

    The prohibition targets self-approval by the same *automated* actor; a human
    approver of an automated draft is the intended human-in-the-loop path. We
    reject only when ``generated_by`` is an automated kind and ``approved_by``
    names that same actor (``name`` or ``kind:name``).
    """
    gen = proposal.get("generated_by")
    if not isinstance(gen, dict):
        return []
    gen_kind = gen.get("kind")
    gen_name = gen.get("name")
    if gen_kind not in _AUTOMATED_GENERATOR_KINDS or not gen_name:
        return []
    approved_by = approval.get("approved_by")
    if approved_by in {gen_name, f"{gen_kind}:{gen_name}"}:
        return [
            "self-approve prohibited: an automated generator "
            f"({gen_kind}:{gen_name}) cannot approve its own proposal"
        ]
    return []


def _effective_values(proposal) -> dict:
    """Resulting parameter values: baseline overlaid with each change's to_value."""
    baseline = proposal.get("baseline") if isinstance(proposal, dict) else {}
    baseline = baseline if isinstance(baseline, dict) else {}
    baseline_values = baseline.get("values") or {}
    effective = dict(baseline_values) if isinstance(baseline_values, dict) else {}
    raw_changes = proposal.get("changes")
    items = raw_changes if isinstance(raw_changes, list) else []
    for change in items:
        if not isinstance(change, dict):
            continue
        name = change.get("parameter")
        if isinstance(name, str):
            effective[name] = change.get("to_value")
    return effective


def _check_cross_parameter(proposal) -> list[str]:
    """Enforce ordering constraints on the effective resulting state (doc §5)."""
    eff = _effective_values(proposal)
    out: list[str] = []
    deep = eff.get("buy_scan_deep_eval_limit")
    shallow = eff.get("buy_scan_shallow_top_k")
    scan_max = eff.get("scan_symbols_max_per_cycle")
    if isinstance(deep, (int, float)) and isinstance(shallow, (int, float)) and deep > shallow:
        out.append(
            "cross-parameter: buy_scan_deep_eval_limit "
            f"({deep}) must not exceed buy_scan_shallow_top_k ({shallow})"
        )
    if (
        isinstance(shallow, (int, float))
        and isinstance(scan_max, (int, float))
        and shallow > scan_max
    ):
        out.append(
            "cross-parameter: buy_scan_shallow_top_k "
            f"({shallow}) must not exceed scan_symbols_max_per_cycle ({scan_max})"
        )
    return out
