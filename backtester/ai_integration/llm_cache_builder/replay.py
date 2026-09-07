"""Archive replay tool for the LLM batch cache generator.

Reads archived raw LLM responses from a previous generation run and
re-runs the *current* parser on them to regenerate per-day cache files —
without making any new API calls.

Two entry points:

* :class:`ArchiveReplayer` — importable class for programmatic use.
* CLI — ``python -m backtester.ai_integration.llm_cache_builder.replay``

Why replay instead of just using the existing cache
----------------------------------------------------
The archive stores the verbatim LLM text (``raw_response``) alongside the
payload that was parsed at generation time.  If:

  * cache files were accidentally deleted, or
  * the parser was tightened after the original run (stricter validation,
    better rank normalisation, etc.), or
  * you want to audit which old responses still validate under the current
    schema,

…replay lets you rebuild or re-validate cheaply by replaying the archived
text through the current ``parse_response()`` rather than calling the LLM
again.

Archive record format (one JSON line per attempt in YYYY-MM-DD.jsonl)::

    {
      "date_key":     "2024-01-15",
      "attempt":      1,
      "timestamp":    "...",
      "success":      true,
      "raw_response": "<verbatim LLM text>",
      "parsed":       {"regime_multiplier": ..., "tickers": {...}},
      "error":        null
    }

Only records where ``success=true`` AND ``raw_response`` is non-empty are
eligible for replay.  ``expected_tickers`` are derived from the archived
``parsed["tickers"]`` keys so no feature JSONL is required.
"""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backtester.ai_integration.llm_cache_builder.parser import ParseError, parse_response

logger = logging.getLogger(__name__)

__all__ = ["ArchiveReplayer", "ReplayStats"]


# ── Stats ─────────────────────────────────────────────────────────────────


@dataclass
class ReplayStats:
    """Counters returned by a :meth:`ArchiveReplayer.replay` call."""

    days_attempted: int = 0
    success_count: int = 0
    parse_failures: int = 0
    skipped_existing: int = 0
    missing_archive_days: int = 0

    #: Dates whose cache file was (re)written successfully.
    dates_written: list[str] = field(default_factory=list)
    #: Dates where the current parser rejected the archived raw response.
    dates_parse_failed: list[str] = field(default_factory=list)
    #: Dates in the requested range with no usable archive record.
    dates_missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "days_attempted": self.days_attempted,
            "success_count": self.success_count,
            "parse_failures": self.parse_failures,
            "skipped_existing": self.skipped_existing,
            "missing_archive_days": self.missing_archive_days,
            "dates_written": self.dates_written,
            "dates_parse_failed": self.dates_parse_failed,
            "dates_missing": self.dates_missing,
        }

    def write_summary(self, path: str | Path) -> None:
        """Write ``replay_summary.json`` to *path*.  Best-effort."""
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("failed to write replay_summary.json: %s", exc)

    def __str__(self) -> str:
        parts = [f"{self.success_count}/{self.days_attempted} replayed OK"]
        if self.parse_failures:
            parts.append(f"{self.parse_failures} parse failure(s)")
        if self.skipped_existing:
            parts.append(f"{self.skipped_existing} skipped (exists)")
        if self.missing_archive_days:
            parts.append(f"{self.missing_archive_days} missing archive day(s)")
        return " | ".join(parts)


# ── Core replayer ─────────────────────────────────────────────────────────


