"""Validate and summarise config/symbol_tags.yaml.

Usage:
    python -m app.tools.validate_symbol_tags
    python -m app.tools.validate_symbol_tags --path config/symbol_tags.yaml
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

from app.core.symbol_tags import (
    KNOWN_NAMESPACES,
    load_symbol_tags,
)


def _load_scan_universe() -> set[str] | None:
    scan = os.getenv("SCAN_SYMBOLS", "").strip()
    if not scan:
        buy = os.getenv("BUY_TARGET_SYMBOLS", "").strip()
        if buy:
            scan = buy
    if not scan:
        fallback = os.getenv("KIS_TARGET_SYMBOL", "").strip()
        if fallback:
            scan = fallback
    if not scan:
        try:
            from app.scanner.symbol_names import SYMBOL_NAME_MAP

            return set(SYMBOL_NAME_MAP.keys())
        except ImportError:
            return None
    return {s.strip().zfill(6) for s in scan.split(",") if s.strip()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate symbol_tags.yaml",
    )
    parser.add_argument(
        "--path",
        default=None,
        help="Path to symbol_tags.yaml (default: config/symbol_tags.yaml)",
    )
    args = parser.parse_args(argv)

    registry = load_symbol_tags(args.path)
    val = registry.validation

    ns_counter: Counter[str] = Counter()
    for entry in registry.symbols.values():
        for tag in entry.tags:
            ns, _, _ = tag.partition(":")
            ns_counter[ns] += 1

    print("=" * 60)
    print("  Symbol Tag Validation Report")
    print("=" * 60)
    print(f"  총 태깅 종목 수: {len(registry)}")
    print()

    print("  [namespace별 태그 수]")
    for ns in sorted(KNOWN_NAMESPACES):
        count = ns_counter.pop(ns, 0)
        print(f"    {ns:12s}: {count}")
    unknown_ns_count = len(ns_counter)
    if unknown_ns_count:
        print(f"    {'(unknown)':12s}: {sum(ns_counter.values())}  ← {dict(ns_counter)}")
    print()

    print(f"  unknown namespace 태그: {len(val.unknown_namespace_tags)}")
    if val.unknown_namespace_tags:
        for t in val.unknown_namespace_tags:
            print(f"    ⚠  {t}")

    print(f"  unknown value 태그 (경고): {len(val.unknown_value_tags)}")
    if val.unknown_value_tags:
        for t in val.unknown_value_tags:
            print(f"    ⚠  {t}")

    print(f"  invalid 태그: {len(val.invalid_tags)}")
    if val.invalid_tags:
        for t in val.invalid_tags:
            print(f"    ✗  {t}")

    print(f"  중복 제거: {val.duplicates_removed}건")
    print()

    scan_universe = _load_scan_universe()
    tagged_codes = set(registry.all_codes())
    if scan_universe is not None:
        not_tagged = scan_universe - tagged_codes
        not_in_universe = tagged_codes - scan_universe
        print(f"  조회 universe 종목 수: {len(scan_universe)}")
        print(f"  universe에 있으나 태그 없음: {len(not_tagged)}")
        if not_tagged:
            for s in sorted(not_tagged):
                print(f"    - {s}")
        print(f"  태그에 있으나 universe에 없음: {len(not_in_universe)}")
        if not_in_universe:
            for s in sorted(not_in_universe):
                print(f"    - {s}")
    else:
        print("  조회 universe: 로드 실패 (SCAN_SYMBOLS 미설정, symbol_names 미발견)")

    print()
    has_issues = bool(val.invalid_tags or val.unknown_namespace_tags)
    if has_issues:
        print("  결과: ⚠ 경고 있음")
    else:
        print("  결과: ✓ 정상")
    print("=" * 60)

    return 1 if val.invalid_tags else 0


if __name__ == "__main__":
    sys.exit(main())
