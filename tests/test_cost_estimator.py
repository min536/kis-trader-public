"""Tests for backtester.ai_integration.llm_cache_builder.cost_estimator."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backtester.ai_integration.llm_cache_builder.cost_estimator import (
    CostEstimate,
    DayStat,
    SliceSummary,
    _build_recommendation,
    _make_slice,
    _output_tokens,
    _tokens,
    estimate,
    format_report,
)
from backtester.ai_integration.llm_cache_builder.prompt import build_system_prompt


# ── Helpers ───────────────────────────────────────────────────────────────

def _write_jsonl(path: Path, records: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def _make_record(date: str, ticker: str, base_score: float = 2.0) -> dict:
    return {
        "date": date,
        "ticker": ticker,
        "base_score": base_score,
        "features": {
            "momentum_quality_score": 0.50,
            "trend_quality_score": 0.50,
            "range_recovery_bonus": 0.20,
            "overheat_penalty": 0.10,
            "pullback_exhaustion_penalty": 0.05,
        },
        "decision": "approved",
        "rule_gate_passed": True,
        "score_gate_passed": True,
    }


class TokenHelperTests(unittest.TestCase):
    """_tokens and _output_tokens unit tests."""

    def test_tokens_basic(self):
        # 8 characters → 2 tokens
        self.assertEqual(_tokens("12345678"), 2)

    def test_tokens_minimum_one(self):
        # Empty string should not return 0
        self.assertEqual(_tokens(""), 1)

    def test_tokens_proportional(self):
        # Double the text → roughly double the tokens
        short = _tokens("a" * 100)
        long_ = _tokens("a" * 200)
        self.assertEqual(long_, short * 2)

    def test_output_tokens_zero_tickers(self):
        # Overhead only
        self.assertEqual(_output_tokens(0), 8)

    def test_output_tokens_one_ticker(self):
        self.assertEqual(_output_tokens(1), 8 + 18)

    def test_output_tokens_five_tickers(self):
        self.assertEqual(_output_tokens(5), 8 + 5 * 18)

    def test_output_tokens_scales_linearly(self):
        diff = _output_tokens(6) - _output_tokens(5)
        self.assertEqual(diff, 18)


class DayStatTests(unittest.TestCase):
    """DayStat dataclass tests."""

    def test_fields_stored(self):
        ds = DayStat(date="2024-01-15", n_tickers=3, input_tokens=500, output_tokens=62)
        self.assertEqual(ds.date, "2024-01-15")
        self.assertEqual(ds.n_tickers, 3)
        self.assertEqual(ds.input_tokens, 500)
        self.assertEqual(ds.output_tokens, 62)


class SliceSummaryTests(unittest.TestCase):
    """SliceSummary computed properties."""

    def _make(self, days: int, total_in: int, total_out: int) -> SliceSummary:
        return SliceSummary(
            label="test",
            days=days,
            total_input_tokens=total_in,
            total_output_tokens=total_out,
            model_costs={"model-a": 0.50},
        )

    def test_total_tokens(self):
        sl = self._make(3, 1000, 200)
        self.assertEqual(sl.total_tokens, 1200)

    def test_avg_input_tokens(self):
        sl = self._make(4, 1000, 0)
        self.assertAlmostEqual(sl.avg_input_tokens, 250.0)

    def test_avg_output_tokens(self):
        sl = self._make(5, 0, 500)
        self.assertAlmostEqual(sl.avg_output_tokens, 100.0)

    def test_avg_tokens_zero_days(self):
        sl = self._make(0, 0, 0)
        self.assertEqual(sl.avg_input_tokens, 0.0)
        self.assertEqual(sl.avg_output_tokens, 0.0)


class MakeSliceTests(unittest.TestCase):
    """_make_slice: model cost calculation."""

    def _day_stats(self, n: int, input_t: int, output_t: int) -> list[DayStat]:
        return [
            DayStat(date=f"2024-01-{i+1:02d}", n_tickers=2,
                    input_tokens=input_t, output_tokens=output_t)
            for i in range(n)
        ]

    def test_totals_summed_correctly(self):
        stats = self._day_stats(3, input_t=1000, output_t=100)
        sl = _make_slice("3-day", stats)
        self.assertEqual(sl.total_input_tokens, 3000)
        self.assertEqual(sl.total_output_tokens, 300)
        self.assertEqual(sl.days, 3)

    def test_model_cost_keys_present(self):
        stats = self._day_stats(1, input_t=1_000_000, output_t=0)
        sl = _make_slice("full", stats)
        # claude-haiku-3-5: $0.80 per million input tokens
        self.assertAlmostEqual(sl.model_costs["claude-haiku-3-5"], 0.80, places=4)
        self.assertAlmostEqual(sl.model_costs["claude-sonnet-4-5"], 3.00, places=4)
        self.assertAlmostEqual(sl.model_costs["claude-opus-4-5"], 15.00, places=4)

    def test_model_cost_zero_tokens(self):
        sl = _make_slice("empty", [])
        for cost in sl.model_costs.values():
            self.assertEqual(cost, 0.0)

    def test_output_token_cost(self):
        # 1M output tokens with haiku → $4.00
        stats = self._day_stats(1, input_t=0, output_t=1_000_000)
        sl = _make_slice("out", stats)
        self.assertAlmostEqual(sl.model_costs["claude-haiku-3-5"], 4.00, places=4)


class EstimateTests(unittest.TestCase):
    """estimate() integration tests (no API calls)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._features = Path(self._tmp.name) / "features.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, records: list[dict]) -> None:
        _write_jsonl(self._features, records)

    def test_empty_file_returns_empty(self):
        self._write([])
        est = estimate(self._features)
        self.assertEqual(est.all_day_stats, [])
        self.assertIsNone(est.start_date)
        self.assertIsNone(est.end_date)

    def test_single_day_produces_one_stat(self):
        self._write([_make_record("2024-01-15", "005930")])
        est = estimate(self._features)
        self.assertEqual(len(est.all_day_stats), 1)
        self.assertEqual(est.all_day_stats[0].date, "2024-01-15")
        self.assertEqual(est.all_day_stats[0].n_tickers, 1)

    def test_input_tokens_include_system_prompt(self):
        self._write([_make_record("2024-01-15", "005930")])
        est = estimate(self._features)
        sys_tokens = _tokens(build_system_prompt())
        # Input tokens must be >= system prompt tokens
        self.assertGreaterEqual(est.all_day_stats[0].input_tokens, sys_tokens)

    def test_system_prompt_tokens_matches_helper(self):
        self._write([_make_record("2024-01-15", "005930")])
        est = estimate(self._features)
        self.assertEqual(est.system_prompt_tokens, _tokens(build_system_prompt()))

    def test_two_tickers_higher_output_than_one(self):
        self._write([
            _make_record("2024-01-15", "005930"),
            _make_record("2024-01-15", "000660"),
        ])
        est_2 = estimate(self._features)

        self._write([_make_record("2024-01-16", "005930")])
        est_1 = estimate(self._features)

        self.assertGreater(
            est_2.all_day_stats[0].output_tokens,
            est_1.all_day_stats[0].output_tokens,
        )

    def test_multiple_days_sorted(self):
        records = [
            _make_record("2024-01-17", "005930"),
            _make_record("2024-01-15", "000660"),
            _make_record("2024-01-16", "006400"),
        ]
        self._write(records)
        est = estimate(self._features)
        dates = [s.date for s in est.all_day_stats]
        self.assertEqual(dates, sorted(dates))

    def test_start_end_date_filter(self):
        records = [_make_record(f"2024-01-{i:02d}", "005930") for i in range(1, 11)]
        self._write(records)
        est = estimate(self._features, start_date="2024-01-03", end_date="2024-01-07")
        self.assertEqual(len(est.all_day_stats), 5)
        self.assertEqual(est.start_date, "2024-01-03")
        self.assertEqual(est.end_date, "2024-01-07")

    def test_limit_days(self):
        records = [_make_record(f"2024-01-{i:02d}", "005930") for i in range(1, 11)]
        self._write(records)
        est = estimate(self._features, limit_days=3)
        self.assertEqual(len(est.all_day_stats), 3)

    def test_slices_returned(self):
        records = [_make_record(f"2024-01-{i:02d}", "005930") for i in range(1, 15)]
        self._write(records)
        est = estimate(self._features)
        labels = [sl.label for sl in est.slices]
        self.assertIn("3-day preview", labels)
        self.assertIn("10-day preview", labels)
        self.assertIn("full range", labels)

    def test_three_day_slice_uses_first_three(self):
        records = [_make_record(f"2024-01-{i:02d}", "005930") for i in range(1, 6)]
        self._write(records)
        est = estimate(self._features)
        three_day = next(sl for sl in est.slices if sl.label == "3-day preview")
        self.assertEqual(three_day.days, 3)

    def test_full_range_slice_covers_all(self):
        records = [_make_record(f"2024-01-{i:02d}", "005930") for i in range(1, 8)]
        self._write(records)
        est = estimate(self._features)
        full = next(sl for sl in est.slices if sl.label == "full range")
        self.assertEqual(full.days, 7)

    def test_fewer_than_three_days_omits_3day_slice(self):
        records = [_make_record(f"2024-01-0{i}", "005930") for i in range(1, 3)]
        self._write(records)
        est = estimate(self._features)
        # With only 2 days the 3-day preview slice is NOT created (window > data).
        three_day = next((sl for sl in est.slices if sl.label == "3-day preview"), None)
        self.assertIsNone(three_day)

    def test_malformed_lines_skipped(self):
        with open(self._features, "w") as fh:
            fh.write("not json\n")
            fh.write(json.dumps(_make_record("2024-01-15", "005930")) + "\n")
            fh.write("{broken\n")
        est = estimate(self._features)
        self.assertEqual(len(est.all_day_stats), 1)

    def test_model_costs_positive(self):
        self._write([_make_record("2024-01-15", "005930")])
        est = estimate(self._features)
        full = next(sl for sl in est.slices if sl.label == "full range")
        for cost in full.model_costs.values():
            self.assertGreater(cost, 0.0)

    def test_haiku_cheaper_than_opus(self):
        records = [_make_record(f"2024-01-{i:02d}", "005930") for i in range(1, 6)]
        self._write(records)
        est = estimate(self._features)
        full = next(sl for sl in est.slices if sl.label == "full range")
        self.assertLess(
            full.model_costs["claude-haiku-3-5"],
            full.model_costs["claude-opus-4-5"],
        )


