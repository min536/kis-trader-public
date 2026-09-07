"""Archive retired account-scope artifacts.

Dry-run is the default. ``--apply`` moves files and writes a MANIFEST, but the
current active account signature is always refused.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from app.auth.account_scope import ACCOUNT_SCOPE_HISTORY_FILE, get_account_signature
from app.auth.settings import PROJECT_ROOT
from app.dashboard.account_discovery import _SIGNATURE_SOURCES


DATA_DEFAULT_DIR = PROJECT_ROOT / "data"
LOGS_DEFAULT_DIR = PROJECT_ROOT / "logs"
ARCHIVE_DEFAULT_ROOT = PROJECT_ROOT / "archive" / "accounts"


class ActiveSignatureArchiveError(ValueError):
    """Raised when an archive plan targets the currently active signature."""


@dataclass(frozen=True)
class ScopeArchiveTarget:
    signature: str
    label: str = ""
    retired_at: str = ""


@dataclass(frozen=True)
class ArchiveFilePlan:
    source: Path
    destination: Path
    family: str
    root_key: str
    size_bytes: int
    partition_date: str | None = None


@dataclass(frozen=True)
class ArchivePlan:
    signature: str
    label: str
    archived_at: str
    archive_dir: Path
    manifest_path: Path
    files: tuple[ArchiveFilePlan, ...]


@dataclass(frozen=True)
class ArchiveOutcome:
    moved: tuple[ArchiveFilePlan, ...]
    manifest_path: Path | None


def _sanitize_part(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "unlabeled"
    normalized = "".join(
        character if character.isalnum() else "_"
        for character in text
    )
    return normalized.strip("_") or "unlabeled"


def _date_token(value: object) -> str:
    text = str(value or "").strip()
    if len(text) >= 10 and re.match(r"^\d{4}-\d{2}-\d{2}", text):
        return text[:10].replace("-", "")
    if re.match(r"^\d{8}$", text):
        return text
    return ""


def _read_history_records(history_file: Path) -> tuple[dict[str, Any], ...]:
    if not history_file.exists():
        return ()
    records: list[dict[str, Any]] = []
    try:
        lines = history_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            records.append(payload)
    return tuple(records)


def load_retired_scope_targets(
    *,
    history_file: Path = ACCOUNT_SCOPE_HISTORY_FILE,
    active_signature: str,
) -> tuple[ScopeArchiveTarget, ...]:
    metadata: dict[str, dict[str, str]] = {}
    for record in _read_history_records(history_file):
        signature = str(record.get("signature") or "").strip()
        label = str(record.get("label") or "").strip()
        first_seen = _date_token(record.get("first_seen_at"))
        if signature:
            current = metadata.setdefault(signature, {})
            if label:
                current["label"] = label
            if first_seen and "first_seen_at" not in current:
                current["first_seen_at"] = first_seen

        retired_previous = str(record.get("retired_previous") or "").strip()
        if retired_previous:
            retired = metadata.setdefault(retired_previous, {})
            retired_at = _date_token(record.get("first_seen_at"))
            if retired_at:
                retired["retired_at"] = retired_at

    active = str(active_signature or "").strip()
    targets: list[ScopeArchiveTarget] = []
    for signature in sorted(metadata):
        if not signature or signature == active:
            continue
        info = metadata[signature]
        targets.append(
            ScopeArchiveTarget(
                signature=signature,
                label=info.get("label", ""),
                retired_at=info.get("retired_at") or info.get("first_seen_at", ""),
            )
        )
    return tuple(targets)


def _iter_signature_files(
    signature: str,
    *,
    data_dir: Path,
    logs_dir: Path,
) -> tuple[tuple[Path, str, str, str | None], ...]:
    files: list[tuple[Path, str, str, str | None]] = []
    for root_key, prefix, extension, partitioned in _SIGNATURE_SOURCES:
        root = data_dir if root_key == "data" else logs_dir
        family = prefix.rstrip("_")
        if partitioned:
            pattern = re.compile(
                rf"^{re.escape(prefix + signature)}_(\d{{8}}){re.escape(extension)}$"
            )
            for path in root.glob(f"{prefix}{signature}_*{extension}"):
                match = pattern.match(path.name)
                if match and path.is_file():
                    files.append((path, family, root_key, match.group(1)))
            continue

        path = root / f"{prefix}{signature}{extension}"
        if path.is_file():
            files.append((path, family, root_key, None))
    return tuple(sorted(files, key=lambda item: str(item[0])))


def plan_archive_for_signature(
    signature: str,
    *,
    active_signature: str,
    label: str = "",
    retired_at: str = "",
    data_dir: Path = DATA_DEFAULT_DIR,
    logs_dir: Path = LOGS_DEFAULT_DIR,
    archive_root: Path = ARCHIVE_DEFAULT_ROOT,
    now: datetime | None = None,
) -> ArchivePlan:
    normalized_signature = str(signature or "").strip()
    if not normalized_signature:
        raise ValueError("signature is required")
    if normalized_signature == str(active_signature or "").strip():
        raise ActiveSignatureArchiveError(
            f"refusing to archive active signature: {normalized_signature}"
        )

    archived_at_dt = now or datetime.now()
    date_prefix = _date_token(retired_at) or archived_at_dt.strftime("%Y%m%d")
    sanitized_label = _sanitize_part(label)
    archive_dir = archive_root / f"{date_prefix}_{sanitized_label}_{normalized_signature}"
    files = tuple(
        ArchiveFilePlan(
            source=path,
            destination=archive_dir / path.name,
            family=family,
            root_key=root_key,
            size_bytes=path.stat().st_size,
            partition_date=partition_date,
        )
        for path, family, root_key, partition_date in _iter_signature_files(
            normalized_signature,
            data_dir=data_dir,
            logs_dir=logs_dir,
        )
    )
    return ArchivePlan(
        signature=normalized_signature,
        label=str(label or "").strip(),
        archived_at=archived_at_dt.isoformat(),
        archive_dir=archive_dir,
        manifest_path=archive_dir / "MANIFEST.json",
        files=files,
    )


def _manifest(plan: ArchivePlan) -> dict[str, Any]:
    partition_dates = sorted(
        date for date in (item.partition_date for item in plan.files) if date
    )
    return {
        "signature": plan.signature,
        "label": plan.label,
        "archived_at": plan.archived_at,
        "archive_dir": str(plan.archive_dir),
        "manifest_path": str(plan.manifest_path),
        "file_count": len(plan.files),
        "total_bytes": sum(item.size_bytes for item in plan.files),
        "date_range": (
            {"min": partition_dates[0], "max": partition_dates[-1]}
            if partition_dates
            else {"min": None, "max": None}
        ),
        "files": [
            {
                "family": item.family,
                "root": item.root_key,
                "source": str(item.source),
                "destination": str(item.destination),
                "bytes": item.size_bytes,
                "partition_date": item.partition_date,
            }
            for item in plan.files
        ],
    }


def apply_archive_plan(plan: ArchivePlan, *, apply: bool = False) -> ArchiveOutcome:
    if not apply or not plan.files:
        return ArchiveOutcome(moved=(), manifest_path=None)

    plan.archive_dir.mkdir(parents=True, exist_ok=True)
    if plan.manifest_path.exists():
        raise FileExistsError(f"manifest already exists: {plan.manifest_path}")
    for item in plan.files:
        if item.destination.exists():
            raise FileExistsError(f"archive destination already exists: {item.destination}")

    moved: list[ArchiveFilePlan] = []
    for item in plan.files:
        shutil.move(str(item.source), str(item.destination))
        moved.append(item)

    plan.manifest_path.write_text(
        json.dumps(_manifest(plan), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return ArchiveOutcome(moved=tuple(moved), manifest_path=plan.manifest_path)


def _render_plan(plan: ArchivePlan, *, apply: bool) -> str:
    mode = "APPLY" if apply else "DRY-RUN"
    lines = [
        f"[archive_account_scope] {mode} signature={plan.signature}",
        f"  label={plan.label or '-'}",
        f"  archive_dir={plan.archive_dir}",
        f"  manifest={plan.manifest_path}",
        f"  files={len(plan.files)}",
    ]
    for item in plan.files:
        lines.append(
            f"  {item.source} ({item.size_bytes}B) -> {item.destination}"
        )
    if not plan.files:
        lines.append("  (no files)")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app.tools.archive_account_scope",
        description=(
            "Archive retired account-scoped artifacts. Default is dry-run; "
            "--apply moves files and writes MANIFEST.json."
        ),
    )
    parser.add_argument("--signature", action="append", default=[], help="Signature to archive; repeatable.")
    parser.add_argument("--retired", action="store_true", help="Archive all non-active signatures from account scope history.")
    parser.add_argument("--data-dir", default=str(DATA_DEFAULT_DIR))
    parser.add_argument("--logs-dir", default=str(LOGS_DEFAULT_DIR))
    parser.add_argument("--archive-root", default=str(ARCHIVE_DEFAULT_ROOT))
    parser.add_argument("--history-file", default=str(ACCOUNT_SCOPE_HISTORY_FILE))
    parser.add_argument("--apply", action="store_true", help="Move files. Without this flag the tool only prints a plan.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    active_signature = get_account_signature()
    data_dir = Path(args.data_dir)
    logs_dir = Path(args.logs_dir)
    archive_root = Path(args.archive_root)

    targets_by_signature: dict[str, ScopeArchiveTarget] = {}
    if args.retired:
        for target in load_retired_scope_targets(
            history_file=Path(args.history_file),
            active_signature=active_signature,
        ):
            targets_by_signature[target.signature] = target
    for signature in args.signature:
        normalized = str(signature or "").strip()
        if normalized:
            targets_by_signature.setdefault(
                normalized,
                ScopeArchiveTarget(signature=normalized),
            )

    if not targets_by_signature:
        print("[archive_account_scope] no archive targets")
        return 0

    plans: list[ArchivePlan] = []
    try:
        for target in targets_by_signature.values():
            plans.append(
                plan_archive_for_signature(
                    target.signature,
                    active_signature=active_signature,
                    label=target.label,
                    retired_at=target.retired_at,
                    data_dir=data_dir,
                    logs_dir=logs_dir,
                    archive_root=archive_root,
                )
            )
    except ActiveSignatureArchiveError as exc:
        print(f"[archive_account_scope] REFUSED: {exc}")
        return 2

    for plan in plans:
        print(_render_plan(plan, apply=bool(args.apply)))
        outcome = apply_archive_plan(plan, apply=bool(args.apply))
        if args.apply:
            print(
                f"[archive_account_scope] moved={len(outcome.moved)} "
                f"manifest={outcome.manifest_path or '-'}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
