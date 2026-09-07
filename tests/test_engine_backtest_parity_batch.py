import json
import tempfile
import unittest
from pathlib import Path

from backtester.engine_backtest.cli import main
from backtester.engine_backtest.parity_batch import build_parity_batch_summary


def _make_report(
    *,
    date: str,
    stage: str,
    slots: int | None,
    buy_signal_count: int,
    buy_scored_candidate_count: int,
    executed_buy_count: int,
    dominant_rule_failure: dict | None = None,
    rejected_score_max: float | None = None,
    selected_buy_score: float | None = None,
    sizing_block_reason: str | None = None,
) -> dict:
    return {
        "parity_level": "low",
        "date_range": {"start": date},
        "engine_backtest_source": {"mode": "historical_signal_proxy"},
        "summary_counts": {
            "engine_backtest": {
                "seeded_position_count": 2,
                "buy_signal_count": buy_signal_count,
                "buy_scored_candidate_count": buy_scored_candidate_count,
                "executed_buy_count": executed_buy_count,
                "selected_buy_score": selected_buy_score,
                "buy_capacity": {
                    "available_slots_before_buy_pass": slots,
                    "positions_before_buy_pass": 1,
                    "max_positions": 3,
                },
                "buy_score_stats": {
                    "min_score_threshold": 3.2,
                    "signal_scores": {"max": 3.1},
                    "score_rejected": {"max": rejected_score_max},
                    "scored_candidates": {"max": 4.0},
                },
                "buy_sizing": {
                    "block_reason_code": sizing_block_reason,
                },
                "dominant_rule_failure": dominant_rule_failure,
                "buy_diagnostics": {
                    "stage": stage,
                    "primary_rejection_reason": "score_below_min",
                    "summary": stage,
                },
            }
        },
    }


class EngineBacktestParityBatchTests(unittest.TestCase):
    def test_build_parity_batch_summary_groups_and_recommends_dates(self) -> None:
        reports = [
            _make_report(
                date="2026-04-10",
                stage="capacity_blocked",
                slots=0,
                buy_signal_count=0,
                buy_scored_candidate_count=0,
                executed_buy_count=0,
            ),
            _make_report(
                date="2026-04-11",
                stage="score_blocked",
                slots=1,
                buy_signal_count=4,
                buy_scored_candidate_count=0,
                executed_buy_count=0,
                rejected_score_max=3.1,
            ),
            _make_report(
                date="2026-04-12",
                stage="executed_buy",
                slots=1,
                buy_signal_count=3,
                buy_scored_candidate_count=2,
                executed_buy_count=1,
                selected_buy_score=4.2,
            ),
            _make_report(
                date="2026-04-13",
                stage="rule_blocked",
                slots=1,
                buy_signal_count=0,
                buy_scored_candidate_count=0,
                executed_buy_count=0,
                dominant_rule_failure={
                    "rule": "controlled_down_day",
                    "fail_count": 7,
                    "pass_rate": 0.22,
                },
            ),
            _make_report(
                date="2026-04-14",
                stage="sizing_blocked",
                slots=1,
                buy_signal_count=2,
                buy_scored_candidate_count=1,
                executed_buy_count=0,
                sizing_block_reason="cash_insufficient",
            ),
            _make_report(
                date="2026-04-15",
                stage="pre_rule_blocked",
                slots=1,
                buy_signal_count=0,
                buy_scored_candidate_count=0,
                executed_buy_count=0,
            ),
            _make_report(
                date="2026-04-16",
                stage="unknown_zero_buy",
                slots=1,
                buy_signal_count=0,
                buy_scored_candidate_count=0,
                executed_buy_count=0,
            ),
        ]

        summary = build_parity_batch_summary(reports)

        self.assertEqual(summary["stage_groups"]["capacity_issue_dates"], ["2026-04-10"])
        self.assertEqual(summary["stage_groups"]["score_issue_dates"], ["2026-04-11"])
        self.assertEqual(summary["stage_groups"]["executed_buy_dates"], ["2026-04-12"])
        self.assertEqual(summary["stage_groups"]["rule_issue_dates"], ["2026-04-13"])
        self.assertEqual(summary["score_tuning_candidates"]["include_primary_dates"], ["2026-04-11", "2026-04-12"])
        self.assertEqual(summary["score_tuning_candidates"]["include_conditional_dates"], ["2026-04-13"])
        self.assertIn("2026-04-10", summary["score_tuning_candidates"]["excluded_dates"])
        self.assertIn("2026-04-15", summary["score_tuning_candidates"]["excluded_dates"])

    def test_cli_parity_batch_reads_report_files_and_writes_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            report_a = tmp / "parity_20260411.json"
            report_b = tmp / "parity_20260412.json"
            output_base = tmp / "parity_batch_summary"

            report_a.write_text(
                json.dumps(
                    _make_report(
                        date="2026-04-11",
                        stage="score_blocked",
                        slots=1,
                        buy_signal_count=4,
                        buy_scored_candidate_count=0,
                        executed_buy_count=0,
                        rejected_score_max=3.1,
                    ),
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            report_b.write_text(
                json.dumps(
                    _make_report(
                        date="2026-04-12",
                        stage="executed_buy",
                        slots=1,
                        buy_signal_count=3,
                        buy_scored_candidate_count=2,
                        executed_buy_count=1,
                        selected_buy_score=4.2,
                    ),
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            main(
                [
                    "parity-batch",
                    "--report-files",
                    f"{report_a},{report_b}",
                    "--output",
                    str(output_base),
                ]
            )

            json_path = tmp / "parity_batch_summary.json"
            csv_path = tmp / "parity_batch_summary.csv"
            self.assertTrue(json_path.exists())
            self.assertTrue(csv_path.exists())

            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["row_count"], 2)
            self.assertEqual(
                payload["score_tuning_candidates"]["include_primary_dates"],
                ["2026-04-11", "2026-04-12"],
            )
            self.assertEqual(
                payload["score_tuning_candidates"]["recommended_dates"],
                ["2026-04-11", "2026-04-12"],
            )


if __name__ == "__main__":
    unittest.main()
