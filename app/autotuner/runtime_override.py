"""Autotuner runtime-override readers (the runtime-reader surface).

Two independently-gated, mock-only, default-OFF readers that load the single
active approved bundle from _workspace/autotuner/proposals/ and produce a
``{settings_name: value}`` override dict:

- ``resolve_runtime_overrides`` (Phase 3) — Tier A, ``approved_low_risk``.
- ``resolve_high_risk_overrides`` (Phase 4) — Tier B, ``approved_high_risk``,
  with stronger gates (>=2 live_log + risk_review). Tier C stays blocked.

Both never raise (any doubt yields ``{}``) and are paired with a safer-of merge
so an override can never beat a protective clamp. Phase 3 activation is wired
through ``app.autotuner.runtime_activation`` and remains human-gated,
mock-only, and default-OFF.
See docs/live_autotuner_phase3_risk.md (M1–M10) and _phase4_risk.md (M11–M15).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.autotuner.persist import workspace_paths
from app.autotuner.validator import validate_proposal

_MAX_PROPOSAL_BYTES = 64 * 1024

# Tier A params the runtime seam actually supports, with the "safer" merge
# direction (M6/M7): intervals are safer larger, caps safer smaller.
_RUNTIME_APPLICABLE = {
    "sell_check_interval_seconds": "max",
    "buy_scan_interval_seconds": "max",
    "scan_symbols_max_per_cycle": "min",
    "buy_scan_shallow_top_k": "min",
    "buy_scan_deep_eval_limit": "min",
}

# Tier B params for the Phase 4 high-risk path (M15). Safer: longer cooldown,
# fewer same-symbol buys. Tier C/D are never here (and stay blocked).
_TIER_B_APPLICABLE = {
    "rebuy_cooldown_minutes": "max",
    "same_symbol_max_buys_per_day": "min",
}


def resolve_high_risk_overrides(*, project_root, now, env="mock", enabled_high_risk=False):
    if not enabled_high_risk or env != "mock":
        return {}
    try:
        return _resolve_high_risk(project_root, now)
    except Exception:
        # M1: a reader feeding a trading cycle must never raise — fail safe to {}.
        return {}


def _resolve_high_risk(project_root, now) -> dict:
    proposals = workspace_paths(project_root)["proposals"]
    eligible: list[dict] = []
    for path in sorted(Path(proposals).glob("*.json")):
        bundle = _read_proposal_json(path)
        if bundle is None:
            continue
        if not isinstance(bundle, dict):
            continue
        if bundle.get("status") != "approved" or bundle.get("mode") != "approved_high_risk":
            continue
        if not validate_proposal(bundle, allow_high_risk=True).accepted:
            continue
        live_logs = sum(
            1
            for e in (bundle.get("evidence") or [])
            if isinstance(e, dict) and e.get("source_type") == "live_log"
        )
        if live_logs < 2:
            continue
        if not bundle.get("risk_review"):
            continue
        if _is_expired(bundle, now):
            continue
        eligible.append(bundle)

    if len(eligible) != 1:
        return {}  # exactly one active high-risk bundle, else fail-safe empty (M4)

    overrides: dict = {}
    for change in eligible[0].get("changes", []):
        name = change.get("parameter")
        if name in _TIER_B_APPLICABLE:
            overrides[name] = change.get("to_value")
    return overrides


def merge_safer_of_settings(current_values, overrides):
    """safer-of merge for Tier B settings params (M15).

    rebuy_cooldown_minutes: larger (longer cooldown) is safer.
    same_symbol_max_buys_per_day: smaller (fewer buys) is safer.
    A protective current value always beats a more-aggressive override.
    """
    merged = dict(current_values or {})
    for name, direction in _TIER_B_APPLICABLE.items():
        if name not in (overrides or {}):
            continue
        override_value = overrides[name]
        if not isinstance(override_value, (int, float)):
            continue
        current = merged.get(name)
        if isinstance(current, (int, float)):
            merged[name] = max(current, override_value) if direction == "max" else min(
                current, override_value
            )
        else:
            merged[name] = override_value
    return merged


def merge_safer_of(runtime_rate_control, overrides):
    """Fold overrides into the runtime dict, always choosing the SAFER value (M7).

    Intervals: take the larger (slower). Caps: take the smaller (fewer). A
    protective degraded/midday clamp therefore always wins over an autotuner
    override that would push toward more aggressive.
    """
    merged = dict(runtime_rate_control or {})
    for name, direction in _RUNTIME_APPLICABLE.items():
        if name not in (overrides or {}):
            continue
        key = f"effective_{name}"
        override_value = overrides[name]
        current = merged.get(key)
        if not isinstance(override_value, (int, float)):
            continue
        if isinstance(current, (int, float)):
            merged[key] = max(current, override_value) if direction == "max" else min(
                current, override_value
            )
        else:
            merged[key] = override_value
    return merged


def _is_expired(bundle: dict, now) -> bool:
    """True if ttl.expires_at is missing/unparseable or at/past the injected now (M3)."""
    expires_raw = (bundle.get("ttl") or {}).get("expires_at")
    if not expires_raw:
        return True
    try:
        expires_at = datetime.fromisoformat(expires_raw)
    except (ValueError, TypeError):
        return True
    if expires_at.tzinfo is None:
        return True
    return now >= expires_at


def _read_proposal_json(path: Path) -> dict | list | None:
    try:
        if path.stat().st_size > _MAX_PROPOSAL_BYTES:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def resolve_runtime_overrides(*, project_root, now, env="mock", enabled=False):
    if not enabled or env != "mock":
        return {}
    try:
        return _resolve_active_overrides(project_root, now)
    except Exception:
        # M1: a reader feeding a trading cycle must never raise — fail safe to {}.
        return {}


def resolve_runtime_shadow_overrides(*, project_root, now):
    """Read-only Stage 4a shadow calculation with no activation side effects.

    This intentionally has no env/flag gate because it is not an activation path:
    it only answers "what would the Tier A reader have selected?" for live
    diagnostics. Any doubt still returns {}.
    """
    try:
        return _resolve_active_overrides(project_root, now)
    except Exception:
        return {}


def _resolve_active_overrides(project_root, now) -> dict:
    proposals = workspace_paths(project_root)["proposals"]
    eligible: list[dict] = []
    for path in sorted(Path(proposals).glob("*.json")):
        bundle = _read_proposal_json(path)
        if bundle is None:
            continue
        if not isinstance(bundle, dict):
            continue
        if bundle.get("status") != "approved" or bundle.get("mode") != "approved_low_risk":
            continue
        if not validate_proposal(bundle).accepted:
            continue
        if _is_expired(bundle, now):
            continue
        eligible.append(bundle)

    if len(eligible) != 1:
        return {}  # exactly one active approved bundle, else fail-safe empty (M4)

    overrides: dict = {}
    for change in eligible[0].get("changes", []):
        name = change.get("parameter")
        if name in _RUNTIME_APPLICABLE:
            overrides[name] = change.get("to_value")
    return overrides
