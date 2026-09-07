"""Bounded read-only access to a sibling open-trading-api checkout.

The short-term integration contract is intentionally narrow:
- locate the sibling repo only through OPEN_TRADING_API_ROOT,
- run only allowlisted read-only subprocess commands with shell=False,
- consume JSON/CSV artifacts only after path, size, and schema checks.
"""

from __future__ import annotations

import csv
import ipaddress
import json
import os
import subprocess
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse

OPEN_TRADING_API_ROOT_ENV = "OPEN_TRADING_API_ROOT"

_DEFAULT_COMMAND_TIMEOUT_SECONDS = 5.0
_DEFAULT_MAX_STDOUT_BYTES = 8 * 1024
_DEFAULT_MAX_STDERR_BYTES = 8 * 1024
_DEFAULT_MAX_ARTIFACT_BYTES = 64 * 1024
_DEFAULT_MAX_CSV_ROWS = 10_000

_ROOT_FILE_MARKERS = ("README.md",)
_ROOT_PATH_MARKERS = (".git",)
_ROOT_DIR_MARKERS = ("backtester",)
_SAFE_ENV_KEYS = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR")


@dataclass(frozen=True)
class RootResolution:
    available: bool
    root: Path | None
    reason: str = ""


@dataclass(frozen=True)
class CommandSpec:
    argv: tuple[str, ...]
    description: str
    timeout_seconds: float = _DEFAULT_COMMAND_TIMEOUT_SECONDS
    max_stdout_bytes: int = _DEFAULT_MAX_STDOUT_BYTES
    max_stderr_bytes: int = _DEFAULT_MAX_STDERR_BYTES


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    command_id: str
    argv: tuple[str, ...]
    cwd: Path | None
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str = ""


@dataclass(frozen=True)
class ArtifactResult:
    ok: bool
    path: Path | None = None
    data: Any = None
    error: str = ""


_ALLOWED_COMMANDS: dict[str, CommandSpec] = {
    "repo_head": CommandSpec(
        argv=("git", "rev-parse", "HEAD"),
        description="Return the checked-out open-trading-api commit hash.",
    ),
    "repo_root": CommandSpec(
        argv=("git", "rev-parse", "--show-toplevel"),
        description="Return git's resolved repository root.",
    ),
}


def build_command_audit_record(result, *, repo_head=None, at=None):
    """Build an observability record for an adapter command (D-plan §observability).

    Captures WHICH command (command_id/argv) ran at WHAT path (cwd) against WHICH
    commit (repo_head), plus outcome. Pure: returns a JSON-serialisable dict;
    persistence is the caller's choice via ``append_audit_record``.
    """
    return {
        "at": at,
        "command_id": result.command_id,
        "argv": list(result.argv),
        "cwd": str(result.cwd) if result.cwd is not None else None,
        "repo_head": repo_head,
        "ok": result.ok,
        "returncode": result.returncode,
        "error": result.error,
    }


def append_audit_record(log_path, record):
    """Append one JSON record as a line to a caller-specified JSONL audit log.

    Creates parent dirs as needed. The caller chooses the path (never a protected
    credential file); this is append-only observability, not a runtime write.
    """
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def is_loopback_url(url: str) -> bool:
    """True only if ``url`` targets the loopback interface over http/https.

    The backtester REST boundary (run_proposal_backtest -> :8002) is a localhost
    research service, not part of the adapter's subprocess/artifact boundary.
    This is the adapter-owned policy that keeps that HTTP path local: it rejects
    any non-loopback host (SSRF / accidental-remote guard) and non-http schemes.
    """
    try:
        parsed = urlparse(str(url))
    except (ValueError, TypeError):
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.hostname
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def resolve_open_trading_api_root(
    *, env: Mapping[str, str] | None = None
) -> RootResolution:
    """Resolve and fingerprint OPEN_TRADING_API_ROOT without falling back to siblings."""
    source_env = os.environ if env is None else env
    raw = str(source_env.get(OPEN_TRADING_API_ROOT_ENV, "")).strip()
    if not raw:
        return RootResolution(
            available=False,
            root=None,
            reason=f"{OPEN_TRADING_API_ROOT_ENV} is not set",
        )

    root = Path(raw).expanduser().resolve()
    return _validate_open_trading_api_root(root)


