"""Runtime Status collector for the postrun-audit workflow.

Read-only. The preferred (T1) source is the in-process EOD builder
``live_health_check.build_health_summary``; it is augmented with the per-account
lock metadata file and a bounded tail of the day's stderr log. Every source is
optional and degrades to a warning — this collector never raises and never
fabricates a metric it could not read.
"""

from __future__ import annotations

import contextlib
import io
import json
from collections import deque
from pathlib import Path

from app.orchestrator.policies import assert_read_allowed
from app.orchestrator.types import CollectorResult, OrchestratorContext

_SECTION = "Runtime Status"
_STDERR_TAIL_LINES = 200


def collect_runtime_status(ctx: OrchestratorContext) -> CollectorResult:
    """Collect runtime status from the EOD builder, lock file, and stderr tail."""

    details: list[str] = []
    warnings: list[str] = []
    sources: list[str] = []

    health = _try_build_health_summary(ctx, warnings)
    if health is not None:
        sources.append("live_health_check.build_health_summary")
        details.append(f"Session: {health.get('session', 'ALL')}")
        details.append(f"Candidate rows: {health.get('candidate_rows')}")
        details.append(f"Snapshot rows: {health.get('snapshot_rows')}")
        if not health.get("snapshot_context_available"):
            warnings.append("no cycle-snapshot context for this date")

    lock_path = ctx.logs_dir / f"app_main_{ctx.account}.lock.json"
    lock = _read_json_guarded(lock_path, warnings)
    if lock is not None:
        sources.append(lock_path.name)
        details.append(f"Lock PID: {lock.get('pid')}")
        details.append(f"Session started_at: {lock.get('started_at')}")

    stderr_path = ctx.logs_dir / f"app_stderr_{ctx.trading_date}.log"
    tail = _tail_guarded(stderr_path, _STDERR_TAIL_LINES, warnings)
    if tail is not None:
        sources.append(stderr_path.name)
        traceback_lines = sum(1 for line in tail if "Traceback" in line)
        if traceback_lines:
            note = (
                f"{traceback_lines} traceback marker(s) in last {len(tail)} stderr lines"
            )
            warnings.append(note)
            details.append(f"Stderr tail: {note}")
        else:
            details.append(
                f"Stderr tail: no traceback markers in last {len(tail)} lines"
            )

    if not sources:
        return CollectorResult(
            section=_SECTION,
            available=False,
            source=None,
            summary="Runtime status unavailable — no source found.",
            details=[],
            warnings=warnings,
            error=None,
        )

    return CollectorResult(
        section=_SECTION,
        available=True,
        source=", ".join(sources),
        summary=(
            f"Runtime status collected for account={ctx.account}, "
            f"date={ctx.trading_date}."
        ),
        details=details,
        warnings=warnings,
        error=None,
    )


def _try_build_health_summary(
    ctx: OrchestratorContext, warnings: list[str]
) -> dict | None:
    """Call the T1 EOD builder, degrading any failure to a warning.

    ``build_health_summary`` calls ``sys.exit(1)`` when there is no candidate
    data, so ``SystemExit`` is caught explicitly alongside ordinary exceptions.
    """

    try:
        from app.tools.live_health_check import build_health_summary
    except Exception as exc:  # pragma: no cover - environmental import failure
        warnings.append(f"live_health_check import failed: {exc!r}")
        return None

    # build_health_summary prints a diagnostic to stderr before exiting on
    # missing data; we translate that into a structured warning, so silence its
    # raw stderr to keep the report clean.
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            return build_health_summary(account=ctx.account, date=ctx.trading_date)
    except SystemExit:
        warnings.append("live_health_check builder reported no data for this date")
        return None
    except Exception as exc:
        warnings.append(f"live_health_check builder failed: {exc!r}")
        return None


def _read_json_guarded(path: Path, warnings: list[str]) -> dict | None:
    """Read a small JSON file through the read guard; warn (don't raise) on trouble."""

    if not path.exists():
        return None
    try:
        assert_read_allowed(path)
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except PermissionError:
        warnings.append(f"read blocked: {path.name}")
        return None
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"malformed or unreadable {path.name}: {exc}")
        return None


def _tail_guarded(path: Path, n: int, warnings: list[str]) -> list[str] | None:
    """Return the last ``n`` lines of ``path`` (bounded memory); warn on trouble."""

    if not path.exists():
        return None
    try:
        assert_read_allowed(path)
        with path.open(encoding="utf-8", errors="replace") as handle:
            return list(deque(handle, maxlen=n))
    except PermissionError:
        warnings.append(f"read blocked: {path.name}")
        return None
    except OSError as exc:
        warnings.append(f"could not read {path.name}: {exc}")
        return None
