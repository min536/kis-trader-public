from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.file_read_limits import LocalReadLimitError, read_text_bounded


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def metadata_path_for(lock_path: Path | str) -> Path:
    path = Path(lock_path)
    return path.with_name(f"{path.name}.json")


def process_alive(pid: int | None) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_process_command(pid: int | None) -> str:
    if not process_alive(pid):
        return ""
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return ""
    return result.stdout.strip()


def _command_tokens(command: str) -> list[str]:
    tokens: list[str] = []
    for raw in str(command or "").replace("->", " ").split():
        token = raw.strip().strip("\"'")
        if not token or token.startswith("-"):
            continue
        base = Path(token).name.lower()
        if base in {"python", "python3", "env"}:
            continue
        tokens.append(base)
    return tokens


def same_program_running(pid: int | None, expected_command: str) -> bool:
    actual = read_process_command(pid)
    if not actual:
        return False
    expected_tokens = _command_tokens(expected_command)
    if not expected_tokens:
        return True
    actual_lower = actual.lower()
    return any(token in actual_lower for token in expected_tokens)


def read_lock_metadata(lock_path: Path | str) -> dict[str, Any] | None:
    metadata_path = metadata_path_for(lock_path)
    if not metadata_path.exists():
        return None
    try:
        raw = json.loads(read_text_bounded(metadata_path, encoding="utf-8"))
    except (LocalReadLimitError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _write_lock_metadata_atomic(lock_path: Path | str, payload: dict[str, Any]) -> None:
    metadata_path = metadata_path_for(lock_path)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        dir=str(metadata_path.parent),
        prefix=f".{metadata_path.name}.tmp.",
        delete=False,
        encoding="utf-8",
    ) as tmp_file:
        json.dump(payload, tmp_file, ensure_ascii=False, indent=2, sort_keys=True)
        tmp_file.write("\n")
        tmp_name = tmp_file.name
    os.replace(tmp_name, metadata_path)


def update_lock_metadata(lock_path: Path | str, **fields: Any) -> dict[str, Any]:
    existing = read_lock_metadata(lock_path) or {}
    merged = dict(existing)
    merged.update({key: value for key, value in fields.items() if value is not None})
    if "started_at" not in merged:
        merged["started_at"] = _utcnow_iso()
    merged["heartbeat_at"] = _utcnow_iso()
    _write_lock_metadata_atomic(lock_path, merged)
    return merged


def remove_lock_metadata(lock_path: Path | str) -> None:
    metadata_path = metadata_path_for(lock_path)
    try:
        metadata_path.unlink()
    except FileNotFoundError:
        return


