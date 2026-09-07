"""Workflow dispatch for the runtime orchestrator."""

from __future__ import annotations

from app.orchestrator.types import OrchestratorContext
from app.orchestrator.workflows import postrun_audit


def run_workflow(ctx: OrchestratorContext) -> str:
    """Dispatch ``ctx`` to its workflow and return the rendered report.

    Raises ``ValueError`` for an unknown workflow rather than guessing.
    """

    if ctx.workflow == "postrun_audit":
        return postrun_audit.build_report(ctx)
    raise ValueError(f"unsupported workflow: {ctx.workflow!r}")
