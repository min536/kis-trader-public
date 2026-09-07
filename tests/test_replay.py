"""Tests for backtester.ai_integration.llm_cache_builder.replay.

Every test is fully offline — no API calls, no LLM client.
Archive fixtures are written as JSONL by hand to match the format
that ResponseArchive.save() produces.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backtester.ai_integration.llm_cache_builder.replay import (
    ArchiveReplayer,
    ReplayStats,
)


# ── Fixture helpers ───────────────────────────────────────────────────────

def _valid_raw() -> str:
    """Minimal raw LLM response that parse_response() accepts."""
    return json.dumps({
        "regime_multiplier": 1.05,
        "tickers": {
            "005930": {
                "delta": 1.5,
                "veto": False,
                "veto_confidence": 0.1,
                "veto_reason": None,
                "rank": 1,
            }
        },
    })


def _valid_parsed() -> dict:
    return {
        "regime_multiplier": 1.05,
        "tickers": {
            "005930": {
                "delta": 1.5,
                "veto": False,
                "veto_confidence": 0.1,
                "veto_reason": None,
                "rank": 1,
            }
        },
    }


def _write_archive(archive_dir: Path, date_key: str, records: list[dict]) -> None:
    """Write a YYYY-MM-DD.jsonl archive file with the given attempt records."""
    path = archive_dir / f"{date_key}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def _success_record(date_key: str, attempt: int = 1) -> dict:
    return {
        "date_key": date_key,
        "attempt": attempt,
        "timestamp": "2024-06-01T12:00:00+00:00",
        "success": True,
        "raw_response": _valid_raw(),
        "parsed": _valid_parsed(),
        "error": None,
    }


def _failure_record(date_key: str, attempt: int = 1) -> dict:
    return {
        "date_key": date_key,
        "attempt": attempt,
        "timestamp": "2024-06-01T12:00:00+00:00",
        "success": False,
        "raw_response": "definitely not json",
        "parsed": None,
        "error": "no JSON object found in response",
    }


# ── ReplayStats tests ─────────────────────────────────────────────────────

class ReplayStatsTests(unittest.TestCase):

    def test_default_all_zero(self):
        s = ReplayStats()
        self.assertEqual(s.days_attempted, 0)
        self.assertEqual(s.success_count, 0)
        self.assertEqual(s.parse_failures, 0)
        self.assertEqual(s.skipped_existing, 0)
        self.assertEqual(s.missing_archive_days, 0)

    def test_to_dict_keys(self):
        s = ReplayStats(days_attempted=3, success_count=2, parse_failures=1)
        d = s.to_dict()
        for key in (
            "days_attempted", "success_count", "parse_failures",
            "skipped_existing", "missing_archive_days",
            "dates_written", "dates_parse_failed", "dates_missing",
        ):
            self.assertIn(key, d)

    def test_str_basic(self):
        s = ReplayStats(days_attempted=5, success_count=4, parse_failures=1)
        self.assertIn("4/5", str(s))
        self.assertIn("parse failure", str(s))

    def test_write_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "replay_summary.json"
            s = ReplayStats(days_attempted=2, success_count=2)
            s.write_summary(path)
            self.assertTrue(path.exists())
            data = json.loads(path.read_text())
            self.assertEqual(data["days_attempted"], 2)
            self.assertEqual(data["success_count"], 2)


# ── ArchiveReplayer.replay — happy path ───────────────────────────────────

class ReplayHappyPathTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._archive = Path(self._tmp.name) / "archive"
        self._archive.mkdir()
        self._output = Path(self._tmp.name) / "output"

    def tearDown(self):
        self._tmp.cleanup()

    def test_replays_single_day_successfully(self):
        date_key = "2024-01-15"
        _write_archive(self._archive, date_key, [_success_record(date_key)])

        r = ArchiveReplayer(self._archive, self._output)
        stats = r.replay()

        self.assertEqual(stats.success_count, 1)
        self.assertEqual(stats.days_attempted, 1)
        self.assertEqual(stats.parse_failures, 0)
        self.assertIn(date_key, stats.dates_written)

    def test_output_file_is_written(self):
        date_key = "2024-01-15"
        _write_archive(self._archive, date_key, [_success_record(date_key)])

        ArchiveReplayer(self._archive, self._output).replay()

        out = self._output / "2024-01-15.json"
        self.assertTrue(out.exists())
        payload = json.loads(out.read_text())
        self.assertIn("regime_multiplier", payload)
        self.assertIn("tickers", payload)
        self.assertIn("005930", payload["tickers"])

    def test_output_schema_matches_aiprovider_format(self):
        """Replayed file must have the exact shape AISignalProvider expects."""
        date_key = "2024-01-20"
        _write_archive(self._archive, date_key, [_success_record(date_key)])
        ArchiveReplayer(self._archive, self._output).replay()

        payload = json.loads((self._output / f"{date_key}.json").read_text())
        self.assertIsInstance(payload["regime_multiplier"], float)
        self.assertIsInstance(payload["tickers"], dict)
        for ticker_data in payload["tickers"].values():
            for field in ("delta", "veto", "veto_confidence", "veto_reason", "rank"):
                self.assertIn(field, ticker_data)

    def test_replays_multiple_days(self):
        for i in range(1, 4):
            d = f"2024-01-{i:02d}"
            _write_archive(self._archive, d, [_success_record(d)])

        stats = ArchiveReplayer(self._archive, self._output).replay()

        self.assertEqual(stats.success_count, 3)
        self.assertEqual(stats.days_attempted, 3)

    def test_uses_last_successful_record(self):
        """When a day has failure then success, the success record is used."""
        date_key = "2024-01-15"
        _write_archive(self._archive, date_key, [
            _failure_record(date_key, attempt=1),
            _success_record(date_key, attempt=2),
        ])

        stats = ArchiveReplayer(self._archive, self._output).replay()

        self.assertEqual(stats.success_count, 1)
        self.assertTrue((self._output / "2024-01-15.json").exists())

    def test_single_date_filter(self):
        for d in ("2024-01-10", "2024-01-11", "2024-01-12"):
            _write_archive(self._archive, d, [_success_record(d)])

        stats = ArchiveReplayer(self._archive, self._output).replay(date="2024-01-11")

        self.assertEqual(stats.success_count, 1)
        self.assertIn("2024-01-11", stats.dates_written)
        self.assertFalse((self._output / "2024-01-10.json").exists())

    def test_date_range_filter(self):
        for i in range(1, 6):
            d = f"2024-01-{i:02d}"
            _write_archive(self._archive, d, [_success_record(d)])

        stats = ArchiveReplayer(self._archive, self._output).replay(
            start_date="2024-01-02", end_date="2024-01-04"
        )

        self.assertEqual(stats.success_count, 3)
        for d in ("2024-01-02", "2024-01-03", "2024-01-04"):
            self.assertIn(d, stats.dates_written)
        for d in ("2024-01-01", "2024-01-05"):
            self.assertNotIn(d, stats.dates_written)

    def test_output_dir_created_if_missing(self):
        deep_output = Path(self._tmp.name) / "nested" / "output"
        self.assertFalse(deep_output.exists())
        date_key = "2024-01-15"
        _write_archive(self._archive, date_key, [_success_record(date_key)])
        ArchiveReplayer(self._archive, deep_output).replay()
        self.assertTrue(deep_output.exists())


# ── Skip / overwrite behaviour ────────────────────────────────────────────

class ReplaySkipOverwriteTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._archive = Path(self._tmp.name) / "archive"
        self._archive.mkdir()
        self._output = Path(self._tmp.name) / "output"
        self._output.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_skips_existing_file_by_default(self):
        date_key = "2024-01-15"
        _write_archive(self._archive, date_key, [_success_record(date_key)])
        existing = self._output / "2024-01-15.json"
        existing.write_text('{"sentinel": true}')

        stats = ArchiveReplayer(self._archive, self._output).replay()

        self.assertEqual(stats.skipped_existing, 1)
        self.assertEqual(stats.days_attempted, 0)
        # File must NOT have been overwritten.
        self.assertTrue(json.loads(existing.read_text()).get("sentinel"))

    def test_overwrites_when_flag_set(self):
        date_key = "2024-01-15"
        _write_archive(self._archive, date_key, [_success_record(date_key)])
        existing = self._output / "2024-01-15.json"
        existing.write_text('{"sentinel": true}')

        stats = ArchiveReplayer(self._archive, self._output, overwrite=True).replay()

        self.assertEqual(stats.success_count, 1)
        payload = json.loads(existing.read_text())
        self.assertNotIn("sentinel", payload)
        self.assertIn("regime_multiplier", payload)

    def test_skipped_count_increments_per_file(self):
        for d in ("2024-01-01", "2024-01-02"):
            _write_archive(self._archive, d, [_success_record(d)])
            (self._output / f"{d}.json").write_text("{}")

        stats = ArchiveReplayer(self._archive, self._output).replay()
        self.assertEqual(stats.skipped_existing, 2)
        self.assertEqual(stats.days_attempted, 0)


# ── Failure paths ─────────────────────────────────────────────────────────

class ReplayFailureTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._archive = Path(self._tmp.name) / "archive"
        self._archive.mkdir()
        self._output = Path(self._tmp.name) / "output"

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_archive_file_counted(self):
        # Request a date with no archive file at all.
        r = ArchiveReplayer(self._archive, self._output)
        stats = r.replay(date="2024-01-15")

        self.assertEqual(stats.missing_archive_days, 1)
        self.assertIn("2024-01-15", stats.dates_missing)
        self.assertEqual(stats.success_count, 0)

    def test_archive_with_only_failures_counted_as_missing(self):
        date_key = "2024-01-15"
        _write_archive(self._archive, date_key, [_failure_record(date_key)])

        stats = ArchiveReplayer(self._archive, self._output).replay()

        self.assertEqual(stats.missing_archive_days, 1)
        self.assertFalse((self._output / "2024-01-15.json").exists())

    def test_parse_failure_counted_no_file_written(self):
        """Archive has a success record but the raw text fails current parser."""
        date_key = "2024-01-15"
        bad_record = {
            "date_key": date_key,
            "attempt": 1,
            "timestamp": "2024-06-01T12:00:00+00:00",
            "success": True,
            # raw_response is missing the 'rank' field — triggers ParseError
            "raw_response": json.dumps({
                "regime_multiplier": 1.0,
                "tickers": {
                    "005930": {
                        "delta": 0.0,
                        "veto": False,
                        "veto_confidence": 0.0,
                        "veto_reason": None,
                        # rank intentionally omitted
                    }
                },
            }),
            "parsed": {
                "regime_multiplier": 1.0,
                "tickers": {"005930": {}},
            },
            "error": None,
        }
        _write_archive(self._archive, date_key, [bad_record])

        stats = ArchiveReplayer(self._archive, self._output).replay()

        self.assertEqual(stats.parse_failures, 1)
        self.assertIn(date_key, stats.dates_parse_failed)
        self.assertFalse((self._output / "2024-01-15.json").exists())

    def test_corrupted_jsonl_lines_skipped_gracefully(self):
        date_key = "2024-01-15"
        path = self._archive / f"{date_key}.jsonl"
        with path.open("w") as fh:
            fh.write("not valid json\n")
            fh.write(json.dumps(_success_record(date_key)) + "\n")
            fh.write("{broken\n")

        stats = ArchiveReplayer(self._archive, self._output).replay()
        self.assertEqual(stats.success_count, 1)

    def test_empty_archive_file_counted_as_missing(self):
        date_key = "2024-01-15"
        (self._archive / f"{date_key}.jsonl").write_text("")

        stats = ArchiveReplayer(self._archive, self._output).replay()
        self.assertEqual(stats.missing_archive_days, 1)


# ── No API calls guarantee ────────────────────────────────────────────────

class ReplayNoApiCallsTests(unittest.TestCase):
    """Replay must never touch the Anthropic client."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._archive = Path(self._tmp.name) / "archive"
        self._archive.mkdir()
        self._output = Path(self._tmp.name) / "output"

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_anthropic_import_during_replay(self):
        """Importing replay must not pull in the anthropic package."""
        import sys
        # anthropic may or may not be installed; what matters is replay
        # doesn't call it. We patch it out to be sure.
        with patch.dict(sys.modules, {"anthropic": None}):
            # Re-importing with anthropic blocked must not raise.
            from backtester.ai_integration.llm_cache_builder import replay  # noqa: F401

    def test_replay_does_not_call_any_http(self):
        """Monkey-patch socket to confirm no network activity."""
        import socket

        def _no_connect(*_a, **_kw):
            raise AssertionError("replay must not open network connections")

        date_key = "2024-01-15"
        _write_archive(self._archive, date_key, [_success_record(date_key)])

        original = socket.socket.connect
        socket.socket.connect = _no_connect
        try:
            stats = ArchiveReplayer(self._archive, self._output).replay()
        finally:
            socket.socket.connect = original

        self.assertEqual(stats.success_count, 1)


