"""Feature inspection / statistics tool for candidate_features.jsonl.

Reads an exported candidate feature file and produces a structured summary
useful for:

  * Understanding the data before a paid LLM run (sanity checking)
  * Designing or tuning the LLM prompt (which features are informative?)
  * Catching export bugs (unexpected zeros, wrong date ranges, etc.)

Public API
----------
``inspect_features(path)``  → :class:`InspectionResult`
``format_report(result)``   → human-readable string

CLI::

    python -m backtester.ai_integration.feature_inspector \\
        --features data/candidate_features.jsonl \\
        --json-out data/feature_stats.json
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


__all__ = ["FieldStats", "InspectionResult", "inspect_features", "format_report"]


# ── Numeric field stats ───────────────────────────────────────────────────

# Fields inspected under the top-level record.
_TOP_LEVEL_NUMERIC_FIELDS: tuple[str, ...] = ("base_score",)

# Feature fields shown first (in this order); any others follow alphabetically.
_PRIORITY_FEATURE_FIELDS: tuple[str, ...] = (
    "momentum_quality_score",
    "trend_quality_score",
    "range_recovery_bonus",
    "overheat_penalty",
    "pullback_exhaustion_penalty",
    "pullback_pct",
    "rebound_pct",
    "range_recovery_ratio",
)


@dataclass
class FieldStats:
    """Descriptive statistics for one numeric field."""

    name: str
    count: int          # non-null values observed
    mean: float
    min: float
    max: float
    zero_fraction: float  # fraction of observed values that are exactly 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "count": self.count,
            "mean": round(self.mean, 6),
            "min": round(self.min, 6),
            "max": round(self.max, 6),
            "zero_fraction": round(self.zero_fraction, 4),
        }


class _NumericAccumulator:
    """Online accumulator for FieldStats — no numpy required."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._count = 0
        self._total = 0.0
        self._min = math.inf
        self._max = -math.inf
        self._zero_count = 0

    def add(self, v: Any) -> None:
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return
        self._count += 1
        self._total += fv
        if fv < self._min:
            self._min = fv
        if fv > self._max:
            self._max = fv
        if fv == 0.0:
            self._zero_count += 1

    def build(self) -> FieldStats:
        if self._count == 0:
            return FieldStats(self.name, 0, 0.0, 0.0, 0.0, 0.0)
        return FieldStats(
            name=self.name,
            count=self._count,
            mean=self._total / self._count,
            min=self._min,
            max=self._max,
            zero_fraction=self._zero_count / self._count,
        )


# ── Inspection result ─────────────────────────────────────────────────────


@dataclass
class InspectionResult:
    """Full inspection summary for one candidate_features.jsonl file."""

    # ── record-level counts ────────────────────────────────────────────────
    total_records: int
    malformed_lines: int

    # ── date / day summary ─────────────────────────────────────────────────
    start_date: str | None
    end_date: str | None
    trading_days: int
    avg_candidates_per_day: float
    max_candidates_per_day: int
    max_candidates_date: str | None

    # ── categorical distributions ──────────────────────────────────────────
    decision_distribution: dict[str, int]
    decision_reason_distribution: dict[str, int]
    rule_gate_distribution: dict[str, int]
    score_gate_distribution: dict[str, int]
    executed_distribution: dict[str, int]

    # ── ticker frequency (top 20) ─────────────────────────────────────────
    ticker_top20: list[tuple[str, int]]    # [(ticker, count), ...]
    unique_tickers: int

    # ── numeric field stats ────────────────────────────────────────────────
    #: Stats for top-level fields (base_score, candidate_rank)
    top_level_stats: list[FieldStats]
    #: Stats for feature sub-fields, priority fields first
    feature_stats: list[FieldStats]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_records": self.total_records,
            "malformed_lines": self.malformed_lines,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "trading_days": self.trading_days,
            "avg_candidates_per_day": round(self.avg_candidates_per_day, 2),
            "max_candidates_per_day": self.max_candidates_per_day,
            "max_candidates_date": self.max_candidates_date,
            "decision_distribution": self.decision_distribution,
            "decision_reason_distribution": self.decision_reason_distribution,
            "rule_gate_distribution": self.rule_gate_distribution,
            "score_gate_distribution": self.score_gate_distribution,
            "executed_distribution": self.executed_distribution,
            "unique_tickers": self.unique_tickers,
            "ticker_top20": [
                {"ticker": t, "count": c} for t, c in self.ticker_top20
            ],
            "top_level_stats": [s.to_dict() for s in self.top_level_stats],
            "feature_stats": [s.to_dict() for s in self.feature_stats],
        }


