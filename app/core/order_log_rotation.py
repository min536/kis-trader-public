"""Bounded rotation for the append-only order log.

The order log (``orders_<account>.jsonl``) is read in full by the order-log
integrity guard to count today's submissions. It has no date partitioning, so
it grows without bound and eventually exceeds the local read-size limit
(:data:`app.core.file_read_limits.DEFAULT_LOCAL_READ_MAX_BYTES`). When that
happens the strict reader raises ``order_log_too_large`` and the risk guard
fail-closes every order — silently halting trading.

This module archives (never deletes) an oversized log and recreates a fresh,
empty one. Rotation runs at session startup, before the day's records are
written, so ``count_today_*`` still sees every one of today's submissions and
the daily-limit accounting stays correct.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OrderLogRotationResult:
    rotated: bool
    archived_path: Path | None
    freed_bytes: int
    reason: str


def _unique_archive_path(archive_dir: Path, stem: str, timestamp: str, suffix: str) -> Path:
    candidate = archive_dir / f"{stem}_oversized_{timestamp}{suffix}"
    counter = 1
    while candidate.exists():
        candidate = archive_dir / f"{stem}_oversized_{timestamp}_{counter}{suffix}"
        counter += 1
    return candidate


def rotate_order_log_if_oversized(
    log_path: Path,
    *,
    max_bytes: int,
    archive_dir: Path,
    timestamp: str | None = None,
) -> OrderLogRotationResult:
    """Archive ``log_path`` and start a fresh empty one when it is too large.

    No-op (``rotated=False``) when the file is missing or under ``max_bytes``.
    The archive preserves the original bytes; the log path is recreated empty
    so the very next integrity read passes.
    """
    log_path = Path(log_path)
    if not log_path.exists():
        return OrderLogRotationResult(False, None, 0, "missing")

    size = log_path.stat().st_size
    if size < max_bytes:
        return OrderLogRotationResult(False, None, 0, "under_threshold")

    if timestamp is None:
        from app.core.time_utils import get_korean_now

        timestamp = get_korean_now().strftime("%Y%m%d_%H%M%S")
    archive_dir = Path(archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived_path = _unique_archive_path(
        archive_dir, log_path.stem, timestamp, log_path.suffix
    )
    shutil.move(str(log_path), str(archived_path))
    log_path.touch()

    return OrderLogRotationResult(True, archived_path, size, "rotated")
