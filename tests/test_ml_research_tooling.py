import json
import tempfile
import unittest
from pathlib import Path

from app.tools.build_ml_labels import main as build_labels_main
from app.tools.build_ml_walkforward_splits import main as build_splits_main
from app.tools.export_ml_candidate_dataset import main as export_ml_main
from app.tools.ml_research_common import load_rows


class MlResearchToolingTests(unittest.TestCase):
    def test_export_ml_candidate_dataset_joins_cost_fields_and_writes_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            signal_outcomes = tmp / "signal_outcomes.jsonl"
            cycle_snapshots = tmp / "cycle_snapshots.jsonl"
            output = tmp / "ml_dataset.jsonl"

            signal_outcomes.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "ts": "2026-04-10T09:12:00+09:00",
                                "cycle_id": "cycle-1",
                                "symbol": "005930",
                                "symbol_name": "Samsung",
                                "session": "REGULAR",
                                "regime": "RISK_ON",
                                "selection_bucket": "core",
                                "selection_profile": "baseline",
                                "stage_reached": "deep_eval",
                                "score_shallow": 2.1,
                                "score_deep": 3.6,
                                "passed_count_deep": 4,
                                "trend_alignment_score": 0.8,
                                "macd_momentum_score": 0.6,
                                "pullback_pct": 1.4,
                                "gap_up_open_pct": 0.7,
                                "range_recovery_ratio": 0.5,
                                "core_shadow_evaluated": True,
                                "core_shadow_passed": False,
                                "core_rescue_applied": False,
                                "ret_30m_bps": 18.0,
                                "ret_eod_bps": 42.0,
                                "final_candidate": True,
                                "executed": True,
                            }
                        )
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            cycle_snapshots.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "cycle_id": "cycle-1",
                                "timestamp": "2026-04-10T09:12:00+09:00",
                                "scanner_candidates_top": [
                                    {
                                        "symbol": "005930",
                                        "expected_total_cost_krw": 1400,
                                        "expected_cost_bps": 9.5,
                                        "net_edge_bps": 21.0,
                                        "net_profit_buffer_bps": 14.2,
                                        "cost_quality_score": 0.09,
                                        "expected_cost_penalty": 0.7,
                                        "cost_block_reason": None,
                                        "market_snapshot": {
                                            "current_price": 70100,
                                            "open_price": 69900,
                                            "low_price": 69500,
                                            "prev_day_change_pct": 1.4,
                                        },
                                    }
                                ],
                            }
                        )
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            exit_code = export_ml_main(
                [
                    "--input",
                    str(signal_outcomes),
                    "--cycle-snapshots",
                    str(cycle_snapshots),
                    "--format",
                    "jsonl",
                    "--output",
                    str(output),
                ]
            )

            self.assertEqual(exit_code, 0)
            rows = load_rows(output)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["time_of_day_bucket"], "open_30m")
            self.assertEqual(rows[0]["expected_cost_bps"], 9.5)
            self.assertEqual(rows[0]["current_price"], 70100)

            manifest = json.loads(
                output.with_name(f"{output.stem}.manifest.json").read_text(encoding="utf-8")
            )
            self.assertTrue(manifest["random_cv_disallowed"])
            self.assertIn("decision_time_safe_feature_columns", manifest["feature_policy"])

            note = output.with_name(f"{output.stem}.research.md").read_text(encoding="utf-8")
            self.assertIn("Random CV is disallowed", note)

    def test_label_and_walkforward_clis_generate_cost_aware_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            dataset_path = tmp / "dataset.jsonl"
            labeled_path = tmp / "dataset_labeled.jsonl"
            split_dir = tmp / "splits"

            rows = [
                {
                    "row_id": f"row-{index}",
                    "ts": f"2026-04-0{index + 1}T09:10:00+09:00",
                    "cycle_id": f"cycle-{index}",
                    "symbol": "005930",
                    "selection_bucket": "core",
                    "stage_reached": "deep_eval",
                    "expected_cost_bps": 10.0,
                    "ret_30m_bps": 5.0 * (index + 1),
                    "ret_eod_bps": 12.0 * (index + 1),
                    "final_candidate": index % 2 == 0,
                }
                for index in range(5)
            ]
            dataset_path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )

            label_exit = build_labels_main(
                [
                    "--input",
                    str(dataset_path),
                    "--format",
                    "jsonl",
                    "--output",
                    str(labeled_path),
                    "--margin-bps",
                    "0",
                ]
            )
            self.assertEqual(label_exit, 0)

            labeled_rows = load_rows(labeled_path)
            self.assertEqual(labeled_rows[0]["net_ret_eod_after_cost_bps"], 2.0)
            self.assertFalse(labeled_rows[0]["label_positive_30m_net_cost"])
            self.assertTrue(labeled_rows[-1]["label_positive_eod_net_cost"])
            self.assertIn("label_top_decile_eod", labeled_rows[-1])

            manifest = json.loads(
                labeled_path.with_name(f"{labeled_path.stem}.manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["parameters"]["top_quantile"], 0.9)

            split_exit = build_splits_main(
                [
                    "--input",
                    str(labeled_path),
                    "--output-dir",
                    str(split_dir),
                    "--format",
                    "jsonl",
                    "--train-days",
                    "2",
                    "--validation-days",
                    "1",
                    "--test-days",
                    "1",
                    "--step-days",
                    "1",
                ]
            )
            self.assertEqual(split_exit, 0)

            split_manifest = json.loads((split_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(split_manifest["folds"]), 2)
            first_fold = split_manifest["folds"][0]
            self.assertEqual(first_fold["train_dates"], ["2026-04-01", "2026-04-02"])
            self.assertEqual(first_fold["validation_dates"], ["2026-04-03"])
            self.assertEqual(first_fold["test_dates"], ["2026-04-04"])

            train_rows = load_rows(split_dir / "fold_001" / "train.jsonl")
            validation_rows = load_rows(split_dir / "fold_001" / "validation.jsonl")
            test_rows = load_rows(split_dir / "fold_001" / "test.jsonl")
            self.assertEqual(len(train_rows), 2)
            self.assertEqual(len(validation_rows), 1)
            self.assertEqual(len(test_rows), 1)


if __name__ == "__main__":
    unittest.main()
