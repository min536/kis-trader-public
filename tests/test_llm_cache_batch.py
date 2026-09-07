"""Tests for backtester.ai_integration.llm_cache_builder batch pipeline.

Covers the response archive, MockLLMClient, and BatchCacheGenerator.
No real LLM calls are made — all tests are fully offline.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backtester.ai_integration.llm_cache_builder.archive import ResponseArchive
from backtester.ai_integration.llm_cache_builder.batch import (
    BatchCacheGenerator,
    GenerationStats,
)
from backtester.ai_integration.llm_cache_builder.client import MockLLMClient

from llm_cache_builder_testkit import (
    _rec,
    _valid_response,
    _veto_response,
    _write_jsonl,
)


# ── Archive tests ─────────────────────────────────────────────────────────


class ArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._archive = ResponseArchive(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_save_creates_jsonl_file(self) -> None:
        self._archive.save("2024-01-02", 1, "raw", None, success=False, error="err")
        path = Path(self._tmp.name) / "2024-01-02.jsonl"
        self.assertTrue(path.exists())

    def test_load_returns_saved_records(self) -> None:
        self._archive.save("2024-01-02", 1, "raw", {"a": 1}, success=True)
        records = self._archive.load("2024-01-02")
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["success"])

    def test_load_returns_empty_for_missing_date(self) -> None:
        result = self._archive.load("2099-01-01")
        self.assertEqual(result, [])

    def test_multiple_attempts_appended(self) -> None:
        self._archive.save("2024-01-02", 1, "r1", None, success=False, error="e")
        self._archive.save("2024-01-02", 2, "r2", {"ok": 1}, success=True)
        records = self._archive.load("2024-01-02")
        self.assertEqual(len(records), 2)
        self.assertFalse(records[0]["success"])
        self.assertTrue(records[1]["success"])

    def test_last_success_returns_parsed_payload(self) -> None:
        payload = {"regime_multiplier": 1.0, "tickers": {}}
        self._archive.save("2024-01-02", 1, "raw", payload, success=True)
        result = self._archive.last_success("2024-01-02")
        self.assertEqual(result, payload)

    def test_last_success_returns_none_when_no_success(self) -> None:
        self._archive.save("2024-01-02", 1, "r", None, success=False, error="e")
        self.assertIsNone(self._archive.last_success("2024-01-02"))

    def test_last_success_returns_most_recent(self) -> None:
        p1 = {"regime_multiplier": 0.9, "tickers": {}}
        p2 = {"regime_multiplier": 1.1, "tickers": {}}
        self._archive.save("2024-01-02", 1, "r1", p1, success=True)
        self._archive.save("2024-01-02", 2, "r2", p2, success=True)
        result = self._archive.last_success("2024-01-02")
        self.assertEqual(result["regime_multiplier"], 1.1)

    def test_dates_with_success_returns_sorted_list(self) -> None:
        self._archive.save("2024-01-03", 1, "r", {"a": 1}, success=True)
        self._archive.save("2024-01-02", 1, "r", {"a": 1}, success=True)
        self._archive.save("2024-01-04", 1, "r", None, success=False, error="e")
        dates = self._archive.dates_with_success()
        self.assertEqual(dates, ["2024-01-02", "2024-01-03"])

    def test_record_contains_timestamp(self) -> None:
        self._archive.save("2024-01-02", 1, "raw", None, success=False, error="e")
        rec = self._archive.load("2024-01-02")[0]
        self.assertIn("timestamp", rec)
        self.assertIsInstance(rec["timestamp"], str)


# ── MockLLMClient tests ───────────────────────────────────────────────────


class MockClientTests(unittest.TestCase):
    def test_returns_responses_in_order(self) -> None:
        client = MockLLMClient(["a", "b", "c"])
        self.assertEqual(client.complete("sys", [{"role": "user", "content": "q"}]), "a")
        self.assertEqual(client.complete("sys", [{"role": "user", "content": "q"}]), "b")

    def test_records_calls(self) -> None:
        client = MockLLMClient(["r"])
        client.complete("sys", [{"role": "user", "content": "q"}])
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["system"], "sys")

    def test_exhausted_raises_runtime_error(self) -> None:
        client = MockLLMClient(["only_one"])
        client.complete("sys", [])
        with self.assertRaises(RuntimeError):
            client.complete("sys", [])


# ── BatchCacheGenerator tests ─────────────────────────────────────────────


class BatchGeneratorHappyPathTests(unittest.TestCase):
    """Single-day and multi-day happy-path scenarios."""

    def _make_generator(
        self,
        responses: list[str],
        tmp: Path,
        *,
        fallback: bool = True,
    ) -> BatchCacheGenerator:
        client = MockLLMClient(responses)
        return BatchCacheGenerator(
            client,
            output_dir=tmp / "cache",
            archive_dir=tmp / "archive",
            max_retries=3,
            fallback_to_heuristic=fallback,
        )

    def test_single_day_writes_cache_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            records = [_rec("2024-01-02", t) for t in ("AAA", "BBB")]
            jsonl = tmp / "f.jsonl"
            _write_jsonl(jsonl, records)
            response = _valid_response(["AAA", "BBB"])
            gen = self._make_generator([response], tmp)
            stats = gen.generate(jsonl)
            self.assertTrue((tmp / "cache" / "2024-01-02.json").exists())
            self.assertEqual(stats.days_success, 1)

    def test_cache_file_is_loadable_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            records = [_rec("2024-01-02", "AAA")]
            jsonl = tmp / "f.jsonl"
            _write_jsonl(jsonl, records)
            gen = self._make_generator([_valid_response(["AAA"])], tmp)
            gen.generate(jsonl)
            payload = json.loads((tmp / "cache" / "2024-01-02.json").read_text())
            self.assertIn("regime_multiplier", payload)
            self.assertIn("tickers", payload)
            self.assertIn("AAA", payload["tickers"])

    def test_multi_day_writes_correct_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            records = (
                [_rec("2024-01-02", "A"), _rec("2024-01-02", "B")]
                + [_rec("2024-01-03", "A"), _rec("2024-01-03", "B")]
                + [_rec("2024-01-04", "A")]
            )
            jsonl = tmp / "f.jsonl"
            _write_jsonl(jsonl, records)
            responses = [
                _valid_response(["A", "B"]),
                _valid_response(["A", "B"]),
                _valid_response(["A"]),
            ]
            gen = self._make_generator(responses, tmp)
            stats = gen.generate(jsonl)
            self.assertEqual(stats.days_success, 3)

    def test_limit_days_stops_early(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            records = [_rec(f"2024-01-{d:02d}", "A") for d in range(2, 8)]
            jsonl = tmp / "f.jsonl"
            _write_jsonl(jsonl, records)
            responses = [_valid_response(["A"])] * 10
            gen = self._make_generator(responses, tmp)
            stats = gen.generate(jsonl, limit_days=3)
            self.assertEqual(stats.days_attempted, 3)

    def test_start_end_date_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            dates = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
            records = [_rec(d, "A") for d in dates]
            jsonl = tmp / "f.jsonl"
            _write_jsonl(jsonl, records)
            responses = [_valid_response(["A"])] * 2
            gen = self._make_generator(responses, tmp)
            stats = gen.generate(jsonl, start_date="2024-01-03", end_date="2024-01-04")
            self.assertEqual(stats.days_attempted, 2)

    def test_skip_existing_file_when_overwrite_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            records = [_rec("2024-01-02", "A")]
            jsonl = tmp / "f.jsonl"
            _write_jsonl(jsonl, records)
            gen = self._make_generator([_valid_response(["A"])], tmp)
            gen.generate(jsonl)
            # Second run — no responses available, should skip.
            gen2 = self._make_generator([], tmp)
            stats = gen2.generate(jsonl)
            self.assertEqual(stats.days_skipped_exists, 1)
            self.assertEqual(stats.days_attempted, 0)

    def test_overwrite_replaces_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            records = [_rec("2024-01-02", "A")]
            jsonl = tmp / "f.jsonl"
            _write_jsonl(jsonl, records)
            client = MockLLMClient([
                _valid_response(["A"]),  # first run
                _valid_response(["A"]),  # second run (overwrite)
            ])
            gen = BatchCacheGenerator(
                client,
                output_dir=tmp / "cache",
                archive_dir=tmp / "archive",
                overwrite=True,
            )
            gen.generate(jsonl)
            gen.generate(jsonl)
            self.assertEqual(len(client.calls), 2)


class BatchGeneratorRetryTests(unittest.TestCase):
    """Retry and fallback behaviour."""

    def test_retry_on_parse_failure_then_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            records = [_rec("2024-01-02", "A")]
            jsonl = tmp / "f.jsonl"
            _write_jsonl(jsonl, records)
            responses = [
                "not json at all",            # attempt 1 fails
                _valid_response(["A"]),        # attempt 2 succeeds
            ]
            client = MockLLMClient(responses)
            gen = BatchCacheGenerator(
                client,
                output_dir=tmp / "cache",
                archive_dir=tmp / "archive",
                max_retries=3,
            )
            stats = gen.generate(jsonl)
            self.assertEqual(stats.days_success, 1)
            self.assertEqual(stats.total_retries, 1)
            self.assertEqual(len(client.calls), 2)

    def test_correction_turn_appended_to_messages(self) -> None:
        """On retry, the conversation messages list should grow."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            records = [_rec("2024-01-02", "A")]
            jsonl = tmp / "f.jsonl"
            _write_jsonl(jsonl, records)
            client = MockLLMClient([
                "bad json",
                _valid_response(["A"]),
            ])
            gen = BatchCacheGenerator(
                client,
                output_dir=tmp / "cache",
                archive_dir=tmp / "archive",
                max_retries=3,
            )
            gen.generate(jsonl)
            # Second call should include assistant + correction user turns.
            second_call_messages = client.calls[1]["messages"]
            self.assertGreater(len(second_call_messages), 1)
            roles = [m["role"] for m in second_call_messages]
            self.assertIn("assistant", roles)

    def test_all_retries_exhausted_falls_back_to_heuristic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            records = [_rec("2024-01-02", t) for t in ("A", "B")]
            jsonl = tmp / "f.jsonl"
            _write_jsonl(jsonl, records)
            client = MockLLMClient(["bad json"] * 3)
            gen = BatchCacheGenerator(
                client,
                output_dir=tmp / "cache",
                archive_dir=tmp / "archive",
                max_retries=3,
                fallback_to_heuristic=True,
            )
            stats = gen.generate(jsonl)
            self.assertEqual(stats.days_heuristic_fallback, 1)
            self.assertEqual(stats.days_success, 0)
            # Cache file still written (from heuristic).
            self.assertTrue((tmp / "cache" / "2024-01-02.json").exists())

    def test_all_retries_exhausted_no_fallback_skips_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            records = [_rec("2024-01-02", "A")]
            jsonl = tmp / "f.jsonl"
            _write_jsonl(jsonl, records)
            client = MockLLMClient(["bad"] * 3)
            gen = BatchCacheGenerator(
                client,
                output_dir=tmp / "cache",
                archive_dir=tmp / "archive",
                max_retries=3,
                fallback_to_heuristic=False,
            )
            stats = gen.generate(jsonl)
            self.assertEqual(stats.days_failed, 1)
            self.assertEqual(stats.days_success, 0)
            self.assertFalse((tmp / "cache" / "2024-01-02.json").exists())
            self.assertIn("2024-01-02", stats.dates_failed)

    def test_archive_records_failed_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            records = [_rec("2024-01-02", "A")]
            jsonl = tmp / "f.jsonl"
            _write_jsonl(jsonl, records)
            client = MockLLMClient(["bad", _valid_response(["A"])])
            gen = BatchCacheGenerator(
                client,
                output_dir=tmp / "cache",
                archive_dir=tmp / "archive",
                max_retries=3,
            )
            gen.generate(jsonl)
            archive = ResponseArchive(tmp / "archive")
            records_arch = archive.load("2024-01-02")
            self.assertEqual(len(records_arch), 2)
            self.assertFalse(records_arch[0]["success"])
            self.assertTrue(records_arch[1]["success"])


