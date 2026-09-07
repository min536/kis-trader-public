"""Retention policy checker (D3, read-only).

Detects §7 policy violations by **reusing** the D2 classifier
(``app.tools.retention_gzip.classify_retention_candidates``) — no duplicated
classification logic. Violations reported:

* ``stale_plain_files`` — plain (uncompressed) files past the gzip threshold that
  should already be gzip+moved (these are exactly the D2 candidates).

The checker is read-only over the tree (metadata only, no file contents). Its
only write is the optional ``--json <path>`` report file.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable, Optional, Sequence

from app.tools.retention_gzip import (
    FileEntry,
    RetentionPolicy,
    _is_active_file,
    classify_retention_candidates,
    scan_file_entries,
)

_DEFAULT_ROOT = Path(__file__).resolve().parents[2]
_OVERSIZED_ACTIVE_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class Violation:
    path: str
    size: int
    age_days: int
    kind: str


@dataclass
class ViolationsReport:
    stale_plain_files: list[Violation] = field(default_factory=list)
    oversized_active_files: list[Violation] = field(default_factory=list)


def build_violations_report(
    entries: Iterable[FileEntry],
    *,
    today: date,
    policy: RetentionPolicy,
) -> ViolationsReport:
    entries = list(entries)
    result = classify_retention_candidates(entries, today=today, policy=policy)
    report = ViolationsReport()
    for cand in result.candidates:
        report.stale_plain_files.append(
            Violation(
                path=cand.path,
                size=cand.size,
                age_days=cand.age_days,
                kind="stale_plain_over_threshold",
            )
        )
    for entry in entries:
        if _is_active_file(entry.path) and entry.size > _OVERSIZED_ACTIVE_BYTES:
            report.oversized_active_files.append(
                Violation(
                    path=entry.path,
                    size=entry.size,
                    age_days=-1,
                    kind="oversized_active_file",
                )
            )
    return report


def _render_report(report: ViolationsReport) -> str:
    lines = ["[retention_check] read-only policy scan", ""]
    lines.append(f"stale plain files past threshold ({len(report.stale_plain_files)}):")
    for v in sorted(report.stale_plain_files, key=lambda x: x.path):
        lines.append(f"  {v.path}  ({v.size}B, age={v.age_days}d)")
    if not report.stale_plain_files:
        lines.append("  (none)")
    lines.append("")
    lines.append(
        f"oversized active files (>100MB) ({len(report.oversized_active_files)}):"
    )
    for v in sorted(report.oversized_active_files, key=lambda x: x.path):
        lines.append(f"  {v.path}  ({v.size}B)")
    if not report.oversized_active_files:
        lines.append("  (none)")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app.tools.retention_check",
        description=(
            "Read-only §7 retention policy checker: reports stale plain files "
            "and oversized active files. Its only write is --json <path>."
        ),
    )
    parser.add_argument("--root", default=str(_DEFAULT_ROOT), help="Project root.")
    parser.add_argument(
        "--gzip-after-days", type=int, default=14, help="Age threshold in days."
    )
    parser.add_argument(
        "--json", dest="json_path", default=None, help="Write JSON report to this path."
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    root = Path(args.root)
    policy = RetentionPolicy(gzip_after_days=args.gzip_after_days)

    entries = scan_file_entries(root)
    report = build_violations_report(entries, today=date.today(), policy=policy)

    print(_render_report(report))

    if args.json_path:
        json_path = Path(args.json_path)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "stale_plain_files": [asdict(v) for v in report.stale_plain_files],
            "oversized_active_files": [
                asdict(v) for v in report.oversized_active_files
            ],
        }
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\n[retention_check] JSON report written: {json_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
