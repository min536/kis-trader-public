import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from app.tools.compare_ml_label_viability import build_report, main


class CompareMlLabelViabilityTests(unittest.TestCase):
    def test_build_report_from_dataset_only_marks_sparse_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            dataset_path = tmp / "labeled.jsonl"
            rows = []
            for idx in range(12):
                row = {
                    "ts": f"2026-04-0{1 + (idx // 4)}T09:{10 + idx:02d}:00+09:00",
                    "label_positive_eod_net_cost": idx < 2,
                    "label_positive_30m_net_cost": idx < 3,
                    "label_final_candidate_vs_reject": idx < 6,
                }
                if idx < 1:
                    row["label_top_decile_eod"] = True
                elif idx < 8:
                    row["label_top_decile_eod"] = False
                rows.append(row)
            dataset_path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )

            report = build_report(
                dataset_path=str(dataset_path),
                min_positive_count=3,
                min_available_count=6,
                min_positive_rate=0.05,
            )

            by_label = {item["label"]: item for item in report["labels"]}
            self.assertEqual(by_label["label_positive_eod_net_cost"]["coverage"]["positive_count"], 2)
            self.assertEqual(by_label["label_positive_eod_net_cost"]["verdict"], "too sparse for now")
            self.assertEqual(by_label["label_positive_30m_net_cost"]["coverage"]["positive_count"], 3)
            self.assertIsNone(by_label["label_top_decile_eod_net_cost"]["coverage"]["positive_rate"])

    def test_main_compares_baselines_and_recommends_first_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            dataset_path = tmp / "labeled.jsonl"
            baseline_dir = tmp / "baselines"
            baseline_dir.mkdir()
            output_path = tmp / "viability.json"

            rows = []
            for idx in range(120):
                row = {
                    "ts": f"2026-04-{1 + (idx // 20):02d}T09:{10 + (idx % 20):02d}:00+09:00",
                    "label_positive_eod_net_cost": idx < 36,
                    "label_positive_30m_net_cost": idx < 28,
                    "label_top_decile_eod": idx < 14,
                    "label_top_decile_eod_net_cost": idx < 12,
                    "label_final_candidate_vs_reject": idx < 70,
                }
                rows.append(row)
            dataset_path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )

            def _baseline(path: Path, *, label: str, pr_auc: float, class_balance: float, f1: float, precision: float, recall: float, roc_auc: float, folds: int, research_read: str, model: str = "PureLogisticRegression") -> None:
                payload = {
                    "experiment_context": {
                        "label": label,
                        "model_requested": "logistic",
                        "model_used": model,
                        "backend": "numpy",
                    },
                    "aggregate": {
                        "evaluated_folds": folds,
                        "skipped_folds": 0,
                        "class_balance": class_balance,
                        "macro_average": {
                            "precision": precision,
                            "recall": recall,
                            "f1": f1,
                            "pr_auc": pr_auc,
                            "roc_auc": roc_auc,
                            "positive_prediction_rate": class_balance,
                        },
                        "micro_average": {
                            "positive_prediction_rate": class_balance,
                        },
                        "research_read": research_read,
                    },
                }
                path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            _baseline(
                baseline_dir / "eod.json",
                label="label_positive_eod_net_cost",
                pr_auc=0.42,
                class_balance=0.30,
                f1=0.22,
                precision=0.33,
                recall=0.17,
                roc_auc=0.61,
                folds=3,
                research_read="promising offline signal",
            )
            _baseline(
                baseline_dir / "30m.json",
                label="label_positive_30m_net_cost",
                pr_auc=0.27,
                class_balance=0.23,
                f1=0.11,
                precision=0.2,
                recall=0.08,
                roc_auc=0.55,
                folds=2,
                research_read="weak baseline",
            )
            _baseline(
                baseline_dir / "top_decile.json",
                label="label_top_decile_eod",
                pr_auc=0.12,
                class_balance=0.12,
                f1=0.05,
                precision=0.09,
                recall=0.04,
                roc_auc=0.5,
                folds=2,
                research_read="weak baseline",
            )
            _baseline(
                baseline_dir / "policy.json",
                label="label_final_candidate_vs_reject",
                pr_auc=0.72,
                class_balance=0.58,
                f1=0.51,
                precision=0.55,
                recall=0.48,
                roc_auc=0.76,
                folds=3,
                research_read="promising offline signal",
            )

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "--dataset",
                        str(dataset_path),
                        "--baseline-dir",
                        str(baseline_dir),
                        "--output",
                        str(output_path),
                        "--min-positive-count",
                        "10",
                        "--min-available-count",
                        "30",
                        "--min-positive-rate",
                        "0.02",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertTrue(output_path.exists())
            self.assertTrue(output_path.with_suffix(".md").exists())

            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["overall_recommendation"]["best_first_target"],
                "label_positive_eod_net_cost",
            )
            by_label = {item["label"]: item for item in payload["labels"]}
            self.assertEqual(
                by_label["label_positive_eod_net_cost"]["verdict"],
                "best first ML target candidate",
            )
            self.assertEqual(
                by_label["label_final_candidate_vs_reject"]["verdict"],
                "keep for later only",
            )

            output = buffer.getvalue()
            self.assertIn("Coverage", output)
            self.assertIn("Baselines", output)
            self.assertIn("best_first_target", output)


if __name__ == "__main__":
    unittest.main()
