"""Offline LLM batch cache generator.

``BatchCacheGenerator`` is the main orchestrator.  It reads a JSONL file
of exported candidate features (one record per ticker per trading day),
calls an LLM once per day to produce structured signals, archives every
raw response, and writes validated per-day JSON cache files in the format
that ``AISignalProvider`` expects.

Design goals
------------
* Fully offline — the LLM is never called inside the backtest loop.
* Deterministic storage — every raw response is archived before any
  post-processing, so a run can always be replayed from the archive.
* Graceful degradation — failed days can fall back to the deterministic
  heuristic from ``cache_builder.py`` rather than silently producing gaps.
* Minimal dependencies — only the standard library plus the optional
  ``anthropic`` package (imported lazily in ``client.py``).
"""
from __future__ import annotations

import json
import logging
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backtester.ai_integration.cache_builder import (
    build_day_payload as _heuristic_day_payload,
)
from backtester.ai_integration.llm_cache_builder.archive import ResponseArchive
from backtester.ai_integration.llm_cache_builder.client import LLMClient
from backtester.ai_integration.llm_cache_builder.parser import ParseError, parse_response
from backtester.ai_integration.llm_cache_builder.prompt import (
    build_correction_prompt,
    build_system_prompt,
    build_user_prompt,
)

logger = logging.getLogger(__name__)

__all__ = ["BatchCacheGenerator", "GenerationStats"]


# ── Stats dataclass ───────────────────────────────────────────────────────


_NO_LLM_WARNING = (
    "NO SUCCESSFUL LLM DAYS — results should not be interpreted as LLM performance"
)

_QUALITY_LLM = "llm"          # every day came from the LLM
_QUALITY_MIXED = "mixed"       # some LLM, some heuristic
_QUALITY_HEURISTIC = "heuristic"  # every day was a heuristic fallback
_QUALITY_EMPTY = "empty"       # nothing was attempted or written


@dataclass
class GenerationStats:
    """Running counters produced by a generation run."""

    days_attempted: int = 0
    days_success: int = 0
    days_heuristic_fallback: int = 0
    days_skipped_exists: int = 0
    days_failed: int = 0
    total_llm_calls: int = 0
    total_retries: int = 0
    total_parse_failures: int = 0   # parse errors across ALL attempts
    total_api_failures: int = 0     # network / auth / rate-limit errors
    #: Date strings that were written (success or heuristic).
    dates_written: list[str] = field(default_factory=list)
    #: Date strings that failed (only populated when fallback_to_heuristic=False).
    dates_failed: list[str] = field(default_factory=list)
    #: One record per attempted day — see ``write_day_log()``.
    day_log: list[dict[str, Any]] = field(default_factory=list)

    # ── derived properties ────────────────────────────────────────────────

    @property
    def is_fully_heuristic(self) -> bool:
        """True when every attempted day fell back to the heuristic (0 LLM days)."""
        return self.days_attempted > 0 and self.days_success == 0

    @property
    def cache_source_quality(self) -> str:
        """One of 'llm' | 'mixed' | 'heuristic' | 'empty'."""
        written = self.days_success + self.days_heuristic_fallback
        if written == 0:
            return _QUALITY_EMPTY
        if self.days_heuristic_fallback == 0:
            return _QUALITY_LLM
        if self.days_success == 0:
            return _QUALITY_HEURISTIC
        return _QUALITY_MIXED

    @property
    def interpretation_warning(self) -> str | None:
        """Non-None string when the cache should not be read as LLM output."""
        if self.is_fully_heuristic:
            return _NO_LLM_WARNING
        if self.cache_source_quality == _QUALITY_MIXED:
            pct = round(
                self.days_heuristic_fallback
                / max(self.days_success + self.days_heuristic_fallback, 1)
                * 100,
            )
            return (
                f"{pct}% of days are heuristic fallback — "
                "interpret mixed-source metrics with caution"
            )
        return None

    # ── serialisation ─────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "days_attempted": self.days_attempted,
            "days_success": self.days_success,
            "days_heuristic_fallback": self.days_heuristic_fallback,
            "days_skipped_exists": self.days_skipped_exists,
            "days_failed": self.days_failed,
            "total_llm_calls": self.total_llm_calls,
            "total_retries": self.total_retries,
            "total_parse_failures": self.total_parse_failures,
            "total_api_failures": self.total_api_failures,
            "cache_source_quality": self.cache_source_quality,
            "interpretation_warning": self.interpretation_warning,
        }

    def write_day_log(self, path: "str | Path") -> None:
        """Write ``generation_day_log.jsonl`` — one JSON line per attempted day.

        Each line contains::

            {
              "date":              "2024-01-15",
              "llm_success":       true,
              "heuristic_fallback": false,
              "failed":            false,
              "retries_used":      1,
              "parse_failures":    1,
              "api_failures":      0,
              "output_path":       "data/ai_signals/2024-01-15.json"
            }

        Writing is best-effort — failure is logged but never re-raised.
        """
        import json  # noqa: PLC0415
        from pathlib import Path as _Path  # noqa: PLC0415
        try:
            p = _Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            lines = [json.dumps(rec, ensure_ascii=False) for rec in self.day_log]
            p.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        except Exception as exc:  # pragma: no cover
            logger.warning("failed to write generation_day_log.jsonl: %s", exc)

    def write_summary(self, path: "str | Path") -> None:
        """Write a ``generation_summary.json`` file at *path*.

        This file is read by the experiment runner to annotate manifests
        with cache provenance.  Writing is best-effort — failure is logged
        but never re-raised.
        """
        import json  # noqa: PLC0415
        from pathlib import Path as _Path  # noqa: PLC0415
        try:
            p = _Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("failed to write generation_summary.json: %s", exc)

    def __str__(self) -> str:
        parts = [
            f"{self.days_success}/{self.days_attempted} days OK (LLM)",
        ]
        if self.days_heuristic_fallback:
            parts.append(f"{self.days_heuristic_fallback} heuristic fallback")
        if self.days_skipped_exists:
            parts.append(f"{self.days_skipped_exists} skipped (exists)")
        if self.days_failed:
            parts.append(f"{self.days_failed} FAILED")
        if self.total_retries:
            parts.append(f"{self.total_retries} retries")
        if self.total_parse_failures:
            parts.append(f"{self.total_parse_failures} parse errors")
        if self.total_api_failures:
            parts.append(f"{self.total_api_failures} API errors")
        return " | ".join(parts)


