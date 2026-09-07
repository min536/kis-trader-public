"""Read-only multi-account path-isolation preflight (C-4 Phase 2 tooling).

Confirms that the *operator-set* per-account env overrides resolve to
pairwise-distinct locations before any parallel mock session starts, so two
accounts can never share a live-snapshot file, a snapshot worker PID/heartbeat,
or a token cache:

  - ``KIS_LIVE_SNAPSHOT_DIR``      -> ``<dir>/live_snapshot.json``
  - ``KIS_LIVE_SNAPSHOT_LOG_DIR``  -> snapshot worker PID/heartbeat dir
  - ``KIS_CREDENTIAL_CACHE_DIR``   -> ``<dir>/kis_auth_{env}.json``

It handles NO secrets and makes NO network/broker calls. The session lock,
runtime_state, orders and cycle_snapshots files are already auto-separated by
``account_signature`` (``app/auth/account_scope.py``), so only these three
manually-set dirs are collision-prone and need checking. The snapshot worker
PID/heartbeat filenames are fixed (not signature-scoped), which is why the
log dir itself must differ per account.

This tool only validates *configuration*. The actual Phase 2 read-only API
sanity steps (token issue / quote / balance / rate-limit observation) are
operator-run against real KIS — see ``docs/multi_account_phase2_runbook.md``.
The agent must never execute those steps.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app.auth.settings import _CREDENTIAL_CACHE_DIR_ENV, get_token_cache_path
from app.market_data.live_snapshot import PROJECT_ROOT

SNAPSHOT_FILENAME = "live_snapshot.json"
DATA_DEFAULT_DIR = PROJECT_ROOT / "data"
LOGS_DEFAULT_DIR = PROJECT_ROOT / "logs"

# resource key -> (human label, operator env var that controls it)
_RESOURCES: tuple[tuple[str, str, str], ...] = (
    ("live_snapshot", "live snapshot file", "KIS_LIVE_SNAPSHOT_DIR"),
    ("snapshot_worker_dir", "snapshot worker PID/heartbeat dir", "KIS_LIVE_SNAPSHOT_LOG_DIR"),
    ("token_cache", "token cache file", "KIS_CREDENTIAL_CACHE_DIR"),
)


@dataclass(frozen=True)
class AccountEnvProfile:
    """A single account's manually-set isolation env overrides (no secrets)."""

    label: str
    snapshot_dir: str | None = None
    snapshot_log_dir: str | None = None
    credential_cache_dir: str | None = None
    env: str = "mock"


@dataclass(frozen=True)
class PreflightIssue:
    severity: str  # "error"
    resource: str
    env_var: str
    message: str
    profiles: tuple[str, ...]


@dataclass(frozen=True)
class PreflightReport:
    ok: bool
    issues: tuple[PreflightIssue, ...]
    resources: dict[str, dict[str, str]] = field(default_factory=dict)

    def format_text(self) -> str:
        lines: list[str] = []
        for label, paths in self.resources.items():
            lines.append(f"[{label}]")
            for resource_key, _human, _env in _RESOURCES:
                lines.append(f"  {resource_key}: {paths.get(resource_key, '')}")
        if self.issues:
            lines.append("")
            lines.append(f"COLLISIONS ({len(self.issues)}):")
            for issue in self.issues:
                lines.append(f"  ! {issue.message}")
        verdict = "OK — all accounts isolated" if self.ok else "FAIL — shared resources detected"
        lines.append("")
        lines.append(verdict)
        return "\n".join(lines)


