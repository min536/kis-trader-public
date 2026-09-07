"""Shared types for the runtime orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Workflow = Literal["postrun_audit"]


@dataclass(frozen=True)
class OrchestratorContext:
    """Immutable run context for a single orchestrator invocation.

    ``account`` is required: every collector source is account-scoped (lock file,
    order/candidate logs, the EOD builders). Multi-account is intentionally
    deferred — a future caller loops over single-account runs rather than this
    context carrying a list.

    ``read_only`` defaults to ``True``: workflows must treat the project tree as
    read-only unless writes are explicitly enabled by the caller.
    """

    project_root: Path
    trading_date: str
    workflow: Workflow
    account: str
    read_only: bool = True

    @property
    def data_dir(self) -> Path:
        return self.project_root / "data"

    @property
    def logs_dir(self) -> Path:
        return self.project_root / "logs"

    @property
    def docs_dir(self) -> Path:
        return self.project_root / "docs"


@dataclass(frozen=True)
class CollectorResult:
    """Structured output of a single postrun-audit collector.

    Collectors never raise (see ``collectors``); they always return this shape so
    the report layer can render every section uniformly and a single failed
    source never aborts the whole audit.

    - ``section``  — report heading this result renders under (e.g. "Runtime Status").
    - ``available``— a usable source was found and parsed.
    - ``source``   — human-readable provenance, or ``None`` when unavailable.
    - ``summary``  — one-line headline; always present, even when unavailable.
    - ``details``  — Markdown-ready bullet lines (empty when no source: never fabricate).
    - ``warnings`` — non-fatal issues (missing/partial/malformed sources).
    - ``error``    — set only if the whole source was unusable; never an exception.
    - ``metrics``  — structured counts (e.g. ``{"failed": 3}``) the action-candidate
      synthesiser reads to apply deterministic rules without parsing detail strings.
    """

    section: str
    available: bool
    source: str | None
    summary: str
    details: list[str]
    warnings: list[str]
    error: str | None = None
    metrics: dict[str, int] = field(default_factory=dict)
