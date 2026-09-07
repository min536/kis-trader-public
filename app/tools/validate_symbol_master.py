"""Validate config/symbol_tags.yaml against local KIS symbol master snapshots.

This tool is offline-only. It reads local CSV snapshots and never fetches KIS
data or touches live scan settings.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

from app.core.symbol_master import (
    DEFAULT_SNAPSHOT_DIR,
    DEFAULT_TAGS_PATH,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    SymbolMasterFinding,
    SymbolMasterValidationResult,
    validate_symbol_master,
)

_SEVERITY_ORDER = (SEVERITY_ERROR, SEVERITY_WARNING, SEVERITY_INFO)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Offline KIS symbol master validator",
    )
    parser.add_argument(
        "--tags-path",
        default=str(DEFAULT_TAGS_PATH),
        help="Path to symbol_tags.yaml",
    )
    parser.add_argument(
        "--snapshot-dir",
        default=str(DEFAULT_SNAPSHOT_DIR),
        help="Path to local KIS stocks_info CSV snapshot directory",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of console report",
    )
    parser.add_argument(
        "--include-coverage-gaps",
        action="store_true",
        help="Report master symbols that are not present in symbol_tags",
    )
    parser.add_argument(
        "--max-details",
        type=int,
        default=20,
        help="Maximum findings to print per severity/category group",
    )
    return parser


def exit_code_for_result(result: SymbolMasterValidationResult) -> int:
    if not result.can_validate:
        return 2
    if result.has_validation_errors:
        return 1
    return 0


def _findings_by_group(
    findings: tuple[SymbolMasterFinding, ...],
) -> dict[tuple[str, str], list[SymbolMasterFinding]]:
    grouped: dict[tuple[str, str], list[SymbolMasterFinding]] = defaultdict(list)
    for finding in findings:
        grouped[(finding.severity, finding.category)].append(finding)
    return grouped


def _format_finding(finding: SymbolMasterFinding) -> str:
    parts = []
    if finding.symbol:
        parts.append(finding.symbol)
    parts.append(finding.message)
    if finding.expected is not None:
        parts.append(f"expected={finding.expected}")
    if finding.actual is not None:
        parts.append(f"actual={finding.actual}")
    return " | ".join(parts)


def print_console_report(
    result: SymbolMasterValidationResult,
    *,
    max_details: int,
) -> None:
    print("=" * 60)
    print("  Symbol Master Validation Report")
    print(f"  snapshot: {result.snapshot_dir}")
    print(f"  tags: {result.tags_path} ({result.tag_symbol_count} symbols)")
    print(
        "  master: "
        f"kospi/kosdaq={result.master_symbol_count} "
        f"konex={result.konex_symbol_count}"
    )
    print("=" * 60)
    print()

    if not result.findings:
        print("  결과: 정상")
        print("=" * 60)
        return

    grouped = _findings_by_group(result.findings)
    for severity in _SEVERITY_ORDER:
        categories = sorted(
            category
            for sev, category in grouped
            if sev == severity
        )
        for category in categories:
            findings = grouped[(severity, category)]
            print(f"[{severity.upper()}] {category}: {len(findings)}")
            visible = findings[: max(0, max_details)]
            if not visible:
                print("  (details hidden by --max-details)")
            for finding in visible:
                print(f"  - {_format_finding(finding)}")
            hidden = len(findings) - len(visible)
            if hidden > 0:
                print(f"  ... {hidden} more not shown")
            print()

    print("=" * 60)
    print(
        "  결과: "
        f"{result.error_count} error, "
        f"{result.warning_count} warnings, "
        f"{result.info_count} info"
    )
    print(f"  exit code: {exit_code_for_result(result)}")
    print("=" * 60)


def _result_to_json(result: SymbolMasterValidationResult) -> str:
    payload = {
        "tags_path": str(result.tags_path),
        "snapshot_dir": str(result.snapshot_dir),
        "can_validate": result.can_validate,
        "tag_symbol_count": result.tag_symbol_count,
        "master_symbol_count": result.master_symbol_count,
        "konex_symbol_count": result.konex_symbol_count,
        "error_count": result.error_count,
        "warning_count": result.warning_count,
        "info_count": result.info_count,
        "exit_code": exit_code_for_result(result),
        "findings": [asdict(finding) for finding in result.findings],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    result = validate_symbol_master(
        tags_path=Path(args.tags_path),
        snapshot_dir=Path(args.snapshot_dir),
        include_coverage_gaps=bool(args.include_coverage_gaps),
    )
    if args.json:
        print(_result_to_json(result))
    else:
        print_console_report(result, max_details=max(0, int(args.max_details)))
    return exit_code_for_result(result)


if __name__ == "__main__":
    sys.exit(main())
