from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from app.tools import compare_runtime_quality as module


class CompareRuntimeQualityTests(unittest.TestCase):
    def test_build_day_metrics_aggregates_cycle_order_and_stdout_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs = root / "logs"
            logs.mkdir(parents=True, exist_ok=True)

            (logs / "cycle_stats_mock_test_20260417.jsonl").write_text(
                "\n".join(
                    [
                        '{"ts":"2026-04-17T09:00:00+09:00","shallow_shortlist_size":4,"deep_eval_count":2,"final_candidate_count":1,"executed_order_count":1,"sell_triggered_count":0,"pre_gate_rejected_daily_pnl_brake":0}',
                        '{"ts":"2026-04-17T13:21:00+09:00","shallow_shortlist_size":2,"deep_eval_count":1,"final_candidate_count":0,"executed_order_count":0,"sell_triggered_count":0,"pre_gate_rejected_daily_pnl_brake":50}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (logs / "orders_mock_test.jsonl").write_text(
                "\n".join(
                    [
                        '{"timestamp":"2026-04-17T09:00:01+09:00","cycle_id":"c1","action":"rate_limit_detected_buy_scan","result":"skipped","order_type":"engine_event"}',
                        '{"timestamp":"2026-04-17T09:00:02+09:00","cycle_id":"c1","action":"skipped_buy_scan_budget_limited","result":"skipped","order_type":"engine_event"}',
                        '{"timestamp":"2026-04-17T09:00:03+09:00","cycle_id":"c2","action":"sell_watch_partial_budget_protection","result":"skipped","order_type":"engine_event"}',
                        '{"timestamp":"2026-04-17T09:00:04+09:00","cycle_id":"c3","action":"rate_limit_detected_sell_watch","result":"skipped","order_type":"engine_event"}',
                        '{"timestamp":"2026-04-17T09:00:05+09:00","cycle_id":"c4","action":"order_succeeded","result":"success","order_type":"market_buy"}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            stdout_path = logs / "stdout_20260417.log"
            stdout_path.write_text(
                textwrap.dedent(
                    """\
                    [INFO] buy universe source=live_snapshot | symbols=10 | updated_at=2026-04-17T10:32:06+09:00 | age=10.0s
                    [INFO] buy universe source=settings | symbols=154 | fallback_reason=stale_or_invalid
                    daily pnl brake: HARD_STOP_READY
                    """
                ),
                encoding="utf-8",
            )

            metrics = module.build_day_metrics(
                root=root,
                account="mock_test",
                date="20260417",
                stdout_path=stdout_path,
            )

            self.assertEqual(metrics["cycles"], 2)
            self.assertEqual(metrics["affected_cycles_by_rate_limit_or_budget_protection"], 3)
            self.assertEqual(metrics["rate_limit_detected_buy_scan"], 1)
            self.assertEqual(metrics["rate_limit_detected_sell_watch"], 1)
            self.assertEqual(metrics["skipped_buy_scan_budget_limited"], 1)
            self.assertEqual(metrics["sell_watch_partial_budget_protection"], 1)
            self.assertEqual(metrics["shortlist_count"], 6)
            self.assertEqual(metrics["deep_eval_count"], 3)
            self.assertAlmostEqual(metrics["deep_eval_utilization_ratio"], 0.5, places=4)
            self.assertEqual(metrics["successful_cycles"], 1)
            self.assertEqual(metrics["executed_order_count"], 1)
            self.assertEqual(metrics["live_snapshot_usage_count"], 1)
            self.assertEqual(metrics["stale_or_invalid_fallback_count"], 1)
            self.assertEqual(metrics["hard_stop_ready_occurrence_count"], 1)

    def test_main_auto_discovers_day_scoped_stdout_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            logs = root / "logs"
            logs.mkdir(parents=True, exist_ok=True)

            (logs / "cycle_stats_mock_test_20260417.jsonl").write_text(
                '{"ts":"2026-04-17T09:00:00+09:00","shallow_shortlist_size":1,"deep_eval_count":1,"final_candidate_count":0,"executed_order_count":0,"sell_triggered_count":0,"pre_gate_rejected_daily_pnl_brake":0}\n',
                encoding="utf-8",
            )
            (logs / "cycle_stats_mock_test_20260418.jsonl").write_text(
                '{"ts":"2026-04-18T09:00:00+09:00","shallow_shortlist_size":1,"deep_eval_count":1,"final_candidate_count":0,"executed_order_count":0,"sell_triggered_count":0,"pre_gate_rejected_daily_pnl_brake":0}\n',
                encoding="utf-8",
            )
            (logs / "orders_mock_test.jsonl").write_text("", encoding="utf-8")
            (logs / "app_stdout_20260417.log").write_text(
                "[INFO] buy universe source=live_snapshot | symbols=10 | updated_at=2026-04-17T10:32:06+09:00 | age=10.0s\n",
                encoding="utf-8",
            )
            (logs / "app_stdout_20260418.log").write_text(
                "[INFO] buy universe source=settings | symbols=154 | fallback_reason=stale_or_invalid\n",
                encoding="utf-8",
            )

            with mock.patch.object(module, "PROJECT_ROOT", root):
                baseline_metrics = module.build_day_metrics(
                    root=root,
                    account="mock_test",
                    date="20260417",
                    stdout_path=module._default_stdout_path(root, "20260417"),
                )
                target_metrics = module.build_day_metrics(
                    root=root,
                    account="mock_test",
                    date="20260418",
                    stdout_path=module._default_stdout_path(root, "20260418"),
                )

            self.assertEqual(baseline_metrics["live_snapshot_usage_count"], 1)
            self.assertEqual(target_metrics["stale_or_invalid_fallback_count"], 1)

    def test_assessment_marks_low_confidence_when_target_has_no_meaningful_activity(self) -> None:
        baseline_metrics = {
            "date": "20260417",
            "cycles": 350,
            "affected_cycles_by_rate_limit_or_budget_protection": 279,
            "rate_limit_detected_buy_scan": 40,
            "rate_limit_detected_sell_watch": 42,
            "skipped_buy_scan_budget_limited": 157,
            "sell_watch_partial_budget_protection": 80,
            "shortlist_count": 368,
            "deep_eval_count": 49,
            "deep_eval_utilization_ratio": 49 / 368,
            "successful_cycles": 16,
            "executed_order_count": 11,
            "live_snapshot_usage_count": None,
            "stale_or_invalid_fallback_count": None,
            "hard_stop_ready_occurrence_count": 16,
            "stdout_scope": "missing",
        }
        target_metrics = {
            "date": "20260418",
            "cycles": 3634,
            "affected_cycles_by_rate_limit_or_budget_protection": 0,
            "rate_limit_detected_buy_scan": 0,
            "rate_limit_detected_sell_watch": 0,
            "skipped_buy_scan_budget_limited": 0,
            "sell_watch_partial_budget_protection": 0,
            "shortlist_count": 0,
            "deep_eval_count": 0,
            "deep_eval_utilization_ratio": 0.0,
            "successful_cycles": 0,
            "executed_order_count": 0,
            "live_snapshot_usage_count": None,
            "stale_or_invalid_fallback_count": None,
            "hard_stop_ready_occurrence_count": 0,
            "stdout_scope": "missing",
        }

        assessment = module.assess_comparison(baseline_metrics, target_metrics)
        rendered = module.render_comparison(baseline_metrics, target_metrics)

        self.assertFalse(assessment["comparable_regular_session_day"])
        self.assertEqual(assessment["confidence"], "low")
        self.assertIn("shortlist_count == 0", assessment["issues"])
        self.assertEqual(
            assessment["false_hard_stop_verdict"],
            "looks improved but low confidence",
        )
        self.assertEqual(
            assessment["buy_scan_bottleneck_verdict"],
            "looks improved but low confidence",
        )
        self.assertEqual(
            assessment["live_snapshot_persistence_verdict"],
            "unavailable",
        )
        self.assertIn("comparable_regular_session_day=no", rendered)
        self.assertIn("confidence=low", rendered)
        self.assertIn("false hard-stop behavior improved? looks improved but low confidence", rendered)


if __name__ == "__main__":
    unittest.main()
