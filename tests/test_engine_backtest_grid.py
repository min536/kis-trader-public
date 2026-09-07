from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backtester.ai_integration import AIIntegrationConfig
from backtester.engine_backtest.cli import main
from backtester.engine_backtest.settings_factory import make_settings


class EngineBacktestGridTests(unittest.TestCase):
    def test_grid_rejects_placeholder_data_path_with_helpful_message(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            main(
                [
                    "grid",
                    "--data",
                    "/path/to/prices.csv",
                    "--config",
                    "backtester/strategies/kis_trader_continuation_family_approx.kis.yaml",
                    "--param",
                    "sell_stop_loss_pct=-3.0,-4.0",
                    "--output",
                    "results/grid",
                ]
            )

        message = str(ctx.exception)
        self.assertIn("Placeholder data path detected", message)
        self.assertIn("data/prices.csv", message)
        self.assertIn("backtester.engine_backtest.cli fetch", message)

    def test_grid_uses_config_baseline_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            data_path = tmp / "prices.csv"
            output_dir = tmp / "grid"
            config_path = tmp / "aggressive_test.kis.yaml"

            with data_path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(
                    fh,
                    fieldnames=[
                        "date",
                        "symbol",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "prev_close",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "date": "2026-01-02",
                        "symbol": "AAA",
                        "open": 9500,
                        "high": 11000,
                        "low": 9400,
                        "close": 10200,
                        "volume": 100000,
                        "prev_close": 10000,
                    }
                )

            base_settings = make_settings(buy_min_score=99.0)

            with patch(
                "backtester.engine_backtest.cli._load_settings_and_ai",
                return_value=(base_settings, AIIntegrationConfig()),
            ) as mocked_load:
                main(
                    [
                        "grid",
                        "--data",
                        str(data_path),
                        "--config",
                        str(config_path),
                        "--param",
                        "sell_stop_loss_pct=-3.0,-4.0",
                        "--output",
                        str(output_dir),
                        "--cash",
                        "10000000",
                    ]
                )

            mocked_load.assert_called_once_with(str(config_path))

            summary_path = output_dir / "grid_summary.csv"
            self.assertTrue(summary_path.exists())

            with summary_path.open(newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))

            self.assertEqual(len(rows), 2)
            self.assertEqual([row["sell_stop_loss_pct"] for row in rows], ["-3.0", "-4.0"])
            self.assertTrue(all(int(row["n_trades"]) == 0 for row in rows))


if __name__ == "__main__":
    unittest.main()
