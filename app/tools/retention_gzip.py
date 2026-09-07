"""Retention gzip candidate tool (D2).

Codifies the §7 retention policy from ``docs/log_data_retention_plan.md``:
``logs/`` and ``data/`` files older than the gzip threshold (default 14 days)
become gzip+move candidates (destination ``archive/{logs,data}/``). §4 active
files are unconditionally excluded, and files whose age cannot be determined are
reported as skips (§8-3: never touch a file we cannot classify).

Classification is a **pure function** over ``(path, size, mtime)`` metadata — it
never opens or reads any file's contents. The CLI defaults to **dry-run** (list
only). ``--apply`` is fail-closed: it refuses when a live ``app.main`` session is
detected (or when session-lock metadata is unparseable / ambiguous).
"""

from __future__ import annotations

import argparse
import gzip
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional, Sequence

from app.core.session_lock import (
    process_alive,
    read_lock_metadata,
    same_program_running,
)

_DEFAULT_ROOT = Path(__file__).resolve().parents[2]
_APP_MAIN_COMMAND = "python -m app.main"

# §9 D1 (c): active (undated) files above this size are the operator
# rotate-active targets — same 100MB threshold the D3 checker uses for
# oversized_active_files.
_OVERSIZED_ACTIVE_BYTES = 100 * 1024 * 1024

_ROTATE_ACTIVE_CAUTION = (
    "[retention_gzip] operator caution: rotating an active cycle_snapshots file "
    "means the next session starts with an EMPTY history file (math-model warmup "
    "gap). Run --rotate-active --apply on weekends/holidays, outside session hours."
)

# §4 active-file stems that are current (no date suffix) → never rotate.
_ACTIVE_UNDATED_PREFIXES = (
    "orders_",
    "cycle_snapshots_",
    "performance_summary_",
)
# Trailing _YYYYMMDD before the file extension marks a dated (rotated) variant.
_DATE_SUFFIX_RE = re.compile(r"_\d{8}$")


def _is_active_file(path: str) -> bool:
    """True when ``path`` matches a §4 active (must-keep) pattern."""
    parts = PurePosixPath(path).parts
    name = PurePosixPath(path).name
    stem = PurePosixPath(name).stem  # drop final extension

    # runtime_state*.json anywhere.
    if name.startswith("runtime_state") and name.endswith(".json"):
        return True
    # Session-lock infra (app_main_*.lock and its .lock.json sidecar): rotating
    # these would break live-session detection and §8 rules.
    if ".lock" in name:
        return True
    # universe_batches/ subtree.
    if "universe_batches" in parts:
        return True
    # Current (undated) orders_/cycle_snapshots_/performance_summary_ files.
    for prefix in _ACTIVE_UNDATED_PREFIXES:
        if name.startswith(prefix) and not _DATE_SUFFIX_RE.search(stem):
            return True
    return False


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileEntry:
    """Metadata for one file — contents are never accessed."""

    path: str
    size: int
    mtime: float | None


@dataclass(frozen=True)
class RetentionPolicy:
    gzip_after_days: int = 14


@dataclass(frozen=True)
class Candidate:
    path: str
    size: int
    age_days: int
    dest: str


@dataclass(frozen=True)
class Skip:
    path: str
    reason: str


@dataclass
class ClassificationResult:
    candidates: list[Candidate] = field(default_factory=list)
    keep: list[FileEntry] = field(default_factory=list)
    skips: list[Skip] = field(default_factory=list)


@dataclass(frozen=True)
class LockRecord:
    """Read-only view of one app.main session lock (never mutated here)."""

    lock_path: str
    metadata: dict | None
    metadata_present: bool
    owner_alive: bool
    same_program: bool


@dataclass(frozen=True)
class ApplyGuardDecision:
    allowed: bool
    reason: str


