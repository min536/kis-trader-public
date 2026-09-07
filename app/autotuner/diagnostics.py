"""Read-only diagnostics for the autotuner runtime-override gate (observability).

The hot-path resolvers (runtime_override.py) fail safe to ``{}`` silently, which
is correct for a trading cycle but opaque to an operator. These helpers re-run the
same gate read-only and return a structured explanation of WHY an override is or is
not applied (flag off, wrong env, no approved bundle, validation failure, expired,
ambiguous). They never raise and never apply anything. Pair with the
``app.tools.autotuner_status`` CLI.
"""

from __future__ import annotations


def diagnose_runtime_overrides(*, project_root, now, env, enabled):
    if not enabled:
        return {
            "flag_enabled": False,
            "env": env,
            "would_apply": {},
            "reason": "flag disabled (AUTOTUNER_RUNTIME_OVERRIDES_ENABLED off)",
        }
    if env != "mock":
        return {
            "flag_enabled": True,
            "env": env,
            "would_apply": {},
            "reason": f"env is {env!r}, not mock (Tier A activation is mock-only)",
        }
    from app.autotuner.runtime_override import resolve_runtime_overrides

    would_apply = resolve_runtime_overrides(
        project_root=project_root, now=now, env=env, enabled=enabled
    )
    if would_apply:
        reason = f"{len(would_apply)} parameter(s) from 1 eligible approved bundle"
    else:
        reason = _explain_empty(project_root, now)
    return {
        "flag_enabled": True,
        "env": env,
        "would_apply": would_apply,
        "reason": reason,
    }


def diagnose_live_shadow_runtime_overrides(*, project_root, now, env):
    """Stage 4a live read-only diagnostics.

    Unlike ``diagnose_runtime_overrides``, this does not model the activation
    gate. It computes what the Tier A reader would select while reporting that
    no runtime application is happening.
    """
    if env != "live":
        return {
            "shadow_only": True,
            "env": env,
            "runtime_activation": False,
            "would_apply": {},
            "reason": f"env is {env!r}, not live (live-shadow diagnostics only)",
        }

    from app.autotuner.runtime_override import resolve_runtime_shadow_overrides

    would_apply = resolve_runtime_shadow_overrides(project_root=project_root, now=now)
    if would_apply:
        reason = (
            "live-shadow only: "
            f"{len(would_apply)} parameter(s) from 1 eligible approved bundle; "
            "runtime activation remains disabled"
        )
    else:
        reason = "live-shadow only: " + _explain_empty(project_root, now)
    return {
        "shadow_only": True,
        "env": env,
        "runtime_activation": False,
        "would_apply": would_apply,
        "reason": reason,
    }


def diagnose_high_risk_overrides(*, project_root, now, env, enabled_high_risk):
    if not enabled_high_risk:
        return {
            "flag_enabled": False,
            "env": env,
            "would_apply": {},
            "reason": "flag disabled (AUTOTUNER_HIGH_RISK_OVERRIDES_ENABLED off)",
        }
    if env != "mock":
        return {
            "flag_enabled": True,
            "env": env,
            "would_apply": {},
            "reason": f"env is {env!r}, not mock (Tier B activation is mock-only)",
        }

    from app.autotuner.runtime_override import resolve_high_risk_overrides

    would_apply = resolve_high_risk_overrides(
        project_root=project_root,
        now=now,
        env=env,
        enabled_high_risk=enabled_high_risk,
    )
    if would_apply:
        reason = f"{len(would_apply)} Tier B parameter(s) from 1 eligible approved bundle"
    else:
        reason = _explain_high_risk_empty(project_root, now)
    return {
        "flag_enabled": True,
        "env": env,
        "would_apply": would_apply,
        "reason": reason,
    }


def _explain_empty(project_root, now) -> str:
    """Classify WHY no override applied: ambiguous (>1 active) vs none eligible.

    The runtime reader fails closed to {} when it cannot find exactly one active
    bundle. From an operator's seat the two empty cases look identical; this
    distinguishes them by re-counting active (approved + valid + unexpired)
    approved_low_risk bundles read-only.
    """
    import json
    from datetime import datetime
    from pathlib import Path

    from app.autotuner.persist import workspace_paths
    from app.autotuner.validator import validate_proposal

    proposals = workspace_paths(project_root)["proposals"]
    active = 0
    for path in sorted(Path(proposals).glob("*.json")):
        try:
            bundle = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(bundle, dict):
            continue
        if bundle.get("status") != "approved" or bundle.get("mode") != "approved_low_risk":
            continue
        if not validate_proposal(bundle).accepted:
            continue
        expires_raw = (bundle.get("ttl") or {}).get("expires_at")
        try:
            expires_at = datetime.fromisoformat(expires_raw) if expires_raw else None
        except (ValueError, TypeError):
            expires_at = None
        if expires_at is None or expires_at.tzinfo is None or now >= expires_at:
            continue
        active += 1

    if active >= 2:
        return (
            f"ambiguous: {active} active approved_low_risk bundles found "
            "(reader requires exactly one; supersede or expire the extras)"
        )
    return "no eligible approved_low_risk bundle (none approved/valid/unexpired)"


def _explain_high_risk_empty(project_root, now) -> str:
    import json
    from datetime import datetime
    from pathlib import Path

    from app.autotuner.persist import workspace_paths
    from app.autotuner.validator import validate_proposal

    proposals = workspace_paths(project_root)["proposals"]
    active = 0
    for path in sorted(Path(proposals).glob("*.json")):
        try:
            bundle = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(bundle, dict):
            continue
        if bundle.get("status") != "approved" or bundle.get("mode") != "approved_high_risk":
            continue
        if not validate_proposal(bundle, allow_high_risk=True).accepted:
            continue
        evidence = bundle.get("evidence") if isinstance(bundle.get("evidence"), list) else []
        live_logs = sum(
            1
            for entry in evidence
            if isinstance(entry, dict) and entry.get("source_type") == "live_log"
        )
        if live_logs < 2 or not bundle.get("risk_review"):
            continue
        expires_raw = (bundle.get("ttl") or {}).get("expires_at")
        try:
            expires_at = datetime.fromisoformat(expires_raw) if expires_raw else None
        except (ValueError, TypeError):
            expires_at = None
        if expires_at is None or expires_at.tzinfo is None or now >= expires_at:
            continue
        active += 1

    if active >= 2:
        return (
            f"ambiguous: {active} active approved_high_risk bundles found "
            "(reader requires exactly one; supersede or expire the extras)"
        )
    return "no eligible approved_high_risk bundle (none approved/valid/unexpired)"
