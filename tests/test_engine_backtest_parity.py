import json
import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest import mock

from backtester.engine_backtest.cli import main
from backtester.engine_backtest.parity import build_parity_report


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _engine_summary() -> dict:
    return {
        "mode": "historical_signal_proxy",
        "input_symbol_count": 1,
        "initial_cash": 10_000_000,
        "seeded_position_count": 0,
        "buy_signal_count": 1,
        "buy_scored_candidate_count": 1,
        "buy_candidate_symbols": ["005930"],
        "buy_rule_names": ["controlled_down_day"],
        "buy_rule_enabled_counts": {"controlled_down_day": 1},
        "buy_rule_pass_counts": {"controlled_down_day": 1},
        "buy_rejection_reason_counts": {},
        "buy_funnel": {
            "symbols_considered": 1,
            "decision_evaluated": 1,
            "buy_signal": 1,
            "scored_candidate": 1,
        },
        "buy_capacity": {
            "positions_before_buy_pass": 0,
            "max_positions": 3,
            "available_slots_before_buy_pass": 3,
        },
        "buy_score_stats": {
            "min_score_threshold": 3.2,
            "signal_scores": {"max": 4.1},
            "score_rejected": {},
            "scored_candidates": {"max": 4.1},
        },
        "buy_sizing": {},
        "selected_buy_symbols": ["005930"],
        "selected_buy_score": 4.1,
        "final_candidate_count": 1,
        "executed_buy_count": 1,
        "sell_evaluated_count": 0,
        "sell_triggered_count": 0,
        "selected_sell_symbols": [],
        "executed_sell_count": 0,
        "sell_reason_distribution": {},
    }


@contextmanager
def _patch_parity_inputs(tmp: Path):
    candidate_path = tmp / "candidate_outcomes_mock_12345678_01_20260410.jsonl"
    stats_path = tmp / "cycle_stats_mock_12345678_01_20260410.jsonl"
    snapshots_path = tmp / "cycle_snapshots_mock_12345678_01.jsonl"
    orders_path = tmp / "orders_mock_12345678_01.jsonl"

    _write_jsonl(
        candidate_path,
        [
            {
                "ts": "2026-04-10T09:10:00+09:00",
                "session": "REGULAR",
                "symbol": "005930",
                "pre_gate_passed": True,
                "shallow_selected": True,
                "deep_evaluated": True,
                "final_candidate": True,
                "executed": True,
            }
        ],
    )
    _write_jsonl(
        stats_path,
        [
            {
                "ts": "2026-04-10T09:10:00+09:00",
                "session": "REGULAR",
                "pre_gate_passed": 1,
                "shallow_ranked_count": 1,
                "shallow_shortlist_size": 1,
                "deep_eval_count": 1,
                "final_candidate_count": 1,
                "executed_order_count": 1,
            }
        ],
    )
    _write_jsonl(
        snapshots_path,
        [
            {
                "timestamp": "2026-04-10T09:10:00+09:00",
                "market_session": {"session": "REGULAR"},
                "cash_krw": 10_000_000,
                "holdings_summary": {"positions": []},
            }
        ],
    )
    _write_jsonl(
        orders_path,
        [
            {
                "timestamp": "2026-04-10T09:11:00+09:00",
                "order_type": "market_buy",
                "action": "buy_succeeded",
                "result": "success",
                "symbol": "005930",
            }
        ],
    )

    signal_rows = [
        {
            "ts": "2026-04-10T09:10:00+09:00",
            "session": "REGULAR",
            "symbol": "005930",
            "current_price": 206000,
            "open_price": 208500,
            "low_price": 205500,
            "prev_day_change_pct": 1.0,
        }
    ]

    patches = (
        mock.patch("backtester.engine_backtest.parity._candidate_outcomes_path", return_value=candidate_path),
        mock.patch("backtester.engine_backtest.parity._cycle_stats_path", return_value=stats_path),
        mock.patch("backtester.engine_backtest.parity._cycle_snapshots_path", return_value=snapshots_path),
        mock.patch("backtester.engine_backtest.parity._orders_path", return_value=orders_path),
        mock.patch(
            "backtester.engine_backtest.parity._load_signal_rows",
            return_value=(signal_rows, str(tmp / "signal_dataset.jsonl"), "signal_dataset_jsonl"),
        ),
        mock.patch(
            "backtester.engine_backtest.parity._run_proxy_replay",
            return_value=_engine_summary(),
        ),
    )
    with ExitStack() as stack:
        for patch in patches:
            stack.enter_context(patch)
        yield


