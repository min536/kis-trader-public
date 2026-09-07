"""Rate Limits collector for the postrun-audit workflow."""

from __future__ import annotations

from collections import deque

from app.orchestrator.policies import assert_read_allowed
from app.orchestrator.types import CollectorResult, OrchestratorContext

_SECTION = "Rate Limits"
_TAIL_LINES = 5000


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


def collect_rate_limits(ctx: OrchestratorContext) -> CollectorResult:
    """Count rate-limit / escalation markers in the day's stdout tail (read-only)."""

    warnings: list[str] = []
    path = ctx.logs_dir / f"app_stdout_{ctx.trading_date}.log"
    if not path.exists():
        return _unavailable("Rate limits unavailable — no stdout log found.", warnings)

    try:
        assert_read_allowed(path)
        with path.open(encoding="utf-8", errors="replace") as handle:
            tail = list(deque(handle, maxlen=_TAIL_LINES))
    except PermissionError:
        warnings.append(f"read blocked: {path.name}")
        return _unavailable("Rate limits unavailable — stdout log blocked.", warnings)
    except OSError as exc:
        warnings.append(f"could not read {path.name}: {exc}")
        return _unavailable("Rate limits unavailable — stdout log unreadable.", warnings)

    egw = sum(1 for line in tail if "EGW00201" in line)
    escalations = sum(1 for line in tail if "MAIN_LOOP_EXCEPTION" in line)

    return CollectorResult(
        section=_SECTION,
        available=True,
        source=path.name,
        summary=f"Rate-limit markers for date={ctx.trading_date}.",
        details=[
            f"EGW00201 (rate-limit) hits: {egw}",
            f"MAIN_LOOP_EXCEPTION: {escalations}",
        ],
        warnings=warnings,
        error=None,
        metrics={"egw00201": egw, "main_loop_exceptions": escalations},
    )
