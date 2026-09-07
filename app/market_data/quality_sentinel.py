"""Pure, read-only market-data quality sentinel (W5).

Offline checks over per-symbol :class:`~app.market_data.schema.MarketSnapshot`
records plus live-snapshot freshness, producing a JSON-serializable report, an
artifact writer, and alert lines.

Contract (v1):
- **Fail-safe / alert-only.** It reports; it never blocks trading, clamps, or
  raises on a malformed/hostile record (input is a sequence of records — each
  bad record is skipped or degraded, not thrown).
- **Per-symbol checks:** missing/zero price (current/open/low/high — ``high_price``
  is the one field with a ``0`` "absent" default, flagged so its missing upper
  bound is visible, not silently skipped), non-positive price, intraday range
  (one-sided lower/upper bounds fire independently), and implausible daily move
  (``abs(prev_day_change_pct) > max_daily_move_pct``; default 30.0 = the KRX
  regular-session price limit).
- **Freshness:** live-snapshot age vs an *interval-derived* threshold
  ``max(refresh_interval*2, 360)`` — mirrors ``live_snapshot_worker_health`` so it
  stays valid across the HT budget profile (4→15 r/s) instead of a fixed constant.
- **Not in v1 (deferred):** bid/ask spread checks, cross-symbol consistency,
  per-symbol staleness, and *coverage* alerting (zero symbols scanned is reported
  as ``symbol_count=0`` but is the caller/worker-health concern wired in W6, not a
  per-record quality defect). ``status`` is ``"ok"``/``"warn"`` only.

Leaf-invariant: this module must not import ``app.research`` — the offline ingest
quality report is a deliberately separate concept (no code sharing).
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from app.market_data.live_snapshot_collect import _parse_runtime_timestamp

PROJECT_ROOT = Path(__file__).resolve().parents[2]
QUALITY_ARTIFACT_PATH = PROJECT_ROOT / "data" / "runtime" / "market_data_quality.json"

CATEGORY_MISSING_FIELD = "missing_field"
CATEGORY_NON_POSITIVE_PRICE = "non_positive_price"
CATEGORY_PRICE_OUT_OF_RANGE = "price_out_of_range"
CATEGORY_IMPLAUSIBLE_MOVE = "implausible_daily_move"


def _field(obj, name):
    # A hostile record's .get or a property may raise; degrade to None rather
    # than let it escape the scan (fail-safe contract).
    try:
        return obj.get(name) if isinstance(obj, Mapping) else getattr(obj, name, None)
    except Exception:
        return None


def _coerce_price(value):
    """Coerce a price field to a non-negative-capable int, or None if unusable.

    Tolerates numeric strings ("70000", "70000.5") and rejects None / junk /
    non-finite (inf, nan) / hostile ``__float__`` by returning None — never
    raises (fail-safe).
    """
    try:
        return int(float(value))
    except Exception:
        return None


@dataclass(frozen=True)
class SymbolQualityIssue:
    symbol: str
    category: str
    detail: str

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "category": self.category, "detail": self.detail}


@dataclass(frozen=True)
class FreshnessStatus:
    updated_at: str | None
    age_seconds: float | None
    stale_threshold_seconds: int
    stale: bool
    reason: str

    def to_dict(self) -> dict:
        return {
            "updated_at": self.updated_at,
            "age_seconds": self.age_seconds,
            "stale_threshold_seconds": self.stale_threshold_seconds,
            "stale": self.stale,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class MarketDataQualityReport:
    checked_at: str | None
    symbol_count: int
    ok_count: int
    issue_count: int
    freshness: FreshnessStatus
    issues: tuple
    status: str

    def to_dict(self) -> dict:
        return {
            "checked_at": self.checked_at,
            "status": self.status,
            "symbol_count": self.symbol_count,
            "ok_count": self.ok_count,
            "issue_count": self.issue_count,
            "freshness": self.freshness.to_dict(),
            "issues": [issue.to_dict() for issue in self.issues],
        }


def evaluate_market_data_quality(
    snapshots,
    *,
    now_epoch,
    refresh_interval_seconds,
    snapshot_updated_at=None,
    checked_at=None,
    max_daily_move_pct=30.0,
):
    issues: list[SymbolQualityIssue] = []
    flagged_rows = 0
    symbol_count = 0
    for obj in snapshots or ():
        raw = _field(obj, "symbol")
        symbol = str(raw).strip() if raw is not None else ""
        if not symbol:
            continue
        symbol_count += 1
        prices = {
            field_name: _coerce_price(_field(obj, field_name))
            for field_name in ("current_price", "open_price", "low_price", "high_price")
        }

        symbol_issues: list[SymbolQualityIssue] = []
        for field_name in ("current_price", "open_price", "low_price"):
            value = prices[field_name]
            if value is None:
                symbol_issues.append(
                    SymbolQualityIssue(
                        symbol, CATEGORY_MISSING_FIELD, f"{field_name}_missing"
                    )
                )
            elif value <= 0:
                symbol_issues.append(
                    SymbolQualityIssue(
                        symbol, CATEGORY_NON_POSITIVE_PRICE, f"{field_name}={value}"
                    )
                )

        current = prices["current_price"]
        low = prices["low_price"]
        high = prices["high_price"]
        # high_price is the one OHLC field with a 0 default (absent stck_hgpr).
        # A present-but-zero high disables the upper bound, so surface it rather
        # than let an over-high glitch with high=0 pass as "ok".
        if high is None or high == 0:
            symbol_issues.append(
                SymbolQualityIssue(symbol, CATEGORY_MISSING_FIELD, "high_price_missing")
            )
        elif high < 0:
            symbol_issues.append(
                SymbolQualityIssue(symbol, CATEGORY_NON_POSITIVE_PRICE, f"high_price={high}")
            )
        low_valid = low is not None and low > 0
        high_valid = high is not None and high > 0
        if low_valid and high_valid and low > high:
            # Inconsistent band: skip the (meaningless) current-vs-band checks.
            symbol_issues.append(
                SymbolQualityIssue(
                    symbol, CATEGORY_PRICE_OUT_OF_RANGE, f"low={low}>high={high}"
                )
            )
        elif current is not None and current > 0:
            # One-sided bounds: each fires independently, so a missing high
            # (high_price=0 default) still lets the lower bound be checked.
            if low_valid and current < low:
                symbol_issues.append(
                    SymbolQualityIssue(
                        symbol, CATEGORY_PRICE_OUT_OF_RANGE, f"current={current}<low={low}"
                    )
                )
            if high_valid and current > high:
                symbol_issues.append(
                    SymbolQualityIssue(
                        symbol, CATEGORY_PRICE_OUT_OF_RANGE, f"current={current}>high={high}"
                    )
                )

        try:
            pct = float(_field(obj, "prev_day_change_pct"))
        except Exception:
            pct = None
        if pct is not None and pct == pct and abs(pct) > max_daily_move_pct:
            symbol_issues.append(
                SymbolQualityIssue(
                    symbol, CATEGORY_IMPLAUSIBLE_MOVE, f"prev_day_change_pct={pct}"
                )
            )

        if symbol_issues:
            flagged_rows += 1
            issues.extend(symbol_issues)

    try:
        interval = int(refresh_interval_seconds)
    except (TypeError, ValueError):
        interval = 0
    threshold = max(interval * 2, 360)

    parsed = _parse_runtime_timestamp(snapshot_updated_at)
    if parsed is None:
        text = str(snapshot_updated_at or "").strip()
        freshness = FreshnessStatus(
            text or None,
            None,
            threshold,
            True,
            "updated_at_invalid" if text else "updated_at_missing",
        )
    else:
        try:
            age = float(now_epoch) - parsed.timestamp()
        except Exception:
            # Hostile now_epoch, or timestamp() raising OSError on some platforms
            # for extreme naive dates → unusable age, never claim "fresh".
            age = float("nan")
        if not math.isfinite(age):
            # Unusable clock/age → never claim "fresh", and keep the artifact
            # JSON-valid (no Infinity/NaN age leaks).
            freshness = FreshnessStatus(
                str(snapshot_updated_at), None, threshold, True, "age_unavailable"
            )
        else:
            stale = age > threshold
            freshness = FreshnessStatus(
                str(snapshot_updated_at),
                round(age, 1),
                threshold,
                stale,
                "stale" if stale else "fresh",
            )

    status = "warn" if (issues or freshness.stale) else "ok"
    return MarketDataQualityReport(
        str(checked_at) if checked_at else None,
        symbol_count,
        symbol_count - flagged_rows,
        len(issues),
        freshness,
        tuple(issues),
        status,
    )


def build_quality_alert_lines(report, *, max_symbols_per_category=5):
    """Alert-only text lines for a quality report (empty when status is ok)."""
    if report.status == "ok":
        return ()
    flagged = report.symbol_count - report.ok_count
    lines = [
        f"market-data quality WARN: {report.issue_count} issue(s), "
        f"{flagged}/{report.symbol_count} symbol(s) flagged"
    ]
    if report.freshness.stale:
        lines.append(
            f"  freshness: {report.freshness.reason} "
            f"(age={report.freshness.age_seconds}s, "
            f"threshold={report.freshness.stale_threshold_seconds}s)"
        )
    by_category: dict[str, list[str]] = {}
    for issue in report.issues or ():
        by_category.setdefault(issue.category, []).append(issue.symbol)
    for category in sorted(by_category):
        symbols = list(dict.fromkeys(by_category[category]))
        sample = ", ".join(symbols[:max_symbols_per_category])
        overflow = len(symbols) - max_symbols_per_category
        suffix = f" (+{overflow} more)" if overflow > 0 else ""
        lines.append(f"  {category}: {len(symbols)} — {sample}{suffix}")
    return tuple(lines)


def write_market_data_quality_artifact(report, *, path=QUALITY_ARTIFACT_PATH) -> bool:
    """Atomically write the report payload as JSON (fail-safe: returns bool).

    A quality-artifact write must never destabilize the runtime, so this catches
    broadly and never raises. ``allow_nan=False`` refuses to emit invalid JSON
    (``Infinity``/``NaN``) — it returns False rather than writing a payload no
    strict reader can parse. The temp file is removed on any failure.
    """
    target = Path(path)
    tmp_name = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            dir=str(target.parent),
            prefix=f".{target.name}.tmp.",
            delete=False,
            encoding="utf-8",
        ) as tmp_file:
            tmp_name = tmp_file.name
            json.dump(report.to_dict(), tmp_file, ensure_ascii=False, indent=2, allow_nan=False)
            tmp_file.write("\n")
        os.replace(tmp_name, target)
        return True
    except Exception:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        return False