class EngineBacktestParityTests(unittest.TestCase):
    def test_build_parity_report_returns_expected_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with _patch_parity_inputs(Path(tmpdir)):
                report = build_parity_report(
                    account="mock_12345678_01",
                    date="20260410",
                )

        self.assertIn(report["parity_level"], {"low", "medium", "high"})
        self.assertEqual(report["date_range"]["start"], "2026-04-10")
        self.assertEqual(report["session"], "REGULAR")
        self.assertIn("live", report["summary_counts"])
        self.assertIn("engine_backtest", report["summary_counts"])
        self.assertGreater(report["summary_counts"]["live"]["candidate_rows"], 0)
        self.assertGreater(report["summary_counts"]["engine_backtest"]["input_symbol_count"], 0)
        self.assertIn("buy_rule_names", report["summary_counts"]["engine_backtest"])
        self.assertIn("buy_funnel", report["summary_counts"]["engine_backtest"])
        self.assertIn("buy_capacity", report["summary_counts"]["engine_backtest"])
        self.assertIn("buy_score_stats", report["summary_counts"]["engine_backtest"])
        self.assertIn("buy_sizing", report["summary_counts"]["engine_backtest"])
        self.assertIn("buy_rule_fail_counts", report["summary_counts"]["engine_backtest"])
        self.assertIn("buy_rule_pass_rates", report["summary_counts"]["engine_backtest"])
        self.assertIn("buy_diagnostics", report["summary_counts"]["engine_backtest"])
        self.assertIn(
            report["summary_counts"]["engine_backtest"]["buy_diagnostics"]["stage"],
            {
                "executed_buy",
                "capacity_blocked",
                "pre_rule_blocked",
                "rule_blocked",
                "score_blocked",
                "sizing_blocked",
                "unknown_zero_buy",
            },
        )
        self.assertIn("known_approximations", report)
        self.assertIn("not_yet_modeled", report)

    def test_cli_parity_writes_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            output_path = tmp / "parity_20260410.json"

            with _patch_parity_inputs(tmp):
                main(
                    [
                        "parity",
                        "--account",
                        "mock_12345678_01",
                        "--date",
                        "20260410",
                        "--output-file",
                        str(output_path),
                    ]
                )

            self.assertTrue(output_path.exists())
            self.assertTrue(output_path.with_suffix(".md").exists())

            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertIn(payload["parity_level"], {"low", "medium", "high"})
            self.assertEqual(
                payload["engine_backtest_source"]["mode"],
                "historical_signal_proxy",
            )
            self.assertIn("buy_rule_names", payload["summary_counts"]["engine_backtest"])
            self.assertIn("buy_funnel", payload["summary_counts"]["engine_backtest"])
            self.assertIn("buy_capacity", payload["summary_counts"]["engine_backtest"])
            self.assertIn("buy_score_stats", payload["summary_counts"]["engine_backtest"])
            self.assertIn("buy_sizing", payload["summary_counts"]["engine_backtest"])
            self.assertIn("buy_rule_fail_counts", payload["summary_counts"]["engine_backtest"])
            self.assertIn("buy_rule_pass_rates", payload["summary_counts"]["engine_backtest"])
            self.assertIn("buy_diagnostics", payload["summary_counts"]["engine_backtest"])


if __name__ == "__main__":
    unittest.main()
