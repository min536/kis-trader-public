import json
import tempfile
import unittest
from pathlib import Path

from app.tools.run_ml_baseline_experiment import main, run_experiment


class RunMlBaselineExperimentTests(unittest.TestCase):
    def test_run_experiment_on_labeled_dataset_with_auto_walkforward(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            dataset_path = tmp / "labeled_dataset.jsonl"

            rows = []
            for day in range(1, 7):
                for idx in range(8):
                    score = idx + day
                    rows.append(
                        {
                            "ts": f"2026-04-0{day}T09:{10 + idx:02d}:00+09:00",
                            "cycle_id": f"cycle-{day}-{idx}",
                            "symbol": f"00{idx:04d}",
                            "selection_bucket": "core" if idx % 2 == 0 else "rotating",
                            "selection_profile": "baseline",
                            "stage_reached": "deep_eval",
                            "time_of_day_bucket": "open_30m" if idx < 4 else "morning",
                            "session": "REGULAR",
                            "regime": "RISK_ON" if day % 2 == 0 else "RISK_MIXED",
                            "strategy_pass_pattern": "P P F F P",
                            "score_deep": float(score),
                            "score_shallow": float(score) / 2.0,
                            "passed_count_deep": 3 + (idx % 2),
                            "trend_alignment_score": 0.2 * score,
                            "macd_momentum_score": 0.15 * score,
                            "pullback_pct": 0.5 + idx * 0.1,
                            "gap_up_open_pct": 0.2 * idx,
                            "range_recovery_ratio": 0.1 * score,
                            "expected_cost_bps": 5.0 + (idx % 3),
                            "net_profit_buffer_bps": 8.0 + score,
                            "cost_quality_score": 0.05 * score,
                            "label_positive_eod_net_cost": score >= 9,
                        }
                    )
            dataset_path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )

            result = run_experiment(
                input_path=str(dataset_path),
                label="label_positive_eod_net_cost",
                model_name="logistic",
                requested_features="auto",
                train_days=3,
                validation_days=1,
                test_days=1,
                step_days=1,
            )

            self.assertGreaterEqual(result["aggregate"]["evaluated_folds"], 1)
            self.assertIn(
                result["aggregate"]["research_read"],
                {
                    "promising offline signal",
                    "weak baseline",
                    "unstable across folds",
                    "high precision but low recall",
                    "too little positive coverage",
                },
            )
            self.assertGreater(result["features"]["encoded_feature_count"], 0)

    def test_main_runs_against_walkforward_directory_and_writes_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            fold_dir = tmp / "walkforward"
            fold1 = fold_dir / "fold_001"
            fold1.mkdir(parents=True)

            def _row(ts: str, score: float, label: bool, bucket: str) -> dict[str, object]:
                return {
                    "ts": ts,
                    "cycle_id": ts,
                    "symbol": "005930",
                    "selection_bucket": bucket,
                    "selection_profile": "baseline",
                    "stage_reached": "deep_eval",
                    "time_of_day_bucket": "open_30m",
                    "session": "REGULAR",
                    "regime": "RISK_ON",
                    "strategy_pass_pattern": "P P F F P",
                    "score_deep": score,
                    "score_shallow": score / 2.0,
                    "passed_count_deep": 3,
                    "trend_alignment_score": score / 10.0,
                    "macd_momentum_score": score / 12.0,
                    "expected_cost_bps": 4.0,
                    "net_profit_buffer_bps": score,
                    "label_positive_eod_net_cost": label,
                }

            train_rows = [
                _row("2026-04-01T09:10:00+09:00", 2.0, False, "rotating"),
                _row("2026-04-01T09:11:00+09:00", 7.0, True, "core"),
                _row("2026-04-02T09:10:00+09:00", 3.0, False, "rotating"),
                _row("2026-04-02T09:11:00+09:00", 8.0, True, "core"),
            ]
            validation_rows = [
                _row("2026-04-03T09:10:00+09:00", 4.0, False, "rotating"),
                _row("2026-04-03T09:11:00+09:00", 9.0, True, "core"),
            ]
            test_rows = [
                _row("2026-04-04T09:10:00+09:00", 5.0, False, "rotating"),
                _row("2026-04-04T09:11:00+09:00", 10.0, True, "core"),
            ]

            for name, rows in {
                "train.jsonl": train_rows,
                "validation.jsonl": validation_rows,
                "test.jsonl": test_rows,
            }.items():
                (fold1 / name).write_text(
                    "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                    encoding="utf-8",
                )

            (fold_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "folds": [
                            {
                                "fold": "fold_001",
                                "train_dates": ["2026-04-01", "2026-04-02"],
                                "validation_dates": ["2026-04-03"],
                                "test_dates": ["2026-04-04"],
                                "paths": {
                                    "train": str(fold1 / "train.jsonl"),
                                    "validation": str(fold1 / "validation.jsonl"),
                                    "test": str(fold1 / "test.jsonl"),
                                },
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            output_path = tmp / "baseline.json"
            exit_code = main(
                [
                    "--input",
                    str(fold_dir),
                    "--label",
                    "label_positive_eod_net_cost",
                    "--model",
                    "logistic",
                    "--output",
                    str(output_path),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.exists())
            self.assertTrue(output_path.with_suffix(".md").exists())

            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["aggregate"]["evaluated_folds"], 1)
            self.assertGreaterEqual(payload["aggregate"]["micro_average"]["f1"], 0.0)
            self.assertIn("top_features", payload["aggregate"])


if __name__ == "__main__":
    unittest.main()
