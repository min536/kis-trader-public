"""Preview tag-based symbol filter categories.

This tool is local-only. It reads symbol tag metadata and does not read live
settings, call broker/KIS APIs, or change scan/order behavior.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from app.core.symbol_tags import load_symbol_tags
from app.strategy.symbol_filters import (
    SymbolFilterPreviewResult,
    preview_symbol_filters,
)

DEFAULT_TAGS_PATH = Path(__file__).resolve().parents[2] / "config" / "symbol_tags.yaml"
_CATEGORY_ORDER = (
    "included",
    "excluded",
    "reporting_only",
    "warning",
    "missing_metadata",
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preview local tag-based symbol filter categories",
    )
    parser.add_argument(
        "--tags-path",
        default=str(DEFAULT_TAGS_PATH),
        help="Path to symbol_tags.yaml",
    )
    parser.add_argument(
        "--symbols",
        default="",
        help="Comma-separated symbols to preview. Defaults to all tagged symbols.",
    )
    parser.add_argument(
        "--symbols-file",
        default="",
        help="Optional file containing symbols separated by comma, whitespace, or newline.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of console report.",
    )
    parser.add_argument(
        "--max-details",
        type=int,
        default=20,
        help="Maximum records to print per category in console output.",
    )
    return parser


def _split_symbols(raw: str) -> tuple[str, ...]:
    normalized = raw.replace(",", " ")
    return tuple(part.strip().zfill(6) for part in normalized.split() if part.strip())


def _load_explicit_symbols(symbols: str, symbols_file: str) -> tuple[str, ...] | None:
    merged: list[str] = []
    if symbols.strip():
        merged.extend(_split_symbols(symbols))
    if symbols_file.strip():
        path = Path(symbols_file)
        text = path.read_text(encoding="utf-8")
        merged.extend(_split_symbols(text))
    if not merged:
        return None
    return tuple(dict.fromkeys(merged))


def _result_to_json(result: SymbolFilterPreviewResult) -> str:
    payload = {
        "category_counts": result.category_counts,
        "reason_counts": result.reason_counts,
        "records": [asdict(record) for record in result.records],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _format_record(record) -> str:
    name = record.name or "-"
    reasons = ",".join(record.reasons) if record.reasons else "-"
    warnings = f" | warnings={'; '.join(record.warnings)}" if record.warnings else ""
    return f"{record.symbol} {name} | reasons={reasons}{warnings}"


def print_console_report(
    result: SymbolFilterPreviewResult,
    *,
    max_details: int,
) -> None:
    print("=" * 60)
    print("  Symbol Filter Preview Report")
    print("=" * 60)
    print(f"  total: {len(result.records)}")
    for category in _CATEGORY_ORDER:
        print(f"  {category:16s}: {result.category_counts.get(category, 0)}")
    print()

    max_visible = max(0, max_details)
    records_by_category = {
        category: [record for record in result.records if record.category == category]
        for category in _CATEGORY_ORDER
    }
    for category in _CATEGORY_ORDER:
        records = records_by_category[category]
        if not records:
            continue
        print(f"[{category}] {len(records)}")
        visible = records[:max_visible]
        if not visible:
            print("  (details hidden by --max-details)")
        for record in visible:
            print(f"  - {_format_record(record)}")
        hidden = len(records) - len(visible)
        if hidden > 0:
            print(f"  ... {hidden} more not shown")
        print()

    if result.reason_counts:
        print("[reason counts]")
        for reason, count in result.reason_counts.items():
            print(f"  {reason}: {count}")
    print("=" * 60)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        explicit_symbols = _load_explicit_symbols(args.symbols, args.symbols_file)
    except OSError as exc:
        print(f"cannot read symbols file: {exc}", file=sys.stderr)
        return 1

    registry = load_symbol_tags(args.tags_path)
    result = preview_symbol_filters(registry, explicit_symbols)
    if args.json:
        print(_result_to_json(result))
    else:
        print_console_report(result, max_details=int(args.max_details))
    return 0


if __name__ == "__main__":
    sys.exit(main())
