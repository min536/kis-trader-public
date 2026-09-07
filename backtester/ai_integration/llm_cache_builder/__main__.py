"""CLI entry point for the LLM batch cache generator.

Usage::

    # Smoke-test: process first 3 days, fall back to heuristic on failure
    python -m backtester.ai_integration.llm_cache_builder \\
        --features data/candidate_features.jsonl \\
        --output-dir data/ai_signals \\
        --archive-dir data/llm_archive \\
        --limit-days 3 \\
        --verbose

    # Full run for a date range
    python -m backtester.ai_integration.llm_cache_builder \\
        --features data/candidate_features.jsonl \\
        --output-dir data/ai_signals \\
        --start-date 2024-01-02 \\
        --end-date 2024-03-31 \\
        --model claude-opus-4-5 \\
        --validate

    # Dry run — preview what would be processed without calling the LLM
    python -m backtester.ai_integration.llm_cache_builder \\
        --features data/candidate_features.jsonl \\
        --output-dir data/ai_signals \\
        --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path


# ── Argument parser ───────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m backtester.ai_integration.llm_cache_builder",
        description=(
            "Generate AISignalProvider cache files from exported candidate "
            "feature JSONL using an LLM (Claude) for signal generation."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # ── Required ──────────────────────────────────────────────────────────
    p.add_argument(
        "--features",
        required=True,
        metavar="PATH",
        help="Path to candidate_features.jsonl (exported by CandidateFeatureExporter)",
    )
    p.add_argument(
        "--output-dir",
        required=True,
        metavar="DIR",
        help="Directory for YYYY-MM-DD.json output cache files",
    )

    # ── Optional paths ────────────────────────────────────────────────────
    p.add_argument(
        "--archive-dir",
        default=None,
        metavar="DIR",
        help=(
            "Directory for raw LLM response archives (YYYY-MM-DD.jsonl). "
            "Defaults to <output-dir>/../llm_archive"
        ),
    )

    # ── LLM settings ──────────────────────────────────────────────────────
    from backtester.ai_integration.llm_cache_builder.client import (  # noqa: PLC0415
        DEFAULT_MODEL,
        KNOWN_MODELS,
    )
    model_choices = "\n".join(
        f"    {name}  ({desc})" for name, desc in KNOWN_MODELS.items()
    )
    p.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        metavar="MODEL",
        help=(
            f"Anthropic model to use (default: {DEFAULT_MODEL}).\n"
            f"Available options:\n{model_choices}\n"
            "Tip: start with the default (haiku) for your first smoke test."
        ),
    )
    p.add_argument(
        "--api-key",
        default=None,
        metavar="KEY",
        help="Anthropic API key (default: $ANTHROPIC_API_KEY environment variable)",
    )
    p.add_argument(
        "--max-retries",
        type=int,
        default=3,
        metavar="N",
        help="Maximum LLM calls per trading day, including the first attempt (default: 3)",
    )

    # ── Behaviour flags ───────────────────────────────────────────────────
    p.add_argument(
        "--no-fallback",
        action="store_true",
        help=(
            "Do NOT fall back to the deterministic heuristic on final parse "
            "failure; skip the day instead"
        ),
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing YYYY-MM-DD.json files (default: skip them)",
    )

    # ── Date / size filters ───────────────────────────────────────────────
    p.add_argument(
        "--start-date",
        default=None,
        metavar="YYYY-MM-DD",
        help="First trading date to process (inclusive)",
    )
    p.add_argument(
        "--end-date",
        default=None,
        metavar="YYYY-MM-DD",
        help="Last trading date to process (inclusive)",
    )
    p.add_argument(
        "--limit-days",
        type=int,
        default=None,
        metavar="N",
        help="Stop after processing N trading dates (smoke-test shortcut)",
    )

    # ── Post-generation ───────────────────────────────────────────────────
    p.add_argument(
        "--validate",
        action="store_true",
        help="Validate all JSON files in --output-dir after generation completes",
    )

    # ── Dry-run / debug ───────────────────────────────────────────────────
    p.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Show what would be processed without calling the LLM or writing "
            "any files"
        ),
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG logging",
    )
    return p


# ── Dry-run helper ────────────────────────────────────────────────────────


def _dry_run(args: argparse.Namespace) -> None:
    """Print a preview of what would be processed."""
    by_date: dict[str, list[str]] = defaultdict(list)
    skipped = 0
    with open(args.features, encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                skipped += 1
                continue
            d = str(rec.get("date", "")).strip()
            t = str(rec.get("ticker", "")).strip()
            if d and t:
                by_date[d].append(t)
            else:
                skipped += 1

    output_dir = Path(args.output_dir)
    dates = sorted(by_date)

    if args.start_date:
        dates = [d for d in dates if d >= args.start_date]
    if args.end_date:
        dates = [d for d in dates if d <= args.end_date]
    if args.limit_days:
        dates = dates[: args.limit_days]

    would_skip = 0
    would_process: list[str] = []
    for d in dates:
        if (output_dir / f"{d}.json").exists() and not args.overwrite:
            would_skip += 1
        else:
            would_process.append(d)

    print("\nDRY RUN — no LLM calls, no files written")
    print(f"  Total dates in JSONL   : {len(by_date)}")
    print(f"  After date-range filter: {len(dates)}")
    print(f"  Skip (cache exists)    : {would_skip}")
    print(f"  Would process          : {len(would_process)} date(s)")
    if would_process:
        print(f"  Date range             : {would_process[0]} → {would_process[-1]}")
        print(f"\n  Sample (first 5):")
        for d in would_process[:5]:
            tickers = sorted(set(by_date[d]))
            extra = f" + {len(tickers) - 8} more" if len(tickers) > 8 else ""
            print(f"    {d}: {len(tickers)} ticker(s) — {', '.join(tickers[:8])}{extra}")
    if skipped:
        print(f"\n  Malformed/dateless lines skipped: {skipped}")


# ── Main ──────────────────────────────────────────────────────────────────


def _main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    output_dir = Path(args.output_dir)
    archive_dir = (
        Path(args.archive_dir)
        if args.archive_dir
        else output_dir.parent / "llm_archive"
    )

    from backtester.ai_integration.llm_cache_builder.client import model_note  # noqa: PLC0415

    print("━" * 60)
    print("  LLM Batch Cache Generator")
    print("━" * 60)
    print(f"  Features   : {args.features}")
    print(f"  Output dir : {output_dir}")
    print(f"  Archive dir: {archive_dir}")
    print(f"  Model      : {args.model}")
    print(f"  Retries    : {args.max_retries}")
    print(f"  Fallback   : {'heuristic' if not args.no_fallback else 'skip'}")
    print(f"  Overwrite  : {args.overwrite}")
    if args.start_date or args.end_date:
        print(f"  Date range : {args.start_date or '(start)'} → {args.end_date or '(end)'}")
    if args.limit_days:
        print(f"  Limit      : {args.limit_days} days")
    print("━" * 60)
    print(f"  ▸ {model_note(args.model)}")
    print("━" * 60)

    if args.dry_run:
        _dry_run(args)
        return

    # Import here so a missing `anthropic` package only errors on real runs.
    from backtester.ai_integration.llm_cache_builder.batch import BatchCacheGenerator  # noqa: PLC0415
    from backtester.ai_integration.llm_cache_builder.client import AnthropicClient  # noqa: PLC0415

    client = AnthropicClient(api_key=args.api_key, model=args.model)

    gen = BatchCacheGenerator(
        client,
        output_dir=output_dir,
        archive_dir=archive_dir,
        max_retries=args.max_retries,
        fallback_to_heuristic=not args.no_fallback,
        overwrite=args.overwrite,
    )

    print("\nStarting …\n")
    stats = gen.generate(
        args.features,
        start_date=args.start_date,
        end_date=args.end_date,
        limit_days=args.limit_days,
    )

    # ── Write machine-readable artifacts into the cache directory ─────────
    summary_path = output_dir / "generation_summary.json"
    day_log_path = output_dir / "generation_day_log.jsonl"
    stats.write_summary(summary_path)
    stats.write_day_log(day_log_path)

    # ── Print terminal summary ─────────────────────────────────────────────
    print("\n" + "━" * 60)
    print("  Generation complete")
    print("━" * 60)
    d = stats.to_dict()
    print(f"  LLM days (success)    : {d['days_success']}")
    print(f"  Heuristic fallback    : {d['days_heuristic_fallback']}")
    print(f"  Skipped (file exists) : {d['days_skipped_exists']}")
    print(f"  Failed (no output)    : {d['days_failed']}")
    print(f"  Total LLM calls       : {d['total_llm_calls']}")
    print(f"  Retries               : {d['total_retries']}")
    print(f"  Parse failures        : {d['total_parse_failures']}")
    print(f"  API failures          : {d['total_api_failures']}")
    print(f"  Cache source quality  : {d['cache_source_quality']}")
    print(f"  Summary written       : {summary_path}")
    print(f"  Day log written       : {day_log_path}")
    if stats.dates_written:
        print(
            f"  Date range written    : "
            f"{stats.dates_written[0]} → {stats.dates_written[-1]}"
        )

    # ── All-fallback guard ─────────────────────────────────────────────────
    warning = stats.interpretation_warning
    if warning:
        print("\n" + "!" * 60)
        print(f"  WARNING: {warning}")
        print("!" * 60)

    if args.validate:
        from backtester.ai_integration.cache_builder import validate_cache_dir  # noqa: PLC0415
        issues = validate_cache_dir(output_dir)
        if issues:
            print(f"\nValidation: {len(issues)} issue(s) found:")
            for iss in issues:
                print(f"  [{iss['file']}] {iss['issue']}")
            sys.exit(1)
        files = [p for p in output_dir.glob("????-??-??.json")]
        print(f"\nValidation: all {len(files)} cache file(s) OK")

    if stats.days_failed:
        print(f"\nERROR: {stats.days_failed} day(s) produced no output: {stats.dates_failed}")
        sys.exit(1)


if __name__ == "__main__":
    _main()