def _normalized(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


@contextlib.contextmanager
def _scoped_env(overrides: dict[str, str | None]):
    """Temporarily apply env overrides, restoring originals on exit.

    A value of ``None``/empty unsets the variable so the resolved default is
    observed. Kept local so resolution reuses the real ``get_token_cache_path``
    logic without permanently mutating the process environment.
    """
    saved: dict[str, str | None] = {}
    try:
        for key, value in overrides.items():
            saved[key] = os.environ.get(key)
            if not value:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, original in saved.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original


def _normalize_path(path: Path) -> Path:
    """Absolute, ``..``/symlink-normalized form for reliable collision compares.

    ``expanduser`` alone leaves ``..`` segments, symlinks and relative paths
    intact, so two strings that point at the same file would compare distinct
    and a real collision would be missed. ``resolve`` (non-strict) canonicalizes
    even for not-yet-existing paths.
    """
    return path.expanduser().resolve()


def _token_cache_path(profile: AccountEnvProfile) -> Path:
    env = (profile.env or "mock").strip().lower()
    # _scoped_env couples to get_token_cache_path reading KIS_CREDENTIAL_CACHE_DIR
    # from os.environ (app/auth/settings.py); reusing it avoids re-deriving the
    # cache-dir/filename convention here and drifting from runtime behavior.
    with _scoped_env({_CREDENTIAL_CACHE_DIR_ENV: _normalized(profile.credential_cache_dir)}):
        return _normalize_path(get_token_cache_path(env))


def resolve_resources(profile: AccountEnvProfile) -> dict[str, Path]:
    """Resolve the three collision-prone resource paths for one account."""
    data_dir = (
        Path(_normalized(profile.snapshot_dir)).expanduser()
        if _normalized(profile.snapshot_dir)
        else DATA_DEFAULT_DIR
    )
    log_dir = (
        Path(_normalized(profile.snapshot_log_dir)).expanduser()
        if _normalized(profile.snapshot_log_dir)
        else LOGS_DEFAULT_DIR
    )
    return {
        "live_snapshot": _normalize_path(data_dir / SNAPSHOT_FILENAME),
        "snapshot_worker_dir": _normalize_path(log_dir),
        "token_cache": _token_cache_path(profile),
    }


def check_isolation(profiles: list[AccountEnvProfile]) -> PreflightReport:
    """Verify every account's three resource paths are pairwise distinct."""
    resources: dict[str, dict[str, str]] = {
        profile.label: {key: str(path) for key, path in resolve_resources(profile).items()}
        for profile in profiles
    }
    issues: list[PreflightIssue] = []

    labels = [profile.label for profile in profiles]
    duplicate_labels = sorted({label for label in labels if labels.count(label) > 1})
    for label in duplicate_labels:
        issues.append(
            PreflightIssue(
                severity="error",
                resource="label",
                env_var="",
                message=f"duplicate profile label {label!r}",
                profiles=(label,),
            )
        )

    if len(profiles) >= 2:
        for resource_key, human, env_var in _RESOURCES:
            grouped: dict[str, list[str]] = {}
            for profile in profiles:
                path_str = resources[profile.label][resource_key]
                grouped.setdefault(path_str, []).append(profile.label)
            for path_str, sharing in grouped.items():
                if len(sharing) > 1:
                    issues.append(
                        PreflightIssue(
                            severity="error",
                            resource=resource_key,
                            env_var=env_var,
                            message=(
                                f"{len(sharing)} accounts share {human} at {path_str} "
                                f"({', '.join(sharing)}); set a distinct {env_var} per account"
                            ),
                            profiles=tuple(sharing),
                        )
                    )

    return PreflightReport(ok=not issues, issues=tuple(issues), resources=resources)


def _profile_from_dict(raw: dict) -> AccountEnvProfile:
    # Prefer the friendly field names; fall back to the env-var names so a
    # profile can be written in either style. Friendly name wins if both exist.
    return AccountEnvProfile(
        label=str(raw.get("label") or raw.get("name") or "").strip() or "<unlabeled>",
        snapshot_dir=raw.get("snapshot_dir") or raw.get("KIS_LIVE_SNAPSHOT_DIR"),
        snapshot_log_dir=raw.get("snapshot_log_dir") or raw.get("KIS_LIVE_SNAPSHOT_LOG_DIR"),
        credential_cache_dir=raw.get("credential_cache_dir") or raw.get("KIS_CREDENTIAL_CACHE_DIR"),
        env=str(raw.get("env") or "mock").strip().lower() or "mock",
    )


def load_profiles(path: str | Path) -> list[AccountEnvProfile]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("accounts") or raw.get("profiles") or []
    if not isinstance(raw, list):
        raise ValueError("profiles JSON must be a list (or {accounts: [...]})")
    return [_profile_from_dict(item) for item in raw]


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.tools.multi_account_preflight",
        description=(
            "병렬 mock 운영 전 계좌별 경로 격리(snapshot/worker/token cache)를 "
            "검증합니다. secret/네트워크 미사용, read-only."
        ),
    )
    parser.add_argument(
        "--profiles",
        required=True,
        help=(
            "계좌 프로파일 JSON 경로 (secret 없이 label + dir override만). "
            "예: [{\"label\":\"A\",\"snapshot_dir\":\"/data/A\",...}]"
        ),
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="JSON 보고서 출력")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    profiles = load_profiles(args.profiles)
    report = check_isolation(profiles)
    if args.as_json:
        print(
            json.dumps(
                {
                    "ok": report.ok,
                    "resources": report.resources,
                    "issues": [
                        {
                            "severity": issue.severity,
                            "resource": issue.resource,
                            "env_var": issue.env_var,
                            "message": issue.message,
                            "profiles": list(issue.profiles),
                        }
                        for issue in report.issues
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(report.format_text())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
