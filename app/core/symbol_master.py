"""Offline validation of symbol metadata against local KIS master snapshots."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from app.core.file_read_limits import (
    LocalReadLimitError,
    read_csv_dicts_with_fieldnames_bounded,
)
from app.core.symbol_tags import SymbolEntry, load_symbol_tags

DEFAULT_SNAPSHOT_DIR = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "vendor"
    / "kis_stocks_info"
    / "latest"
)
DEFAULT_TAGS_PATH = Path(__file__).resolve().parents[2] / "config" / "symbol_tags.yaml"

REQUIRED_SNAPSHOT_FILES = ("kospi.csv", "kosdaq.csv")
OPTIONAL_SNAPSHOT_FILES = ("konex.csv",)
REQUIRED_COLUMNS = frozenset({"symbol", "name", "market"})
SNAPSHOT_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "symbol": ("symbol", "code"),
    "name": ("name",),
    "market": ("market", "exchange"),
}

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class SymbolMasterFinding:
    severity: str
    category: str
    symbol: str
    message: str
    expected: str | None = None
    actual: str | None = None


@dataclass(frozen=True)
class SymbolMasterRow:
    symbol: str
    name: str
    market: str
    asset: str | None = None
    sector: str | None = None
    is_etf: bool | None = None
    is_trading_halt: bool | None = None
    is_administrative_issue: bool | None = None


@dataclass(frozen=True)
class SymbolMasterSnapshot:
    snapshot_dir: Path
    rows_by_symbol: dict[str, SymbolMasterRow] = field(default_factory=dict)
    konex_rows_by_symbol: dict[str, SymbolMasterRow] = field(default_factory=dict)


@dataclass(frozen=True)
class SymbolMasterValidationResult:
    tags_path: Path
    snapshot_dir: Path
    can_validate: bool
    tag_symbol_count: int = 0
    master_symbol_count: int = 0
    konex_symbol_count: int = 0
    findings: tuple[SymbolMasterFinding, ...] = ()

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == SEVERITY_ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == SEVERITY_WARNING)

    @property
    def info_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == SEVERITY_INFO)

    @property
    def has_validation_errors(self) -> bool:
        return self.can_validate and self.error_count > 0


def normalize_symbol(value: object) -> str:
    return str(value).strip().zfill(6)


def normalize_name(value: object) -> str:
    return _WHITESPACE_RE.sub(" ", str(value).strip())


def normalize_market(value: object) -> str:
    raw = str(value).strip().lower()
    aliases = {
        "ks": "kospi",
        "kospi": "kospi",
        "유가": "kospi",
        "유가증권": "kospi",
        "유가증권시장": "kospi",
        "kq": "kosdaq",
        "kosdaq": "kosdaq",
        "코스닥": "kosdaq",
        "kn": "konex",
        "konex": "konex",
        "코넥스": "konex",
    }
    return aliases.get(raw, raw)


def normalize_asset(value: object) -> str | None:
    raw = str(value).strip().lower()
    if not raw:
        return None
    aliases = {
        "stock": "stock",
        "equity": "stock",
        "주식": "stock",
        "common": "stock",
        "etf": "etf",
        "etn": "etf",
        "etp": "etf",
    }
    return aliases.get(raw, raw)


def parse_optional_bool(value: object) -> bool | None:
    raw = str(value).strip().lower()
    if not raw:
        return None
    if raw in {"1", "true", "t", "yes", "y", "etf", "etp", "halt", "admin"}:
        return True
    if raw in {"0", "false", "f", "no", "n", "normal"}:
        return False
    return None


def _csv_path(snapshot_dir: Path, filename: str) -> Path:
    return snapshot_dir / filename


def _schema_error(path: Path, message: str) -> SymbolMasterFinding:
    return SymbolMasterFinding(
        severity=SEVERITY_ERROR,
        category="snapshot_schema_error",
        symbol="",
        message=f"{path}: {message}",
    )


def _resolve_snapshot_columns(fieldnames: set[str]) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for logical_name, aliases in SNAPSHOT_COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in fieldnames:
                resolved[logical_name] = alias
                break
    return resolved


def _load_snapshot_file(
    path: Path,
    *,
    default_market: str,
) -> tuple[dict[str, SymbolMasterRow], tuple[SymbolMasterFinding, ...]]:
    findings: list[SymbolMasterFinding] = []
    rows_by_symbol: dict[str, SymbolMasterRow] = {}

    try:
        raw_rows, fieldnames_raw = read_csv_dicts_with_fieldnames_bounded(
            path,
            encoding="utf-8-sig",
        )
        fieldnames = set(fieldnames_raw)
        columns = _resolve_snapshot_columns(fieldnames)
        missing_columns = REQUIRED_COLUMNS - set(columns)
        if missing_columns:
            findings.append(
                _schema_error(
                    path,
                    "missing required columns: "
                    + ", ".join(sorted(missing_columns)),
                )
            )
            return {}, tuple(findings)

        for line_no, raw_row in enumerate(raw_rows, start=2):
            symbol = normalize_symbol(raw_row.get(columns["symbol"], ""))
            if symbol and len(symbol) == 6 and not symbol.isdigit():
                continue
            if not symbol or len(symbol) != 6:
                findings.append(
                    _schema_error(path, f"line {line_no}: invalid symbol {symbol!r}")
                )
                continue
            market = normalize_market(raw_row.get(columns["market"], "") or default_market)
            row = SymbolMasterRow(
                symbol=symbol,
                name=normalize_name(raw_row.get(columns["name"], "")),
                market=market,
                asset=normalize_asset(raw_row.get("asset", "")),
                sector=normalize_name(raw_row.get("sector", "")) or None,
                is_etf=parse_optional_bool(raw_row.get("is_etf", "")),
                is_trading_halt=parse_optional_bool(
                    raw_row.get("is_trading_halt", "")
                ),
                is_administrative_issue=parse_optional_bool(
                    raw_row.get("is_administrative_issue", "")
                ),
            )
            rows_by_symbol[symbol] = row
    except (LocalReadLimitError, OSError) as exc:
        findings.append(_schema_error(path, f"cannot read CSV: {exc}"))

    return rows_by_symbol, tuple(findings)


def load_symbol_master_snapshot(
    snapshot_dir: str | Path,
) -> tuple[SymbolMasterSnapshot | None, tuple[SymbolMasterFinding, ...]]:
    root = Path(snapshot_dir)
    findings: list[SymbolMasterFinding] = []

    if not root.exists() or not root.is_dir():
        return None, (
            SymbolMasterFinding(
                severity=SEVERITY_ERROR,
                category="snapshot_missing",
                symbol="",
                message=f"snapshot directory not found: {root}",
            ),
        )

    for filename in REQUIRED_SNAPSHOT_FILES:
        path = _csv_path(root, filename)
        if not path.exists():
            findings.append(
                SymbolMasterFinding(
                    severity=SEVERITY_ERROR,
                    category="snapshot_missing",
                    symbol="",
                    message=f"required snapshot file not found: {path}",
                )
            )

    if findings:
        return None, tuple(findings)

    rows_by_symbol: dict[str, SymbolMasterRow] = {}
    konex_rows_by_symbol: dict[str, SymbolMasterRow] = {}
    for filename in REQUIRED_SNAPSHOT_FILES:
        market = filename.removesuffix(".csv")
        rows, file_findings = _load_snapshot_file(
            _csv_path(root, filename),
            default_market=market,
        )
        findings.extend(file_findings)
        for symbol, row in rows.items():
            if symbol in rows_by_symbol:
                findings.append(
                    SymbolMasterFinding(
                        severity=SEVERITY_WARNING,
                        category="duplicate_symbol",
                        symbol=symbol,
                        message=(
                            f"{symbol} appears in multiple KOSPI/KOSDAQ snapshots; "
                            f"using row from {filename}"
                        ),
                    )
                )
            rows_by_symbol[symbol] = row

    for filename in OPTIONAL_SNAPSHOT_FILES:
        path = _csv_path(root, filename)
        if not path.exists():
            continue
        rows, file_findings = _load_snapshot_file(path, default_market="konex")
        findings.extend(file_findings)
        konex_rows_by_symbol.update(rows)

    if any(f.category == "snapshot_schema_error" for f in findings):
        return None, tuple(findings)

    return (
        SymbolMasterSnapshot(
            snapshot_dir=root,
            rows_by_symbol=rows_by_symbol,
            konex_rows_by_symbol=konex_rows_by_symbol,
        ),
        tuple(findings),
    )


def _tags_by_namespace(entry: SymbolEntry, namespace: str) -> tuple[str, ...]:
    prefix = f"{namespace}:"
    return tuple(tag.removeprefix(prefix) for tag in entry.tags if tag.startswith(prefix))


def _single_tag_value(entry: SymbolEntry, namespace: str) -> str | None:
    values = _tags_by_namespace(entry, namespace)
    return values[0] if values else None


def _master_asset(row: SymbolMasterRow) -> str | None:
    if row.asset:
        return row.asset
    if row.is_etf is True:
        return "etf"
    if row.is_etf is False:
        return "stock"
    return None


def _validate_entry_against_master(
    entry: SymbolEntry,
    row: SymbolMasterRow,
) -> Iterable[SymbolMasterFinding]:
    tags_name = normalize_name(entry.name)
    master_name = normalize_name(row.name)
    if tags_name != master_name:
        yield SymbolMasterFinding(
            severity=SEVERITY_WARNING,
            category="name_mismatch",
            symbol=entry.code,
            message=f"{entry.code} name differs from KIS master",
            expected=master_name,
            actual=tags_name,
        )

    tags_market = normalize_market(_single_tag_value(entry, "market") or "")
    if tags_market and row.market and tags_market != row.market:
        yield SymbolMasterFinding(
            severity=SEVERITY_ERROR,
            category="market_mismatch",
            symbol=entry.code,
            message=f"{entry.code} market tag differs from KIS master",
            expected=row.market,
            actual=tags_market,
        )

    tags_asset = normalize_asset(_single_tag_value(entry, "asset") or "")
    master_asset = _master_asset(row)
    if tags_asset and master_asset and tags_asset != master_asset:
        yield SymbolMasterFinding(
            severity=SEVERITY_WARNING,
            category="asset_mismatch",
            symbol=entry.code,
            message=f"{entry.code} asset tag differs from KIS master",
            expected=master_asset,
            actual=tags_asset,
        )

    if row.is_trading_halt is True:
        yield SymbolMasterFinding(
            severity=SEVERITY_WARNING,
            category="trading_halt",
            symbol=entry.code,
            message=f"{entry.code} is marked as trading halt in KIS master",
            actual="true",
        )

    if row.is_administrative_issue is True:
        yield SymbolMasterFinding(
            severity=SEVERITY_WARNING,
            category="administrative_issue",
            symbol=entry.code,
            message=f"{entry.code} is marked as administrative issue in KIS master",
            actual="true",
        )

    if row.sector:
        tag_sector = _single_tag_value(entry, "sector")
        if tag_sector == "etc":
            yield SymbolMasterFinding(
                severity=SEVERITY_INFO,
                category="sector_suggestion",
                symbol=entry.code,
                message=f"{entry.code} has sector:etc; KIS master sector may help review",
                expected=row.sector,
                actual="sector:etc",
            )


def validate_symbol_master(
    *,
    tags_path: str | Path = DEFAULT_TAGS_PATH,
    snapshot_dir: str | Path = DEFAULT_SNAPSHOT_DIR,
    include_coverage_gaps: bool = False,
) -> SymbolMasterValidationResult:
    tags = load_symbol_tags(tags_path)
    tags_path_obj = Path(tags_path)
    snapshot_dir_obj = Path(snapshot_dir)

    snapshot, snapshot_findings = load_symbol_master_snapshot(snapshot_dir_obj)
    if snapshot is None:
        return SymbolMasterValidationResult(
            tags_path=tags_path_obj,
            snapshot_dir=snapshot_dir_obj,
            can_validate=False,
            tag_symbol_count=len(tags),
            findings=tuple(snapshot_findings),
        )

    findings: list[SymbolMasterFinding] = list(snapshot_findings)
    for code, entry in tags.symbols.items():
        row = snapshot.rows_by_symbol.get(code)
        if row is None:
            if code in snapshot.konex_rows_by_symbol:
                findings.append(
                    SymbolMasterFinding(
                        severity=SEVERITY_WARNING,
                        category="konex_only",
                        symbol=code,
                        message=f"{code} is present only in KONEX snapshot",
                        expected="kospi/kosdaq",
                        actual="konex",
                    )
                )
            else:
                findings.append(
                    SymbolMasterFinding(
                        severity=SEVERITY_ERROR,
                        category="missing_from_master",
                        symbol=code,
                        message=f"{code} is in symbol_tags but not in KIS master snapshot",
                    )
                )
            continue

        findings.extend(_validate_entry_against_master(entry, row))

    if include_coverage_gaps:
        tagged_codes = set(tags.all_codes())
        for code, row in sorted(snapshot.rows_by_symbol.items()):
            if code not in tagged_codes:
                findings.append(
                    SymbolMasterFinding(
                        severity=SEVERITY_INFO,
                        category="coverage_gap",
                        symbol=code,
                        message=f"{code} exists in KIS master snapshot but not symbol_tags",
                        actual=row.name,
                    )
                )

    return SymbolMasterValidationResult(
        tags_path=tags_path_obj,
        snapshot_dir=snapshot_dir_obj,
        can_validate=True,
        tag_symbol_count=len(tags),
        master_symbol_count=len(snapshot.rows_by_symbol),
        konex_symbol_count=len(snapshot.konex_rows_by_symbol),
        findings=tuple(findings),
    )