def apply_guard_decision(records: Sequence[LockRecord]) -> ApplyGuardDecision:
    """Fail-closed --apply gate.

    Refuse whenever a live ``app.main`` session is detected, OR whenever a lock
    file exists but its metadata cannot be parsed (unknown state). Only an
    unambiguously clear tree (no locks, or only stale/dead ones) permits apply.
    """
    for record in records:
        if not record.metadata_present:
            return ApplyGuardDecision(
                allowed=False,
                reason=(
                    f"session lock without metadata sidecar at "
                    f"{record.lock_path}; state unknown, refusing --apply "
                    f"(fail-closed)"
                ),
            )
        if record.metadata_present and record.metadata is None:
            return ApplyGuardDecision(
                allowed=False,
                reason=(
                    f"unknown session-lock state (unparseable metadata) at "
                    f"{record.lock_path}; refusing --apply (fail-closed)"
                ),
            )
        if record.owner_alive and record.same_program:
            return ApplyGuardDecision(
                allowed=False,
                reason=(
                    f"live app.main session detected at {record.lock_path}; "
                    f"refusing --apply (§8-2)"
                ),
            )
    return ApplyGuardDecision(
        allowed=True, reason="no live app.main session detected"
    )


# ---------------------------------------------------------------------------
# Apply (gzip + move) — per-file isolation, fail-safe original removal
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Compressed:
    path: str
    dest: str


@dataclass(frozen=True)
class Failed:
    path: str
    reason: str


@dataclass
class ApplyOutcome:
    compressed: list[Compressed] = field(default_factory=list)
    failed: list[Failed] = field(default_factory=list)


def _verify_gzip(dest: Path) -> bool:
    """Re-open the gzip once (``gzip -t`` equivalent) to confirm integrity."""
    try:
        with gzip.open(dest, "rb") as handle:
            while handle.read(1024 * 1024):
                pass
    except OSError:
        return False
    return True


