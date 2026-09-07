"""CLI: proposal_generator

Read a research snapshot and emit a research proposal JSON.

The generator applies rule-based logic on the snapshot to decide:
  1. Which family to target (core vs continuation)
  2. Which parameters to vary
  3. What direction (relax / tighten / shift)

Each proposal is research_only by default.
Live promotion requires separate human approval via proposal_registry.

Usage:
    python3 -m app.tools.proposal_generator \\
        --snapshot research/snapshots/snapshot_20260412.json

    python3 -m app.tools.proposal_generator \\
        --snapshot research/snapshots/snapshot_20260412.json \\
        --output-file research/proposals/proposal_20260412_v1.json

    # Dry-run: print without saving
    python3 -m app.tools.proposal_generator \\
        --snapshot research/snapshots/snapshot_20260412.json \\
        --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app.tools.proposal_constraints import (
    MAX_PARAMS_PER_PROPOSAL,
    ConstraintViolation,
    ParamSpec,
    params_for_family,
    validate_proposal_changes,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Signal extraction from snapshot
# ---------------------------------------------------------------------------


def _get(d: Any, *keys: str, default: Any = None) -> Any:
    """Safe nested dict access."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, default)
        if cur is None:
            return default
    return cur


def _ml_direction_hint(ml: dict[str, Any]) -> str | None:
    """
    Map the ML viability best_label → a direction hint.

    Only used when ML confidence is medium or high — never overrides
    hard evidence like sharpe comparison or dominant_core_reason.
    Returns None when confidence is low or no best label.
    """
    confidence = str(ml.get("best_label_confidence") or "low").lower()
    if confidence == "low":
        return None
    best_label = str(ml.get("best_label_candidate") or "").lower()
    if not best_label:
        return None
    # 30m label → intraday momentum → continuation-style entries perform better
    if "30m" in best_label:
        return "continuation_shift"
    # top_decile label → quality-focused → tighten risk to select best names
    if "top_decile" in best_label:
        return "tighten_core_risk"
    # eod label → end-of-day hold → relax entry threshold to capture more
    if "eod" in best_label:
        return "relax_core_threshold"
    return None


