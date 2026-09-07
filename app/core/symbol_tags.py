"""Symbol tag registry – loads and queries config/symbol_tags.yaml."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "symbol_tags.yaml"

KNOWN_NAMESPACES: frozenset[str] = frozenset(
    {
        "market",
        "asset",
        "sector",
        "theme",
        "cap",
        "liq",
        "vol",
        "price",
        "universe",
        "fit",
        "risk",
    }
)

RECOMMENDED_VALUES: dict[str, list[str]] = {
    "asset": [
        "stock",
        "etf",
    ],
    "market": [
        "kospi",
        "kosdaq",
        "konex",
        "etn",
    ],
    "sector": [
        "semiconductor",
        "battery",
        "bio",
        "internet",
        "game",
        "auto",
        "shipbuilding",
        "steel",
        "chemical",
        "energy",
        "finance",
        "insurance",
        "securities",
        "construction",
        "retail",
        "food",
        "cosmetic",
        "entertainment",
        "defense",
        "telecom",
        "holding",
        "etc",
    ],
    "theme": [
        "ai",
        "hbm",
        "robot",
        "secondary_battery",
        "ev",
        "bio_cdmo",
        "obesity",
        "cosmetic_export",
        "defense_export",
        "shipbuilding_cycle",
        "nuclear",
        "hydrogen",
        "data_center",
        "webtoon",
        "entertainment_ip",
        "china_consumption",
        "low_pbr",
        "dividend",
        "policy",
    ],
    "cap": [
        "mega",
        "large",
        "mid",
        "small",
        "micro",
    ],
    "liq": [
        "very_high",
        "high",
        "mid",
        "low",
        "very_low",
    ],
    "vol": [
        "very_high",
        "high",
        "mid",
        "low",
    ],
    "price": [
        "penny",
        "low",
        "mid",
        "high",
        "very_high",
    ],
    "universe": [
        "core",
        "extended",
        "experimental",
        "excluded",
    ],
    "fit": [
        "momentum",
        "mean_reversion",
        "breakout",
        "trend_following",
        "volume_surge",
        "gap",
        "news_sensitive",
        "defensive",
    ],
    "risk": [
        "earnings_sensitive",
        "news_sensitive",
        "theme_spike",
        "low_liquidity",
        "wide_spread",
        "halt_risk",
        "leveraged",
        "derivative",
        "single_product",
        "china_exposure",
        "fx_sensitive",
        "commodity_sensitive",
        "cycle_sensitive",
    ],
}


@dataclass(frozen=True)
class SymbolEntry:
    code: str
    name: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class ValidationResult:
    unknown_namespace_tags: tuple[str, ...]
    invalid_tags: tuple[str, ...]
    unknown_value_tags: tuple[str, ...]
    duplicates_removed: int


@dataclass
class SymbolTagRegistry:
    _entries: dict[str, SymbolEntry] = field(default_factory=dict)
    _validation: ValidationResult = field(
        default_factory=lambda: ValidationResult((), (), (), 0)
    )

    @property
    def symbols(self) -> dict[str, SymbolEntry]:
        return dict(self._entries)

    @property
    def validation(self) -> ValidationResult:
        return self._validation

    def get(self, symbol: str) -> SymbolEntry | None:
        return self._entries.get(symbol)

    def get_name(self, symbol: str) -> str | None:
        entry = self._entries.get(symbol)
        return entry.name if entry else None

    def get_tags(self, symbol: str) -> tuple[str, ...]:
        entry = self._entries.get(symbol)
        return entry.tags if entry else ()

    def has_tag(self, symbol: str, tag: str) -> bool:
        return tag in self.get_tags(symbol)

    def get_tags_by_namespace(self, symbol: str, namespace: str) -> tuple[str, ...]:
        return tuple(
            t for t in self.get_tags(symbol) if t.startswith(f"{namespace}:")
        )

    def symbols_with_tag(self, tag: str) -> tuple[str, ...]:
        return tuple(
            code for code, entry in self._entries.items() if tag in entry.tags
        )

    def symbols_with_namespace_value(
        self, namespace: str, value: str
    ) -> tuple[str, ...]:
        full_tag = f"{namespace}:{value}"
        return self.symbols_with_tag(full_tag)

    def all_codes(self) -> tuple[str, ...]:
        return tuple(self._entries.keys())

    def __len__(self) -> int:
        return len(self._entries)


def _parse_tag(raw: str) -> tuple[str | None, str | None, str]:
    raw = raw.strip()
    if not raw:
        return None, None, raw
    if ":" not in raw:
        return None, None, raw
    ns, _, val = raw.partition(":")
    return ns, val, raw


def _validate_and_dedup(
    raw_tags: list[str],
) -> tuple[list[str], list[str], list[str], list[str], int]:
    seen: set[str] = set()
    clean: list[str] = []
    invalid: list[str] = []
    unknown_ns: list[str] = []
    unknown_val: list[str] = []
    dup_count = 0

    for raw in raw_tags:
        ns, val, full = _parse_tag(raw)
        if ns is None or val is None or not val:
            invalid.append(full)
            continue
        if full in seen:
            dup_count += 1
            continue
        seen.add(full)
        if ns not in KNOWN_NAMESPACES:
            unknown_ns.append(full)
        elif ns in RECOMMENDED_VALUES and val not in RECOMMENDED_VALUES[ns]:
            unknown_val.append(full)
        clean.append(full)

    return clean, invalid, unknown_ns, unknown_val, dup_count


def load_symbol_tags(path: str | Path | None = None) -> SymbolTagRegistry:
    tag_path = Path(path) if path else _DEFAULT_PATH

    if not tag_path.exists():
        print(
            f"[symbol_tags] 태그 파일 없음, 빈 registry 사용: {tag_path}",
            file=sys.stderr,
        )
        return SymbolTagRegistry()

    try:
        raw = yaml.safe_load(tag_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        print(
            f"[symbol_tags] YAML 파싱 실패: {tag_path}: {exc}",
            file=sys.stderr,
        )
        return SymbolTagRegistry()

    if not isinstance(raw, dict) or "symbols" not in raw:
        print(
            f"[symbol_tags] 'symbols' 키가 없습니다: {tag_path}",
            file=sys.stderr,
        )
        return SymbolTagRegistry()

    entries: dict[str, SymbolEntry] = {}
    all_invalid: list[str] = []
    all_unknown_ns: list[str] = []
    all_unknown_val: list[str] = []
    total_dups = 0

    symbols_raw: dict[str, Any] = raw["symbols"]
    for code_raw, info in symbols_raw.items():
        code = str(code_raw).zfill(6)
        name = str(info.get("name", ""))
        raw_tags: list[str] = [str(t) for t in info.get("tags", [])]
        clean, inv, unk_ns, unk_val, dups = _validate_and_dedup(raw_tags)
        all_invalid.extend(inv)
        all_unknown_ns.extend(unk_ns)
        all_unknown_val.extend(unk_val)
        total_dups += dups
        entries[code] = SymbolEntry(code=code, name=name, tags=tuple(clean))

    validation = ValidationResult(
        unknown_namespace_tags=tuple(all_unknown_ns),
        invalid_tags=tuple(all_invalid),
        unknown_value_tags=tuple(all_unknown_val),
        duplicates_removed=total_dups,
    )

    if all_invalid:
        print(
            f"[symbol_tags] invalid 태그 {len(all_invalid)}개: {all_invalid}",
            file=sys.stderr,
        )
    if all_unknown_ns:
        print(
            f"[symbol_tags] unknown namespace 태그 {len(all_unknown_ns)}개: "
            f"{all_unknown_ns}",
            file=sys.stderr,
        )

    return SymbolTagRegistry(_entries=entries, _validation=validation)
