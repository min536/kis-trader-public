"""proposal_constraints

Defines the allowed parameter space and mutation rules for research proposals.
These constraints are enforced in code — not left to prompt engineering —
so that the proposal generator cannot produce structurally invalid candidates.

Rules:
  - Each proposal may change at most MAX_PARAMS_PER_PROPOSAL parameters.
  - Each individual parameter change may not exceed its max_step in a single proposal.
  - Parameter values must stay within [min_val, max_val].
  - Two parameters listed in the same CONFLICT group cannot both change in the same proposal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_PARAMS_PER_PROPOSAL = 3

# ---------------------------------------------------------------------------
# Parameter descriptor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParamSpec:
    """Describes one tunable parameter within a family YAML."""

    family: str           # e.g. "core_family_approx"
    param_id: str         # unique name used in proposals, e.g. "rsi_entry_lower"
    yaml_path: str        # dot-path into the YAML, e.g. "strategy.entry.conditions[2].value"
    current_value: float
    min_val: float
    max_val: float
    max_step: float       # maximum absolute change per proposal
    description: str = ""
    conflict_group: str = ""   # params in the same group cannot co-change


# ---------------------------------------------------------------------------
# Core family parameters
# ---------------------------------------------------------------------------

CORE_PARAMS: list[ParamSpec] = [
    ParamSpec(
        family="core_family_approx",
        param_id="rsi_entry_lower",
        yaml_path="strategy.entry.conditions[2].value",
        current_value=48.0,
        min_val=38.0,
        max_val=56.0,
        max_step=4.0,
        description="RSI lower bound for core entry (currently 48)",
        conflict_group="rsi_entry",
    ),
    ParamSpec(
        family="core_family_approx",
        param_id="rsi_entry_upper",
        yaml_path="strategy.entry.conditions[3].value",
        current_value=72.0,
        min_val=62.0,
        max_val=80.0,
        max_step=4.0,
        description="RSI upper bound for core entry (currently 72)",
        conflict_group="rsi_entry",
    ),
    ParamSpec(
        family="core_family_approx",
        param_id="rsi_exit_threshold",
        yaml_path="strategy.exit.conditions[1].value",
        current_value=42.0,
        min_val=32.0,
        max_val=50.0,
        max_step=4.0,
        description="RSI exit threshold for core (currently 42)",
        conflict_group="rsi_exit",
    ),
    ParamSpec(
        family="core_family_approx",
        param_id="stop_loss_pct",
        yaml_path="risk.stop_loss.percent",
        current_value=3.5,
        min_val=2.0,
        max_val=6.0,
        max_step=0.5,
        description="Stop-loss percent for core (currently 3.5%)",
        conflict_group="risk",
    ),
    ParamSpec(
        family="core_family_approx",
        param_id="take_profit_pct",
        yaml_path="risk.take_profit.percent",
        current_value=7.0,
        min_val=4.0,
        max_val=12.0,
        max_step=1.0,
        description="Take-profit percent for core (currently 7.0%)",
        conflict_group="risk",
    ),
    ParamSpec(
        family="core_family_approx",
        param_id="trailing_stop_pct",
        yaml_path="risk.trailing_stop.percent",
        current_value=2.5,
        min_val=1.5,
        max_val=4.0,
        max_step=0.5,
        description="Trailing stop percent for core (currently 2.5%)",
        conflict_group="risk",
    ),
    ParamSpec(
        family="core_family_approx",
        param_id="sma_trend_period",
        yaml_path="strategy.indicators[2].params.period",
        current_value=20.0,
        min_val=10.0,
        max_val=30.0,
        max_step=5.0,
        description="SMA trend period for core entry gate (currently 20)",
        conflict_group="ma_period",
    ),
]

# ---------------------------------------------------------------------------
# Continuation family parameters
# ---------------------------------------------------------------------------

CONTINUATION_PARAMS: list[ParamSpec] = [
    ParamSpec(
        family="continuation_family_approx",
        param_id="rsi_entry_lower",
        yaml_path="strategy.entry.conditions[2].value",
        current_value=55.0,
        min_val=48.0,
        max_val=62.0,
        max_step=4.0,
        description="RSI lower bound for continuation entry (currently 55)",
        conflict_group="rsi_entry",
    ),
    ParamSpec(
        family="continuation_family_approx",
        param_id="rsi_entry_upper",
        yaml_path="strategy.entry.conditions[3].value",
        current_value=68.0,
        min_val=62.0,
        max_val=76.0,
        max_step=4.0,
        description="RSI upper bound for continuation entry (currently 68)",
        conflict_group="rsi_entry",
    ),
    ParamSpec(
        family="continuation_family_approx",
        param_id="rsi_exit_threshold",
        yaml_path="strategy.exit.conditions[1].value",
        current_value=48.0,
        min_val=38.0,
        max_val=55.0,
        max_step=4.0,
        description="RSI exit threshold for continuation (currently 48)",
        conflict_group="rsi_exit",
    ),
    ParamSpec(
        family="continuation_family_approx",
        param_id="extension_cap_scalar",
        yaml_path="strategy.entry.conditions[4].compare_scalar",
        current_value=1.06,
        min_val=1.03,
        max_val=1.12,
        max_step=0.02,
        description="Price extension cap vs EMA-20 (currently 1.06 = 6%)",
        conflict_group="extension",
    ),
    ParamSpec(
        family="continuation_family_approx",
        param_id="stop_loss_pct",
        yaml_path="risk.stop_loss.percent",
        current_value=4.0,
        min_val=2.5,
        max_val=6.0,
        max_step=0.5,
        description="Stop-loss percent for continuation (currently 4.0%)",
        conflict_group="risk",
    ),
    ParamSpec(
        family="continuation_family_approx",
        param_id="take_profit_pct",
        yaml_path="risk.take_profit.percent",
        current_value=8.5,
        min_val=5.0,
        max_val=14.0,
        max_step=1.5,
        description="Take-profit percent for continuation (currently 8.5%)",
        conflict_group="risk",
    ),
    ParamSpec(
        family="continuation_family_approx",
        param_id="trailing_stop_pct",
        yaml_path="risk.trailing_stop.percent",
        current_value=3.0,
        min_val=2.0,
        max_val=5.0,
        max_step=0.5,
        description="Trailing stop percent for continuation (currently 3.0%)",
        conflict_group="risk",
    ),
]

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

ALL_PARAMS: dict[str, ParamSpec] = {
    f"{p.family}/{p.param_id}": p
    for p in CORE_PARAMS + CONTINUATION_PARAMS
}


def get_param(family: str, param_id: str) -> ParamSpec | None:
    return ALL_PARAMS.get(f"{family}/{param_id}")


def params_for_family(family: str) -> list[ParamSpec]:
    return [p for p in ALL_PARAMS.values() if p.family == family]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@dataclass
class ConstraintViolation:
    rule: str
    detail: str


def validate_proposal_changes(
    changes: list[dict[str, Any]]
) -> list[ConstraintViolation]:
    """
    Validate a list of proposed changes.

    Each change must be: {"family": str, "param_id": str, "new_value": float}

    Returns a list of violations (empty = valid).
    """
    violations: list[ConstraintViolation] = []

    if len(changes) > MAX_PARAMS_PER_PROPOSAL:
        violations.append(ConstraintViolation(
            rule="max_params_per_proposal",
            detail=f"proposal changes {len(changes)} params; max is {MAX_PARAMS_PER_PROPOSAL}",
        ))

    # (family, conflict_group) -> first param_id that claimed it
    seen_conflict_groups: dict[tuple[str, str], str] = {}

    for change in changes:
        family = str(change.get("family") or "")
        param_id = str(change.get("param_id") or "")
        new_value = change.get("new_value")

        spec = get_param(family, param_id)
        if spec is None:
            violations.append(ConstraintViolation(
                rule="unknown_param",
                detail=f"{family}/{param_id} is not in the allowed parameter registry",
            ))
            continue

        if new_value is None:
            violations.append(ConstraintViolation(
                rule="missing_new_value",
                detail=f"{family}/{param_id} has no new_value",
            ))
            continue

        try:
            val = float(new_value)
        except (TypeError, ValueError):
            violations.append(ConstraintViolation(
                rule="invalid_value_type",
                detail=f"{family}/{param_id} new_value={new_value!r} is not numeric",
            ))
            continue

        if val < spec.min_val or val > spec.max_val:
            violations.append(ConstraintViolation(
                rule="out_of_range",
                detail=(
                    f"{family}/{param_id} new_value={val} is outside [{spec.min_val}, {spec.max_val}]"
                ),
            ))

        delta = abs(val - spec.current_value)
        if delta > spec.max_step + 1e-9:
            violations.append(ConstraintViolation(
                rule="step_too_large",
                detail=(
                    f"{family}/{param_id} change={delta:.4f} exceeds max_step={spec.max_step}"
                ),
            ))

        if spec.conflict_group:
            # Conflict groups are scoped per family — different families are independent
            cg_key = (family, spec.conflict_group)
            if cg_key in seen_conflict_groups:
                violations.append(ConstraintViolation(
                    rule="conflict_group",
                    detail=(
                        f"{family}/{param_id} and {seen_conflict_groups[cg_key]} "
                        f"are in the same conflict_group '{spec.conflict_group}' within {family}"
                    ),
                ))
            else:
                seen_conflict_groups[cg_key] = f"{family}/{param_id}"

    return violations
