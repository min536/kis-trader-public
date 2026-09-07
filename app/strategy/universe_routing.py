"""Tag-driven universe routing helper (C-4 multi-account Phase 3).

Pure, read-only conversion of ``SymbolTagRegistry`` tag queries into a
``SCAN_SYMBOLS``-formatted string for per-account universe routing
(``docs/multi_account_parallelization_plan.md`` §8). No KIS API calls and
no runtime wiring — operators paste the generated string into a per-account
env value. Keeping it side-effect free is why it lives apart from the
order/risk logic even though it sits under ``app/strategy/``.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable

from app.core.symbol_tags import SymbolTagRegistry, load_symbol_tags


def _normalize_tag(tag: str) -> str:
    """Normalize a ``namespace:value`` tag to lowercase, validating shape.

    Registry tags are stored lowercase (per ``config/symbol_tags.yaml``
    convention), so user input is lowercased to match. A tag without a
    non-empty namespace and value is a usage error, not an empty result.
    """
    raw = (tag or "").strip().lower()
    namespace, separator, value = raw.partition(":")
    if not separator or not namespace.strip() or not value.strip():
        raise ValueError(f"태그는 'namespace:value' 형식이어야 합니다: {tag!r}")
    return f"{namespace.strip()}:{value.strip()}"


def _codes_for_tag(registry: SymbolTagRegistry, tag: str) -> set[str]:
    return set(registry.symbols_with_tag(_normalize_tag(tag)))


def symbols_by_tags_all(
    registry: SymbolTagRegistry, tags: Iterable[str]
) -> tuple[str, ...]:
    """Symbols matching ALL given tags (intersection), sorted.

    Empty ``tags`` returns an empty tuple — deliberately conservative so a
    misconfigured route never silently selects the entire universe.
    """
    sets = [_codes_for_tag(registry, tag) for tag in tags]
    if not sets:
        return ()
    return tuple(sorted(set.intersection(*sets)))


def symbols_by_tags_any(
    registry: SymbolTagRegistry, tags: Iterable[str]
) -> tuple[str, ...]:
    """Symbols matching ANY given tag (union), sorted."""
    result: set[str] = set()
    for tag in tags:
        result |= _codes_for_tag(registry, tag)
    return tuple(sorted(result))


def exclude_tags(
    registry: SymbolTagRegistry,
    symbols: Iterable[str],
    excluded_tags: Iterable[str],
) -> tuple[str, ...]:
    """Drop any symbol carrying one of ``excluded_tags`` from ``symbols``."""
    excluded: set[str] = set()
    for tag in excluded_tags:
        excluded |= _codes_for_tag(registry, tag)
    return tuple(sorted(set(symbols) - excluded))


def to_scan_symbols_string(symbols: Iterable[str]) -> str:
    """Format symbol codes as a deterministic ``SCAN_SYMBOLS`` CSV string.

    Dedupes, drops blanks, and sorts so the output is stable across runs.
    Codes are emitted verbatim (registry codes are already canonical 6-digit).
    Round-trips through ``split_symbol_items`` for non-empty results; an empty
    result yields ``""`` (and ``split_symbol_items("")`` is ``("",)``), so
    callers use the ``scan.split(",") if scan else []`` guard (see ``main``).
    """
    cleaned = {symbol.strip() for symbol in symbols if symbol and symbol.strip()}
    return ",".join(sorted(cleaned))


def format_universe_route(
    registry: SymbolTagRegistry,
    tags: Iterable[str],
    *,
    mode: str = "AND",
    exclude: Iterable[str] | None = None,
) -> str:
    """Resolve a tag query into a ``SCAN_SYMBOLS`` CSV string.

    ``mode`` is ``AND`` (intersection) or ``OR`` (union). ``exclude`` drops
    symbols carrying any excluded tag after the base query resolves.
    """
    mode_normalized = (mode or "AND").upper()
    if mode_normalized == "AND":
        base = symbols_by_tags_all(registry, tags)
    elif mode_normalized == "OR":
        base = symbols_by_tags_any(registry, tags)
    else:
        raise ValueError(f"mode 는 AND 또는 OR 여야 합니다: {mode!r}")
    if exclude:
        base = exclude_tags(registry, base, exclude)
    return to_scan_symbols_string(base)


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.strategy.universe_routing",
        description=(
            "태그 쿼리를 SCAN_SYMBOLS 문자열로 변환합니다 "
            "(multi-account 유니버스 라우팅, read-only)."
        ),
    )
    parser.add_argument(
        "--tags",
        required=True,
        help="콤마 구분 태그 (예: universe:core,asset:stock)",
    )
    parser.add_argument(
        "--mode",
        default="AND",
        choices=["AND", "OR", "and", "or"],
        help="다중 태그 결합 방식: AND(교집합, 기본) 또는 OR(합집합)",
    )
    parser.add_argument(
        "--exclude",
        default="",
        help="콤마 구분 제외 태그 (예: risk:leveraged,risk:derivative)",
    )
    parser.add_argument(
        "--tags-path",
        default=None,
        help="symbol_tags.yaml 경로 (기본: config/symbol_tags.yaml)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="JSON으로 출력 (태그/개수/심볼 포함)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    registry = load_symbol_tags(args.tags_path)
    tags = _split_csv(args.tags)
    exclude = _split_csv(args.exclude)
    mode = args.mode.upper()

    scan_symbols = format_universe_route(
        registry, tags, mode=mode, exclude=exclude
    )
    symbols = scan_symbols.split(",") if scan_symbols else []

    if args.as_json:
        payload = {
            "query_tags": tags,
            "mode": mode,
            "exclude_tags": exclude,
            "match_count": len(symbols),
            "scan_symbols_string": scan_symbols,
            "symbols": symbols,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f'SCAN_SYMBOLS="{scan_symbols}"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
