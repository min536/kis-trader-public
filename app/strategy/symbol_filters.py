"""Local-only symbol filter preview helpers.

This module classifies symbol tag metadata for review/reporting. It is not
connected to live scan selection or order execution.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from app.core.symbol_tags import SymbolEntry, SymbolTagRegistry

CATEGORY_INCLUDED = "included"
CATEGORY_EXCLUDED = "excluded"
CATEGORY_WARNING = "warning"
CATEGORY_REPORTING_ONLY = "reporting_only"
CATEGORY_MISSING_METADATA = "missing_metadata"

REASON_STOCK_CANDIDATE = "stock_candidate"
REASON_UNIVERSE_EXCLUDED = "universe_excluded"
REASON_ASSET_ETF_REPORTING_ONLY = "asset_etf_reporting_only"
REASON_RISK_LEVERAGED = "risk_leveraged"
REASON_RISK_DERIVATIVE = "risk_derivative"
REASON_MISSING_REGISTRY_ENTRY = "missing_registry_entry"
REASON_MISSING_ASSET_TAG = "missing_asset_tag"
REASON_MISSING_MARKET_TAG = "missing_market_tag"
REASON_MISSING_UNIVERSE_TAG = "missing_universe_tag"
REASON_MISSING_NAME = "missing_name"

REQUIRED_METADATA_REASONS = {
    REASON_MISSING_REGISTRY_ENTRY,
    REASON_MISSING_ASSET_TAG,
    REASON_MISSING_MARKET_TAG,
    REASON_MISSING_UNIVERSE_TAG,
    REASON_MISSING_NAME,
}

WARNING_REASONS = {
    REASON_RISK_LEVERAGED,
    REASON_RISK_DERIVATIVE,
}


@dataclass(frozen=True)
class SymbolFilterPreviewRecord:
    symbol: str
    name: str | None
    category: str
    reasons: tuple[str, ...]
    tags: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SymbolFilterPreviewResult:
    records: tuple[SymbolFilterPreviewRecord, ...]
    category_counts: dict[str, int] = field(default_factory=dict)
    reason_counts: dict[str, int] = field(default_factory=dict)


def normalize_symbol(value: object) -> str:
    return str(value).strip().zfill(6)


def _has_namespace(entry: SymbolEntry, namespace: str) -> bool:
    prefix = f"{namespace}:"
    return any(tag.startswith(prefix) for tag in entry.tags)


def _warning_messages(reasons: Iterable[str]) -> tuple[str, ...]:
    messages: list[str] = []
    reason_set = set(reasons)
    if REASON_RISK_LEVERAGED in reason_set:
        messages.append("leveraged instrument: preview only, not a stock BUY candidate")
    if REASON_RISK_DERIVATIVE in reason_set:
        messages.append("derivative-linked instrument: preview only, not a stock BUY candidate")
    return tuple(messages)


def preview_symbol_filter(
    registry: SymbolTagRegistry,
    symbol: str,
) -> SymbolFilterPreviewRecord:
    code = normalize_symbol(symbol)
    entry = registry.get(code)
    if entry is None:
        return SymbolFilterPreviewRecord(
            symbol=code,
            name=None,
            category=CATEGORY_MISSING_METADATA,
            reasons=(REASON_MISSING_REGISTRY_ENTRY,),
            tags=(),
        )

    reasons: list[str] = []
    tags = entry.tags
    tag_set = set(tags)
    asset_is_stock = "asset:stock" in tag_set
    asset_is_etf = "asset:etf" in tag_set

    if not entry.name.strip():
        reasons.append(REASON_MISSING_NAME)
    if not _has_namespace(entry, "asset"):
        reasons.append(REASON_MISSING_ASSET_TAG)
    if not _has_namespace(entry, "market"):
        reasons.append(REASON_MISSING_MARKET_TAG)
    if not _has_namespace(entry, "universe"):
        reasons.append(REASON_MISSING_UNIVERSE_TAG)
    if "universe:excluded" in tag_set:
        reasons.append(REASON_UNIVERSE_EXCLUDED)
    if asset_is_etf:
        reasons.append(REASON_ASSET_ETF_REPORTING_ONLY)
    if "risk:leveraged" in tag_set:
        reasons.append(REASON_RISK_LEVERAGED)
    if "risk:derivative" in tag_set:
        reasons.append(REASON_RISK_DERIVATIVE)
    if asset_is_stock and not reasons:
        reasons.append(REASON_STOCK_CANDIDATE)

    reason_set = set(reasons)
    if REASON_UNIVERSE_EXCLUDED in reason_set:
        category = CATEGORY_EXCLUDED
    elif reason_set & REQUIRED_METADATA_REASONS:
        category = CATEGORY_MISSING_METADATA
    elif REASON_ASSET_ETF_REPORTING_ONLY in reason_set:
        category = CATEGORY_REPORTING_ONLY
    elif reason_set & WARNING_REASONS:
        category = CATEGORY_WARNING
    else:
        category = CATEGORY_INCLUDED

    return SymbolFilterPreviewRecord(
        symbol=code,
        name=entry.name or None,
        category=category,
        reasons=tuple(reasons),
        tags=tags,
        warnings=_warning_messages(reasons),
    )


def preview_symbol_filters(
    registry: SymbolTagRegistry,
    symbols: Iterable[str] | None = None,
) -> SymbolFilterPreviewResult:
    codes = (
        tuple(normalize_symbol(symbol) for symbol in symbols)
        if symbols is not None
        else registry.all_codes()
    )
    records = tuple(preview_symbol_filter(registry, symbol) for symbol in codes)
    category_counts = Counter(record.category for record in records)
    reason_counts = Counter(reason for record in records for reason in record.reasons)
    return SymbolFilterPreviewResult(
        records=records,
        category_counts=dict(sorted(category_counts.items())),
        reason_counts=dict(sorted(reason_counts.items())),
    )
