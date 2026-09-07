"""Markdown rendering for collector results.

The collectors return structured :class:`~app.orchestrator.types.CollectorResult`
objects; this module owns turning one into a Markdown section. Keeping rendering
here (not in the collectors) means every section renders uniformly and the
rendering is testable in isolation.
"""

from __future__ import annotations

from pathlib import Path

from app.orchestrator.policies import assert_write_allowed
from app.orchestrator.types import CollectorResult, OrchestratorContext


def render_section(result: CollectorResult) -> str:
    """Render one collector result as a Markdown section (no trailing newline)."""

    lines = [f"## {result.section}", result.summary]

    if result.available:
        if result.details:
            lines.append("")
            lines.extend(f"- {detail}" for detail in result.details)
    else:
        # No usable source: state it plainly, never fabricate metrics.
        lines.append("")
        lines.append("_not collected_")

    if result.warnings:
        lines.append("")
        lines.extend(f"> ⚠️ {warning}" for warning in result.warnings)

    if result.error:
        lines.append("")
        lines.append(f"_error: {result.error}_")

    lines.append("")
    lines.append(f"_source: {result.source or 'none'}_")

    return "\n".join(lines)


def write_report(ctx: OrchestratorContext, report: str) -> Path:
    """Persist ``report`` under ``_workspace/`` — blocked in read-only mode.

    The path is gated by ``assert_write_allowed``: a read-only context raises
    ``PermissionError`` before anything touches disk.
    """

    path = ctx.project_root / "_workspace" / f"postrun_audit_{ctx.account}_{ctx.trading_date}.md"
    assert_write_allowed(path, read_only=ctx.read_only)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")
    return path
