import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from app.tools.compare_backtest_summaries import main, render_comparison


class CompareBacktestSummariesTests(unittest.TestCase):
    def test_render_comparison_reports_low_confidence_when_trade_counts_are_small(self) -> None:
        left = {
            "strategy_name": "core",
            "total_return": 2.0,
            "cagr": 4.0,
            "max_drawdown": 6.0,
            "sharpe": 0.8,
            "win_rate": 45.0,
            "profit_factor": 1.1,
            "trade_count": 18,
            "expectancy": 1000.0,
            "missing_fields": [],
            "research_read": "low sample confidence",
        }
        right = {
            "strategy_name": "continuation",
            "total_return": 7.0,
            "cagr": 12.0,
            "max_drawdown": 5.0,
            "sharpe": 1.2,
            "win_rate": 58.0,
            "profit_factor": 1.3,
            "trade_count": 19,
            "expectancy": 5000.0,
            "missing_fields": [],
            "research_read": "low sample confidence",
        }

        text = render_comparison(left, right, left_label="core", right_label="continuation")
        self.assertIn("low-confidence comparison because trade counts are too small", text)
        self.assertIn("Total Return", text)
        self.assertIn("continuation", text)

    def test_main_prints_mixed_read_and_handles_missing_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            left_path = tmp / "left.json"
            right_path = tmp / "right.json"

            left_path.write_text(
                json.dumps(
                    {
                        "strategy_name": "core",
                        "total_return": 4.0,
                        "cagr": 8.0,
                        "max_drawdown": 5.5,
                        "sharpe": 1.0,
                        "win_rate": 51.0,
                        "profit_factor": 1.3,
                        "trade_count": 42,
                        "expectancy": 25000.0,
                        "missing_fields": [],
                    }
                ),
                encoding="utf-8",
            )
            right_path.write_text(
                json.dumps(
                    {
                        "strategy_name": "continuation",
                        "total_return": 4.5,
                        "cagr": 7.5,
                        "max_drawdown": 5.2,
                        "sharpe": 0.95,
                        "win_rate": None,
                        "profit_factor": 1.35,
                        "trade_count": 39,
                        "expectancy": 22000.0,
                        "missing_fields": ["win_rate"],
                    }
                ),
                encoding="utf-8",
            )

            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = main(
                    [
                        "--left",
                        str(left_path),
                        "--right",
                        str(right_path),
                        "--left-label",
                        "core",
                        "--right-label",
                        "continuation",
                    ]
                )

            self.assertEqual(exit_code, 0)
            output = buffer.getvalue()
            self.assertIn("Comparison Read", output)
            self.assertIn("evidence is mixed", output)
            self.assertIn("Missing Fields", output)
            self.assertIn("continuation: win_rate", output)


if __name__ == "__main__":
    unittest.main()
