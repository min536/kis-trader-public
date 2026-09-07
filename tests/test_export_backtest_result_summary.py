import json
import tempfile
import unittest
from pathlib import Path

from app.tools.export_backtest_result_summary import build_summary, main, render_markdown


class ExportBacktestResultSummaryTests(unittest.TestCase):
    def test_build_summary_uses_config_and_statistics(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            result_path = tmp / "Algorithm.json"
            config_path = tmp / "config.json"

            result_path.write_text(
                json.dumps(
                    {
                        "statistics": {
                            "Net Profit": "16.5%",
                            "Compounding Annual Return": "12.1%",
                            "Drawdown": "8.4%",
                            "Sharpe Ratio": "1.42",
                            "Win Rate": "55.0%",
                            "Profit-Loss Ratio": "1.80",
                            "Total Orders": "24",
                            "Average Win": "$125000",
                            "Average Loss": "$-70000",
                            "Expectancy": "$22000",
                            "Start Equity": "$100000000",
                            "End Equity": "$116500000",
                            "Information Ratio": "0.71",
                        },
                        "charts": {
                            "Benchmark": {
                                "series": {
                                    "Benchmark": {
                                        "values": [
                                            [1704067200, 0.0],
                                            [1704153600, 5.5],
                                        ]
                                    }
                                }
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            config_path.write_text(
                json.dumps(
                    {
                        "strategy_id": "demo_strategy",
                        "strategy_name": "demo_strategy",
                        "parameters": {
                            "symbols": "005930,000660",
                            "start_date": "2025-01-01",
                            "end_date": "2025-06-30",
                            "initial_capital": "100000000",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            summary = build_summary(result_path=result_path, config_path=config_path)

            self.assertEqual(summary["strategy_name"], "demo_strategy")
            self.assertEqual(summary["symbols"], ["005930", "000660"])
            self.assertEqual(summary["start_date"], "2025-01-01")
            self.assertEqual(summary["end_date"], "2025-06-30")
            self.assertEqual(summary["starting_cash"], 100000000.0)
            self.assertEqual(summary["trade_count"], 24)
            self.assertEqual(summary["research_read"], "favorable baseline")
            self.assertEqual(summary["benchmark_comparison"]["information_ratio"], 0.71)
            self.assertFalse(summary["missing_fields"])

    def test_main_writes_both_outputs_and_handles_missing_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            project = tmp / "projects" / "bt_custom_demo"
            backtests = project / "backtests"
            backtests.mkdir(parents=True)
            result_path = backtests / "Algorithm.json"
            config_path = project / "config.json"
            output_base = tmp / "summary_output"

            result_path.write_text(
                json.dumps(
                    {
                        "statistics": {
                            "Net Profit": "-3.2%",
                            "Drawdown": "14.0%",
                            "Total Orders": "9",
                        },
                        "algorithmConfiguration": {
                            "startDate": "2025-02-01T00:00:00Z",
                            "endDate": "2025-04-30T23:59:59Z",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            config_path.write_text(
                json.dumps(
                    {
                        "strategy_id": "demo_missing_fields",
                        "parameters": {
                            "symbols": "005930",
                            "initial_capital": "50000000",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            exit_code = main(
                [
                    "--input",
                    str(result_path),
                    "--config-file",
                    str(config_path),
                    "--format",
                    "both",
                    "--output",
                    str(output_base),
                ]
            )

            self.assertEqual(exit_code, 0)
            json_path = tmp / "summary_output.json"
            md_path = tmp / "summary_output.md"
            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())

            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertIn("cagr", payload["missing_fields"])
            self.assertEqual(payload["research_read"], "low sample confidence")

            markdown = render_markdown(payload)
            self.assertIn("## Missing / Not Available", markdown)
            self.assertIn("demo_missing_fields", markdown)


if __name__ == "__main__":
    unittest.main()
