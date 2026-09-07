import hashlib
import json
import os
from pathlib import Path
from typing import Any

from app.auth.settings import (
    PROJECT_ROOT,
    Settings,
    classify_kis_base_url_env,
    get_settings,
)
from app.core.time_utils import get_korean_now

ACCOUNT_SCOPE_META_FILE = PROJECT_ROOT / "data" / "account_scope_meta.json"
ACCOUNT_SCOPE_HISTORY_FILE = PROJECT_ROOT / "data" / "account_scope_history.jsonl"
ACCOUNT_LABEL_ENV = "KIS_ACCOUNT_LABEL"
STATE_ROOT_ENV = "KIS_STATE_ROOT"


def state_root() -> Path:
    """Root under which per-account ``data/``·``logs/`` state files resolve.

    Defaults to :data:`PROJECT_ROOT` so production paths are unchanged. Tests set
    ``KIS_STATE_ROOT`` (see ``tests/conftest.py``) to redirect every signature
    state writer into a per-test temporary root — the single env seam that
    fixes the real-path test-isolation leak (design:
    ``docs/test_isolation_leak_design_20260707.md`` §4-1 P1 / G3). A blank value
    falls back to :data:`PROJECT_ROOT`.
    """
    override = os.environ.get(STATE_ROOT_ENV, "").strip()
    if override:
        return Path(override)
    return PROJECT_ROOT


def _resolve_settings(settings: Settings | None = None) -> Settings:
    return settings if settings is not None else get_settings()


def _sanitize_part(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "unknown"
    normalized = "".join(
        character if character.isalnum() else "_"
        for character in text
    )
    return normalized.strip("_") or "unknown"


def get_account_environment(settings: Settings | None = None) -> str:
    resolved = _resolve_settings(settings)
    environment = classify_kis_base_url_env(str(resolved.base_url or ""))
    if environment is None:
        raise ValueError("KIS base URL host is not recognized.")
    return environment


def get_account_signature(settings: Settings | None = None) -> str:
    resolved = _resolve_settings(settings)
    environment = get_account_environment(resolved)
    digest_input = "\0".join(
        (
            "kis-trader-account-scope-v1",
            environment,
            str(resolved.cano or "").strip(),
            str(resolved.acnt_prdt_cd or "").strip(),
            str(getattr(resolved, "app_key", "") or "").strip(),
            str(getattr(resolved, "app_secret", "") or "").strip(),
        )
    )
    digest = hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:16]
    return f"{_sanitize_part(environment)}_acct_{digest}"


def get_legacy_account_signature(settings: Settings | None = None) -> str:
    resolved = _resolve_settings(settings)
    environment = get_account_environment(resolved)
    return (
        f"{_sanitize_part(environment)}_"
        f"{_sanitize_part(resolved.cano)}_"
        f"{_sanitize_part(resolved.acnt_prdt_cd)}"
    )


def get_masked_account_display(settings: Settings | None = None) -> str:
    resolved = _resolve_settings(settings)
    cano = str(resolved.cano or "").strip()
    product_code = str(resolved.acnt_prdt_cd or "").strip()
    if len(cano) >= 6:
        masked_cano = f"{cano[:4]}***{cano[-2:]}"
    elif cano:
        masked_cano = f"{cano[:2]}***"
    else:
        masked_cano = "unknown"
    return f"{masked_cano}-{product_code or 'unknown'}"


def get_account_scope_context(settings: Settings | None = None) -> dict[str, str]:
    resolved = _resolve_settings(settings)
    return {
        "account_signature": get_account_signature(resolved),
        "account_environment": get_account_environment(resolved),
        "masked_account_display": get_masked_account_display(resolved),
    }


def _data_path_for_signature(prefix: str, extension: str, *, signature: str) -> Path:
    return state_root() / "data" / f"{prefix}_{signature}{extension}"


def _log_path_for_signature(prefix: str, extension: str, *, signature: str) -> Path:
    return state_root() / "logs" / f"{prefix}_{signature}{extension}"


def dashboard_paths_for_signature(
    signature: str,
    *,
    data_dir: Path | None = None,
    logs_dir: Path | None = None,
) -> dict[str, Path]:
    """Resolve the five dashboard source files for an explicit account signature.

    Discovered signatures are opaque hashes, so a ``Settings`` cannot be
    reconstructed from them; this maps a signature straight to its on-disk paths
    for the read-only multi-account overview. ``data_dir``/``logs_dir`` default to
    the repo's ``data/``/``logs/`` and exist mainly for hermetic tests.
    """
    data_root = data_dir if data_dir is not None else state_root() / "data"
    logs_root = logs_dir if logs_dir is not None else state_root() / "logs"
    return {
        "runtime_state": data_root / f"runtime_state_{signature}.json",
        "cycle_snapshots": data_root / f"cycle_snapshots_{signature}.jsonl",
        "performance_snapshots": data_root / f"performance_snapshots_{signature}.jsonl",
        "orders": logs_root / f"orders_{signature}.jsonl",
        "performance_summary": logs_root / f"performance_summary_{signature}.jsonl",
    }


