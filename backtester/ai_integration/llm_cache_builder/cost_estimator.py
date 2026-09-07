"""Offline token-usage and cost estimator for the LLM batch cache generator.

Reads the real candidate_features.jsonl, builds exact prompt payloads using
the same functions the live pipeline uses, and prints a cost breakdown without
making any API calls.

Usage::

    python -m backtester.ai_integration.llm_cache_builder.cost_estimator \\
        --features data/candidate_features.jsonl

    # Limit to first N days for a quick preview
    python -m backtester.ai_integration.llm_cache_builder.cost_estimator \\
        --features data/candidate_features.jsonl \\
        --limit-days 10

    # Filter to a date range
    python -m backtester.ai_integration.llm_cache_builder.cost_estimator \\
        --features data/candidate_features.jsonl \\
        --start-date 2024-01-02 \\
        --end-date 2024-03-31
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backtester.ai_integration.llm_cache_builder.prompt import (
    build_system_prompt,
    build_user_prompt,
)


# ── Pricing table (USD per million tokens, as of 2026) ────────────────────
# Source: https://www.anthropic.com/pricing
# Deprecated models (kept for cost estimation of historical runs)
_MODELS: dict[str, dict[str, float]] = {
    "claude-haiku-4-5": {
        "input_per_m": 1.00,
        "output_per_m": 5.00,
    },
    "claude-sonnet-4-6": {
        "input_per_m": 3.00,
        "output_per_m": 15.00,
    },
    "claude-opus-4-6": {
        "input_per_m": 5.00,
        "output_per_m": 25.00,
    },
    # Legacy models (deprecated, kept for cost estimation reference)
    "claude-haiku-3-5": {
        "input_per_m": 0.80,
        "output_per_m": 4.00,
    },
    "claude-sonnet-4-5": {
        "input_per_m": 3.00,
        "output_per_m": 15.00,
    },
    "claude-opus-4-5": {
        "input_per_m": 15.00,
        "output_per_m": 75.00,
    },
}


# ── Token estimation helpers ──────────────────────────────────────────────


def _tokens(text: str) -> int:
    """Character-based token approximation (no API dependency).

    Uses the widely-accepted ``len(text) // 4`` heuristic.  Clamped to 1
    so callers never receive zero.
    """
    return max(1, len(text) // 4)


def _output_tokens(n_tickers: int) -> int:
    """Estimate output tokens for a single trading-day response.

    Based on the compact one-ticker-per-line JSON format the model is
    instructed to produce::

        {
          "regime_multiplier": 1.05,    ← ~10 tokens overhead
          "tickers": {
            "<TICKER>": {...},          ← ~18 tokens per ticker
            ...
          }
        }

    Overhead of ``{"regime_multiplier": ..., "tickers": {...}}`` is ~8
    tokens; each ticker entry averages ~18 tokens in compact form.
    """
    return 8 + n_tickers * 18


# ── Data structures ───────────────────────────────────────────────────────


@dataclass
class DayStat:
    """Token statistics for a single trading day."""

    date: str
    n_tickers: int
    input_tokens: int   # system + user prompt
    output_tokens: int  # estimated response size


@dataclass
class SliceSummary:
    """Aggregated cost estimate for a date slice."""

    label: str
    days: int
    total_input_tokens: int
    total_output_tokens: int
    model_costs: dict[str, float] = field(default_factory=dict)
    # {model_name: total_usd}

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    @property
    def avg_input_tokens(self) -> float:
        return self.total_input_tokens / self.days if self.days else 0.0

    @property
    def avg_output_tokens(self) -> float:
        return self.total_output_tokens / self.days if self.days else 0.0


@dataclass
class CostEstimate:
    """Full estimation result returned by :func:`estimate`."""

    all_day_stats: list[DayStat]          # one per trading day (full range)
    system_prompt_tokens: int
    slices: list[SliceSummary]            # 3-day, 10-day, full
    start_date: str | None
    end_date: str | None


# ── Core estimation logic ─────────────────────────────────────────────────


def _load_days(
    features_path: str | Path,
    start_date: str | None = None,
    end_date: str | None = None,
    limit_days: int | None = None,
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Load JSONL, group by date, apply filters.

    Returns a sorted list of ``(date_key, records)`` tuples.
    """
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with open(features_path, encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            d = str(rec.get("date", "")).strip()
            if d:
                by_date[d].append(rec)

    dates = sorted(by_date)
    if start_date:
        dates = [d for d in dates if d >= start_date]
    if end_date:
        dates = [d for d in dates if d <= end_date]
    if limit_days:
        dates = dates[:limit_days]

    return [(d, by_date[d]) for d in dates]


def _make_slice(
    label: str,
    day_stats: list[DayStat],
) -> SliceSummary:
    total_in = sum(s.input_tokens for s in day_stats)
    total_out = sum(s.output_tokens for s in day_stats)
    model_costs: dict[str, float] = {}
    for model, pricing in _MODELS.items():
        cost = (
            total_in / 1_000_000 * pricing["input_per_m"]
            + total_out / 1_000_000 * pricing["output_per_m"]
        )
        model_costs[model] = cost
    return SliceSummary(
        label=label,
        days=len(day_stats),
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        model_costs=model_costs,
    )


def estimate(
    features_path: str | Path,
    start_date: str | None = None,
    end_date: str | None = None,
    limit_days: int | None = None,
) -> CostEstimate:
    """Build prompts for every trading day and estimate token usage.

    Parameters
    ----------
    features_path:
        Path to the candidate_features.jsonl produced by
        ``CandidateFeatureExporter``.
    start_date / end_date:
        Optional ISO date strings to restrict the date range.
    limit_days:
        Cap the number of trading days analysed.
    """
    system_prompt = build_system_prompt()
    system_tokens = _tokens(system_prompt)

    days = _load_days(features_path, start_date, end_date, limit_days)

    all_stats: list[DayStat] = []
    for date_key, records in days:
        user_prompt = build_user_prompt(date_key, records)
        input_tokens = system_tokens + _tokens(user_prompt)
        output_tokens = _output_tokens(len(records))
        all_stats.append(
            DayStat(
                date=date_key,
                n_tickers=len(records),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )

    # Build slices: first 3, first 10, full range.
    # Preview slices are only added when the dataset is at least as large as
    # their window — a "10-day preview" with 5 days would be misleading.
    slices: list[SliceSummary] = []
    for label, n in [("3-day preview", 3), ("10-day preview", 10), ("full range", None)]:
        subset = all_stats[:n] if n is not None else all_stats
        if subset and (n is None or len(all_stats) >= n):
            slices.append(_make_slice(label, subset))

    actual_start = days[0][0] if days else None
    actual_end = days[-1][0] if days else None

    return CostEstimate(
        all_day_stats=all_stats,
        system_prompt_tokens=system_tokens,
        slices=slices,
        start_date=actual_start,
        end_date=actual_end,
    )


# ── Report formatter ──────────────────────────────────────────────────────

_BAR = "━" * 64

# Parse-stability threshold: if the cheapest model's 3-day cost exceeds this
# (in USD) we note the user should still start small regardless.
_SMOKE_TEST_CHEAP_THRESHOLD = 0.10


def _build_recommendation(est: CostEstimate) -> str:
    """Return a recommendation block derived purely from the estimate data.

    Identifies the cheapest model (by full-range cost), then emits
    staged guidance: smoke test → pilot → full run.  Falls back
    gracefully when fewer than 3 or 10 days are available.
    """
    slice_by_label: dict[str, SliceSummary] = {sl.label: sl for sl in est.slices}
    full = slice_by_label.get("full range")

    if full is None:
        return ""

    # ── cheapest model ────────────────────────────────────────────────────
    cheapest = min(full.model_costs, key=lambda m: full.model_costs[m])
    cheapest_full_cost = full.model_costs[cheapest]

    lines: list[str] = []
    lines.append("")
    lines.append(_BAR)
    lines.append("  Recommendation")
    lines.append(_BAR)
    lines.append(f"  Cheapest model   : {cheapest}")
    lines.append("")

    # ── per-stage costs for the cheapest model ────────────────────────────
    stage_labels = [
        ("3-day smoke test",  "3-day preview"),
        ("10-day pilot",      "10-day preview"),
        ("Full range",        "full range"),
    ]
    for stage_name, slice_label in stage_labels:
        sl = slice_by_label.get(slice_label)
        if sl is None:
            # Fewer days than this slice covers — show actual count instead.
            days_available = len(est.all_day_stats)
            sl = full  # use what we have
            label_str = f"  {stage_name:<20}: ${sl.model_costs[cheapest]:.4f}  ({days_available} day(s) available)"
        else:
            cost = sl.model_costs[cheapest]
            label_str = f"  {stage_name:<20}: ${cost:.4f}"
        lines.append(label_str)

    lines.append("")

    # ── guidance ──────────────────────────────────────────────────────────
    smoke_sl = slice_by_label.get("3-day preview", full)
    smoke_cost = smoke_sl.model_costs[cheapest]

    lines.append("  Suggested workflow:")
    lines.append(f"  1. Run a 3-day smoke test (${smoke_cost:.4f}) to validate parse")
    lines.append("     stability before committing to a larger run.")

    pilot_sl = slice_by_label.get("10-day preview")
    if pilot_sl is not None:
        pilot_cost = pilot_sl.model_costs[cheapest]
        lines.append(f"  2. If ≥ 90% of days parse cleanly, expand to 10 days (${pilot_cost:.4f}).")
    else:
        lines.append("  2. If parse stability is good, expand the date range gradually.")

    lines.append(f"  3. Full range costs ${cheapest_full_cost:.4f} with {cheapest}.")

    if cheapest != list(_MODELS)[-1]:
        # Not already suggesting the most capable model — mention upgrade path.
        lines.append(f"     Upgrade to a more capable model only if parse failures persist.")

    lines.append("")
    lines.append("  Parse stability check: days_success / days_attempted in the")
    lines.append("  generation_day_log.jsonl written after each run.")
    lines.append(_BAR)

    return "\n".join(lines)


def format_report(est: CostEstimate) -> str:
    """Render a human-readable cost report from a :class:`CostEstimate`."""
    lines: list[str] = []

    lines.append(_BAR)
    lines.append("  LLM Cost Estimator  (no API calls — character heuristic)")
    lines.append(_BAR)

    if not est.all_day_stats:
        lines.append("  No trading days found.")
        return "\n".join(lines)

    n_days = len(est.all_day_stats)
    avg_tickers = sum(s.n_tickers for s in est.all_day_stats) / n_days
    avg_input = sum(s.input_tokens for s in est.all_day_stats) / n_days
    avg_output = sum(s.output_tokens for s in est.all_day_stats) / n_days
    max_stat = max(est.all_day_stats, key=lambda s: s.input_tokens)

    lines.append(f"  Date range       : {est.start_date} → {est.end_date}")
    lines.append(f"  Trading days     : {n_days}")
    lines.append(f"  System prompt    : {est.system_prompt_tokens:,} tokens (shared)")
    lines.append(f"  Avg candidates   : {avg_tickers:.1f} tickers/day")
    lines.append(f"  Avg input tokens : {avg_input:,.0f}/day")
    lines.append(f"  Avg output tokens: {avg_output:,.0f}/day")
    lines.append(
        f"  Largest day      : {max_stat.date} "
        f"({max_stat.n_tickers} tickers, {max_stat.input_tokens:,} input tokens)"
    )
    lines.append("")

    # Per-slice cost table
    model_names = list(_MODELS)
    header_models = "  ".join(f"{m:<22}" for m in model_names)
    lines.append(f"  {'Slice':<20}  {'Days':>5}  {'Input tok':>10}  {'Output tok':>10}  {header_models}")
    lines.append("  " + "-" * 100)

    for sl in est.slices:
        cost_cols = "  ".join(
            f"${sl.model_costs.get(m, 0.0):>9.4f}          " for m in model_names
        )
        lines.append(
            f"  {sl.label:<20}  {sl.days:>5}  "
            f"{sl.total_input_tokens:>10,}  {sl.total_output_tokens:>10,}  "
            f"{cost_cols}"
        )

    lines.append("")
    lines.append("  Pricing basis (USD/million tokens):")
    for model, pricing in _MODELS.items():
        lines.append(
            f"    {model:<22}  input ${pricing['input_per_m']:.2f}  "
            f"output ${pricing['output_per_m']:.2f}"
        )
    lines.append("")
    lines.append("  Note: token counts use the len(text)//4 character heuristic.")
    lines.append("        Actual API token counts may differ by ±15%.")
    lines.append(_BAR)

    lines.append(_build_recommendation(est))

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m backtester.ai_integration.llm_cache_builder.cost_estimator",
        description="Estimate LLM token usage and cost without making API calls.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--features",
        required=True,
        metavar="PATH",
        help="Path to candidate_features.jsonl",
    )
    p.add_argument(
        "--start-date",
        default=None,
        metavar="YYYY-MM-DD",
        help="First trading date to include (inclusive)",
    )
    p.add_argument(
        "--end-date",
        default=None,
        metavar="YYYY-MM-DD",
        help="Last trading date to include (inclusive)",
    )
    p.add_argument(
        "--limit-days",
        type=int,
        default=None,
        metavar="N",
        help="Cap the number of trading days analysed",
    )
    return p


def _main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    est = estimate(
        args.features,
        start_date=args.start_date,
        end_date=args.end_date,
        limit_days=args.limit_days,
    )
    print(format_report(est))


if __name__ == "__main__":
    _main()