class FormatReportTests(unittest.TestCase):
    """format_report() output structure tests."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._features = Path(self._tmp.name) / "features.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def _est_with_days(self, n: int) -> CostEstimate:
        records = [
            _make_record(f"2024-01-{i:02d}", "005930")
            for i in range(1, n + 1)
        ]
        _write_jsonl(self._features, records)
        return estimate(self._features)

    def test_empty_estimate_shows_no_days(self):
        _write_jsonl(self._features, [])
        est = estimate(self._features)
        report = format_report(est)
        self.assertIn("No trading days found", report)

    def test_report_contains_date_range(self):
        est = self._est_with_days(5)
        report = format_report(est)
        self.assertIn("2024-01-01", report)
        self.assertIn("2024-01-05", report)

    def test_report_contains_model_names(self):
        est = self._est_with_days(3)
        report = format_report(est)
        self.assertIn("claude-haiku-3-5", report)
        self.assertIn("claude-sonnet-4-5", report)
        self.assertIn("claude-opus-4-5", report)

    def test_report_contains_slice_labels(self):
        est = self._est_with_days(12)
        report = format_report(est)
        self.assertIn("3-day preview", report)
        self.assertIn("10-day preview", report)
        self.assertIn("full range", report)

    def test_report_mentions_heuristic_note(self):
        est = self._est_with_days(1)
        report = format_report(est)
        self.assertIn("heuristic", report.lower())

    def test_report_is_string(self):
        est = self._est_with_days(2)
        self.assertIsInstance(format_report(est), str)

    def test_report_contains_trading_days_count(self):
        est = self._est_with_days(7)
        report = format_report(est)
        self.assertIn("7", report)

    def test_report_shows_largest_day(self):
        est = self._est_with_days(4)
        report = format_report(est)
        self.assertIn("Largest day", report)

    def test_report_contains_dollar_sign(self):
        est = self._est_with_days(3)
        report = format_report(est)
        self.assertIn("$", report)


class RecommendationTests(unittest.TestCase):
    """_build_recommendation and format_report recommendation section tests."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._features = Path(self._tmp.name) / "features.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def _est_with_days(self, n: int) -> CostEstimate:
        records = [
            _make_record(f"2024-01-{i:02d}", "005930")
            for i in range(1, n + 1)
        ]
        _write_jsonl(self._features, records)
        return estimate(self._features)

    # ── _build_recommendation unit tests ─────────────────────────────────

    def test_empty_estimate_returns_empty_string(self):
        _write_jsonl(self._features, [])
        est = estimate(self._features)
        self.assertEqual(_build_recommendation(est), "")

    def test_cheapest_model_is_haiku(self):
        # haiku is always cheapest given current pricing
        est = self._est_with_days(12)
        rec = _build_recommendation(est)
        self.assertIn("claude-haiku-3-5", rec)
        self.assertIn("Cheapest model", rec)

    def test_smoke_test_cost_present(self):
        est = self._est_with_days(12)
        rec = _build_recommendation(est)
        self.assertIn("3-day smoke test", rec)
        self.assertIn("$", rec)

    def test_pilot_cost_present_when_enough_days(self):
        est = self._est_with_days(12)
        rec = _build_recommendation(est)
        self.assertIn("10 days", rec)

    def test_full_range_cost_present(self):
        est = self._est_with_days(12)
        rec = _build_recommendation(est)
        self.assertIn("Full range", rec)

    def test_staged_guidance_numbers(self):
        # Steps 1, 2, 3 should all appear
        est = self._est_with_days(12)
        rec = _build_recommendation(est)
        self.assertIn("1.", rec)
        self.assertIn("2.", rec)
        self.assertIn("3.", rec)

    def test_parse_stability_reference(self):
        est = self._est_with_days(5)
        rec = _build_recommendation(est)
        self.assertIn("parse", rec.lower())

    def test_day_log_reference(self):
        est = self._est_with_days(5)
        rec = _build_recommendation(est)
        self.assertIn("generation_day_log.jsonl", rec)

    def test_fewer_than_three_days_no_crash(self):
        # Edge case: only 1 day in the data
        est = self._est_with_days(1)
        rec = _build_recommendation(est)
        self.assertIsInstance(rec, str)
        self.assertGreater(len(rec), 0)

    def test_fewer_than_ten_days_no_pilot_slice(self):
        # With 5 days there is no 10-day preview slice;
        # recommendation should still render without error
        est = self._est_with_days(5)
        rec = _build_recommendation(est)
        self.assertIsInstance(rec, str)
        # Graceful fallback message present
        self.assertIn("gradually", rec)

    # ── format_report integration tests ──────────────────────────────────

    def test_report_contains_recommendation_header(self):
        est = self._est_with_days(12)
        report = format_report(est)
        self.assertIn("Recommendation", report)

    def test_report_recommendation_comes_after_pricing(self):
        est = self._est_with_days(12)
        report = format_report(est)
        pricing_pos = report.index("Pricing basis")
        rec_pos = report.index("Recommendation")
        self.assertGreater(rec_pos, pricing_pos)

    def test_report_recommendation_absent_for_empty(self):
        _write_jsonl(self._features, [])
        est = estimate(self._features)
        report = format_report(est)
        self.assertNotIn("Recommendation", report)

    def test_report_smoke_test_dollar_amount_is_numeric(self):
        # The smoke-test line must contain a parseable dollar amount
        est = self._est_with_days(12)
        report = format_report(est)
        # Extract the smoke-test line
        smoke_line = next(
            l for l in report.splitlines() if "smoke test" in l.lower()
        )
        # Should contain a dollar sign followed by digits
        import re
        self.assertRegex(smoke_line, r"\$\d+\.\d+")


if __name__ == "__main__":
    unittest.main()