def _extract_signals(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Pull the key decision signals out of a snapshot."""
    ot = _get(snapshot, "live_diagnostics", "overnight_tuning", default={})
    cd = _get(snapshot, "live_diagnostics", "core_change_decision", default={})
    bts = snapshot.get("backtest_baselines") or {}
    ml = snapshot.get("ml_status") or {}
    meta = snapshot.get("snapshot_meta") or {}

    core_bt = bts.get("core_family_approx") or {}
    cont_bt = bts.get("continuation_family_approx") or {}

    return {
        # Live funnel
        "core_deep": _get(ot, "core_funnel", "deep_eval", default=0),
        "core_final": _get(ot, "core_funnel", "final_candidate", default=0),
        "core_exec": _get(ot, "core_funnel", "executed", default=0),
        "dominant_core_reason": _get(ot, "bottleneck", "dominant_core_reason", default="unknown"),
        "rule_contrast_verdict": _get(cd, "known_facts", "rule_contrast", "verdict", default="unknown"),
        "gap_to_threshold": _get(cd, "known_facts", "rule_contrast", "gap_to_threshold"),
        # Operational pressure
        "sell_watch_partial": _get(ot, "operational_pressure", "sell_watch_partial", default=0),
        "sell_watch_rl": _get(ot, "operational_pressure", "sell_watch_rate_limit", default=0),
        "rate_limit_triggered": _get(ot, "operational_pressure", "rate_limit_triggered", default=0),
        # Backtest comparison
        "core_sharpe": _get(core_bt, "performance", "sharpe"),
        "core_return": _get(core_bt, "performance", "total_return"),
        "core_mdd": _get(core_bt, "performance", "max_drawdown"),
        "cont_sharpe": _get(cont_bt, "performance", "sharpe"),
        "cont_return": _get(cont_bt, "performance", "total_return"),
        "cont_mdd": _get(cont_bt, "performance", "max_drawdown"),
        # Baseline staleness (surfaced from snapshot_meta)
        "stale_baselines": meta.get("stale_baselines") or [],
        # ML status
        "ml_best_label": ml.get("best_label_candidate"),
        "ml_confidence": ml.get("best_label_confidence", "low"),
        # ML directional hints: derived from best label name
        # label_positive_30m_net_cost  → short-hold momentum → continuation_shift
        # label_positive_eod_net_cost  → day-close hold      → relax_core_threshold
        # label_top_decile_eod*        → quality filter       → tighten_core_risk
        "ml_direction_hint": _ml_direction_hint(ml),
        # Direction from core_change_decision
        "core_decision_recommended": _get(cd, "decision_read", "recommended", default="direction_1"),
    }


# ---------------------------------------------------------------------------
# Direction logic
# ---------------------------------------------------------------------------

# Possible directions for a proposal
_DIR_RELAX_CORE_THRESHOLD = "relax_core_threshold"       # lower RSI bar, reduce passed_count pressure
_DIR_TIGHTEN_CORE_RISK = "tighten_core_risk"             # tighter stop, wider TP — quality filter
_DIR_CONTINUATION_SHIFT = "continuation_shift"           # push toward continuation-style entry
_DIR_CONTINUATION_RISK = "continuation_risk_tune"        # tune risk params in continuation family
_DIR_NO_CHANGE = "no_change"

# Fallback chain: if a direction is exhausted, try the next one in order
_DIRECTION_FALLBACK: dict[str, str] = {
    _DIR_CONTINUATION_SHIFT: _DIR_RELAX_CORE_THRESHOLD,
    _DIR_RELAX_CORE_THRESHOLD: _DIR_TIGHTEN_CORE_RISK,
    _DIR_TIGHTEN_CORE_RISK: _DIR_CONTINUATION_SHIFT,
}


def _pick_direction(sig: dict[str, Any]) -> str:
    reason = str(sig["dominant_core_reason"]).lower()
    verdict = str(sig["rule_contrast_verdict"]).lower()
    core_sharpe = sig["core_sharpe"]
    cont_sharpe = sig["cont_sharpe"]
    ml_hint = sig.get("ml_direction_hint")  # set when ML confidence >= medium

    # ── Hard evidence (always overrides ML) ──────────────────────────────────

    # If continuation baseline is clearly better, explore shifting
    if (
        core_sharpe is not None
        and cont_sharpe is not None
        and cont_sharpe > core_sharpe + 0.3
        and cont_sharpe > 0
    ):
        return _DIR_CONTINUATION_SHIFT

    # If core is failing due to passed_count and verdict says threshold pressure
    if "passed_count_insufficient" in reason and "threshold" in verdict:
        return _DIR_RELAX_CORE_THRESHOLD

    # If core is failing due to profit_buffer
    if "profit_buffer" in reason:
        return _DIR_TIGHTEN_CORE_RISK

    # ── ML hint as tiebreaker (only when hard evidence is ambiguous) ─────────
    # Reaches here when no single hard signal dominates. ML direction hint
    # (derived from the best label at medium/high confidence) tips the balance.
    if ml_hint:
        return ml_hint

    # Default: relax threshold
    return _DIR_RELAX_CORE_THRESHOLD


# ---------------------------------------------------------------------------
# Registry history helpers
# ---------------------------------------------------------------------------


def _tried_new_values(
    registry: dict[str, Any],
    family: str,
    param_id: str,
    include_statuses: frozenset[str] = frozenset({
        "proposed", "research_only", "shadow_candidate", "live_candidate"
    }),
) -> set[float]:
    """Return the set of new_values already attempted for {family, param_id}."""
    seen: set[float] = set()
    for entry in (registry.get("proposals") or {}).values():
        if entry.get("status") not in include_statuses:
            continue
        for c in entry.get("changes") or []:
            if c.get("family") == family and c.get("param_id") == param_id:
                v = c.get("new_value")
                if v is not None:
                    seen.add(float(v))
    return seen


def _exhausted_for_direction(
    registry: dict[str, Any],
    direction: str,
) -> bool:
    """Return True if every candidate change for this direction has been tried."""
    candidates = _candidate_changes_for_direction(direction, registry)
    return len(candidates) == 0


# ---------------------------------------------------------------------------
# Change builders per direction (registry-aware)
# ---------------------------------------------------------------------------
#
# Each builder enumerates candidate (param_id, new_value) pairs in priority
# order and picks the first one NOT already present in the registry.
# This ensures successive runs explore new territory instead of repeating
# the same failed change.
# ---------------------------------------------------------------------------


def _next_untried(
    family: str,
    candidates: list[tuple[str, float, str]],  # (param_id, new_value, rationale)
    registry: dict[str, Any],
) -> tuple[str, float, str] | None:
    """Return the first (param_id, new_value, rationale) not yet in the registry."""
    tried: dict[str, set[float]] = {}
    for entry in (registry.get("proposals") or {}).values():
        if entry.get("status") in ("rejected",):
            continue
        for c in entry.get("changes") or []:
            if c.get("family") == family:
                pid = str(c.get("param_id", ""))
                v = c.get("new_value")
                if v is not None:
                    tried.setdefault(pid, set()).add(float(v))
    for param_id, new_val, rationale in candidates:
        if new_val not in tried.get(param_id, set()):
            return param_id, new_val, rationale
    return None


def _candidate_changes_for_direction(
    direction: str,
    registry: dict[str, Any],
    sig: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Return the next untried change for a direction, or [] if exhausted.
    sig is optional — only used for gap-scaled steps in relax_core_threshold.
    """
    from app.tools.proposal_constraints import CORE_PARAMS, CONTINUATION_PARAMS

    if direction == _DIR_CONTINUATION_SHIFT:
        rsi_lower = next((p for p in CONTINUATION_PARAMS if p.param_id == "rsi_entry_lower"), None)
        tp = next((p for p in CONTINUATION_PARAMS if p.param_id == "take_profit_pct"), None)
        if rsi_lower is None:
            return []
        # Escalating relaxation: -2, -4, -6 steps from baseline
        cands: list[tuple[str, float, str]] = []
        for step in (2.0, 4.0, 6.0):
            nv = max(rsi_lower.min_val, round(rsi_lower.current_value - step, 1))
            cands.append((
                "rsi_entry_lower", nv,
                f"Relax continuation RSI entry by {step:.0f} pts to increase candidate frequency",
            ))
        # Fallback: widen take-profit (+1.5, +3.0)
        if tp is not None:
            for step in (1.5, 3.0):
                nv = min(tp.max_val, round(tp.current_value + step, 1))
                cands.append((
                    "take_profit_pct", nv,
                    f"Widen continuation take-profit by {step} to capture larger moves",
                ))
        result = _next_untried("continuation_family_approx", cands, registry)
        if result is None:
            return []
        param_id, new_val, rationale = result
        spec = next((p for p in CONTINUATION_PARAMS if p.param_id == param_id), None)
        if spec is None:
            return []
        return [{
            "family": "continuation_family_approx",
            "param_id": param_id,
            "current_value": spec.current_value,
            "new_value": new_val,
            "delta": round(new_val - spec.current_value, 4),
            "rationale": rationale,
        }]

    if direction == _DIR_RELAX_CORE_THRESHOLD:
        gap = float((sig or {}).get("gap_to_threshold") or 0)
        base_step = min(4.0, max(2.0, round(abs(gap) * 0.5, 1))) if gap else 2.0
        rsi_lower = next((p for p in CORE_PARAMS if p.param_id == "rsi_entry_lower"), None)
        if rsi_lower is None:
            return []
        cands = []
        for step in sorted({base_step, base_step * 2, 4.0}):
            nv = max(rsi_lower.min_val, round(rsi_lower.current_value - step, 1))
            cands.append((
                "rsi_entry_lower", nv,
                f"Relax core RSI entry lower bound by {step:.1f} to reduce passed_count pressure",
            ))
        result = _next_untried("core_family_approx", cands, registry)
        if result is None:
            return []
        param_id, new_val, rationale = result
        spec = next((p for p in CORE_PARAMS if p.param_id == param_id), None)
        if spec is None:
            return []
        return [{
            "family": "core_family_approx",
            "param_id": param_id,
            "current_value": spec.current_value,
            "new_value": new_val,
            "delta": round(new_val - spec.current_value, 4),
            "rationale": rationale,
        }]

    if direction == _DIR_TIGHTEN_CORE_RISK:
        sl = next((p for p in CORE_PARAMS if p.param_id == "stop_loss_pct"), None)
        tp = next((p for p in CORE_PARAMS if p.param_id == "take_profit_pct"), None)
        if sl is None or tp is None:
            return []
        cands_sl = [
            ("stop_loss_pct",
             max(sl.min_val, round(sl.current_value - 0.5, 1)),
             "Tighten stop-loss to improve cost-adjusted quality for core"),
            ("stop_loss_pct",
             max(sl.min_val, round(sl.current_value - 1.0, 1)),
             "Further tighten stop-loss for core quality filter"),
        ]
        cands_tp = [
            ("take_profit_pct",
             min(tp.max_val, round(tp.current_value + 1.0, 1)),
             "Widen take-profit to let winners run further in core"),
            ("take_profit_pct",
             min(tp.max_val, round(tp.current_value + 2.0, 1)),
             "Further widen take-profit in core"),
        ]
        changes = []
        r_sl = _next_untried("core_family_approx", cands_sl, registry)
        if r_sl:
            pid, nv, rat = r_sl
            changes.append({
                "family": "core_family_approx",
                "param_id": pid,
                "current_value": sl.current_value,
                "new_value": nv,
                "delta": round(nv - sl.current_value, 4),
                "rationale": rat,
            })
        r_tp = _next_untried("core_family_approx", cands_tp, registry)
        if r_tp:
            pid, nv, rat = r_tp
            changes.append({
                "family": "core_family_approx",
                "param_id": pid,
                "current_value": tp.current_value,
                "new_value": nv,
                "delta": round(nv - tp.current_value, 4),
                "rationale": rat,
            })
        return changes[:MAX_PARAMS_PER_PROPOSAL]

    return []


def _changes_for_direction(
    direction: str,
    sig: dict[str, Any],
    registry: dict[str, Any] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """
    Return (effective_direction, changes) for the given direction, skipping
    already-tried values.  Falls back through _DIRECTION_FALLBACK chain if
    the primary direction is exhausted.

    Returns ("", []) if all directions in the fallback chain are exhausted.
    """
    reg = registry or {}
    visited: set[str] = set()
    current = direction
    while current and current not in visited:
        visited.add(current)
        changes = _candidate_changes_for_direction(current, reg, sig)
        if changes:
            return current, changes
        current = _DIRECTION_FALLBACK.get(current, "")
    return "", []


# ---------------------------------------------------------------------------
# Risk flags
# ---------------------------------------------------------------------------


def _build_risk_flags(sig: dict[str, Any], direction: str) -> list[str]:
    flags: list[str] = []

    ml_confidence = sig.get("ml_confidence", "low")
    ml_hint = sig.get("ml_direction_hint")
    if ml_confidence == "low":
        flags.append("ML evidence is low-confidence (1 fold) — treat as supporting signal only")
    elif ml_hint and ml_hint != direction:
        flags.append(
            f"ML hint ({ml_hint}) differs from chosen direction ({direction}) — "
            "hard evidence took precedence"
        )

    core_sharpe = sig.get("core_sharpe")
    cont_sharpe = sig.get("cont_sharpe")
    if core_sharpe is not None and cont_sharpe is not None:
        if direction == _DIR_CONTINUATION_SHIFT and cont_sharpe < 0.5:
            flags.append("Continuation baseline Sharpe is positive but still modest — sample size is low")

    if sig.get("stale_baselines"):
        stale = ", ".join(sig["stale_baselines"])
        flags.append(
            f"Baseline(s) may be stale ({stale}) — run update_baselines.sh for fresh comparison"
        )

    if sig["sell_watch_partial"] >= 20 or sig["sell_watch_rl"] >= 10:
        flags.append("Operational sell_watch pressure is high — any new entries must respect rate limits")

    if direction in (_DIR_RELAX_CORE_THRESHOLD,):
        flags.append("Threshold relaxation may admit weaker core names if applied too broadly")

    return flags


# ---------------------------------------------------------------------------
# Expected effects
# ---------------------------------------------------------------------------


def _build_expected_effects(direction: str, sig: dict[str, Any]) -> list[str]:
    if direction == _DIR_RELAX_CORE_THRESHOLD:
        return [
            "Increase core deep→final conversion by reducing passed_count pressure",
            "May increase core execution count if conversion improves",
        ]
    if direction == _DIR_TIGHTEN_CORE_RISK:
        return [
            "Improve profit_buffer pass rate by widening expected net edge vs cost",
            "Tighter stop may reduce per-trade loss magnitude",
        ]
    if direction == _DIR_CONTINUATION_SHIFT:
        return [
            "Increase continuation candidate frequency",
            "Leverage continuation family's stronger Sharpe baseline",
        ]
    return ["No change expected"]


# ---------------------------------------------------------------------------
# Proposal builder
# ---------------------------------------------------------------------------


def generate_proposal(
    snapshot: dict[str, Any],
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sig = _extract_signals(snapshot)
    primary_direction = _pick_direction(sig)
    effective_direction, changes = _changes_for_direction(primary_direction, sig, registry or {})
    direction = effective_direction or primary_direction

    # All directions in the fallback chain are exhausted
    exhausted = not changes
    if exhausted:
        proposal_id = (
            f"proposal_{datetime.now().strftime('%Y%m%d_%H%M%S')}_exhausted"
            f"_{uuid.uuid4().hex[:6]}"
        )
        return {
            "proposal_id": proposal_id,
            "generated_at": datetime.now().isoformat(),
            "source_snapshot_date": snapshot.get("as_of_date", ""),
            "source_account": snapshot.get("account", ""),
            "proposal_type": "research_parameter_candidate",
            "direction": direction,
            "changes": [],
            "reasoning_summary": [
                "All parameter candidates in the full fallback chain have already been tried "
                "(continuation_shift → relax_core_threshold → tighten_core_risk). "
                "No new untried change is available.",
                "Consider: reject or archive old proposals, widen param step range, "
                "or add new parameter variants to proposal_constraints.py.",
            ],
            "expected_effects": [],
            "risk_flags": ["Proposal exhausted — no changes to apply"],
            "signals_used": {
                "primary_direction": primary_direction,
                "direction_fallback_used": True,
                "ml_confidence": sig.get("ml_confidence", "low"),
                "ml_direction_hint": sig.get("ml_direction_hint"),
            },
            "constraint_violations": [],
            "status": "exhausted",
        }

    # Validate before writing
    violations = validate_proposal_changes(changes)

    proposal_id = (
        f"proposal_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{direction[:12]}"
        f"_{uuid.uuid4().hex[:6]}"
    )

    return {
        "proposal_id": proposal_id,
        "generated_at": datetime.now().isoformat(),
        "source_snapshot_date": snapshot.get("as_of_date", ""),
        "source_account": snapshot.get("account", ""),
        "proposal_type": "research_parameter_candidate",
        "direction": direction,
        "changes": changes,
        "reasoning_summary": _build_reasoning_summary(sig, direction, primary_direction),
        "expected_effects": _build_expected_effects(direction, sig),
        "risk_flags": _build_risk_flags(sig, direction),
        "signals_used": {
            "dominant_core_reason": sig["dominant_core_reason"],
            "rule_contrast_verdict": sig["rule_contrast_verdict"],
            "core_sharpe": sig["core_sharpe"],
            "cont_sharpe": sig["cont_sharpe"],
            "ml_confidence": sig["ml_confidence"],
            "ml_direction_hint": sig.get("ml_direction_hint"),
            "ml_hint_used": sig.get("ml_direction_hint") == direction and sig.get("ml_direction_hint") is not None,
            "primary_direction": primary_direction,
            "direction_fallback_used": direction != primary_direction,
        },
        "constraint_violations": [
            {"rule": v.rule, "detail": v.detail} for v in violations
        ],
        "status": "proposed" if not violations else "invalid",
    }


def _build_reasoning_summary(
    sig: dict[str, Any], direction: str, primary_direction: str = ""
) -> list[str]:
    lines: list[str] = []

    if sig["core_deep"] > 0 and sig["core_final"] == 0:
        lines.append(
            f"core reaches deep_eval ({sig['core_deep']}) but zero final — conversion is the bottleneck"
        )
    elif sig["core_exec"] == 0:
        lines.append("core executed 0 trades — strategy is not producing live entries")

    reason = sig["dominant_core_reason"]
    if reason and reason != "unknown":
        lines.append(f"dominant core rejection reason: {reason}")

    verdict = sig["rule_contrast_verdict"]
    if verdict and verdict != "unknown":
        lines.append(f"rule-contrast verdict: {verdict}")

    core_s = sig["core_sharpe"]
    cont_s = sig["cont_sharpe"]
    if core_s is not None and cont_s is not None:
        lines.append(
            f"backtest comparison — core Sharpe={core_s:.3f} vs continuation Sharpe={cont_s:.3f}"
        )

    if primary_direction and primary_direction != direction:
        lines.append(
            f"primary direction ({primary_direction}) exhausted in registry — "
            f"falling back to {direction}"
        )

    if direction == _DIR_CONTINUATION_SHIFT:
        lines.append("continuation family outperforms core baseline — exploring shift")
    elif direction == _DIR_RELAX_CORE_THRESHOLD:
        lines.append("threshold pressure is the primary blocker — exploring relaxation")
    elif direction == _DIR_TIGHTEN_CORE_RISK:
        lines.append("profit_buffer gating is limiting entries — exploring risk profile adjustment")

    ml_hint = sig.get("ml_direction_hint")
    ml_confidence = sig.get("ml_confidence", "low")
    if ml_hint and ml_confidence != "low":
        if ml_hint == direction:
            lines.append(
                f"ML signal (confidence={ml_confidence}, hint={ml_hint}) "
                "confirms chosen direction"
            )
        else:
            lines.append(
                f"ML signal (confidence={ml_confidence}) suggested {ml_hint} "
                f"but hard evidence points to {direction}"
            )

    return lines


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a research proposal JSON from a research snapshot"
    )
    parser.add_argument(
        "--snapshot",
        required=True,
        help="Path to research_snapshot JSON file",
    )
    parser.add_argument(
        "--output-file",
        default="",
        help="Write proposal JSON to this path. Defaults to stdout.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print to stdout regardless of --output-file",
    )
    parser.add_argument(
        "--registry",
        default="",
        help=(
            "Path to proposal_registry.json. If provided, warns when the generated "
            "change set is identical to an existing non-rejected proposal."
        ),
    )
    args = parser.parse_args()

    snapshot_path = Path(args.snapshot)
    if not snapshot_path.exists():
        print(f"ERROR: snapshot file not found: {snapshot_path}", file=sys.stderr)
        sys.exit(1)

    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

    # Load registry for history-aware generation
    reg: dict[str, Any] = {}
    if args.registry:
        registry_path = Path(args.registry)
        if registry_path.exists():
            from app.tools.proposal_registry import _load_registry
            reg = _load_registry(registry_path)

    proposal = generate_proposal(snapshot, registry=reg)

    # Duplicate check (same change set, non-rejected)
    if reg:
        from app.tools.proposal_registry import find_duplicate
        dup_id = find_duplicate(reg, proposal.get("changes") or [])
        if dup_id:
            print(
                f"WARNING: identical change set already exists in registry as {dup_id} "
                f"(status={reg['proposals'][dup_id].get('status', '?')}). "
                "Consider reviewing that proposal before registering a new one.",
                file=sys.stderr,
            )
            proposal["duplicate_of"] = dup_id

    serialised = json.dumps(proposal, ensure_ascii=False, indent=2)

    if proposal.get("status") == "invalid":
        print("WARNING: proposal has constraint violations:", file=sys.stderr)
        for v in proposal["constraint_violations"]:
            print(f"  [{v['rule']}] {v['detail']}", file=sys.stderr)

    if args.dry_run or not args.output_file:
        sys.stdout.write(serialised + "\n")
        return

    out_path = Path(args.output_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(serialised, encoding="utf-8")
    print(f"proposal written → {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
