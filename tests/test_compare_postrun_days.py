from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.tools import compare_postrun_days as module


def _budget(*, cycles: int, requests: float, quotes: float) -> dict:
    return {
        "meta": {"cycles_analyzed": cycles},
        "flags": {
            "rate_limit_triggered": 4,
            "skipped_buy_scan_budget_limited": 2,
            "sell_watch_partial": 6,
        },
        "api": {
            "avg_requests_per_cycle": requests,
            "avg_quotes_per_cycle": quotes,
        },
        "drain": {
            "sell_watch_drain_count": 1,
            "sell_watch_drain_avg_ms": 1000.0,
            "exec_tail_drain_count": 0,
            "exec_tail_drain_avg_ms": 0.0,
        },
    }


def _technical(*, analyzed: int, usable: int, active: int, errors: int = 0) -> dict:
    return {
        "meta": {
            "analyzed_rows": analyzed,
            "errors": ["warning"] * errors,
        },
        "activation_summary": {
            "history_usable_count": usable,
            "either_nonzero_count": active,
        },
    }


class ComparePostrunDaysTests(unittest.TestCase):
    def test_load_jsonl_recovers_concatenated_candidate_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "candidate.jsonl"
            path.write_text(
                '{"final_candidate":true}{"executed":true}\n',
                encoding="utf-8",
            )

            rows = module._load_jsonl(path)

        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]["final_candidate"])
        self.assertTrue(rows[1]["executed"])

    def test_build_day_metrics_includes_api_and_technical_rates(self) -> None:
        with (
            mock.patch.object(module, "_run_budget", return_value=_budget(cycles=10, requests=3.2, quotes=2.1)),
            mock.patch.object(
                module,
                "_run_technical_activation",
                return_value=_technical(analyzed=20, usable=12, active=5, errors=1),
            ),
            mock.patch.object(
                module,
                "_load_candidate_stats",
                return_value={
                    "final_candidate_count": 3,
                    "executed_count": 1,
                    "dominant_core_rejection": "—",
                    "data_present": True,
                },
            ),
        ):
            metrics = module.build_day_metrics("mock_test", "20260424", "REGULAR")

        self.assertEqual(metrics["cycles"], 10)
        self.assertEqual(metrics["avg_requests_per_cycle"], 3.2)
        self.assertEqual(metrics["avg_quotes_per_cycle"], 2.1)
        self.assertEqual(metrics["technical_analyzed_rows"], 20)
        self.assertEqual(metrics["technical_history_usable_count"], 12)
        self.assertEqual(metrics["technical_active_count"], 5)
        self.assertAlmostEqual(metrics["technical_history_usable_rate"], 0.6)
        self.assertAlmostEqual(metrics["technical_active_rate"], 0.25)
        self.assertEqual(metrics["technical_load_warning_count"], 1)

    def test_operational_health_surfaces_api_and_technical_directions(self) -> None:
        a = {
            "date": "20260423",
            "cycles": 100,
            "rl_triggered": 20,
            "buy_skip": 15,
            "sell_partial": 30,
            "avg_requests_per_cycle": 3.5,
            "avg_quotes_per_cycle": 2.5,
            "sw_drain_cycles": 0,
            "exec_drain_cycles": 0,
            "final_candidate_count": 4,
            "executed_count": 1,
            "technical_history_usable_rate": 0.10,
            "technical_active_rate": 0.05,
        }
        b = {
            **a,
            "date": "20260424",
            "rl_triggered": 10,
            "buy_skip": 6,
            "sell_partial": 12,
            "avg_requests_per_cycle": 2.7,
            "avg_quotes_per_cycle": 1.8,
            "technical_history_usable_rate": 0.35,
            "technical_active_rate": 0.20,
        }

        health = module.operational_health_summary(a, b)

        self.assertEqual(health["sell_pressure"]["verdict"], "IMPROVED")
        self.assertEqual(health["buy_survivability"]["avg_requests_sub"], "✓")
        self.assertEqual(health["buy_survivability"]["avg_quotes_sub"], "✓")
        self.assertEqual(health["technical_cold_start"]["history_sub"], "✓")
        self.assertEqual(health["technical_cold_start"]["active_sub"], "✓")

    def test_suggest_tuning_flags_worse_api_pressure_and_cold_start(self) -> None:
        a = {
            "date": "20260423",
            "cycles": 100,
            "rl_triggered": 0,
            "buy_skip": 0,
            "sell_partial": 0,
            "avg_requests_per_cycle": 2.0,
            "avg_quotes_per_cycle": 1.0,
            "sw_drain_cycles": 0,
            "exec_drain_cycles": 0,
            "executed_count": 1,
            "dominant_core_rejection": "—",
            "technical_history_usable_rate": 0.50,
        }
        b = {
            **a,
            "date": "20260424",
            "avg_requests_per_cycle": 2.5,
            "avg_quotes_per_cycle": 1.5,
            "technical_history_usable_rate": 0.10,
        }

        suggestions = module.suggest_tuning(a, b)
        text = "\n".join(item[0] for item in suggestions)

        self.assertIn("average API pressure worsened", text)
        self.assertIn("technical history usable rate worsened", text)


if __name__ == "__main__":
    unittest.main()
