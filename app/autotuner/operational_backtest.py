"""Offline operational-cadence evaluator for autotuner screening.

This is the autotuner-domain producer for ``autotuner_screen``. It evaluates
whitelisted operational cadence candidates (``{parameter, to_value}``) against
local session-summary evidence and emits the aggregate eval JSON shape consumed
by ``app.autotuner.screening.evals_to_screening``.

No broker calls, no session start, no runtime apply. This is proxy evidence for
human review, not an approval or activation path.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.autotuner.validator import load_whitelist

MAX_SESSION_SUMMARY_BYTES = 512 * 1024

_SAFER_DIRECTION = {
    "sell_check_interval_seconds": "max",
    "buy_scan_interval_seconds": "max",
    "scan_symbols_max_per_cycle": "min",
    "buy_scan_shallow_top_k": "min",
    "buy_scan_deep_eval_limit": "min",
    "rebuy_cooldown_minutes": "max",
    "same_symbol_max_buys_per_day": "min",
}

_METRIC_ALIASES = {
    "api_call_count": ("api_call_count", "api_calls", "broker_api_calls"),
    "rate_limit_hits": ("rate_limit_hits", "rate_limits", "rate_limit_count"),
    "runtime_errors": ("runtime_errors", "exception_count", "exceptions"),
    "broker_errors": ("broker_errors", "api_errors", "broker_error_count"),
    "order_rejections": ("order_rejections", "order_rejection_count"),
    "buy_scan_cycles": ("buy_scan_cycles", "buy_cycles"),
    "sell_check_cycles": ("sell_check_cycles", "sell_cycles"),
    "skipped_buy_scan_cadence": (
        "skipped_buy_scan_cadence",
        "skipped_buy_scan_cadence_count",
    ),
}


def build_operational_cadence_eval(
    candidates: list[dict[str, Any]],
    *,
    baseline_values: dict[str, Any],
    session_summaries: list[dict[str, Any]],
    generated_at: datetime,
    source_paths: list[str] | None = None,
    repo_head: str | None = None,
    holdout_summaries: list[dict[str, Any]] | None = None,
    holdout_source_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Return an autotuner-domain eval artifact for cadence candidates.

    ``repo_head`` is an optional caller-supplied commit id stamped into provenance
    for audit parity with research-domain evals. It is injected rather than read
    from git so this producer stays a pure, offline, side-effect-free function.

    When ``holdout_summaries`` is provided (W1 walk-forward), each evaluation is
    additionally scored against the disjoint out-of-sample window and gains additive
    ``in_sample`` / ``out_of_sample`` / ``verdict_reconciliation`` fields. The
    top-level ``verdict`` stays the in-sample verdict — the conservative downgrade of
    a holdout-contradicted pass is enforced downstream in ``screening.py`` so this
    producer surfaces the raw walk-forward result for human review.
    """

    observations = summarize_session_observations(session_summaries)
    # Load the whitelist once for the whole batch instead of per candidate.
    whitelist_params = load_whitelist().get("parameters", {})
    holdout_observations = (
        summarize_session_observations(holdout_summaries) if holdout_summaries else None
    )
    evaluations: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        evaluation = evaluate_candidate(
            candidate,
            baseline_values=baseline_values,
            observations=observations,
            whitelist_params=whitelist_params,
        )
        if holdout_observations is not None:
            holdout_eval = evaluate_candidate(
                candidate,
                baseline_values=baseline_values,
                observations=holdout_observations,
                whitelist_params=whitelist_params,
            )
            _attach_walk_forward(evaluation, holdout_eval)
        evaluations.append(evaluation)
    pass_count = sum(1 for item in evaluations if item.get("verdict") == "pass")
    fail_count = sum(1 for item in evaluations if item.get("verdict") == "fail")
    if not evaluations:
        overall = "empty"
    elif pass_count and not fail_count:
        overall = "pass"
    elif pass_count:
        overall = "mixed"
    else:
        overall = "fail"
    provenance = {
        "source": "kis-trader-operational-cadence",
        "generated_at": generated_at.isoformat(),
        "input_sessions": list(source_paths or []),
        "proxy_evidence": True,
        "repo_head": repo_head,
    }
    if holdout_observations is not None:
        # Record the holdout window inputs for audit only when walk-forward is used,
        # so the non-holdout artifact stays byte-identical to the pre-W1 shape.
        provenance["holdout_sessions"] = list(holdout_source_paths or [])
    artifact = {
        "proposal_id": f"operational_cadence_{generated_at.strftime('%Y%m%d_%H%M%S')}",
        "provenance": provenance,
        "baseline": {"values": dict(baseline_values or {})},
        "session_observations": observations,
        "evaluations": evaluations,
        "overall_verdict": overall,
    }
    if holdout_observations is not None:
        # overall_verdict reflects the in-sample window only; surface a holdout-aware
        # summary so a human / JSON reader is not misled by overall_verdict="pass" on a
        # holdout-contradicted move (the machine path already downgrades it in screening).
        statuses = [
            (item.get("verdict_reconciliation") or {}).get("status") for item in evaluations
        ]
        artifact["walk_forward"] = {
            "confirmed": statuses.count("confirmed"),
            "holdout_contradicted": statuses.count("holdout_contradicts"),
            "in_sample_fail": statuses.count("in_sample_fail"),
            "note": (
                "overall_verdict is in-sample only; holdout_contradicted candidates are "
                "downgraded to inconclusive in screening and dropped before suggestion"
            ),
        }
    return artifact