# ── Core inspection logic ─────────────────────────────────────────────────


def inspect_features(
    features_path: str | Path,
) -> InspectionResult:
    """Read *features_path* and return a populated :class:`InspectionResult`.

    Parameters
    ----------
    features_path:
        Path to ``candidate_features.jsonl`` produced by
        ``CandidateFeatureExporter``.
    """
    features_path = Path(features_path)

    # Accumulators
    total_records = 0
    malformed_lines = 0
    by_date: dict[str, int] = defaultdict(int)     # date → candidate count
    ticker_counter: Counter[str] = Counter()
    decision_counter: Counter[str] = Counter()
    decision_reason_counter: Counter[str] = Counter()
    rule_gate_counter: Counter[str] = Counter()
    score_gate_counter: Counter[str] = Counter()
    executed_counter: Counter[str] = Counter()

    # Numeric accumulators — built lazily so we handle any feature set.
    top_accums: dict[str, _NumericAccumulator] = {
        "base_score": _NumericAccumulator("base_score"),
        "candidate_rank": _NumericAccumulator("candidate_rank"),
    }
    feat_accums: dict[str, _NumericAccumulator] = {}

    with features_path.open(encoding="utf-8") as fh:
        for raw_line in fh:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                rec = json.loads(raw_line)
            except json.JSONDecodeError:
                malformed_lines += 1
                continue

            total_records += 1
            date_key = str(rec.get("date", "")).strip()
            if date_key:
                by_date[date_key] += 1

            ticker = str(rec.get("ticker", "")).strip()
            if ticker:
                ticker_counter[ticker] += 1

            # Categorical fields
            decision_counter[str(rec.get("decision", ""))] += 1
            decision_reason_counter[str(rec.get("decision_reason", ""))] += 1
            rule_gate_counter[str(rec.get("rule_gate_passed", ""))] += 1
            score_gate_counter[str(rec.get("score_gate_passed", ""))] += 1
            executed_counter[str(rec.get("executed", ""))] += 1

            # Top-level numerics
            for key, acc in top_accums.items():
                acc.add(rec.get(key))

            # Feature sub-dict numerics
            features: dict[str, Any] = rec.get("features") or {}
            for feat_key, feat_val in features.items():
                if feat_key not in feat_accums:
                    feat_accums[feat_key] = _NumericAccumulator(feat_key)
                feat_accums[feat_key].add(feat_val)

    # ── Assemble result ───────────────────────────────────────────────────
    sorted_dates = sorted(by_date)
    trading_days = len(sorted_dates)
    start_date = sorted_dates[0] if sorted_dates else None
    end_date = sorted_dates[-1] if sorted_dates else None
    max_candidates_date = (
        max(by_date, key=lambda d: by_date[d]) if by_date else None
    )
    max_candidates_per_day = by_date[max_candidates_date] if max_candidates_date else 0
    avg_per_day = (
        sum(by_date.values()) / trading_days if trading_days else 0.0
    )

    # Ordered feature stats: priority fields first, then remaining alphabetically.
    priority_set = set(_PRIORITY_FEATURE_FIELDS)
    extra_keys = sorted(k for k in feat_accums if k not in priority_set)
    ordered_feat_keys = [
        k for k in _PRIORITY_FEATURE_FIELDS if k in feat_accums
    ] + extra_keys

    return InspectionResult(
        total_records=total_records,
        malformed_lines=malformed_lines,
        start_date=start_date,
        end_date=end_date,
        trading_days=trading_days,
        avg_candidates_per_day=avg_per_day,
        max_candidates_per_day=max_candidates_per_day,
        max_candidates_date=max_candidates_date,
        decision_distribution=dict(decision_counter),
        decision_reason_distribution=dict(decision_reason_counter),
        rule_gate_distribution=dict(rule_gate_counter),
        score_gate_distribution=dict(score_gate_counter),
        executed_distribution=dict(executed_counter),
        ticker_top20=ticker_counter.most_common(20),
        unique_tickers=len(ticker_counter),
        top_level_stats=[acc.build() for acc in top_accums.values()],
        feature_stats=[feat_accums[k].build() for k in ordered_feat_keys],
    )


