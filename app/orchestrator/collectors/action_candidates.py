"""Action Candidates collector for the postrun-audit workflow."""

from __future__ import annotations

from app.orchestrator.types import CollectorResult, OrchestratorContext

_SECTION = "Action Candidates"

# (section, metric key, message template). A rule fires only when the metric is
# present and > 0, so a healthy run produces no candidates. ``{n}`` is the count.
_METRIC_RULES: tuple[tuple[str, str, str], ...] = (
    ("Orders", "failed", "inspect {n} failed order(s)"),
    ("Reconciliation", "pending_sell_intents", "reconcile {n} pending sell intent(s)"),
    ("Rate Limits", "egw00201", "review API budget/cadence — {n} EGW00201 backoff(s)"),
    ("Rate Limits", "main_loop_exceptions", "investigate {n} MAIN_LOOP_EXCEPTION escalation(s)"),
)


def collect_action_candidates(
    ctx: OrchestratorContext, upstream: list[CollectorResult]
) -> CollectorResult:
    """Derive deterministic follow-up candidates from upstream collector results.

    Every upstream warning becomes a candidate tagged with its source section.
    This is pure synthesis — it reads no files and makes no calls, so it always
    "succeeds"; an empty list is reported plainly rather than as unavailable.
    """

    candidates = [
        f"[{result.section}] follow up: {warning}"
        for result in upstream
        for warning in result.warnings
    ]
    for result in upstream:
        for section, key, template in _METRIC_RULES:
            if result.section != section:
                continue
            value = result.metrics.get(key, 0)
            if value > 0:
                candidates.append(f"[{result.section}] {template.format(n=value)}")
    count = len(candidates)
    details = candidates or ["No deterministic action candidates from collected sources."]

    return CollectorResult(
        section=_SECTION,
        available=True,
        source="derived from collected sections",
        summary=f"{count} action candidate(s) derived.",
        details=details,
        warnings=[],
        error=None,
    )