def resolve_backtester_root(
    *, env: Mapping[str, str] | None = None
) -> RootResolution:
    """Resolve the backtester directory from OPEN_TRADING_API_ROOT."""
    repo = resolve_open_trading_api_root(env=env)
    if not repo.available or repo.root is None:
        return repo
    backtester = repo.root / "backtester"
    if not backtester.is_dir():
        return RootResolution(
            available=False,
            root=None,
            reason=f"backtester directory is missing under {repo.root}",
        )
    return RootResolution(available=True, root=backtester, reason="")


def run_allowed_open_trading_api_command(
    command_id: str,
    *,
    root: Path,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> CommandResult:
    """Run an allowlisted read-only command inside the sibling repo."""
    spec = _ALLOWED_COMMANDS.get(command_id)
    if spec is None:
        return CommandResult(
            ok=False,
            command_id=command_id,
            argv=(),
            cwd=None,
            error=f"command is not allowed: {command_id}",
        )

    root_check = _validate_open_trading_api_root(Path(root).expanduser().resolve())
    if not root_check.available or root_check.root is None:
        return CommandResult(
            ok=False,
            command_id=command_id,
            argv=spec.argv,
            cwd=None,
            error=root_check.reason,
        )

    actual_timeout = spec.timeout_seconds
    if timeout is not None:
        actual_timeout = min(float(timeout), spec.timeout_seconds)

    try:
        completed = runner(
            spec.argv,
            cwd=root_check.root,
            shell=False,
            timeout=actual_timeout,
            check=False,
            capture_output=True,
            text=True,
            env=_sanitize_env(env),
        )
    except subprocess.TimeoutExpired:
        return CommandResult(
            ok=False,
            command_id=command_id,
            argv=spec.argv,
            cwd=root_check.root,
            error=f"command timed out after {actual_timeout:g}s",
        )
    except OSError as exc:
        return CommandResult(
            ok=False,
            command_id=command_id,
            argv=spec.argv,
            cwd=root_check.root,
            error=str(exc),
        )

    stdout = _coerce_text(completed.stdout)
    stderr = _coerce_text(completed.stderr)
    if len(stdout.encode("utf-8")) > spec.max_stdout_bytes:
        return CommandResult(
            ok=False,
            command_id=command_id,
            argv=spec.argv,
            cwd=root_check.root,
            returncode=completed.returncode,
            error=f"stdout exceeded {spec.max_stdout_bytes} bytes",
        )
    if len(stderr.encode("utf-8")) > spec.max_stderr_bytes:
        return CommandResult(
            ok=False,
            command_id=command_id,
            argv=spec.argv,
            cwd=root_check.root,
            returncode=completed.returncode,
            error=f"stderr exceeded {spec.max_stderr_bytes} bytes",
        )

    ok = completed.returncode == 0
    return CommandResult(
        ok=ok,
        command_id=command_id,
        argv=spec.argv,
        cwd=root_check.root,
        returncode=completed.returncode,
        stdout=stdout,
        stderr=stderr,
        error="" if ok else (stderr.strip() or f"exit code {completed.returncode}"),
    )


def read_json_artifact(
    *,
    root: Path,
    relative_path: str,
    required_keys: Sequence[str] = (),
    max_bytes: int = _DEFAULT_MAX_ARTIFACT_BYTES,
) -> ArtifactResult:
    """Read a bounded JSON object artifact from inside OPEN_TRADING_API_ROOT."""
    path_result = _resolve_artifact_path(root, relative_path)
    if not path_result.ok or path_result.path is None:
        return path_result

    text_result = _read_bounded_text(path_result.path, max_bytes=max_bytes)
    if not text_result.ok:
        return text_result
    try:
        payload = json.loads(text_result.data)
    except json.JSONDecodeError as exc:
        return ArtifactResult(ok=False, path=path_result.path, error=f"invalid JSON: {exc}")
    if not isinstance(payload, dict):
        return ArtifactResult(ok=False, path=path_result.path, error="JSON artifact must be an object")

    missing = [key for key in required_keys if key not in payload]
    if missing:
        return ArtifactResult(
            ok=False,
            path=path_result.path,
            error=f"missing required keys: {', '.join(missing)}",
        )
    return ArtifactResult(ok=True, path=path_result.path, data=payload)


def read_csv_artifact(
    *,
    root: Path,
    relative_path: str,
    required_columns: Sequence[str] = (),
    fieldnames: Sequence[str] | None = None,
    max_bytes: int = _DEFAULT_MAX_ARTIFACT_BYTES,
    max_rows: int = _DEFAULT_MAX_CSV_ROWS,
) -> ArtifactResult:
    """Read a bounded CSV artifact as a list of dict rows."""
    path_result = _resolve_artifact_path(root, relative_path)
    if not path_result.ok or path_result.path is None:
        return path_result

    text_result = _read_bounded_text(path_result.path, max_bytes=max_bytes)
    if not text_result.ok:
        return text_result

    reader = csv.DictReader(
        StringIO(text_result.data),
        fieldnames=tuple(fieldnames) if fieldnames is not None else None,
    )
    if not reader.fieldnames:
        return ArtifactResult(ok=False, path=path_result.path, error="CSV artifact has no header")
    missing = [column for column in required_columns if column not in reader.fieldnames]
    if missing:
        return ArtifactResult(
            ok=False,
            path=path_result.path,
            error=f"missing required columns: {', '.join(missing)}",
        )

    rows: list[dict[str, str]] = []
    for index, row in enumerate(reader, start=1):
        if index > max_rows:
            return ArtifactResult(
                ok=False,
                path=path_result.path,
                error=f"CSV artifact exceeded {max_rows} rows",
            )
        rows.append(dict(row))
    return ArtifactResult(ok=True, path=path_result.path, data=rows)


def _validate_open_trading_api_root(root: Path) -> RootResolution:
    if not root.exists():
        return RootResolution(False, None, f"path does not exist: {root}")
    if not root.is_dir():
        return RootResolution(False, None, f"path is not a directory: {root}")

    missing_markers: list[str] = []
    for marker in _ROOT_FILE_MARKERS:
        if not (root / marker).is_file():
            missing_markers.append(marker)
    for marker in _ROOT_PATH_MARKERS:
        if not (root / marker).exists():
            missing_markers.append(marker)
    for marker in _ROOT_DIR_MARKERS:
        if not (root / marker).is_dir():
            missing_markers.append(marker)
    if missing_markers:
        return RootResolution(
            False,
            None,
            f"open-trading-api marker missing: {', '.join(missing_markers)}",
        )
    return RootResolution(True, root, "")


def _sanitize_env(env: Mapping[str, str] | None) -> dict[str, str]:
    source_env = os.environ if env is None else env
    safe: dict[str, str] = {}
    for key in _SAFE_ENV_KEYS:
        value = source_env.get(key)
        if value is not None:
            safe[key] = str(value)
    safe.setdefault("PATH", os.defpath)
    return safe


def _coerce_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _protected_exact_names() -> set[str]:
    return {
        "." + "env",
        "." + "token" + "_cache.json",
        "kis_" + "devlp.yaml",
        "token" + "_cache.json",
    }


def _resolve_artifact_path(root: Path, relative_path: str) -> ArtifactResult:
    root_check = _validate_open_trading_api_root(Path(root).expanduser().resolve())
    if not root_check.available or root_check.root is None:
        return ArtifactResult(ok=False, error=root_check.reason)

    raw = Path(relative_path)
    if raw.is_absolute():
        return ArtifactResult(ok=False, error="artifact path must be relative")
    protected_names = _protected_exact_names()
    for part in raw.parts:
        lower = part.lower()
        if lower in protected_names or lower.startswith("." + "env"):
            return ArtifactResult(ok=False, error=f"protected artifact path rejected: {part}")
        if ("token" + "_cache") in lower:
            return ArtifactResult(ok=False, error=f"protected artifact path rejected: {part}")

    path = (root_check.root / raw).resolve()
    try:
        path.relative_to(root_check.root)
    except ValueError:
        return ArtifactResult(ok=False, error="artifact path escapes OPEN_TRADING_API_ROOT")
    if not path.is_file():
        return ArtifactResult(ok=False, path=path, error=f"artifact file not found: {relative_path}")
    return ArtifactResult(ok=True, path=path)


def _read_bounded_text(path: Path, *, max_bytes: int) -> ArtifactResult:
    try:
        size = path.stat().st_size
    except OSError as exc:
        return ArtifactResult(ok=False, path=path, error=str(exc))
    if size > max_bytes:
        return ArtifactResult(ok=False, path=path, error=f"artifact exceeded {max_bytes} bytes")
    try:
        return ArtifactResult(ok=True, path=path, data=path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        return ArtifactResult(ok=False, path=path, error=str(exc))
