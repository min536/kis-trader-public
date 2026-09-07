"""Tests for backtester.ai_integration.feature_inspector."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backtester.ai_integration.feature_inspector import (
    FieldStats,
    InspectionResult,
    _NumericAccumulator,
    format_report,
    inspect_features,
)


# ── Fixtures ──────────────────────────────────────────────────────────────

def _record(
    date: str = "2024-01-15",
    ticker: str = "005930",
    base_score: float = 4.0,
    decision: str = "approved",
    decision_reason: str = "executed",
    rule_gate: bool = True,
    score_gate: bool = True,
    executed: bool = True,
    candidate_rank: int = 1,
    features: dict | None = None,
) -> dict:
    return {
        "date": date,
        "ticker": ticker,
        "base_score": base_score,
        "candidate_rank": candidate_rank,
        "decision": decision,
        "decision_reason": decision_reason,
        "rule_gate_passed": rule_gate,
        "score_gate_passed": score_gate,
        "executed": executed,
        "features": features or {
            "momentum_quality_score": 0.50,
            "trend_quality_score": 0.60,
            "range_recovery_bonus": 0.20,
            "overheat_penalty": 0.05,
            "pullback_exhaustion_penalty": 0.00,
            "pullback_pct": 1.50,
            "rebound_pct": 2.00,
            "range_recovery_ratio": 1.00,
        },
    }


def _write(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._path = Path(self._tmp.name) / "features.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def _inspect(self, records: list[dict]) -> InspectionResult:
        _write(self._path, records)
        return inspect_features(self._path)


# ── _NumericAccumulator ───────────────────────────────────────────────────

class AccumulatorTests(unittest.TestCase):

    def test_empty_accumulator(self):
        acc = _NumericAccumulator("x")
        s = acc.build()
        self.assertEqual(s.count, 0)
        self.assertEqual(s.mean, 0.0)
        self.assertEqual(s.zero_fraction, 0.0)

    def test_single_value(self):
        acc = _NumericAccumulator("x")
        acc.add(3.0)
        s = acc.build()
        self.assertEqual(s.count, 1)
        self.assertAlmostEqual(s.mean, 3.0)
        self.assertAlmostEqual(s.min, 3.0)
        self.assertAlmostEqual(s.max, 3.0)

    def test_mean_min_max(self):
        acc = _NumericAccumulator("x")
        for v in [1.0, 2.0, 3.0, 4.0]:
            acc.add(v)
        s = acc.build()
        self.assertAlmostEqual(s.mean, 2.5)
        self.assertAlmostEqual(s.min, 1.0)
        self.assertAlmostEqual(s.max, 4.0)

    def test_zero_fraction(self):
        acc = _NumericAccumulator("x")
        for v in [0.0, 0.0, 1.0, 2.0]:
            acc.add(v)
        s = acc.build()
        self.assertAlmostEqual(s.zero_fraction, 0.5)

    def test_all_zeros(self):
        acc = _NumericAccumulator("x")
        for _ in range(5):
            acc.add(0.0)
        s = acc.build()
        self.assertAlmostEqual(s.zero_fraction, 1.0)

    def test_non_numeric_skipped(self):
        acc = _NumericAccumulator("x")
        acc.add("not a number")
        acc.add(None)
        acc.add(2.0)
        s = acc.build()
        self.assertEqual(s.count, 1)
        self.assertAlmostEqual(s.mean, 2.0)

    def test_to_dict_rounds(self):
        acc = _NumericAccumulator("x")
        acc.add(1.123456789)
        s = acc.build()
        d = s.to_dict()
        self.assertEqual(len(str(d["mean"]).split(".")[-1]), 6)


# ── inspect_features — counts and date range ──────────────────────────────

class CountsAndDateRangeTests(_Base):

    def test_total_records(self):
        r = self._inspect([_record("2024-01-15"), _record("2024-01-16")])
        self.assertEqual(r.total_records, 2)

    def test_empty_file(self):
        r = self._inspect([])
        self.assertEqual(r.total_records, 0)
        self.assertIsNone(r.start_date)
        self.assertIsNone(r.end_date)

    def test_date_range(self):
        records = [_record("2024-01-15"), _record("2024-01-20"), _record("2024-01-10")]
        r = self._inspect(records)
        self.assertEqual(r.start_date, "2024-01-10")
        self.assertEqual(r.end_date, "2024-01-20")

    def test_trading_days_count(self):
        records = [
            _record("2024-01-15"),
            _record("2024-01-15"),  # same day, 2 candidates
            _record("2024-01-16"),
        ]
        r = self._inspect(records)
        self.assertEqual(r.trading_days, 2)

    def test_malformed_lines_counted(self):
        with self._path.open("w") as fh:
            fh.write("not json\n")
            fh.write(json.dumps(_record("2024-01-15")) + "\n")
            fh.write("{broken\n")
        r = inspect_features(self._path)
        self.assertEqual(r.malformed_lines, 2)
        self.assertEqual(r.total_records, 1)

    def test_unique_tickers(self):
        records = [
            _record("2024-01-15", ticker="005930"),
            _record("2024-01-15", ticker="000660"),
            _record("2024-01-16", ticker="005930"),
        ]
        r = self._inspect(records)
        self.assertEqual(r.unique_tickers, 2)


# ── Per-day aggregation ───────────────────────────────────────────────────

class PerDayAggregationTests(_Base):

    def test_avg_candidates_per_day(self):
        records = [
            _record("2024-01-15", ticker="005930"),
            _record("2024-01-15", ticker="000660"),
            _record("2024-01-16", ticker="005930"),
        ]
        r = self._inspect(records)
        # Day 1: 2 candidates, Day 2: 1 candidate → avg = 1.5
        self.assertAlmostEqual(r.avg_candidates_per_day, 1.5)

    def test_max_candidates_per_day(self):
        records = [
            _record("2024-01-15", ticker="005930"),
            _record("2024-01-15", ticker="000660"),
            _record("2024-01-15", ticker="006400"),
            _record("2024-01-16", ticker="005930"),
        ]
        r = self._inspect(records)
        self.assertEqual(r.max_candidates_per_day, 3)
        self.assertEqual(r.max_candidates_date, "2024-01-15")

    def test_single_day_avg_equals_count(self):
        records = [_record("2024-01-15", ticker=t) for t in ("A", "B", "C")]
        r = self._inspect(records)
        self.assertAlmostEqual(r.avg_candidates_per_day, 3.0)


# ── Categorical distributions ─────────────────────────────────────────────

class CategoricalDistributionTests(_Base):

    def test_decision_distribution(self):
        records = [
            _record(decision="approved"),
            _record(decision="approved"),
            _record(decision="rejected"),
        ]
        r = self._inspect(records)
        self.assertEqual(r.decision_distribution["approved"], 2)
        self.assertEqual(r.decision_distribution["rejected"], 1)

    def test_decision_reason_distribution(self):
        records = [
            _record(decision_reason="executed"),
            _record(decision_reason="not_selected"),
            _record(decision_reason="not_selected"),
        ]
        r = self._inspect(records)
        self.assertEqual(r.decision_reason_distribution["not_selected"], 2)

    def test_rule_gate_distribution(self):
        records = [
            _record(rule_gate=True),
            _record(rule_gate=True),
            _record(rule_gate=False),
        ]
        r = self._inspect(records)
        self.assertEqual(r.rule_gate_distribution["True"], 2)
        self.assertEqual(r.rule_gate_distribution["False"], 1)

    def test_score_gate_distribution(self):
        records = [_record(score_gate=True)] * 3 + [_record(score_gate=False)]
        r = self._inspect(records)
        self.assertEqual(r.score_gate_distribution["True"], 3)
        self.assertEqual(r.score_gate_distribution["False"], 1)

    def test_executed_distribution(self):
        records = [_record(executed=True), _record(executed=False)]
        r = self._inspect(records)
        self.assertEqual(r.executed_distribution["True"], 1)
        self.assertEqual(r.executed_distribution["False"], 1)


# ── Ticker frequency ──────────────────────────────────────────────────────

class TickerFrequencyTests(_Base):

    def test_ticker_top20_sorted_by_count(self):
        records = (
            [_record(ticker="AAA")] * 5
            + [_record(ticker="BBB")] * 3
            + [_record(ticker="CCC")] * 1
        )
        r = self._inspect(records)
        tickers = [t for t, _ in r.ticker_top20]
        self.assertEqual(tickers[0], "AAA")
        self.assertEqual(tickers[1], "BBB")

    def test_ticker_top20_max_twenty(self):
        records = [_record(ticker=f"T{i:03d}") for i in range(30)]
        r = self._inspect(records)
        self.assertLessEqual(len(r.ticker_top20), 20)


# ── Numeric / zero-fraction stats ─────────────────────────────────────────

class NumericStatsTests(_Base):

    def test_base_score_mean(self):
        records = [
            _record(base_score=2.0),
            _record(base_score=4.0),
        ]
        r = self._inspect(records)
        bs = next(s for s in r.top_level_stats if s.name == "base_score")
        self.assertAlmostEqual(bs.mean, 3.0)

    def test_base_score_min_max(self):
        records = [_record(base_score=b) for b in [1.0, 5.0, 3.0]]
        r = self._inspect(records)
        bs = next(s for s in r.top_level_stats if s.name == "base_score")
        self.assertAlmostEqual(bs.min, 1.0)
        self.assertAlmostEqual(bs.max, 5.0)

    def test_zero_fraction_feature(self):
        features_zero = {
            "momentum_quality_score": 0.0,
            "trend_quality_score": 0.0,
            "range_recovery_bonus": 0.0,
            "overheat_penalty": 0.0,
            "pullback_exhaustion_penalty": 0.0,
            "pullback_pct": 0.0,
            "rebound_pct": 0.0,
            "range_recovery_ratio": 0.0,
        }
        features_nonzero = {k: 1.0 for k in features_zero}
        records = [
            _record(features=features_zero),
            _record(features=features_nonzero),
        ]
        r = self._inspect(records)
        mqs = next(s for s in r.feature_stats if s.name == "momentum_quality_score")
        self.assertAlmostEqual(mqs.zero_fraction, 0.5)

    def test_priority_features_appear_first(self):
        r = self._inspect([_record()])
        feat_names = [s.name for s in r.feature_stats]
        # momentum_quality_score is first priority field
        self.assertEqual(feat_names[0], "momentum_quality_score")

    def test_all_priority_features_present(self):
        r = self._inspect([_record()])
        feat_names = {s.name for s in r.feature_stats}
        for name in (
            "momentum_quality_score",
            "trend_quality_score",
            "range_recovery_bonus",
            "overheat_penalty",
            "pullback_exhaustion_penalty",
        ):
            self.assertIn(name, feat_names)

    def test_extra_feature_keys_included(self):
        """Features beyond the priority list are picked up automatically."""
        r = self._inspect([_record(features={"novel_feature": 0.42})])
        feat_names = {s.name for s in r.feature_stats}
        self.assertIn("novel_feature", feat_names)


# ── to_dict / JSON output ─────────────────────────────────────────────────

class ToDictTests(_Base):

    def test_to_dict_has_required_keys(self):
        r = self._inspect([_record()])
        d = r.to_dict()
        for key in (
            "total_records", "malformed_lines", "start_date", "end_date",
            "trading_days", "avg_candidates_per_day", "max_candidates_per_day",
            "decision_distribution", "rule_gate_distribution",
            "unique_tickers", "ticker_top20", "top_level_stats", "feature_stats",
        ):
            self.assertIn(key, d, f"missing key: {key}")

    def test_to_dict_is_json_serialisable(self):
        r = self._inspect([_record()])
        json.dumps(r.to_dict())  # must not raise

    def test_json_out_file_written(self):
        _write(self._path, [_record()])
        out = Path(self._tmp.name) / "stats.json"
        from backtester.ai_integration.feature_inspector import _main
        _main(["--features", str(self._path), "--json-out", str(out)])
        self.assertTrue(out.exists())
        data = json.loads(out.read_text())
        self.assertEqual(data["total_records"], 1)

    def test_json_feature_stats_shape(self):
        r = self._inspect([_record()])
        d = r.to_dict()
        for fs in d["feature_stats"]:
            for key in ("name", "count", "mean", "min", "max", "zero_fraction"):
                self.assertIn(key, fs)

    def test_ticker_top20_dict_shape(self):
        r = self._inspect([_record(ticker="005930")])
        d = r.to_dict()
        self.assertIn("ticker", d["ticker_top20"][0])
        self.assertIn("count", d["ticker_top20"][0])


# ── format_report ─────────────────────────────────────────────────────────

class FormatReportTests(_Base):

    def test_empty_report_no_crash(self):
        r = self._inspect([])
        report = format_report(r)
        self.assertIn("No records found", report)

    def test_report_contains_total_records(self):
        r = self._inspect([_record()] * 7)
        report = format_report(r)
        self.assertIn("7", report)

    def test_report_contains_date_range(self):
        r = self._inspect([_record("2024-03-01"), _record("2024-03-15")])
        report = format_report(r)
        self.assertIn("2024-03-01", report)
        self.assertIn("2024-03-15", report)

    def test_report_contains_decision_label(self):
        r = self._inspect([_record()])
        report = format_report(r)
        self.assertIn("decision", report)

    def test_report_contains_zero_pct(self):
        r = self._inspect([_record()])
        report = format_report(r)
        self.assertIn("%", report)

    def test_report_contains_ticker(self):
        r = self._inspect([_record(ticker="000660")])
        report = format_report(r)
        self.assertIn("000660", report)

    def test_report_is_string(self):
        r = self._inspect([_record()])
        self.assertIsInstance(format_report(r), str)


if __name__ == "__main__":
    unittest.main()