def _data_path(prefix: str, extension: str, *, settings: Settings | None = None) -> Path:
    return _data_path_for_signature(
        prefix,
        extension,
        signature=get_account_signature(settings),
    )


def _legacy_data_path(prefix: str, extension: str, *, settings: Settings | None = None) -> Path:
    return _data_path_for_signature(
        prefix,
        extension,
        signature=get_legacy_account_signature(settings),
    )


def _log_path(prefix: str, extension: str, *, settings: Settings | None = None) -> Path:
    return _log_path_for_signature(
        prefix,
        extension,
        signature=get_account_signature(settings),
    )


def _legacy_log_path(prefix: str, extension: str, *, settings: Settings | None = None) -> Path:
    return _log_path_for_signature(
        prefix,
        extension,
        signature=get_legacy_account_signature(settings),
    )


def _dedupe_paths(paths: list[Path]) -> tuple[Path, ...]:
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return tuple(deduped)


def get_runtime_state_path(settings: Settings | None = None) -> Path:
    return _data_path("runtime_state", ".json", settings=settings)


def get_runtime_state_read_paths(settings: Settings | None = None) -> tuple[Path, ...]:
    return _dedupe_paths(
        [
            get_runtime_state_path(settings),
            _legacy_data_path("runtime_state", ".json", settings=settings),
        ]
    )


def get_cycle_snapshots_path(settings: Settings | None = None) -> Path:
    return _data_path("cycle_snapshots", ".jsonl", settings=settings)


def get_performance_snapshots_path(settings: Settings | None = None) -> Path:
    return _data_path("performance_snapshots", ".jsonl", settings=settings)


def get_order_log_path(settings: Settings | None = None) -> Path:
    return _log_path("orders", ".jsonl", settings=settings)


def get_order_log_read_paths(settings: Settings | None = None) -> tuple[Path, ...]:
    return _dedupe_paths(
        [
            get_order_log_path(settings),
            _legacy_log_path("orders", ".jsonl", settings=settings),
        ]
    )


def get_performance_summary_path(settings: Settings | None = None) -> Path:
    return _log_path("performance_summary", ".jsonl", settings=settings)


def get_partitioned_log_path(
    prefix: str,
    *,
    suffix: str,
    settings: Settings | None = None,
) -> Path:
    signature = get_account_signature(settings)
    return state_root() / "logs" / f"{prefix}_{signature}_{suffix}.jsonl"


def load_account_scope_meta() -> dict[str, Any]:
    if not ACCOUNT_SCOPE_META_FILE.exists():
        return {}
    try:
        raw = json.loads(ACCOUNT_SCOPE_META_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _account_label() -> str:
    return os.getenv(ACCOUNT_LABEL_ENV, "").strip()


def append_account_scope_history(
    context: dict[str, str],
    *,
    previous_account_signature: str | None,
) -> None:
    payload = {
        "signature": str(context.get("account_signature") or "").strip(),
        "environment": str(context.get("account_environment") or "").strip(),
        "masked_display": str(context.get("masked_account_display") or "").strip(),
        "label": _account_label(),
        "first_seen_at": get_korean_now().isoformat(),
        "retired_previous": previous_account_signature,
    }
    try:
        ACCOUNT_SCOPE_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with ACCOUNT_SCOPE_HISTORY_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    except OSError:
        pass


def sync_account_scope_meta(settings: Settings | None = None) -> dict[str, Any]:
    context = get_account_scope_context(settings)
    previous = load_account_scope_meta()
    previous_signature = str(previous.get("last_account_signature") or "").strip() or None
    current_signature = context["account_signature"]
    changed = previous_signature is not None and previous_signature != current_signature
    payload = {
        "last_account_signature": current_signature,
        "last_account_environment": context["account_environment"],
        "last_masked_account_display": context["masked_account_display"],
        "last_updated_at": get_korean_now().isoformat(),
    }
    try:
        ACCOUNT_SCOPE_META_FILE.parent.mkdir(parents=True, exist_ok=True)
        ACCOUNT_SCOPE_META_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass
    if changed:
        append_account_scope_history(
            context,
            previous_account_signature=previous_signature,
        )
    return {
        **context,
        "account_scope_changed": changed,
        "previous_account_signature": previous_signature,
    }
