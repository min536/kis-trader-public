import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from app.tools.run_ml_label_baseline_batch import build_batch_report, main


class RunMlLabelBaselineBatchTests(unittest.TestCase):
    def _write_dataset(self, path: Path, *, days: int, label_name: str) -> None:
        rows = []
        for day in range(1, days + 1):
            for idx in range(8):
                score = idx + day
                rows.append(
                    {
                        "ts": f"2026-04-{day:02d}T09:{10 + idx:02d}:00+09:00",
                        "cycle_id": f"{path.stem}-{day}-{idx}",
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
                        "expected_cost_bps": 4.0 + (idx % 2),
                        "net_profit_buffer_bps": 7.0 + score,
                        label_name: score >= 9,
                    }
                )
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )

    def test_build_batch_report_aggregates_multiple_windows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            first = tmp / "window_a_labeled.jsonl"
            second = tmp / "window_b_labeled.jsonl"
            self._write_dataset(first, days=6, label_name="label_positive_30m_net_cost")
            self._write_dataset(second, days=7, label_name="label_positive_30m_net_cost")

            report = build_batch_report(
                input_paths=[first, second],
                label="label_positive_30m_net_cost",
                model="logistic",
                features="auto",
                train_days=3,
                validation_days=1,
                test_days=1,
                step_days=1,
            )

            self.assertEqual(report["aggregate"]["windows_requested"], 2)
            self.assertEqual(report["aggregate"]["windows_succeeded"], 2)
            self.assertGreaterEqual(report["aggregate"]["total_evaluated_folds"], 2)
            self.assertIn(
                report["aggregate"]["stability_read"],
                {
                    "stable enough for next ML step",
                    "promising but unstable",
                    "weak across windows",
                    "decent precision, weak recall",
                    "still too sparse / low-confidence",
                },
            )

    def test_main_handles_failed_short_window_and_glob(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            valid = tmp / "valid_labeled.jsonl"
            short = tmp / "short_labeled.jsonl"
            output = tmp / "batch.json"
            self._write_dataset(valid, days=6, label_name="label_positive_30m_net_cost")
            self._write_dataset(short, days=2, label_name="label_positive_30m_net_cost")

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "--glob",
                        str(tmp / "*_labeled.jsonl"),
                        "--label",
                        "label_positive_30m_net_cost",
                        "--model",
                        "logistic",
                        "--output",
                        str(output),
                        "--train-days",
                        "3",
                        "--validation-days",
                        "1",
                        "--test-days",
                        "1",
                        "--step-days",
                        "1",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertTrue(output.exists())
            self.assertTrue(output.with_suffix(".md").exists())

            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["label"], "label_positive_30m_net_cost")
            self.assertEqual(payload["aggregate"]["windows_requested"], 2)
            self.assertEqual(payload["aggregate"]["windows_succeeded"], 1)
            self.assertEqual(payload["aggregate"]["windows_failed"], 1)

            by_window = {item["window"]: item for item in payload["windows"]}
            self.assertEqual(by_window["short_labeled"]["status"], "failed")
            self.assertEqual(by_window["valid_labeled"]["status"], "ok")

            output_text = buffer.getvalue()
            self.assertIn("Windows", output_text)
            self.assertIn("Aggregate", output_text)
            self.assertIn("stability_read", output_text)


if __name__ == "__main__":
    unittest.main()
