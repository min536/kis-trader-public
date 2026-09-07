"""Read-only postrun-audit workflow.

Renders a deterministic Markdown report from the run context. Every section is
backed by a real read-only collector that degrades safely (never raises, never
fabricates a metric it could not read). Action Candidates is synthesised from
the upstream collectors' warnings, so it runs last.
"""

from __future__ import annotations

from app.orchestrator.collectors.action_candidates import collect_action_candidates
from app.orchestrator.collectors.orders import collect_orders
from app.orchestrator.collectors.rate_limits import collect_rate_limits
from app.orchestrator.collectors.reconciliation import collect_reconciliation
from app.orchestrator.collectors.runtime_status import collect_runtime_status
from app.orchestrator.report import render_section
from app.orchestrator.types import OrchestratorContext


def build_report(ctx: OrchestratorContext) -> str:
    """Build the postrun-audit Markdown report for ``ctx``."""

    mode = "read-only" if ctx.read_only else "read-write"
    lines = [
        f"# Postrun Audit — {ctx.trading_date}",
        "",
        f"- **Trading date:** {ctx.trading_date}",
        f"- **Account:** {ctx.account}",
        f"- **Mode:** {mode}",
        "",
    ]

    # Collect the evidence sections first; Action Candidates derives from them.
    upstream = [
        collect_runtime_status(ctx),
        collect_orders(ctx),
        collect_rate_limits(ctx),
        collect_reconciliation(ctx),
    ]
    sections = [*upstream, collect_action_candidates(ctx, upstream)]

    for result in sections:
        lines.append(render_section(result))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