# ── Generator ─────────────────────────────────────────────────────────────


class BatchCacheGenerator:
    """Read exported features, call LLM per day, write AISignalProvider cache.

    Parameters
    ----------
    client:
        Any object satisfying the ``LLMClient`` protocol
        (``AnthropicClient`` or ``MockLLMClient``).
    output_dir:
        Directory for ``YYYY-MM-DD.json`` output files.
    archive_dir:
        Directory for ``YYYY-MM-DD.jsonl`` raw response archives.
    max_retries:
        Total LLM calls allowed per day (1 = no retries).
    fallback_to_heuristic:
        When True (default), use the deterministic heuristic on final
        parse failure rather than skipping/raising.
    overwrite:
        When False (default), skip dates whose cache file already exists.
    """

    def __init__(
        self,
        client: LLMClient,
        *,
        output_dir: str | Path,
        archive_dir: str | Path,
        max_retries: int = 3,
        fallback_to_heuristic: bool = True,
        overwrite: bool = False,
    ) -> None:
        self._client = client
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._archive = ResponseArchive(archive_dir)
        self._max_retries = max(1, int(max_retries))
        self._fallback = fallback_to_heuristic
        self._overwrite = overwrite
        self._system_prompt = build_system_prompt()

    # ── Public API ────────────────────────────────────────────────────────

    def generate(
        self,
        features_path: str | Path,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        limit_days: int | None = None,
    ) -> GenerationStats:
        """Process a features JSONL file and write per-day cache files.

        Parameters
        ----------
        features_path:
            Path to the ``candidate_features.jsonl`` written by
            ``CandidateFeatureExporter``.
        start_date / end_date:
            Optional ISO strings (``"YYYY-MM-DD"``) to restrict the date
            range processed.  Inclusive on both ends.
        limit_days:
            Stop after attempting this many trading dates.  Useful for
            smoke-testing with a small slice before committing to a full run.

        Returns
        -------
        GenerationStats
            Counters for the completed run.
        """
        by_date = self._load_features(features_path)
        stats = GenerationStats()

        for date_key in sorted(by_date):
            # Date-range filter.
            if start_date and date_key < start_date:
                continue
            if end_date and date_key > end_date:
                continue
            # Limit.
            if limit_days is not None and stats.days_attempted >= limit_days:
                break

            out_path = self._output_dir / f"{date_key}.json"
            if out_path.exists() and not self._overwrite:
                stats.days_skipped_exists += 1
                logger.debug("skip %s — cache file exists", date_key)
                continue

            stats.days_attempted += 1
            self._process_day(date_key, by_date[date_key], out_path, stats)

        return stats

    # ── Internals ─────────────────────────────────────────────────────────

    @staticmethod
    def _load_features(
        features_path: str | Path,
    ) -> dict[str, list[dict[str, Any]]]:
        """Read JSONL and group records by date string."""
        features_path = Path(features_path)
        by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
        skipped = 0
        with open(features_path, encoding="utf-8") as fh:
            for raw_line in fh:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    rec = json.loads(raw_line)
                except json.JSONDecodeError:
                    skipped += 1
                    continue
                date_key = str(rec.get("date", "")).strip()
                if not date_key:
                    skipped += 1
                    continue
                by_date[date_key].append(rec)
        if skipped:
            warnings.warn(
                f"llm_cache_builder: skipped {skipped} malformed/dateless "
                "records while loading features",
                stacklevel=3,
            )
        return dict(by_date)

    def _process_day(
        self,
        date_key: str,
        records: list[dict[str, Any]],
        out_path: Path,
        stats: GenerationStats,
    ) -> None:
        """Process one trading day and write its cache file."""
        # De-duplicate by ticker (keep highest base_score).
        by_ticker: dict[str, dict[str, Any]] = {}
        for rec in records:
            ticker = str(rec.get("ticker", ""))
            if not ticker:
                continue
            existing = by_ticker.get(ticker)
            if existing is None or float(rec.get("base_score", 0.0)) > float(
                existing.get("base_score", 0.0)
            ):
                by_ticker[ticker] = rec

        if not by_ticker:
            logger.warning("%s: no valid tickers — skipping", date_key)
            return

        records_deduped = list(by_ticker.values())
        expected_tickers = list(by_ticker.keys())

        payload, used_heuristic, day_counts = self._call_with_retries(
            date_key, records_deduped, expected_tickers, stats
        )

        if payload is None:
            stats.days_failed += 1
            stats.dates_failed.append(date_key)
            stats.day_log.append({
                "date": date_key,
                "llm_success": False,
                "heuristic_fallback": False,
                "failed": True,
                "retries_used": day_counts["retries_used"],
                "parse_failures": day_counts["parse_failures"],
                "api_failures": day_counts["api_failures"],
                "output_path": None,
            })
            logger.error(
                "%s: all %d attempt(s) failed — day skipped",
                date_key,
                self._max_retries,
            )
            return

        # Write cache file atomically (write to .tmp then rename).
        tmp_path = out_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False),
            encoding="utf-8",
        )
        tmp_path.replace(out_path)

        stats.dates_written.append(date_key)
        stats.day_log.append({
            "date": date_key,
            "llm_success": not used_heuristic,
            "heuristic_fallback": used_heuristic,
            "failed": False,
            "retries_used": day_counts["retries_used"],
            "parse_failures": day_counts["parse_failures"],
            "api_failures": day_counts["api_failures"],
            "output_path": str(out_path),
        })
        if used_heuristic:
            stats.days_heuristic_fallback += 1
            logger.info("%s: wrote %d tickers [heuristic fallback]", date_key, len(payload["tickers"]))
        else:
            stats.days_success += 1
            logger.info("%s: wrote %d tickers [LLM]", date_key, len(payload["tickers"]))

    def _call_with_retries(
        self,
        date_key: str,
        records: list[dict[str, Any]],
        expected_tickers: list[str],
        stats: GenerationStats,
    ) -> tuple[dict[str, Any] | None, bool, dict[str, int]]:
        """Call the LLM up to ``max_retries`` times for one day.

        Returns
        -------
        tuple[payload | None, used_heuristic, day_counts]
            ``payload`` is None only when every attempt failed and
            ``fallback_to_heuristic`` is False.
            ``day_counts`` has keys ``retries_used``, ``parse_failures``,
            ``api_failures`` scoped to this single day.
        """
        day_counts: dict[str, int] = {
            "retries_used": 0,
            "parse_failures": 0,
            "api_failures": 0,
        }
        messages: list[dict[str, str]] = [
            {"role": "user", "content": build_user_prompt(date_key, records)},
        ]

        for attempt in range(1, self._max_retries + 1):
            stats.total_llm_calls += 1
            if attempt > 1:
                stats.total_retries += 1
                day_counts["retries_used"] += 1

            # ── LLM call ──────────────────────────────────────────────────
            try:
                raw = self._client.complete(self._system_prompt, messages)
            except Exception as exc:  # network / rate-limit / auth error
                stats.total_api_failures += 1
                day_counts["api_failures"] += 1
                logger.warning(
                    "%s attempt %d/%d: LLM call error: %s",
                    date_key,
                    attempt,
                    self._max_retries,
                    exc,
                )
                self._archive.save(
                    date_key, attempt, "", None,
                    success=False, error=str(exc),
                )
                if attempt < self._max_retries:
                    # Retry without a correction turn (transient error).
                    continue
                break

            # ── Parse & validate ──────────────────────────────────────────
            try:
                parsed = parse_response(raw, expected_tickers)
                self._archive.save(
                    date_key, attempt, raw, parsed, success=True,
                )
                return parsed, False, day_counts  # success

            except ParseError as exc:
                stats.total_parse_failures += 1
                day_counts["parse_failures"] += 1
                logger.warning(
                    "%s attempt %d/%d: parse error: %s",
                    date_key,
                    attempt,
                    self._max_retries,
                    exc,
                )
                self._archive.save(
                    date_key, attempt, raw, None,
                    success=False, error=str(exc),
                )
                if attempt < self._max_retries:
                    # Append assistant turn + correction user turn.
                    messages = messages + [
                        {"role": "assistant", "content": raw},
                        {"role": "user", "content": build_correction_prompt(str(exc))},
                    ]

        # ── All attempts exhausted ─────────────────────────────────────────
        if self._fallback:
            logger.warning(
                "%s: falling back to heuristic after %d failed attempt(s)",
                date_key,
                self._max_retries,
            )
            return _heuristic_day_payload(records), True, day_counts

        return None, False, day_counts
