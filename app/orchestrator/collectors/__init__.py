"""Read-only collectors for orchestrator workflows.

Each collector takes an :class:`~app.orchestrator.types.OrchestratorContext` and
returns a :class:`~app.orchestrator.types.CollectorResult`. Collectors never
raise and never fabricate metrics for a source they could not read.
"""

from app.orchestrator.collectors.runtime_status import collect_runtime_status

__all__ = ["collect_runtime_status"]