# ── Report formatter ──────────────────────────────────────────────────────

_BAR = "━" * 64
_SEP = "─" * 64


def format_report(result: InspectionResult) -> str:
    """Return a human-readable inspection summary string."""
    lines: list[str] = []

    lines.append(_BAR)
    lines.append("  Candidate Feature Inspector")
    lines.append(_BAR)

    if result.total_records == 0:
        lines.append("  No records found.")
        return "\n".join(lines)

    # ── Record overview ───────────────────────────────────────────────────
    lines.append(f"  Total records        : {result.total_records:,}")
    if result.malformed_lines:
        lines.append(f"  Malformed lines      : {result.malformed_lines}")
    lines.append(f"  Date range           : {result.start_date} → {result.end_date}")
    lines.append(f"  Trading days         : {result.trading_days}")
    lines.append(f"  Avg candidates/day   : {result.avg_candidates_per_day:.1f}")
    lines.append(
        f"  Max candidates/day   : {result.max_candidates_per_day}"
        f"  ({result.max_candidates_date})"
    )
    lines.append(f"  Unique tickers       : {result.unique_tickers}")

    # ── Categorical distributions ──────────────────────────────────────────
    lines.append("")
    lines.append(_SEP)
    lines.append("  Categorical distributions")
    lines.append(_SEP)
    _append_dist(lines, "decision",        result.decision_distribution)
    _append_dist(lines, "decision_reason", result.decision_reason_distribution)
    _append_dist(lines, "rule_gate_passed", result.rule_gate_distribution)
    _append_dist(lines, "score_gate_passed", result.score_gate_distribution)
    _append_dist(lines, "executed",        result.executed_distribution)

    # ── Ticker frequency ──────────────────────────────────────────────────
    lines.append("")
    lines.append(_SEP)
    lines.append(f"  Ticker frequency  (top {len(result.ticker_top20)}, {result.unique_tickers} unique)")
    lines.append(_SEP)
    for ticker, count in result.ticker_top20:
        bar = "█" * min(count, 40)
        lines.append(f"    {ticker:<10}  {count:>4}  {bar}")

    # ── Numeric stats ──────────────────────────────────────────────────────
    lines.append("")
    lines.append(_SEP)
    lines.append("  Numeric field statistics")
    lines.append(_SEP)
    lines.append(
        f"  {'Field':<34}  {'N':>5}  {'Mean':>8}  {'Min':>8}  {'Max':>8}  {'Zeros':>6}"
    )
    lines.append("  " + "-" * 78)

    for fs in result.top_level_stats + result.feature_stats:
        zero_pct = f"{fs.zero_fraction * 100:.0f}%"
        lines.append(
            f"  {fs.name:<34}  {fs.count:>5}  "
            f"{fs.mean:>8.3f}  {fs.min:>8.3f}  {fs.max:>8.3f}  {zero_pct:>6}"
        )

    lines.append(_BAR)
    return "\n".join(lines)


def _append_dist(
    lines: list[str],
    label: str,
    dist: dict[str, int],
) -> None:
    total = sum(dist.values())
    parts = ", ".join(
        f"{k}={v} ({v/total*100:.0f}%)"
        for k, v in sorted(dist.items(), key=lambda kv: -kv[1])
    )
    lines.append(f"  {label:<22}: {parts}")


# ── CLI ───────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m backtester.ai_integration.feature_inspector",
        description="Inspect candidate_features.jsonl without any API calls.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--features",
        required=True,
        metavar="PATH",
        help="Path to candidate_features.jsonl",
    )
    p.add_argument(
        "--json-out",
        default=None,
        metavar="PATH",
        help="Write machine-readable stats to this JSON file",
    )
    return p


def _main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)

    result = inspect_features(args.features)
    print(format_report(result))

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n  JSON stats written: {out_path}")


if __name__ == "__main__":
    _main()
