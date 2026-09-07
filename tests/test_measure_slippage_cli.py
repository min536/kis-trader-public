from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.tools.measure_slippage import (
    build_slippage_report_text,
    load_order_log_records,
)


class MeasureSlippageCliTest(unittest.TestCase):
    def test_build_report_text_uses_settings_policy_bps(self) -> None:
        settings = SimpleNamespace(buy_slippage_bps=5.0, sell_slippage_bps=6.0)
        records = [
            {
                "action": "order_succeeded",
                "raw_response": {
                    "reference_price_krw": 70000,
                    "quote_at_submit": 70035,
                },
            },
        ]
        text = build_slippage_report_text(
            records, settings=settings, report_date="20260613"
        )
        with self.subTest(line="header"):
            self.assertIn("Slippage report 20260613", text)
        with self.subTest(line="policy"):
            self.assertIn("policy=BUY 5.00bps / SELL 6.00bps", text)
        with self.subTest(line="buy"):
            self.assertIn("BUY  n=1", text)


    def test_report_text_includes_submit_side_scope_note(self) -> None:
        # The operator-facing output must carry the honest scope caveat (the W2
        # overclaim trap): 0.00bps today is submit-side-only, not real fill slippage.
        settings = SimpleNamespace(buy_slippage_bps=5.0, sell_slippage_bps=5.0)
        text = build_slippage_report_text([], settings=settings, report_date="20260613")
        with self.subTest(check="submit_side"):
            self.assertIn("submit-side", text.lower())
        with self.subTest(check="fill_caveat"):
            self.assertIn("fill", text.lower())

    def test_load_records_parses_jsonl_skips_junk_and_filters_by_date(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "orders.jsonl"
            lines = [
                json.dumps(
                    {
                        "action": "order_succeeded",
                        "timestamp": "2026-06-13T10:00:00+09:00",
                        "raw_response": {"reference_price_krw": 70000},
                    }
                ),
                json.dumps(
                    {
                        "action": "sell_order_succeeded",
                        "timestamp": "2026-06-12T10:00:00+09:00",
                        "raw_response": {},
                    }
                ),
                "this is not json",
                "",
                json.dumps(["not", "a", "dict"]),
            ]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            with self.subTest(case="no_filter_skips_junk_and_non_dict"):
                self.assertEqual(len(load_order_log_records(path)), 2)
            with self.subTest(case="date_prefix_filter"):
                today = load_order_log_records(path, date_prefix="2026-06-13")
                self.assertEqual(len(today), 1)
                self.assertEqual(today[0]["action"], "order_succeeded")
            with self.subTest(case="missing_file_returns_empty"):
                self.assertEqual(load_order_log_records(Path(d) / "nope.jsonl"), [])


    def test_parse_args_handles_date_and_default(self) -> None:
        from app.tools.measure_slippage import _parse_args

        with self.subTest(case="with_date"):
            self.assertEqual(_parse_args(["--date", "2026-06-13"]).date, "2026-06-13")
        with self.subTest(case="default_none"):
            self.assertIsNone(_parse_args([]).date)

    def test_parse_args_rejects_malformed_date(self) -> None:
        from app.tools.measure_slippage import _parse_args

        for bad in ("2026/06/13", "garbage", "2026-6-1"):
            with self.subTest(value=bad):
                with self.assertRaises(SystemExit):
                    _parse_args(["--date", bad])


    def test_main_prints_report_end_to_end(self) -> None:
        import app.tools.measure_slippage as cli

        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "orders.jsonl"
            path.write_text(
                json.dumps(
                    {
                        "action": "order_succeeded",
                        "timestamp": "2026-06-13T10:00:00+09:00",
                        "raw_response": {
                            "reference_price_krw": 70000,
                            "quote_at_submit": 70000,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            settings = SimpleNamespace(buy_slippage_bps=5.0, sell_slippage_bps=5.0)
            buf = io.StringIO()
            with patch.object(cli, "get_settings", return_value=settings), patch.object(
                cli, "get_order_log_path", return_value=path
            ), contextlib.redirect_stdout(buf):
                rc = cli.main(["--date", "2026-06-13"])
            out = buf.getvalue()
        with self.subTest(check="return_code"):
            self.assertEqual(rc, 0)
        with self.subTest(check="header"):
            self.assertIn("Slippage report 20260613", out)
        with self.subTest(check="buy_line"):
            self.assertIn("BUY  n=1", out)


if __name__ == "__main__":
    unittest.main()