def apply_candidates(
    candidates: Sequence[Candidate],
    *,
    project_root: Path,
) -> ApplyOutcome:
    """Gzip+move each candidate. Per-file isolation: one failure never aborts
    the run; the original is deleted only after the gzip integrity check."""
    root = Path(project_root)
    outcome = ApplyOutcome()
    for cand in candidates:
        src = root / cand.path
        dest = root / cand.dest
        try:
            if not src.is_file():
                raise FileNotFoundError(f"source missing: {cand.path}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with src.open("rb") as src_fh, gzip.open(dest, "wb") as dest_fh:
                shutil.copyfileobj(src_fh, dest_fh)
            if not _verify_gzip(dest):
                # Integrity failed → drop the partial archive, keep the original.
                dest.unlink(missing_ok=True)
                raise OSError(f"gzip integrity check failed: {cand.dest}")
            src.unlink()
            outcome.compressed.append(Compressed(path=cand.path, dest=cand.dest))
        except OSError as exc:
            outcome.failed.append(Failed(path=cand.path, reason=str(exc)))
    return outcome


# ---------------------------------------------------------------------------
# Active-file rotation (§9 D1 option (c) — operator-run, offline)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RotationPlan:
    """A planned rename of one oversized active (undated) file."""

    path: str
    size: int
    rotated_path: str


@dataclass(frozen=True)
class Rotated:
    path: str
    rotated_path: str


@dataclass(frozen=True)
class RotateFailed:
    path: str
    reason: str


@dataclass
class RotateOutcome:
    rotated: list[Rotated] = field(default_factory=list)
    failed: list[RotateFailed] = field(default_factory=list)


def plan_active_rotations(
    entries: Iterable[FileEntry],
    *,
    now: datetime,
) -> list[RotationPlan]:
    """Pure planner: oversized (>100MB) active (undated) files → RotationPlan.

    The rotated name is ``<stem>_rotated_<YYYYMMDD>_<HHMMSS><ext>`` in the SAME
    directory, so it becomes a dated file eligible for the existing D2 gzip flow.
    Contents are never read — metadata only.
    """
    stamp = now.strftime("%Y%m%d_%H%M%S")
    plans: list[RotationPlan] = []
    for entry in entries:
        if not _is_active_file(entry.path):
            continue
        if entry.size <= _OVERSIZED_ACTIVE_BYTES:
            continue
        pure = PurePosixPath(entry.path)
        parent = pure.parent
        # ``.stem``/``.suffix`` split off only the final extension.
        stem = pure.stem
        suffix = pure.suffix
        rotated_name = f"{stem}_rotated_{stamp}{suffix}"
        rotated_path = (parent / rotated_name).as_posix()
        plans.append(
            RotationPlan(
                path=entry.path, size=entry.size, rotated_path=rotated_path
            )
        )
    return plans


def rotate_active_files(
    plans: Sequence[RotationPlan],
    *,
    project_root: Path,
) -> RotateOutcome:
    """Apply each RotationPlan: rename the active file to its dated name, then
    recreate an EMPTY active file with the original name + permissions.

    Per-file isolation: one failure never aborts the run.
    """
    root = Path(project_root)
    outcome = RotateOutcome()
    for plan in plans:
        src = root / plan.path
        dest = root / plan.rotated_path
        try:
            if not src.is_file():
                raise FileNotFoundError(f"source missing: {plan.path}")
            if dest.exists():
                raise FileExistsError(f"rotated target already exists: {plan.rotated_path}")
            original_mode = src.stat().st_mode & 0o777
            src.rename(dest)
            # Recreate an empty active file with the original name + permissions.
            src.touch()
            os.chmod(src, original_mode)
            outcome.rotated.append(
                Rotated(path=plan.path, rotated_path=plan.rotated_path)
            )
        except OSError as exc:
            outcome.failed.append(RotateFailed(path=plan.path, reason=str(exc)))
    return outcome


# ---------------------------------------------------------------------------
# Filesystem inventory (metadata only) + lock scan
# ---------------------------------------------------------------------------


def scan_file_entries(
    root: Path | str, *, subdirs: Sequence[str] = ("logs", "data")
) -> list[FileEntry]:
    """Walk ``root/<subdir>`` and produce ``FileEntry`` metadata via ``stat``.

    File **contents are never read** — only ``os.stat`` size/mtime. Paths are
    returned relative to ``root`` with POSIX separators.
    """
    root_path = Path(root)
    entries: list[FileEntry] = []
    for subdir in subdirs:
        base = root_path / subdir
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.is_symlink():
                continue
            rel = path.relative_to(root_path).as_posix()
            try:
                stat = path.stat()
            except OSError:
                entries.append(FileEntry(path=rel, size=0, mtime=None))
                continue
            entries.append(FileEntry(path=rel, size=stat.st_size, mtime=stat.st_mtime))
    return entries


def scan_lock_records(root: Path | str) -> list[LockRecord]:
    """Read app.main session-lock metadata read-only (never touches the lock)."""
    logs_dir = Path(root) / "logs"
    records: list[LockRecord] = []
    if not logs_dir.exists():
        return records
    for lock_path in sorted(logs_dir.glob("app_main_*.lock")):
        metadata_path = lock_path.with_name(f"{lock_path.name}.json")
        metadata_present = metadata_path.exists()
        metadata = read_lock_metadata(lock_path) if metadata_present else None
        pid = None
        if isinstance(metadata, dict):
            pid = metadata.get("app_pid") or metadata.get("pid")
            try:
                pid = int(pid) if pid else None
            except (TypeError, ValueError):
                pid = None
        command = ""
        if isinstance(metadata, dict):
            command = str(metadata.get("command") or _APP_MAIN_COMMAND)
        records.append(
            LockRecord(
                lock_path=lock_path.as_posix(),
                metadata=metadata,
                metadata_present=metadata_present,
                owner_alive=process_alive(pid),
                same_program=same_program_running(pid, command or _APP_MAIN_COMMAND),
            )
        )
    return records


# ---------------------------------------------------------------------------
# Reporting + CLI
# ---------------------------------------------------------------------------


def _render_report(result: ClassificationResult, *, apply: bool) -> str:
    mode = "APPLY" if apply else "dry-run"
    lines = [f"[retention_gzip] mode={mode}", ""]
    lines.append(f"gzip+move candidates ({len(result.candidates)}):")
    for cand in sorted(result.candidates, key=lambda c: c.path):
        lines.append(
            f"  {cand.path}  ({cand.size}B, age={cand.age_days}d) -> {cand.dest}"
        )
    if not result.candidates:
        lines.append("  (none)")
    lines.append("")
    lines.append(f"skipped ({len(result.skips)}):")
    for skip in sorted(result.skips, key=lambda s: s.path):
        lines.append(f"  {skip.path}  [{skip.reason}]")
    if not result.skips:
        lines.append("  (none)")
    return "\n".join(lines)


def _render_rotation_report(plans: Sequence[RotationPlan], *, apply: bool) -> str:
    mode = "APPLY" if apply else "dry-run"
    lines = [f"[retention_gzip] rotate-active mode={mode}", ""]
    lines.append(f"oversized active rotation candidates ({len(plans)}):")
    for plan in sorted(plans, key=lambda p: p.path):
        lines.append(
            f"  {plan.path}  ({plan.size}B) -> {plan.rotated_path}"
        )
    if not plans:
        lines.append("  (none)")
    lines.append("")
    lines.append(_ROTATE_ACTIVE_CAUTION)
    return "\n".join(lines)


def _run_rotate_active(root: Path, *, apply: bool) -> int:
    entries = scan_file_entries(root)
    plans = plan_active_rotations(entries, now=datetime.now())

    print(_render_rotation_report(plans, apply=apply))

    if not apply:
        return 0

    decision = apply_guard_decision(scan_lock_records(root))
    if not decision.allowed:
        print(f"\n[retention_gzip] --apply REFUSED: {decision.reason}")
        return 2

    outcome = rotate_active_files(plans, project_root=root)
    print(
        f"\n[retention_gzip] rotated: renamed={len(outcome.rotated)} "
        f"failed={len(outcome.failed)}"
    )
    for failed in outcome.failed:
        print(f"  FAILED {failed.path}: {failed.reason}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app.tools.retention_gzip",
        description=(
            "List (dry-run) or apply gzip+move retention candidates per the §7 "
            "policy. Default is dry-run; --apply is fail-closed on a live app.main."
        ),
    )
    parser.add_argument(
        "--root",
        default=str(_DEFAULT_ROOT),
        help="Project root (default: repo root).",
    )
    parser.add_argument(
        "--gzip-after-days",
        type=int,
        default=14,
        help="Age threshold in days (strictly greater => candidate).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute gzip+move (guarded by session-lock detection).",
    )
    parser.add_argument(
        "--rotate-active",
        action="store_true",
        help=(
            "Operator mode (§9 D1 (c)): rotate oversized (>100MB) active files by "
            "renaming to a dated variant + recreating an empty active file. "
            "Default dry-run; --apply is fail-closed on a live app.main session."
        ),
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    root = Path(args.root)

    if args.rotate_active:
        return _run_rotate_active(root, apply=args.apply)

    policy = RetentionPolicy(gzip_after_days=args.gzip_after_days)

    entries = scan_file_entries(root)
    result = classify_retention_candidates(entries, today=date.today(), policy=policy)

    print(_render_report(result, apply=args.apply))

    if not args.apply:
        return 0

    decision = apply_guard_decision(scan_lock_records(root))
    if not decision.allowed:
        print(f"\n[retention_gzip] --apply REFUSED: {decision.reason}")
        return 2

    outcome = apply_candidates(result.candidates, project_root=root)
    print(
        f"\n[retention_gzip] applied: compressed={len(outcome.compressed)} "
        f"failed={len(outcome.failed)}"
    )
    for failed in outcome.failed:
        print(f"  FAILED {failed.path}: {failed.reason}")
    return 0


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _age_days(mtime: float, today: date) -> int:
    file_day = datetime.fromtimestamp(mtime).date()
    return (today - file_day).days


def classify_retention_candidates(
    entries: Iterable[FileEntry],
    *,
    today: date,
    policy: RetentionPolicy,
) -> ClassificationResult:
    result = ClassificationResult()
    for entry in entries:
        if _is_active_file(entry.path):
            result.keep.append(entry)
            continue
        top = PurePosixPath(entry.path).parts[0] if entry.path else ""
        if top not in {"logs", "data"}:
            result.skips.append(
                Skip(path=entry.path, reason=f"out_of_scope_subtree:{top or '?'}")
            )
            continue
        if entry.mtime is None:
            result.skips.append(Skip(path=entry.path, reason="unknown_mtime"))
            continue
        age = _age_days(entry.mtime, today)
        if age > policy.gzip_after_days:
            dest = f"archive/{top}/{PurePosixPath(entry.path).name}.gz"
            result.candidates.append(
                Candidate(path=entry.path, size=entry.size, age_days=age, dest=dest)
            )
        else:
            result.keep.append(entry)
    return result


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