def _attach_walk_forward(
    evaluation: dict[str, Any], holdout_eval: dict[str, Any]
) -> None:
    """Add additive walk-forward fields to an in-sample evaluation in place.

    ``in_sample`` / ``out_of_sample`` record each window's verdict + deltas, and
    ``verdict_reconciliation`` describes whether the windows agree. A pass that the
    holdout window contradicts is flagged ``downgrade_to="inconclusive"`` -- the
    actual downgrade is enforced conservatively in ``screening.py``; this producer
    never mutates the in-sample verdict.
    """

    in_verdict = evaluation.get("verdict")
    out_verdict = holdout_eval.get("verdict")
    # Copy the nested deltas/data_window so in_sample does not alias the top-level
    # evaluation's mutable objects (a consumer mutating one must not corrupt the other).
    in_deltas = evaluation.get("deltas")
    in_window = evaluation.get("data_window")
    evaluation["in_sample"] = {
        "verdict": in_verdict,
        "deltas": dict(in_deltas) if isinstance(in_deltas, dict) else in_deltas,
        "data_window": dict(in_window) if isinstance(in_window, dict) else in_window,
    }
    evaluation["out_of_sample"] = {
        "verdict": out_verdict,
        "deltas": holdout_eval.get("deltas"),
        "data_window": holdout_eval.get("data_window"),
        "reasons": holdout_eval.get("reasons"),
    }
    if in_verdict == "pass" and out_verdict == "pass":
        status, agree, downgrade = "confirmed", True, None
    elif in_verdict == "pass" and out_verdict == "fail":
        status, agree, downgrade = "holdout_contradicts", False, "inconclusive"
    else:  # in-sample already non-pass; holdout cannot rescue it (conservative)
        status, agree, downgrade = "in_sample_fail", out_verdict == "fail", None
    evaluation["verdict_reconciliation"] = {
        "in_sample_verdict": in_verdict,
        "out_of_sample_verdict": out_verdict,
        "agree": agree,
        "status": status,
        "downgrade_to": downgrade,
    }