def inspect_lock(lock_path: Path | str, *, expected_command: str = "") -> dict[str, Any]:
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path = metadata_path_for(path)
    metadata_corrupted = metadata_path.exists() and read_lock_metadata(path) is None
    metadata = read_lock_metadata(path) or {}
    owner_pid = int(metadata.get("pid") or 0) or None
    app_pid = int(metadata.get("app_pid") or 0) or None
    holder_pid = int(metadata.get("holder_pid") or 0) or None

    with open(path, "a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_held = True
        else:
            lock_held = False
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    active_pid = app_pid if process_alive(app_pid) else owner_pid
    stale_reason = ""
    if metadata_corrupted:
        stale_reason = "corrupted_metadata"
    elif not lock_held and metadata:
        stale_reason = "stale_metadata_without_lock"
    elif lock_held and not process_alive(owner_pid) and not process_alive(app_pid):
        stale_reason = "lock_holder_without_live_owner"

    return {
        "lock_path": str(path),
        "metadata_path": str(metadata_path),
        "lock_held": lock_held,
        "pid": owner_pid,
        "app_pid": app_pid,
        "holder_pid": holder_pid,
        "started_at": metadata.get("started_at"),
        "heartbeat_at": metadata.get("heartbeat_at"),
        "command": str(metadata.get("command") or ""),
        "status": str(metadata.get("status") or ""),
        "owner_alive": process_alive(owner_pid),
        "app_alive": process_alive(app_pid),
        "holder_alive": process_alive(holder_pid),
        "same_program": same_program_running(active_pid, expected_command),
        "stale": bool(stale_reason),
        "stale_reason": stale_reason or None,
        "metadata_corrupted": metadata_corrupted,
    }


def hold_lock(
    *,
    lock_path: Path | str,
    owner_pid: int,
    command: str,
    ready_file: Path | str | None = None,
    heartbeat_interval_seconds: float = 5.0,
) -> int:
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    preexisting_status = inspect_lock(path, expected_command=command)
    handle = open(path, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        status = inspect_lock(path, expected_command=command)
        active_pid = status.get("app_pid") or status.get("pid") or "unknown"
        if status.get("stale"):
            print(
                f"[session_lock] stale_or_corrupt_lock_detected | pid={active_pid} | "
                f"reason={status.get('stale_reason') or 'unknown'}",
                flush=True,
            )
        else:
            print(
                f"[session_lock] active_lock_detected | pid={active_pid} | "
                f"command={status.get('command') or '-'} | same_program={status.get('same_program')}",
                flush=True,
            )
        return 1

    stop_requested = False

    def _handle_signal(_signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        if preexisting_status.get("stale"):
            print(
                f"[session_lock] stale_lock_reclaimed | pid={preexisting_status.get('app_pid') or preexisting_status.get('pid') or 'unknown'} | "
                f"reason={preexisting_status.get('stale_reason') or 'unknown'}",
                flush=True,
            )
        update_lock_metadata(
            path,
            pid=owner_pid,
            holder_pid=os.getpid(),
            command=command,
            status="starting",
        )
        if ready_file is not None:
            Path(ready_file).write_text("ready\n", encoding="utf-8")
        print(
            f"[session_lock] acquired | pid={owner_pid} | holder_pid={os.getpid()} | command={command}",
            flush=True,
        )
        while not stop_requested:
            if not process_alive(owner_pid):
                print(
                    f"[session_lock] owner_exited | pid={owner_pid} | releasing_lock",
                    flush=True,
                )
                break
            update_lock_metadata(
                path,
                pid=owner_pid,
                holder_pid=os.getpid(),
                command=command,
            )
            time.sleep(max(1.0, float(heartbeat_interval_seconds)))
        return 0
    finally:
        if ready_file is not None:
            try:
                Path(ready_file).unlink()
            except FileNotFoundError:
                pass
        remove_lock_metadata(path)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


def summarize_lock_status(lock_path: Path | str, *, expected_command: str = "") -> str:
    status = inspect_lock(lock_path, expected_command=expected_command)
    pid = status.get("app_pid") or status.get("pid") or "-"
    if status.get("lock_held") and status.get("same_program"):
        return (
            f"active lock pid={pid} status={status.get('status') or '-'} "
            f"heartbeat_at={status.get('heartbeat_at') or '-'}"
        )
    if status.get("stale"):
        return f"stale lock pid={pid} reason={status.get('stale_reason') or '-'}"
    if status.get("metadata_corrupted"):
        return "lock metadata is corrupted"
    return "lock is not held"


class AppMainAlreadyRunningError(RuntimeError):
    """Raised when another app.main is already running for the same account_signature."""


def acquire_app_main_lock(
    lock_path: Path | str,
    *,
    account_signature: str,
    command: str = "",
) -> Any:
    """Acquire an exclusive fcntl lock scoped to one account_signature.

    Returns the open file handle.  The caller MUST keep this reference alive
    for the entire lifetime of the process — closing or GC-ing it releases the
    lock and allows a second instance to start.

    On success, writes a metadata JSON file (same path + ".json") recording the
    current pid, account_signature, and started_at timestamp.  Metadata is
    written only after the lock is acquired, so a failing second instance never
    overwrites the first instance's metadata.

    Raises AppMainAlreadyRunningError if another process already holds the lock.
    The existing metadata (if any) is included in the error message for diagnosis.

    Because fcntl locks are released by the OS when the owning process exits
    (including SIGKILL), there is no stale-lock risk: a dead process always
    frees the lock automatically.
    """
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    handle = open(path, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        existing = read_lock_metadata(path) or {}
        existing_pid = existing.get("pid") or "unknown"
        existing_started_at = existing.get("started_at") or "-"
        raise AppMainAlreadyRunningError(
            f"[app_main_lock] duplicate_run_detected | "
            f"account_signature={account_signature!r} | "
            f"existing_pid={existing_pid} | "
            f"started_at={existing_started_at} | "
            f"lock_path={path}"
        ) from None

    _write_lock_metadata_atomic(
        path,
        {
            "pid": os.getpid(),
            "account_signature": account_signature,
            "started_at": _utcnow_iso(),
            "heartbeat_at": _utcnow_iso(),
            "command": command or f"python -m app.main (pid={os.getpid()})",
        },
    )
    print(
        f"[app_main_lock] acquired | "
        f"account_signature={account_signature!r} | "
        f"pid={os.getpid()} | "
        f"lock_path={path}",
        flush=True,
    )
    return handle