# ── Summary file ──────────────────────────────────────────────────────────

class ReplaySummaryTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._archive = Path(self._tmp.name) / "archive"
        self._archive.mkdir()
        self._output = Path(self._tmp.name) / "output"

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, dates: list[str]) -> ReplayStats:
        for d in dates:
            _write_archive(self._archive, d, [_success_record(d)])
        r = ArchiveReplayer(self._archive, self._output)
        stats = r.replay()
        stats.write_summary(self._output / "replay_summary.json")
        return stats

    def test_summary_file_written(self):
        self._run(["2024-01-15"])
        self.assertTrue((self._output / "replay_summary.json").exists())

    def test_summary_counts_correct(self):
        self._run(["2024-01-15", "2024-01-16"])
        data = json.loads((self._output / "replay_summary.json").read_text())
        self.assertEqual(data["days_attempted"], 2)
        self.assertEqual(data["success_count"], 2)
        self.assertEqual(data["parse_failures"], 0)

    def test_summary_dates_written_list(self):
        self._run(["2024-01-15", "2024-01-16"])
        data = json.loads((self._output / "replay_summary.json").read_text())
        self.assertEqual(sorted(data["dates_written"]), ["2024-01-15", "2024-01-16"])

    def test_summary_missing_days_recorded(self):
        # Explicitly request a date with no archive file via --date.
        # Range-based discovery only scans files that exist, so missing
        # days are only surfaced when a specific date is requested.
        r = ArchiveReplayer(self._archive, self._output)
        stats = r.replay(date="2024-01-16")
        self.assertEqual(stats.missing_archive_days, 1)
        self.assertIn("2024-01-16", stats.dates_missing)


# ── Determinism ───────────────────────────────────────────────────────────

class ReplayDeterminismTests(unittest.TestCase):
    """Same archive → identical output on repeated runs."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._archive = Path(self._tmp.name) / "archive"
        self._archive.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_two_runs_produce_identical_output(self):
        date_key = "2024-01-15"
        _write_archive(self._archive, date_key, [_success_record(date_key)])

        out1 = Path(self._tmp.name) / "out1"
        out2 = Path(self._tmp.name) / "out2"

        ArchiveReplayer(self._archive, out1).replay()
        ArchiveReplayer(self._archive, out2).replay()

        content1 = (out1 / "2024-01-15.json").read_text()
        content2 = (out2 / "2024-01-15.json").read_text()
        self.assertEqual(content1, content2)


if __name__ == "__main__":
    unittest.main()
