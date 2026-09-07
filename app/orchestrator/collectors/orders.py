"""Orders collector for the postrun-audit workflow.

Read-only. Scans the account-scoped order log (``orders_{account}.jsonl``)
*backwards* from the end, collecting the trading date's records and stopping as
soon as it reaches an older date — so it captures the full target day without
reading the (large, append-only) log whole, even when many newer rows follow.
Records are bucketed by ``action`` (submitted / succeeded / failed / blocked)
plus a ``failure_category`` tally. Never fabricates a metric it could not read.
"""

from __future__ import annotations

import io
import json
from collections import Counter
from pathlib import Path
from typing import Iterator

from app.orchestrator.policies import assert_read_allowed
from app.orchestrator.types import CollectorResult, OrchestratorContext

_SECTION = "Orders"
# Hard safety bound so a corrupt/huge file can never be scanned without limit.
_MAX_SCAN_LINES = 2_000_000


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


def collect_orders(ctx: OrchestratorContext) -> CollectorResult:
    """Summarise the day's orders from the account-scoped order log (read-only)."""

    warnings: list[str] = []
    path = ctx.logs_dir / f"orders_{ctx.account}.jsonl"
    if not path.exists():
        return _unavailable("Orders unavailable — no order log found.", warnings)

    date_iso = f"{ctx.trading_date[:4]}-{ctx.trading_date[4:6]}-{ctx.trading_date[6:8]}"
    day_rows = _read_orders_for_date(path, date_iso, warnings)
    if day_rows is None:
        return _unavailable("Orders unavailable — order log unreadable.", warnings)

    submitted = sum(1 for r in day_rows if str(r.get("action", "")).endswith("order_submitted"))
    succeeded = sum(1 for r in day_rows if str(r.get("action", "")).endswith("order_succeeded"))
    failed = sum(1 for r in day_rows if str(r.get("action", "")).endswith("failed"))
    blocked = sum(1 for r in day_rows if str(r.get("action", "")).startswith("blocked_"))
    categories = Counter(
        str(r.get("failure_category")) for r in day_rows if r.get("failure_category")
    )

    details = [
        f"Submitted: {submitted}",
        f"Succeeded: {succeeded}",
        f"Failed: {failed}",
        f"Blocked: {blocked}",
    ]
    if categories:
        details.append(
            "Failure categories: "
            + ", ".join(f"{name}x{count}" for name, count in sorted(categories.items()))
        )

    return CollectorResult(
        section=_SECTION,
        available=True,
        source=path.name,
        summary=(
            f"{len(day_rows)} order record(s) for account={ctx.account}, "
            f"date={ctx.trading_date} (date-aware reverse scan)."
        ),
        details=details,
        warnings=warnings,
        error=None,
        metrics={
            "submitted": submitted,
            "succeeded": succeeded,
            "failed": failed,
            "blocked": blocked,
        },
    )


def _read_orders_for_date(
    path: Path, date_iso: str, warnings: list[str]
) -> list[dict] | None:
    """Collect the target date's rows by scanning the append-only log backwards.

    Reading newest-first lets us skip later-dated rows, gather the target day,
    and stop at the first older row — bounded by the day's volume, not the file.
    A hard ``_MAX_SCAN_LINES`` cap guards a corrupt/huge file and warns if hit.
    """

    try:
        assert_read_allowed(path)
    except PermissionError:
        warnings.append(f"read blocked: {path.name}")
        return None

    rows: list[dict] = []
    scanned = 0
    try:
        for line in _iter_lines_reverse(path):
            scanned += 1
            if scanned > _MAX_SCAN_LINES:
                warnings.append(
                    f"scan cap reached ({_MAX_SCAN_LINES} lines) in {path.name}; "
                    "counts may be partial"
                )
                break
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            timestamp = str(obj.get("timestamp", ""))
            if timestamp.startswith(date_iso):
                rows.append(obj)
            elif timestamp and timestamp < date_iso:
                # Older than the target day; nothing earlier can match.
                break
    except OSError as exc:
        warnings.append(f"could not read {path.name}: {exc}")
        return None
    return rows


def _iter_lines_reverse(path: Path, block_size: int = 65536) -> Iterator[str]:
    """Yield the file's lines newest-first, reading fixed-size blocks from the end."""

    with path.open("rb") as handle:
        handle.seek(0, io.SEEK_END)
        pos = handle.tell()
        carry = b""
        while pos > 0:
            read = min(block_size, pos)
            pos -= read
            handle.seek(pos)
            data = handle.read(read) + carry
            parts = data.split(b"\n")
            carry = parts[0]  # may be an incomplete leading line
            for part in reversed(parts[1:]):
                yield part.decode("utf-8", "replace")
        if carry:
            yield carry.decode("utf-8", "replace")