class ArchiveReplayer:
    """Replay archived raw LLM responses through the current parser.

    Parameters
    ----------
    archive_dir:
        Directory containing ``YYYY-MM-DD.jsonl`` archive files written
        by :class:`~backtester.ai_integration.llm_cache_builder.archive.ResponseArchive`.
    output_dir:
        Directory where regenerated ``YYYY-MM-DD.json`` cache files are
        written.
    overwrite:
        When ``False`` (default), skip dates whose output file already
        exists.
    """

    def __init__(
        self,
        archive_dir: str | Path,
        output_dir: str | Path,
        *,
        overwrite: bool = False,
    ) -> None:
        self._archive_dir = Path(archive_dir)
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._overwrite = overwrite

    # ── Public API ────────────────────────────────────────────────────────

    def replay(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        date: str | None = None,
    ) -> ReplayStats:
        """Replay all eligible archive files that match the given filters.

        Parameters
        ----------
        start_date / end_date:
            ISO date strings (``"YYYY-MM-DD"``) for an inclusive range.
            Ignored when *date* is set.
        date:
            Replay exactly one trading date; overrides range parameters.

        Returns
        -------
        ReplayStats
            Counts for the completed replay run.
        """
        if date is not None:
            dates_to_replay = [date]
        else:
            dates_to_replay = self._discover_dates(start_date, end_date)

        stats = ReplayStats()
        for date_key in dates_to_replay:
            out_path = self._output_dir / f"{date_key}.json"

            if out_path.exists() and not self._overwrite:
                stats.skipped_existing += 1
                logger.debug("skip %s — output file exists", date_key)
                continue

            stats.days_attempted += 1
            self._replay_day(date_key, out_path, stats)

        return stats

    # ── Internals ─────────────────────────────────────────────────────────

    def _discover_dates(
        self,
        start_date: str | None,
        end_date: str | None,
    ) -> list[str]:
        """Return sorted date strings for all archive files in range."""
        dates: list[str] = []
        for p in sorted(self._archive_dir.glob("????-??-??.jsonl")):
            d = p.stem
            if start_date and d < start_date:
                continue
            if end_date and d > end_date:
                continue
            dates.append(d)
        return dates

    def _load_best_raw(self, date_key: str) -> tuple[str, list[str]] | None:
        """Find the most recent successful raw response for *date_key*.

        Returns ``(raw_response, expected_tickers)`` or ``None`` if no
        usable record exists.

        ``expected_tickers`` is derived from ``parsed["tickers"]`` in the
        archived record — the tickers the LLM was asked to score and
        successfully covered at generation time.
        """
        archive_path = self._archive_dir / f"{date_key}.jsonl"
        if not archive_path.exists():
            return None

        best: dict[str, Any] | None = None
        with archive_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    rec.get("success")
                    and rec.get("raw_response")
                    and rec.get("parsed")
                    and isinstance(rec["parsed"].get("tickers"), dict)
                ):
                    best = rec  # keep iterating — want the *last* success

        if best is None:
            return None

        raw_response: str = best["raw_response"]
        expected_tickers: list[str] = list(best["parsed"]["tickers"].keys())
        return raw_response, expected_tickers

    def _replay_day(
        self,
        date_key: str,
        out_path: Path,
        stats: ReplayStats,
    ) -> None:
        """Replay one trading day and write its cache file."""
        result = self._load_best_raw(date_key)

        if result is None:
            stats.missing_archive_days += 1
            stats.dates_missing.append(date_key)
            logger.warning("%s: no usable archive record found — skipping", date_key)
            return

        raw_response, expected_tickers = result

        try:
            payload = parse_response(raw_response, expected_tickers)
        except ParseError as exc:
            stats.parse_failures += 1
            stats.dates_parse_failed.append(date_key)
            logger.warning("%s: parse failure during replay: %s", date_key, exc)
            return

        # Atomic write: .tmp then rename.
        tmp = out_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False),
            encoding="utf-8",
        )
        tmp.replace(out_path)

        stats.success_count += 1
        stats.dates_written.append(date_key)
        logger.info(
            "%s: replayed %d ticker(s) → %s",
            date_key,
            len(payload["tickers"]),
            out_path,
        )


# ── CLI ───────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m backtester.ai_integration.llm_cache_builder.replay",
        description=(
            "Regenerate AISignalProvider cache files from archived raw LLM "
            "responses without making any new API calls."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--archive-dir",
        required=True,
        metavar="DIR",
        help="Directory containing YYYY-MM-DD.jsonl archive files",
    )
    p.add_argument(
        "--output-dir",
        required=True,
        metavar="DIR",
        help="Directory to write regenerated YYYY-MM-DD.json cache files",
    )
    p.add_argument(
        "--start-date",
        default=None,
        metavar="YYYY-MM-DD",
        help="First date to replay (inclusive)",
    )
    p.add_argument(
        "--end-date",
        default=None,
        metavar="YYYY-MM-DD",
        help="Last date to replay (inclusive)",
    )
    p.add_argument(
        "--date",
        default=None,
        metavar="YYYY-MM-DD",
        help="Replay exactly one date (overrides --start-date / --end-date)",
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing output files (default: skip them)",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG logging",
    )
    return p


def _main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    replayer = ArchiveReplayer(
        archive_dir=args.archive_dir,
        output_dir=args.output_dir,
        overwrite=args.overwrite,
    )

    _BAR = "━" * 56
    print(_BAR)
    print("  Archive Replay  (no API calls)")
    print(_BAR)
    print(f"  Archive dir : {args.archive_dir}")
    print(f"  Output dir  : {args.output_dir}")
    if args.date:
        print(f"  Date        : {args.date}")
    else:
        print(f"  Date range  : {args.start_date or '(all)'} → {args.end_date or '(all)'}")
    print(f"  Overwrite   : {args.overwrite}")
    print(_BAR)
    print()

    stats = replayer.replay(
        start_date=args.start_date,
        end_date=args.end_date,
        date=args.date,
    )

    summary_path = Path(args.output_dir) / "replay_summary.json"
    stats.write_summary(summary_path)

    print(_BAR)
    print("  Replay complete")
    print(_BAR)
    print(f"  Days attempted      : {stats.days_attempted}")
    print(f"  Replayed OK         : {stats.success_count}")
    print(f"  Parse failures      : {stats.parse_failures}")
    print(f"  Skipped (exists)    : {stats.skipped_existing}")
    print(f"  Missing archive     : {stats.missing_archive_days}")
    if stats.dates_written:
        print(f"  Date range written  : {stats.dates_written[0]} → {stats.dates_written[-1]}")
    if stats.dates_parse_failed:
        print(f"  Parse-failed dates  : {stats.dates_parse_failed}")
    print(f"  Summary written     : {summary_path}")
    print(_BAR)

    if stats.parse_failures:
        import sys
        sys.exit(1)


if __name__ == "__main__":
    _main()
