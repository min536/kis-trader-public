"""Tests for backtester.ai_integration.llm_cache_builder.

Covers parser, archive, prompt, batch generator, and the MockLLMClient.
No real LLM calls are made — all tests are fully offline.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

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


# ── Integration: llm cache → AISignalProvider round-trip ──────────────────


class LLMCacheProviderRoundTripTests(unittest.TestCase):
    """Write LLM-generated cache files and load them with AISignalProvider."""

    def test_provider_reads_llm_generated_cache(self) -> None:
        from backtester.ai_integration.provider import AISignalProvider

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            tickers = ["AAA", "BBB", "CCC"]
            records = [_rec("2024-01-02", t) for t in tickers]
            jsonl = tmp / "f.jsonl"
            _write_jsonl(jsonl, records)
            client = MockLLMClient([_valid_response(tickers)])
            gen = BatchCacheGenerator(
                client,
                output_dir=tmp / "cache",
                archive_dir=tmp / "archive",
            )
            gen.generate(jsonl)

            provider = AISignalProvider(str(tmp / "cache"), mode="combined")
            for t in tickers:
                delta = provider.get_score_delta("2024-01-02", t)
                self.assertIsInstance(delta, float)
            rm = provider.get_regime_multiplier("2024-01-02")
            self.assertGreaterEqual(rm, 0.8)
            self.assertLessEqual(rm, 1.2)

    def test_veto_readable_by_provider(self) -> None:
        from backtester.ai_integration.provider import AISignalProvider

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

            # Low threshold so veto fires.
            provider = AISignalProvider(
                str(tmp / "cache"), mode="veto", veto_threshold=0.5
            )
            veto = provider.get_veto("2024-01-02", "HOT")
            self.assertIsNotNone(veto)
            self.assertIsNone(provider.get_veto("2024-01-02", "COOL"))


# ── GenerationStats observability tests ──────────────────────────────────


class GenerationStatsObservabilityTests(unittest.TestCase):
    """Tests for the new provenance / quality / warning fields."""

    def _stats(self, *, success: int = 0, heuristic: int = 0,
               parse_fail: int = 0, api_fail: int = 0,
               attempted: int | None = None) -> GenerationStats:
        s = GenerationStats()
        s.days_success = success
        s.days_heuristic_fallback = heuristic
        s.days_attempted = attempted if attempted is not None else success + heuristic
        s.total_parse_failures = parse_fail
        s.total_api_failures = api_fail
        return s

    # cache_source_quality ─────────────────────────────────────────────────

    def test_quality_llm_when_all_llm(self) -> None:
        s = self._stats(success=5)
        self.assertEqual(s.cache_source_quality, "llm")

    def test_quality_heuristic_when_all_fallback(self) -> None:
        s = self._stats(heuristic=5)
        self.assertEqual(s.cache_source_quality, "heuristic")

    def test_quality_mixed_when_both(self) -> None:
        s = self._stats(success=3, heuristic=2)
        self.assertEqual(s.cache_source_quality, "mixed")

    def test_quality_empty_when_nothing_written(self) -> None:
        s = GenerationStats()
        self.assertEqual(s.cache_source_quality, "empty")

    # is_fully_heuristic ───────────────────────────────────────────────────

    def test_is_fully_heuristic_true_when_all_fallback(self) -> None:
        s = self._stats(heuristic=3)
        self.assertTrue(s.is_fully_heuristic)

    def test_is_fully_heuristic_false_when_some_llm(self) -> None:
        s = self._stats(success=2, heuristic=1)
        self.assertFalse(s.is_fully_heuristic)

    def test_is_fully_heuristic_false_when_nothing_attempted(self) -> None:
        s = GenerationStats()
        self.assertFalse(s.is_fully_heuristic)

    # interpretation_warning ───────────────────────────────────────────────

    def test_warning_none_when_all_llm(self) -> None:
        s = self._stats(success=5)
        self.assertIsNone(s.interpretation_warning)

    def test_warning_present_when_fully_heuristic(self) -> None:
        s = self._stats(heuristic=5)
        w = s.interpretation_warning
        self.assertIsNotNone(w)
        self.assertIn("NO SUCCESSFUL LLM DAYS", w)

    def test_warning_present_when_mixed(self) -> None:
        s = self._stats(success=2, heuristic=8)
        w = s.interpretation_warning
        self.assertIsNotNone(w)
        self.assertIn("heuristic fallback", w)

    # to_dict ──────────────────────────────────────────────────────────────

    def test_to_dict_includes_new_fields(self) -> None:
        s = self._stats(success=2, parse_fail=1, api_fail=3)
        d = s.to_dict()
        self.assertIn("total_parse_failures", d)
        self.assertIn("total_api_failures", d)
        self.assertIn("cache_source_quality", d)
        self.assertIn("interpretation_warning", d)
        self.assertEqual(d["total_parse_failures"], 1)
        self.assertEqual(d["total_api_failures"], 3)

    # write_summary ────────────────────────────────────────────────────────

    def test_write_summary_creates_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "generation_summary.json"
            s = self._stats(success=3, heuristic=1)
            s.write_summary(path)
            self.assertTrue(path.exists())
            loaded = json.loads(path.read_text())
            self.assertEqual(loaded["days_success"], 3)
            self.assertEqual(loaded["days_heuristic_fallback"], 1)
            self.assertIn("cache_source_quality", loaded)

    def test_write_summary_creates_parent_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "dir" / "summary.json"
            GenerationStats().write_summary(path)
            self.assertTrue(path.exists())

    # Counter tracking ─────────────────────────────────────────────────────

    def test_parse_failures_counted_per_attempt(self) -> None:
        """Each parse-error attempt increments total_parse_failures."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            records = [_rec("2024-01-02", "A")]
            jsonl = tmp / "f.jsonl"
            _write_jsonl(jsonl, records)
            # 2 bad + 1 good → 2 parse failures, 1 success
            client = MockLLMClient(["bad", "bad", _valid_response(["A"])])
            gen = BatchCacheGenerator(
                client,
                output_dir=tmp / "cache",
                archive_dir=tmp / "archive",
                max_retries=3,
            )
            stats = gen.generate(jsonl)
            self.assertEqual(stats.total_parse_failures, 2)
            self.assertEqual(stats.days_success, 1)

    def test_api_failures_counted(self) -> None:
        """API errors increment total_api_failures."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            records = [_rec("2024-01-02", "A")]
            jsonl = tmp / "f.jsonl"
            _write_jsonl(jsonl, records)

            class _BoomClient:
                calls: list = []
                def complete(self, system, messages):  # noqa: ANN
                    self.calls.append(1)
                    raise RuntimeError("credit balance too low")

            boom = _BoomClient()
            gen = BatchCacheGenerator(
                boom,  # type: ignore[arg-type]
                output_dir=tmp / "cache",
                archive_dir=tmp / "archive",
                max_retries=3,
                fallback_to_heuristic=True,
            )
            stats = gen.generate(jsonl)
            self.assertGreater(stats.total_api_failures, 0)


# ── generation_summary.json round-trip via batch pipeline ────────────────


class GenerationSummaryFileTests(unittest.TestCase):
    """write_summary integrates with the full generate() flow."""

    def test_generate_then_write_summary_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            records = [_rec("2024-01-02", t) for t in ("A", "B")]
            jsonl = tmp / "f.jsonl"
            _write_jsonl(jsonl, records)
            # 1 parse failure, then success
            client = MockLLMClient(["bad json", _valid_response(["A", "B"])])
            gen = BatchCacheGenerator(
                client,
                output_dir=tmp / "cache",
                archive_dir=tmp / "archive",
                max_retries=3,
            )
            stats = gen.generate(jsonl)
            summary_path = tmp / "cache" / "generation_summary.json"
            stats.write_summary(summary_path)

            loaded = json.loads(summary_path.read_text())
            self.assertEqual(loaded["days_success"], 1)
            self.assertEqual(loaded["total_parse_failures"], 1)
            self.assertEqual(loaded["cache_source_quality"], "llm")
            self.assertIsNone(loaded["interpretation_warning"])

    def test_fully_heuristic_summary_contains_warning(self) -> None:
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
                fallback_to_heuristic=True,
            )
            stats = gen.generate(jsonl)
            summary_path = tmp / "cache" / "generation_summary.json"
            stats.write_summary(summary_path)

            loaded = json.loads(summary_path.read_text())
            self.assertEqual(loaded["cache_source_quality"], "heuristic")
            self.assertIn("NO SUCCESSFUL LLM DAYS", loaded["interpretation_warning"])


# ── Per-day log tests ─────────────────────────────────────────────────────


class DayLogTests(unittest.TestCase):
    """Tests for the per-day generation log."""

    def _gen(self, tmp: Path, responses: list[str],
             *, max_retries: int = 3, fallback: bool = True) -> "BatchCacheGenerator":
        client = MockLLMClient(responses)
        return BatchCacheGenerator(
            client,
            output_dir=tmp / "cache",
            archive_dir=tmp / "archive",
            max_retries=max_retries,
            fallback_to_heuristic=fallback,
        )

    def test_successful_day_logged_correctly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _write_jsonl(tmp / "f.jsonl", [_rec("2024-01-02", "A")])
            stats = self._gen(tmp, [_valid_response(["A"])]).generate(tmp / "f.jsonl")
            self.assertEqual(len(stats.day_log), 1)
            entry = stats.day_log[0]
            self.assertEqual(entry["date"], "2024-01-02")
            self.assertTrue(entry["llm_success"])
            self.assertFalse(entry["heuristic_fallback"])
            self.assertFalse(entry["failed"])
            self.assertEqual(entry["retries_used"], 0)
            self.assertEqual(entry["parse_failures"], 0)
            self.assertEqual(entry["api_failures"], 0)
            self.assertIsNotNone(entry["output_path"])
            self.assertIn("2024-01-02.json", entry["output_path"])

    def test_heuristic_day_logged_correctly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _write_jsonl(tmp / "f.jsonl", [_rec("2024-01-02", "A")])
            stats = self._gen(tmp, ["bad"] * 3, fallback=True).generate(tmp / "f.jsonl")
            entry = stats.day_log[0]
            self.assertFalse(entry["llm_success"])
            self.assertTrue(entry["heuristic_fallback"])
            self.assertFalse(entry["failed"])
            self.assertIsNotNone(entry["output_path"])

    def test_failed_day_logged_correctly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _write_jsonl(tmp / "f.jsonl", [_rec("2024-01-02", "A")])
            stats = self._gen(tmp, ["bad"] * 3, fallback=False).generate(tmp / "f.jsonl")
            entry = stats.day_log[0]
            self.assertFalse(entry["llm_success"])
            self.assertFalse(entry["heuristic_fallback"])
            self.assertTrue(entry["failed"])
            self.assertIsNone(entry["output_path"])

    def test_retries_and_parse_failures_per_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _write_jsonl(tmp / "f.jsonl", [_rec("2024-01-02", "A")])
            # 2 bad attempts then success → 2 parse failures, 2 retries used
            stats = self._gen(tmp, ["bad", "bad", _valid_response(["A"])]).generate(tmp / "f.jsonl")
            entry = stats.day_log[0]
            self.assertEqual(entry["parse_failures"], 2)
            self.assertEqual(entry["retries_used"], 2)
            self.assertTrue(entry["llm_success"])

    def test_multi_day_log_has_one_entry_per_day(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            records = [_rec("2024-01-02", "A"), _rec("2024-01-03", "A"), _rec("2024-01-04", "A")]
            _write_jsonl(tmp / "f.jsonl", records)
            responses = [_valid_response(["A"])] * 3
            stats = self._gen(tmp, responses).generate(tmp / "f.jsonl")
            self.assertEqual(len(stats.day_log), 3)
            dates = [e["date"] for e in stats.day_log]
            self.assertEqual(sorted(dates), ["2024-01-02", "2024-01-03", "2024-01-04"])

    def test_skipped_days_not_in_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _write_jsonl(tmp / "f.jsonl", [_rec("2024-01-02", "A")])
            gen = self._gen(tmp, [_valid_response(["A"])])
            gen.generate(tmp / "f.jsonl")
            # Second run — file exists, overwrite=False → skipped
            gen2 = self._gen(tmp, [])
            stats2 = gen2.generate(tmp / "f.jsonl")
            self.assertEqual(len(stats2.day_log), 0)
            self.assertEqual(stats2.days_skipped_exists, 1)

    def test_write_day_log_creates_jsonl_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _write_jsonl(tmp / "f.jsonl", [_rec("2024-01-02", "A"), _rec("2024-01-03", "A")])
            stats = self._gen(tmp, [_valid_response(["A"])] * 2).generate(tmp / "f.jsonl")
            log_path = tmp / "day_log.jsonl"
            stats.write_day_log(log_path)
            self.assertTrue(log_path.exists())
            lines = [l for l in log_path.read_text().splitlines() if l.strip()]
            self.assertEqual(len(lines), 2)

    def test_write_day_log_each_line_is_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            records = [_rec(f"2024-01-{d:02d}", "A") for d in range(2, 5)]
            _write_jsonl(tmp / "f.jsonl", records)
            stats = self._gen(tmp, [_valid_response(["A"])] * 3).generate(tmp / "f.jsonl")
            log_path = tmp / "day_log.jsonl"
            stats.write_day_log(log_path)
            for line in log_path.read_text().splitlines():
                if line.strip():
                    parsed = json.loads(line)
                    for key in ("date", "llm_success", "heuristic_fallback",
                                "failed", "retries_used", "parse_failures",
                                "api_failures", "output_path"):
                        self.assertIn(key, parsed)

    def test_write_day_log_empty_when_no_days_attempted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            log_path = tmp / "day_log.jsonl"
            GenerationStats().write_day_log(log_path)
            self.assertTrue(log_path.exists())
            self.assertEqual(log_path.read_text().strip(), "")

    def test_mixed_day_log_distinguishes_llm_from_heuristic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            records = [_rec("2024-01-02", "A"), _rec("2024-01-03", "A")]
            _write_jsonl(tmp / "f.jsonl", records)
            # Day 1: success. Day 2: all bad → heuristic.
            responses = [_valid_response(["A"]), "bad", "bad", "bad"]
            stats = self._gen(tmp, responses, fallback=True).generate(tmp / "f.jsonl")
            llm_entries = [e for e in stats.day_log if e["llm_success"]]
            heuristic_entries = [e for e in stats.day_log if e["heuristic_fallback"]]
            self.assertEqual(len(llm_entries), 1)
            self.assertEqual(len(heuristic_entries), 1)
            self.assertEqual(llm_entries[0]["date"], "2024-01-02")
            self.assertEqual(heuristic_entries[0]["date"], "2024-01-03")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
