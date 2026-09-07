"""Evidence adapters for autotuner proposals (Phase 2).

Pure shaping functions: given already-collected references (a backtest result or
a live-log reconstruction), produce a schema-valid evidence entry with the
correct ``source_type`` and an honest ``limitations`` note. They make NO
network/broker calls and read no files — the caller collects, these shape.
See docs/live_autotuner_proposal_schema.md §7 and decisions D4.
"""

from __future__ import annotations


def backtest_evidence(
    *,
    source_id: str,
    source_path: str,
    generated_at: str,
    summary: str,
    confidence: str = "low",
    notes: str = "",
    provenance: dict | None = None,
) -> dict:
    """Shape an open-trading-api backtest result into a proxy evidence entry."""
    entry = {
        "source_type": "backtest",
        "source_id": source_id,
        "source_path": source_path,
        "generated_at": generated_at,
        "summary": summary,
        "limitations": (
            "Lean DSL / preset strategy engine, not the live app/strategy engine; "
            "proxy evidence only — does not replay the live buy/sell decision code."
        ),
        "confidence": confidence,
        "notes": notes,
    }
    if provenance is not None:
        # E1: auditable proxy origin (source/repo_head/data_window/generated_at).
        entry["provenance"] = provenance
    return entry


def live_log_evidence(
    *,
    source_id: str,
    source_path: str,
    generated_at: str,
    summary: str,
    confidence: str = "low",
    notes: str = "",
) -> dict:
    """Shape an app/backtest log reconstruction into a live_log evidence entry."""
    return {
        "source_type": "live_log",
        "source_id": source_id,
        "source_path": source_path,
        "generated_at": generated_at,
        "summary": summary,
        "limitations": (
            "Log/signal reconstruction from live cycle logs, not a live strategy "
            "replay; single-session unless aggregated."
        ),
        "confidence": confidence,
        "notes": notes,
    }
