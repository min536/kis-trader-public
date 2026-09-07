"""Shared snapshot primitives (defaults + safe value coercion).

Relocated verbatim from app.notifications.runtime_status_snapshot (R3-S3).
runtime_status_snapshot keeps legacy bindings so existing import sites and
patch targets remain valid.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.notifications.sanitize import sanitize_text

DEFAULT_SNAPSHOT_PATH = Path("data/runtime/slack_status_snapshot.json")
DEFAULT_STALE_AFTER_SEC = 15 * 60


@dataclass(frozen=True)
class RuntimeSnapshotReadResult:
    status: str
    snapshot: dict[str, Any] | None = None
    age_seconds: float | None = None


def _parse_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

_SIDE_LABELS = {
    "BUY": "매수",
    "SELL": "매도",
}


def _safe_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_text(value: object) -> str | None:
    text = sanitize_text(value).strip()
    return text or None


def _safe_price(value: object) -> int | None:
    amount = _safe_int(value)
    return amount if amount > 0 else None