class BatchGeneratorVetoTests(unittest.TestCase):
    """Verify that veto signals survive the full parse → write → load cycle."""

    def test_veto_ticker_preserved_in_cache_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            records = [_rec("2024-01-02", t) for t in ("HOT", "COOL")]
            jsonl = tmp / "f.jsonl"
            _write_jsonl(jsonl, records)
            response = _veto_response(["HOT", "COOL"], veto_ticker="HOT")
            client = MockLLMClient([response])
            gen = BatchCacheGenerator(
                client,
                output_dir=tmp / "cache",
                archive_dir=tmp / "archive",
            )
            gen.generate(jsonl)
            payload = json.loads((tmp / "cache" / "2024-01-02.json").read_text())
            self.assertTrue(payload["tickers"]["HOT"]["veto"])
            self.assertFalse(payload["tickers"]["COOL"]["veto"])
            self.assertEqual(payload["tickers"]["HOT"]["veto_reason"], "overheat")


class BatchGeneratorStatsTests(unittest.TestCase):
    def test_stats_to_dict_has_all_keys(self) -> None:
        s = GenerationStats()
        d = s.to_dict()
        for key in (
            "days_attempted", "days_success", "days_heuristic_fallback",
            "days_failed", "total_llm_calls", "total_retries",
        ):
            self.assertIn(key, d)

    def test_stats_str_contains_summary(self) -> None:
        s = GenerationStats()
        s.days_success = 5
        s.days_attempted = 5
        result = str(s)
        self.assertIn("5", result)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
