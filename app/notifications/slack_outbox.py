"""F3 — durable file outbox for failed Slack notifications (E3).

Design: docs/daily_error_triage_design_20260707.md §4.

A transient local DNS/network blip lost an ``order_accepted`` alert with no
retry. This module persists failed durable-class notifications to
``logs/slack_outbox.jsonl`` (under ``state_root()`` so tests redirect it) and
resends them on the next notify opportunity — bounded by ``OUTBOX_MAX_ATTEMPTS``
and ``OUTBOX_TTL_SECONDS``. Pure and defensive: every filesystem op is guarded;
no function raises.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from app.auth.account_scope import state_root

OUTBOX_MAX_ATTEMPTS = 3
OUTBOX_TTL_SECONDS = 3600

# Event classes worth retrying — orders/fills/guard. Mirrors slack.ORDER_EVENT_TYPES
# plus the OrderGate guard event; kept local to avoid an import cycle.
DURABLE_OUTBOX_EVENT_TYPES = frozenset(
    {
        "order_submitted",
        "order_accepted",
        "order_rejected",
        "order_cancelled",
        "order_gate_blocked",
    }
)


def slack_outbox_path() -> Path:
    return state_root() / "logs" / "slack_outbox.jsonl"


def is_durable_event(event_type: str) -> bool:
    return str(event_type or "") in DURABLE_OUTBOX_EVENT_TYPES


def _read_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            entries.append(obj)
    return entries


def _write_entries(path: Path, entries: list[dict[str, Any]]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry, ensure_ascii=False, default=str))
                handle.write("\n")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _is_alive(entry: dict[str, Any], *, now_epoch: float, ttl: int, max_attempts: int) -> bool:
    enqueued = entry.get("enqueued_epoch")
    if not isinstance(enqueued, (int, float)):
        return False
    if now_epoch - float(enqueued) > ttl:
        return False
    try:
        attempts = int(entry.get("attempts", 0))
    except (TypeError, ValueError):
        return False
    return attempts < max_attempts


def enqueue_failed_notification(
    *,
    event_type: str,
    payload: Any,
    now_epoch: float,
    path: Path | None = None,
) -> bool:
    """Append a failed durable notification for later resend. Never raises."""
    target = path or slack_outbox_path()
    entries = _read_entries(target)
    entries.append(
        {
            "event_type": str(event_type or ""),
            "payload": payload,
            "enqueued_epoch": float(now_epoch),
            "attempts": 0,
        }
    )
    return _write_entries(target, entries)


def load_pending(
    *,
    now_epoch: float,
    path: Path | None = None,
    ttl: int = OUTBOX_TTL_SECONDS,
    max_attempts: int = OUTBOX_MAX_ATTEMPTS,
) -> list[dict[str, Any]]:
    target = path or slack_outbox_path()
    return [
        entry
        for entry in _read_entries(target)
        if _is_alive(entry, now_epoch=now_epoch, ttl=ttl, max_attempts=max_attempts)
    ]


def resend_pending(
    *,
    sender: Callable[[Any], bool],
    now_epoch: float,
    path: Path | None = None,
    ttl: int = OUTBOX_TTL_SECONDS,
    max_attempts: int = OUTBOX_MAX_ATTEMPTS,
) -> dict[str, int]:
    """Resend live entries via ``sender(payload) -> bool``.

    Success removes the entry; failure increments attempts and keeps it while it
    is still alive; dead (expired/exhausted) entries are dropped. Rewrites the
    outbox with survivors. Never raises — sender exceptions count as failures.
    """
    target = path or slack_outbox_path()
    entries = _read_entries(target)
    survivors: list[dict[str, Any]] = []
    sent = failed = dropped = 0
    for entry in entries:
        if not _is_alive(entry, now_epoch=now_epoch, ttl=ttl, max_attempts=max_attempts):
            dropped += 1
            continue
        try:
            ok = bool(sender(entry.get("payload")))
        except Exception:
            ok = False
        if ok:
            sent += 1
            continue
        entry["attempts"] = int(entry.get("attempts", 0)) + 1
        if _is_alive(entry, now_epoch=now_epoch, ttl=ttl, max_attempts=max_attempts):
            survivors.append(entry)
            failed += 1
        else:
            dropped += 1
    _write_entries(target, survivors)
    return {"sent": sent, "failed": failed, "dropped": dropped}
