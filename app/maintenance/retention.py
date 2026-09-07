"""Log/data file retention scanner + gzip executor (C-1).

Implements the policy in ``docs/log_data_retention_plan.md`` as a deterministic,
**metadata-only** tool: it stats files (size + mtime) and never reads their
contents, so it cannot leak large log/data payloads. It classifies each file and
recommends a retention action, then (only with ``--apply``) gzips the
historical/dated candidates.

Safety posture (mirrors the plan's §8 금지사항):
- **Dry-run by default.** ``--apply`` is required to actually compress anything.
- **Never rotates while ``app.main`` is running** — refuses if any
  ``logs/app_main_*.lock`` is held.
- **Never deletes data** — gzip writes ``foo.gz`` and only removes ``foo`` after
  the compressed copy is on disk; ``--keep-original`` keeps both.
- **Never touches active files.** A non-archive file with no *valid* date stamp
  is treated as a rolling current file (e.g. ``orders_mock_<sig>.jsonl``) and
  kept, regardless of age. An 8-digit account number is **not** a date stamp.
- Only non-archive files that are BOTH older than the gzip age AND carry a valid
  ``YYYYMMDD`` stamp become candidates — so a freshly-written current file
  (recent mtime) is always protected even if date parsing somehow misfired.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]

RECENT_DAYS = 7
GZIP_AGE_DAYS = 14
DELETE_REVIEW_DAYS = 90

SCAN_DIRS = ("logs", "data", "archive", "results")
COMPRESSED_SUFFIXES = (".gz", ".tgz", ".tar.gz", ".zip", ".bz2", ".xz", ".zst")

ACTIVE = "active"
RECENT = "recent"
GZIP_CANDIDATE = "gzip_candidate"
COMPRESSED = "compressed"
RESEARCH_RESULT = "research_result"
OTHER = "other"

KEEP = "keep"
GZIP = "gzip"
ARCHIVE_TARGZ = "archive_targz"

_DATE_RUN_RE = re.compile(r"(?<!\d)(\d{8})(?!\d)")
_MIN_STAMP_YEAR = 2015
_MAX_STAMP_YEAR = 2099


def _valid_date_stamps(name: str) -> list[str]:
    stamps: list[str] = []
    for match in _DATE_RUN_RE.finditer(name):
        token = match.group(1)
        try:
            parsed = datetime.strptime(token, "%Y%m%d")
        except ValueError:
            continue
        if _MIN_STAMP_YEAR <= parsed.year <= _MAX_STAMP_YEAR:
            stamps.append(token)
    return stamps


def has_valid_date_stamp(name: str) -> bool:
    return bool(_valid_date_stamps(name))


@dataclass(frozen=True)
class FileClassification:
    category: str
    action: str
    reason: str


def classify(
    *,
    name: str,
    under_archive: bool,
    under_results: bool,
    size_bytes: int,
    mtime_epoch: float,
    now_epoch: float,
) -> FileClassification:
    age_days = max(0.0, (now_epoch - mtime_epoch) / 86_400.0)
    lower = name.lower()

    if lower.endswith(COMPRESSED_SUFFIXES):
        return FileClassification(COMPRESSED, KEEP, "already_compressed")

    if under_results:
        return FileClassification(
            RESEARCH_RESULT, ARCHIVE_TARGZ, "research_result_experiment_bundle"
        )

    if under_archive:
        return FileClassification(GZIP_CANDIDATE, GZIP, "archive_historical_plain")

    if age_days <= GZIP_AGE_DAYS:
        category = RECENT if has_valid_date_stamp(name) else ACTIVE
        return FileClassification(category, KEEP, f"age<={GZIP_AGE_DAYS}d")

    if has_valid_date_stamp(name):
        return FileClassification(GZIP_CANDIDATE, GZIP, f"dated_log_age>{GZIP_AGE_DAYS}d")
    return FileClassification(ACTIVE, KEEP, "undated_nonarchive_keep")


@dataclass(frozen=True)
class ScannedFile:
    relpath: str
    size_bytes: int
    mtime_epoch: float
    age_days: float
    classification: FileClassification


def scan_retention(
    root: Path | str,
    *,
    now_epoch: float,
    scan_dirs: Sequence[str] = SCAN_DIRS,
) -> list[ScannedFile]:
    root_path = Path(root)
    scanned: list[ScannedFile] = []
    for sub in scan_dirs:
        base = root_path / sub
        if not base.exists():
            continue
        under_archive = sub == "archive"
        under_results = sub == "results"
        for path in sorted(base.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            age_days = max(0.0, (now_epoch - stat.st_mtime) / 86_400.0)
            classification = classify(
                name=path.name,
                under_archive=under_archive,
                under_results=under_results,
                size_bytes=stat.st_size,
                mtime_epoch=stat.st_mtime,
                now_epoch=now_epoch,
            )
            scanned.append(
                ScannedFile(
                    relpath=path.relative_to(root_path).as_posix(),
                    size_bytes=stat.st_size,
                    mtime_epoch=stat.st_mtime,
                    age_days=round(age_days, 2),
                    classification=classification,
                )
            )
    return scanned


@dataclass(frozen=True)
class RetentionReport:
    total_files: int
    total_bytes: int
    by_category: dict[str, dict[str, int]]
    gzip_candidate_count: int
    gzip_candidate_bytes: int
    gzip_candidates: tuple[tuple[str, int, float], ...]
    stale_review: tuple[tuple[str, int, float], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "total_files": self.total_files,
            "total_bytes": self.total_bytes,
            "by_category": self.by_category,
            "gzip_candidate_count": self.gzip_candidate_count,
            "gzip_candidate_bytes": self.gzip_candidate_bytes,
            "gzip_candidates": [
                {"relpath": rel, "size_bytes": size, "age_days": age}
                for rel, size, age in self.gzip_candidates
            ],
            "stale_review": [
                {"relpath": rel, "size_bytes": size, "age_days": age}
                for rel, size, age in self.stale_review
            ],
        }


def build_retention_report(
    scanned: Iterable[ScannedFile], *, top_n: int = 20
) -> RetentionReport:
    scanned = list(scanned)
    by_category: dict[str, dict[str, int]] = {}
    total_bytes = 0
    candidates: list[tuple[str, int, float]] = []
    stale: list[tuple[str, int, float]] = []
    for sf in scanned:
        total_bytes += sf.size_bytes
        bucket = by_category.setdefault(
            sf.classification.category, {"count": 0, "bytes": 0}
        )
        bucket["count"] += 1
        bucket["bytes"] += sf.size_bytes
        if sf.classification.action == GZIP:
            candidates.append((sf.relpath, sf.size_bytes, sf.age_days))
            if sf.age_days > DELETE_REVIEW_DAYS:
                stale.append((sf.relpath, sf.size_bytes, sf.age_days))
    candidates.sort(key=lambda item: item[1], reverse=True)
    stale.sort(key=lambda item: item[1], reverse=True)
    return RetentionReport(
        total_files=len(scanned),
        total_bytes=total_bytes,
        by_category=by_category,
        gzip_candidate_count=len(candidates),
        gzip_candidate_bytes=sum(size for _, size, _ in candidates),
        gzip_candidates=tuple(candidates[:top_n]),
        stale_review=tuple(stale[:top_n]),
    )


@dataclass(frozen=True)
class GzipResult:
    relpath: str
    status: str
    size_before: int
    size_after: int | None
    detail: str = ""


def gzip_file(
    path: Path | str, *, dry_run: bool = True, keep_original: bool = False
) -> GzipResult:
    p = Path(path)
    relpath = p.name if not p.is_absolute() else str(p)
    try:
        size_before = p.stat().st_size
    except OSError as exc:
        return GzipResult(relpath, "error", 0, None, f"stat_failed:{exc.__class__.__name__}")

    gz_path = p.with_name(p.name + ".gz")
    if gz_path.exists():
        return GzipResult(relpath, "skipped_exists", size_before, None, "gz_exists")
    if dry_run:
        return GzipResult(relpath, "planned", size_before, None, "dry_run")

    try:
        with p.open("rb") as src, gzip.open(gz_path, "wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
        size_after = gz_path.stat().st_size
    except OSError as exc:
        try:
            gz_path.unlink(missing_ok=True)
        except OSError:
            pass
        return GzipResult(relpath, "error", size_before, None, f"gzip_failed:{exc.__class__.__name__}")

    if not keep_original:
        try:
            p.unlink()
        except OSError as exc:
            return GzipResult(
                relpath, "error", size_before, size_after, f"unlink_failed:{exc.__class__.__name__}"
            )
    return GzipResult(relpath, "compressed", size_before, size_after, "")


def app_main_running(logs_dir: Path | str) -> bool:
    from app.core.session_lock import inspect_lock

    logs_path = Path(logs_dir)
    if not logs_path.exists():
        return False
    for lock_path in sorted(logs_path.glob("app_main_*.lock")):
        try:
            status = inspect_lock(lock_path)
        except OSError:
            continue
        if status.get("lock_held"):
            return True
    return False


def apply_retention(
    scanned: Iterable[ScannedFile],
    *,
    root: Path | str,
    dry_run: bool = True,
    app_main_is_running: bool | None = None,
    keep_original: bool = False,
) -> list[GzipResult]:
    root_path = Path(root)
    running = (
        app_main_running(root_path / "logs")
        if app_main_is_running is None
        else app_main_is_running
    )
    if running:
        return [GzipResult("*", "refused", 0, None, "app_main_running")]
    results: list[GzipResult] = []
    for sf in scanned:
        if sf.classification.action != GZIP:
            continue
        r = gzip_file(
            root_path / sf.relpath,
            dry_run=dry_run,
            keep_original=keep_original,
        )
        results.append(GzipResult(sf.relpath, r.status, r.size_before, r.size_after, r.detail))
    return results


def _format_bytes(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}TB"


def _render_report_text(report: RetentionReport) -> str:
    lines = [
        "📦 Retention scan",
        f"  files: {report.total_files} | size: {_format_bytes(report.total_bytes)}",
    ]
    for category in sorted(report.by_category):
        bucket = report.by_category[category]
        lines.append(
            f"  {category}: {bucket['count']} files / {_format_bytes(bucket['bytes'])}"
        )
    lines.append(
        f"  gzip candidates: {report.gzip_candidate_count} "
        f"({_format_bytes(report.gzip_candidate_bytes)} reclaimable)"
    )
    for rel, size, age in report.gzip_candidates[:10]:
        lines.append(f"    - {rel} ({_format_bytes(size)}, {age:.0f}d)")
    if report.stale_review:
        lines.append(f"  ⚠️ stale (>{DELETE_REVIEW_DAYS}d, delete-review): {len(report.stale_review)}")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scan log/data files for retention and optionally gzip old/historical ones.",
    )
    parser.add_argument(
        "--root", default=str(PROJECT_ROOT), help="Project root to scan (default: repo root)."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually gzip candidates (default: dry-run report only).",
    )
    parser.add_argument(
        "--keep-original",
        action="store_true",
        help="Keep the plain file alongside the .gz (default: remove after compress).",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    parser.add_argument("--top", type=int, default=20, help="Max candidates to list.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    root = Path(args.root)
    now = time.time()
    scanned = scan_retention(root, now_epoch=now)
    report = build_retention_report(scanned, top_n=args.top)

    if not args.apply:
        if args.json:
            print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True))
        else:
            print(_render_report_text(report))
            print("\n(dry-run — pass --apply to compress candidates)")
        return 0

    if app_main_running(root / "logs"):
        print("❌ refused: app.main is running — will not rotate logs while live.")
        return 2
    results = apply_retention(
        scanned,
        root=root,
        dry_run=False,
        app_main_is_running=False,
        keep_original=args.keep_original,
    )
    compressed = [r for r in results if r.status == "compressed"]
    reclaimed = sum(
        (r.size_before - (r.size_after or 0)) for r in compressed if r.size_after is not None
    )
    if args.json:
        print(
            json.dumps(
                {
                    "compressed": len(compressed),
                    "reclaimed_bytes": reclaimed,
                    "results": [r.__dict__ for r in results],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    else:
        print(f"✅ compressed {len(compressed)} file(s), reclaimed ~{_format_bytes(reclaimed)}")
        for r in results:
            if r.status != "compressed":
                print(f"  {r.status}: {r.relpath} ({r.detail})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
