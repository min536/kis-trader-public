from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.file_read_limits import (
    LocalReadLimitError,
    iter_lines_bounded,
    read_text_bounded,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Per-account path separation (C-4 Phase 1). Each parallel account sets a
# distinct KIS_LIVE_SNAPSHOT_DIR / KIS_LIVE_SNAPSHOT_LOG_DIR so their snapshot,
# worker-PID and heartbeat files never collide. Defaults are unchanged (repo
# data/ + logs/), so existing single-account deployments need no migration.
LIVE_SNAPSHOT_DIR_ENV = "KIS_LIVE_SNAPSHOT_DIR"
LIVE_SNAPSHOT_LOG_DIR_ENV = "KIS_LIVE_SNAPSHOT_LOG_DIR"


def resolve_snapshot_data_dir() -> Path:
    return Path(os.getenv(LIVE_SNAPSHOT_DIR_ENV, "") or (PROJECT_ROOT / "data")).expanduser()


def resolve_snapshot_log_dir() -> Path:
    return Path(os.getenv(LIVE_SNAPSHOT_LOG_DIR_ENV, "") or (PROJECT_ROOT / "logs")).expanduser()


SNAPSHOT_PATH = resolve_snapshot_data_dir() / "live_snapshot.json"
WORKER_PID_PATH = resolve_snapshot_log_dir() / "kis_trader_live_snapshot.pid"
WORKER_HEARTBEAT_PATH = resolve_snapshot_log_dir() / "kis_trader_live_snapshot.heartbeat"
_VALID_SYMBOL_PATTERN = re.compile(r"^\d{6}$")
_WORKER_FAILURE_WARNING_THRESHOLD = 3
_HEALTHY_WORKER_TTL_GRACE_SECONDS = 120


@dataclass(frozen=True)
class LiveSnapshotSignal:
    symbol: str
    updated_at: str | None
    combined_rank: int | None
    volume_rank: int | None
    fluctuation_rank: int | None
    volume_power_rank: int | None
    ranked_source_count: int


def _parse_updated_at(updated_at: str) -> float:
    try:
        return datetime.fromisoformat(updated_at).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _coerce_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_float(value: object, default: float | None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_symbol_list(raw_symbols: object) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_symbol in raw_symbols or ():
        symbol = str(raw_symbol).strip()
        if not _VALID_SYMBOL_PATTERN.fullmatch(symbol):
            continue
        if symbol in seen:
            continue
        seen.add(symbol)
        normalized.append(symbol)
    return tuple(normalized)


def _pid_alive(pid: int | None) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_worker_heartbeat() -> dict[str, str]:
    if not WORKER_HEARTBEAT_PATH.exists():
        return {}
    values: dict[str, str] = {}
    try:
        for _lineno, raw_line in iter_lines_bounded(WORKER_HEARTBEAT_PATH):
            line = raw_line.strip()
            if not line or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    except (LocalReadLimitError, OSError, UnicodeDecodeError):
        return {}
    return values


def _read_worker_pid() -> int | None:
    if not WORKER_PID_PATH.exists():
        return None
    try:
        return int(read_text_bounded(WORKER_PID_PATH, encoding="utf-8").strip())
    except (LocalReadLimitError, OSError, UnicodeDecodeError, ValueError):
        return None


def live_snapshot_worker_health(
    *,
    refresh_interval_seconds: int | None = None,
) -> dict[str, object]:
    heartbeat = _read_worker_heartbeat()
    worker_pid = _read_worker_pid()
    worker_alive = _pid_alive(worker_pid)
    heartbeat_at = str(heartbeat.get("timestamp") or "")
    heartbeat_epoch = _parse_updated_at(heartbeat_at)
    heartbeat_age_seconds = (
        time.time() - heartbeat_epoch if heartbeat_epoch > 0 else None
    )
    last_success_at = str(heartbeat.get("last_success_at") or "")
    last_success_epoch = _parse_updated_at(last_success_at)
    last_success_age_seconds = (
        time.time() - last_success_epoch if last_success_epoch > 0 else None
    )
    effective_refresh_interval = int(refresh_interval_seconds or 180)
    heartbeat_stale_threshold_seconds = max(effective_refresh_interval * 2, 360)
    consecutive_failures = _coerce_int(heartbeat.get("consecutive_failures"), 0)
    status = str(heartbeat.get("status") or "")
    warnings: list[str] = []

    if worker_pid is not None and not worker_alive:
        warnings.append(
            f"snapshot worker missing or unhealthy; fallback risk increased | pid={worker_pid}"
        )
    if heartbeat_age_seconds is not None and heartbeat_age_seconds > heartbeat_stale_threshold_seconds:
        warnings.append(
            f"snapshot worker heartbeat stale: age={round(heartbeat_age_seconds, 1)}s"
        )
    if consecutive_failures >= _WORKER_FAILURE_WARNING_THRESHOLD:
        warnings.append(
            f"snapshot refresh failures consecutive={consecutive_failures}"
        )

    return {
        "worker_pid": worker_pid,
        "worker_alive": worker_alive,
        "heartbeat_at": heartbeat_at or None,
        "heartbeat_age_seconds": (
            round(heartbeat_age_seconds, 1) if heartbeat_age_seconds is not None else None
        ),
        "heartbeat_stale_threshold_seconds": heartbeat_stale_threshold_seconds,
        "last_success_at": last_success_at or None,
        "last_success_age_seconds": (
            round(last_success_age_seconds, 1) if last_success_age_seconds is not None else None
        ),
        "consecutive_failures": consecutive_failures,
        "status": status or None,
        "warnings": tuple(warnings),
    }


def _build_rank_map(symbols: tuple[str, ...]) -> dict[str, int]:
    return {
        symbol: index + 1
        for index, symbol in enumerate(symbols)
    }


def _build_snapshot_status(snapshot: dict[str, Any] | None) -> dict[str, object]:
    if snapshot is None:
        return {
            "available": False,
            "stale": True,
            "reason": "file_missing_or_parse_error",
            "detail": "snapshot_file_missing_or_unreadable",
            "age_seconds": None,
            "ttl_seconds": None,
            "refresh_interval_seconds": None,
            "refresh_age_ratio": None,
            "symbol_count": 0,
            "updated_at": None,
            "errors": [],
        }

    ttl_seconds = _coerce_int(snapshot.get("ttl_seconds"), 420)
    refresh_interval_seconds = _coerce_int(
        snapshot.get("refresh_interval_seconds"),
        180,
    )
    updated_at_epoch = _parse_updated_at(str(snapshot.get("updated_at") or ""))
    age_seconds = time.time() - updated_at_epoch if updated_at_epoch > 0 else float("inf")
    symbols = _normalize_symbol_list(snapshot.get("top_symbols") or [])
    worker_health: dict[str, object] = {}
    ttl_grace_seconds = 0
    within_worker_grace = False

    if updated_at_epoch <= 0:
        stale = True
        reason = "stale_or_invalid"
        detail = "updated_at_missing_or_invalid"
    elif age_seconds > ttl_seconds:
        worker_health = live_snapshot_worker_health(
            refresh_interval_seconds=refresh_interval_seconds,
        )
        worker_healthy = (
            bool(worker_health.get("worker_alive"))
            and str(worker_health.get("status") or "").strip() == "success"
            and int(worker_health.get("consecutive_failures") or 0) == 0
            and not tuple(worker_health.get("warnings") or ())
        )
        ttl_grace_seconds = min(
            _HEALTHY_WORKER_TTL_GRACE_SECONDS,
            max(0, refresh_interval_seconds),
        )
        within_worker_grace = bool(
            worker_healthy
            and ttl_grace_seconds > 0
            and age_seconds <= ttl_seconds + ttl_grace_seconds
        )
        if within_worker_grace:
            stale = False
            reason = "ok"
            detail = "refresh_delayed_beyond_ttl_worker_healthy"
        else:
            stale = True
            reason = "stale_or_invalid"
            detail = "refresh_stopped_beyond_ttl"
    elif age_seconds > refresh_interval_seconds:
        stale = False
        reason = "ok"
        detail = "refresh_delayed_but_within_ttl"
    else:
        stale = False
        reason = "ok"
        detail = "fresh_within_refresh_window"

    return {
        "available": not stale,
        "stale": stale,
        "reason": reason,
        "detail": detail,
        "age_seconds": round(age_seconds, 1) if updated_at_epoch > 0 else None,
        "ttl_seconds": ttl_seconds,
        "refresh_interval_seconds": refresh_interval_seconds,
        "refresh_age_ratio": (
            round(age_seconds / refresh_interval_seconds, 2)
            if updated_at_epoch > 0 and refresh_interval_seconds > 0
            else None
        ),
        "ttl_grace_seconds": ttl_grace_seconds,
        "within_worker_grace": within_worker_grace,
        "worker_health": worker_health,
        "symbol_count": len(symbols),
        "updated_at": snapshot.get("updated_at"),
        "errors": list(snapshot.get("errors") or []),
    }


def _fresh_snapshot(max_age_seconds: int | None = None) -> dict[str, Any] | None:
    snapshot = load_live_snapshot()
    if snapshot is None:
        return None

    status = _build_snapshot_status(snapshot)
    effective_max_age = (
        _coerce_int(snapshot.get("ttl_seconds"), 420)
        if max_age_seconds is None
        else int(max_age_seconds)
    )
    age_seconds = float(status.get("age_seconds") or 0.0)
    if bool(status.get("stale")):
        return None
    if max_age_seconds is not None and age_seconds > effective_max_age:
        return None
    return snapshot


def load_live_snapshot() -> dict[str, Any] | None:
    if not SNAPSHOT_PATH.exists():
        return None
    try:
        raw = json.loads(read_text_bounded(SNAPSHOT_PATH, encoding="utf-8"))
    except (LocalReadLimitError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def load_live_snapshot_symbols(
    max_age_seconds: int | None = None,
) -> tuple[str, ...] | None:
    snapshot = _fresh_snapshot(max_age_seconds)
    if snapshot is None:
        return None

    symbols = _normalize_symbol_list(snapshot.get("top_symbols") or [])
    return symbols or None


def get_live_snapshot_signal(
    symbol: str,
    max_age_seconds: int | None = None,
) -> LiveSnapshotSignal | None:
    normalized_symbol = str(symbol).strip()
    if not _VALID_SYMBOL_PATTERN.fullmatch(normalized_symbol):
        return None

    snapshot = _fresh_snapshot(max_age_seconds)
    if snapshot is None:
        return None

    source_symbols = snapshot.get("source_symbols") or {}
    top_symbols = _normalize_symbol_list(snapshot.get("top_symbols") or [])
    volume_symbols = _normalize_symbol_list(source_symbols.get("volume_rank") or [])
    fluctuation_symbols = _normalize_symbol_list(source_symbols.get("fluctuation_rank") or [])
    volume_power_symbols = _normalize_symbol_list(source_symbols.get("volume_power_rank") or [])

    combined_rank_map = _build_rank_map(top_symbols)
    volume_rank_map = _build_rank_map(volume_symbols)
    fluctuation_rank_map = _build_rank_map(fluctuation_symbols)
    volume_power_rank_map = _build_rank_map(volume_power_symbols)

    source_ranks = (
        volume_rank_map.get(normalized_symbol),
        fluctuation_rank_map.get(normalized_symbol),
        volume_power_rank_map.get(normalized_symbol),
    )
    ranked_source_count = sum(rank is not None for rank in source_ranks)
    return LiveSnapshotSignal(
        symbol=normalized_symbol,
        updated_at=str(snapshot.get("updated_at") or "") or None,
        combined_rank=combined_rank_map.get(normalized_symbol),
        volume_rank=volume_rank_map.get(normalized_symbol),
        fluctuation_rank=fluctuation_rank_map.get(normalized_symbol),
        volume_power_rank=volume_power_rank_map.get(normalized_symbol),
        ranked_source_count=ranked_source_count,
    )


def live_snapshot_status() -> dict[str, object]:
    return _build_snapshot_status(load_live_snapshot())
