"""Reconciliation collector for the postrun-audit workflow.

Read-only. Summarises the account's ``runtime_state_{account}.json`` — how many
positions the broker last synced and how many sell intents are still pending —
so a manual-trade or stuck-intent drift is visible in the audit. Counts only;
no broker call is ever made to "confirm" the local state.
"""

from __future__ import annotations

import json

from app.orchestrator.policies import assert_read_allowed
from app.orchestrator.types import CollectorResult, OrchestratorContext

_SECTION = "Reconciliation"


def _unavailable(summary: str, warnings: list[str]) -> CollectorResult:
    return CollectorResult(
        section=_SECTION,
        available=False,
        source=None,
        summary=summary,
        details=[],
        warnings=warnings,
        error=None,
    )


def collect_reconciliation(ctx: OrchestratorContext) -> CollectorResult:
    """Summarise broker-sync / pending-intent state from runtime_state (read-only)."""

    warnings: list[str] = []
    path = ctx.data_dir / f"runtime_state_{ctx.account}.json"
    if not path.exists():
        return _unavailable("Reconciliation unavailable — no runtime_state found.", warnings)

    try:
        assert_read_allowed(path)
        with path.open(encoding="utf-8") as handle:
            state = json.load(handle)
    except PermissionError:
        warnings.append(f"read blocked: {path.name}")
        return _unavailable("Reconciliation unavailable — runtime_state blocked.", warnings)
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"malformed or unreadable runtime_state {path.name}: {exc}")
        return _unavailable("Reconciliation unavailable — runtime_state unreadable.", warnings)

    if not isinstance(state, dict):
        warnings.append(f"unexpected runtime_state shape in {path.name}")
        return _unavailable("Reconciliation unavailable — unexpected runtime_state.", warnings)

    synced = state.get("broker_last_synced_positions_by_symbol")
    pending = state.get("pending_sell_intents_by_symbol")
    synced_n = len(synced) if isinstance(synced, dict) else 0
    pending_n = len(pending) if isinstance(pending, dict) else 0

    return CollectorResult(
        section=_SECTION,
        available=True,
        source=path.name,
        summary=f"Reconciliation state for account={ctx.account}.",
        details=[
            f"Broker-synced positions: {synced_n}",
            f"Pending sell intents: {pending_n}",
        ],
        warnings=warnings,
        error=None,
        metrics={"broker_synced": synced_n, "pending_sell_intents": pending_n},
    )