def evaluate_candidate(
    candidate: dict[str, Any],
    *,
    baseline_values: dict[str, Any],
    observations: dict[str, Any],
    whitelist_params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parameter = candidate.get("parameter")
    to_value = candidate.get("to_value")
    from_value = candidate.get("from_value", baseline_values.get(parameter))
    change = {
        "parameter": parameter,
        "from_value": from_value,
        "to_value": to_value,
    }
    reasons: list[str] = []

    # Callers may pass a pre-loaded whitelist param map (batch path); fall back to
    # a lazy load so evaluate_candidate stays usable standalone.
    if whitelist_params is None:
        whitelist_params = load_whitelist().get("parameters", {})
    spec = whitelist_params.get(parameter)
    if not spec:
        reasons.append("parameter is not in autotuner whitelist")
    elif spec.get("blocked") or spec.get("tier") not in {"A", "B"}:
        reasons.append(
            "parameter is not screenable "
            f"(tier={spec.get('tier')}, blocked={spec.get('blocked')})"
        )

    if not isinstance(from_value, (int, float)) or isinstance(from_value, bool):
        reasons.append("from_value must be numeric")
    if not isinstance(to_value, (int, float)) or isinstance(to_value, bool):
        reasons.append("to_value must be numeric")

    if spec and isinstance(to_value, (int, float)) and not isinstance(to_value, bool):
        bound_min = spec.get("bound_min")
        bound_max = spec.get("bound_max")
        if bound_min is not None and to_value < bound_min:
            reasons.append(f"to_value below bound_min {bound_min}")
        if bound_max is not None and to_value > bound_max:
            reasons.append(f"to_value above bound_max {bound_max}")
        max_step = spec.get("max_step")
        if (
            max_step is not None
            and isinstance(from_value, (int, float))
            and not isinstance(from_value, bool)
            and abs(to_value - from_value) > max_step
        ):
            reasons.append(
                f"step {abs(to_value - from_value)} exceeds max_step {max_step}"
            )

    direction = _SAFER_DIRECTION.get(str(parameter))
    pressure_multiplier = _pressure_multiplier(from_value, to_value, direction)
    if direction is None:
        reasons.append("parameter has no operational-cadence safer direction")
    elif pressure_multiplier is None:
        reasons.append("could not estimate request-pressure direction")
    elif pressure_multiplier > 1.0:
        reasons.append("candidate increases operational pressure; not screenable offline")
    elif pressure_multiplier == 1.0:
        reasons.append("candidate is a no-op against baseline")

    if int(observations.get("runtime_errors", 0) or 0) > 0:
        reasons.append("source sessions include runtime errors")
    if int(observations.get("broker_errors", 0) or 0) > 0:
        reasons.append("source sessions include broker errors")
    if int(observations.get("order_rejections", 0) or 0) > 0:
        reasons.append("source sessions include order rejections")

    verdict = "fail" if reasons else "pass"
    if pressure_multiplier is None:
        pressure_delta_pct = None
    else:
        pressure_delta_pct = round((pressure_multiplier - 1.0) * 100.0, 4)
    return {
        "evaluation_type": "operational_cadence_replay",
        "strategy_family": "autotuner_operational_cadence",
        "data_window": {
            "session_count": observations.get("session_count", 0),
            "session_ids": observations.get("session_ids", []),
        },
        "changes": [change],
        "deltas": {
            "estimated_request_pressure_pct": pressure_delta_pct,
            "rate_limit_hits": observations.get("rate_limit_hits", 0),
            "api_call_count": observations.get("api_call_count", 0),
        },
        "verdict": verdict,
        "reasons": reasons or ["conservative in-bounds cadence move"],
    }


def summarize_session_observations(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    session_ids: list[str] = []
    totals = {name: 0 for name in _METRIC_ALIASES}
    for idx, summary in enumerate(summaries):
        if not isinstance(summary, dict):
            continue
        session_ids.append(str(summary.get("session_id") or summary.get("date") or idx))
        metrics = (
            summary.get("metrics")
            if isinstance(summary.get("metrics"), dict)
            else summary
        )
        for name, aliases in _METRIC_ALIASES.items():
            totals[name] += _first_numeric(metrics, aliases)
    return {
        "session_count": len(session_ids),
        "session_ids": session_ids,
        **totals,
    }


def load_session_summaries(paths: list[Path]) -> tuple[list[dict[str, Any]], list[str]]:
    summaries: list[dict[str, Any]] = []
    loaded_paths: list[str] = []
    for path in paths:
        obj = _read_bounded_json(path)
        if isinstance(obj, dict):
            summaries.append(obj)
            loaded_paths.append(str(path))
    return summaries, loaded_paths


def _read_bounded_json(path: Path) -> Any:
    try:
        if path.stat().st_size > MAX_SESSION_SUMMARY_BYTES:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _first_numeric(mapping: dict[str, Any], aliases: tuple[str, ...]) -> int:
    for key in aliases:
        value = mapping.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return int(value)
    return 0


def _pressure_multiplier(
    from_value: Any, to_value: Any, direction: str | None
) -> float | None:
    if (
        direction not in {"min", "max"}
        or not isinstance(from_value, (int, float))
        or isinstance(from_value, bool)
        or not isinstance(to_value, (int, float))
        or isinstance(to_value, bool)
        or from_value <= 0
        or to_value <= 0
    ):
        return None
    if direction == "max":
        return float(from_value) / float(to_value)
    return float(to_value) / float(from_value)
