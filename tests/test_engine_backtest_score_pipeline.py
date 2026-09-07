import json
import tempfile
import unittest
from pathlib import Path

from backtester.engine_backtest.cli import main
from backtester.engine_backtest.parity_score_pipeline import (
    build_score_analysis_summary,
    build_score_tuning_dataset_summary,
    build_tuning_scaffold,
)


class EngineBacktestScorePipelineTests(unittest.TestCase):
    def test_analysis_and_scaffold_with_synthetic_datasets(self) -> None:
        datasets = {
            "positive_rows": [
                {
                    "date": "2026-04-11",
                    "symbol": "AAA",
                    "score": 3.35,
                    "score_margin_to_threshold": 0.15,
                    "min_score_threshold": 3.2,
                },
                {
                    "date": "2026-04-12",
                    "symbol": "BBB",
                    "score": 3.28,
                    "score_margin_to_threshold": 0.08,
                    "min_score_threshold": 3.2,
                },
            ],
            "negative_rows": [
                {
                    "date": "2026-04-11",
                    "symbol": "CCC",
                    "score": 3.18,
                    "score_margin_to_threshold": -0.02,
                    "min_score_threshold": 3.2,
                },
                {
                    "date": "2026-04-13",
                    "symbol": "DDD",
                    "score": 3.05,
                    "score_margin_to_threshold": -0.15,
                    "min_score_threshold": 3.2,
                },
            ],
            "conditional_rows": [
                {
                    "date": "2026-04-14",
                    "symbol": "EEE",
                    "score": None,
                    "score_margin_to_threshold": None,
                    "min_score_threshold": 3.2,
                }
            ],
            "errors": [],
        }
        datasets["summary"] = build_score_tuning_dataset_summary(datasets)

        analysis = build_score_analysis_summary(datasets)
        scaffold = build_tuning_scaffold(datasets, analysis)

        self.assertFalse(analysis["insufficient_data"])
        self.assertGreaterEqual((analysis["review_band"] or {}).get("effective", 0), 0.05)
        self.assertIn("2026-04-11", analysis["borderline_dates"])
        self.assertEqual(len(scaffold["scenarios"]), 3)
        self.assertIn(3.2, scaffold["current_thresholds"])

    def test_cli_pipeline_handles_no_recommended_dates(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            output_dir = tmp / "pipeline_outputs"
            report_a = tmp / "parity_20260410.json"
            report_b = tmp / "parity_20260413.json"
            base_report = {
                "account": "mock_12345678_01",
                "session": "REGULAR",
                "parity_level": "low",
                "engine_backtest_source": {"mode": "historical_signal_proxy"},
                "summary_counts": {
                    "engine_backtest": {
                        "seeded_position_count": 2,
                        "buy_signal_count": 0,
                        "buy_scored_candidate_count": 0,
                        "executed_buy_count": 0,
                        "selected_buy_score": None,
                        "buy_capacity": {
                            "positions_before_buy_pass": 3,
                            "max_positions": 3,
                            "available_slots_before_buy_pass": 0,
                        },
                        "buy_score_stats": {
                            "min_score_threshold": 3.2,
                            "signal_scores": {},
                            "score_rejected": {},
                            "scored_candidates": {},
                        },
                        "buy_sizing": {},
                        "dominant_rule_failure": None,
                    }
                },
            }
            report_a_payload = {
                **base_report,
                "date_range": {"start": "2026-04-10"},
                "summary_counts": {
                    "engine_backtest": {
                        **base_report["summary_counts"]["engine_backtest"],
                        "buy_diagnostics": {
                            "stage": "capacity_blocked",
                            "summary": "capacity_blocked",
                        },
                    }
                },
            }
            report_b_payload = {
                **base_report,
                "date_range": {"start": "2026-04-13"},
                "summary_counts": {
                    "engine_backtest": {
                        **base_report["summary_counts"]["engine_backtest"],
                        "buy_capacity": {
                            "positions_before_buy_pass": 0,
                            "max_positions": 3,
                            "available_slots_before_buy_pass": 3,
                        },
                        "buy_diagnostics": {
                            "stage": "pre_rule_blocked",
                            "summary": "pre_rule_blocked",
                        },
                    }
                },
            }
            report_a.write_text(
                json.dumps(report_a_payload, ensure_ascii=False),
                encoding="utf-8",
            )
            report_b.write_text(
                json.dumps(report_b_payload, ensure_ascii=False),
                encoding="utf-8",
            )

            main(
                [
                    "parity-score-pipeline",
                    "--report-files",
                    f"{report_a},{report_b}",
                    "--output-dir",
                    str(output_dir),
                ]
            )

            batch_json = output_dir / "parity_batch_summary.json"
            dataset_json = output_dir / "score_dataset_summary.json"
            analysis_json = output_dir / "score_analysis_summary.json"
            scaffold_json = output_dir / "score_tuning_scaffold.json"

            self.assertTrue(batch_json.exists())
            self.assertTrue(dataset_json.exists())
            self.assertTrue(analysis_json.exists())
            self.assertTrue(scaffold_json.exists())

            batch_payload = json.loads(batch_json.read_text(encoding="utf-8"))
            self.assertEqual(batch_payload["score_tuning_candidates"]["recommended_dates"], [])

            dataset_payload = json.loads(dataset_json.read_text(encoding="utf-8"))
            self.assertEqual(dataset_payload["positive_set"]["sample_count"], 0)
            self.assertEqual(dataset_payload["negative_set"]["sample_count"], 0)

            analysis_payload = json.loads(analysis_json.read_text(encoding="utf-8"))
            self.assertTrue(analysis_payload["insufficient_data"])


if __name__ == "__main__":
    unittest.main()
