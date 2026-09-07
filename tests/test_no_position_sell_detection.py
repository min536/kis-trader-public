"""Tests for _looks_like_no_position_sell_response and failure_category in SELL failure logs.

Covers:
- msg_cd == "40240000" → True
- msg1 contains "잔고내역" → True
- message contains "잔고내역" → True
- string containing "40240000" → True
- unrelated KIS rate-limit error (EGW00201) → False
- unrelated BUY-untradable error (40070000) → False
- sell_order_failed raw_response includes failure_category="no_position_on_sell" for no-position responses
- unrelated sell_order_failed raw_response does NOT include failure_category
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class NoPositionHelperTests(unittest.TestCase):
    def _helper(self, response):
        from app.core.error_classification import (
            looks_like_no_position_sell_response as _looks_like_no_position_sell_response,
        )
        return _looks_like_no_position_sell_response(response)

    def test_msg_cd_40240000_returns_true(self):
        self.assertTrue(self._helper({"rt_cd": "1", "msg_cd": "40240000", "msg1": "잔고내역이 없습니다"}))

    def test_msg_cd_40240000_without_msg1_returns_true(self):
        self.assertTrue(self._helper({"rt_cd": "1", "msg_cd": "40240000"}))

    def test_msg1_contains_잔고내역_returns_true(self):
        self.assertTrue(self._helper({"rt_cd": "1", "msg_cd": "APBK9999", "msg1": "잔고내역 없음"}))

    def test_message_contains_잔고내역_returns_true(self):
        self.assertTrue(self._helper({"rt_cd": "1", "message": "잔고내역이 없습니다"}))

    def test_string_containing_40240000_returns_true(self):
        self.assertTrue(self._helper("매도 주문 실패: {'rt_cd': '1', 'msg_cd': '40240000'}"))

    def test_string_containing_잔고내역_returns_true(self):
        self.assertTrue(self._helper("잔고내역이 없습니다"))

    def test_rate_limit_EGW00201_returns_false(self):
        self.assertFalse(self._helper({"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."}))

    def test_buy_untradable_40070000_returns_false(self):
        self.assertFalse(self._helper({"rt_cd": "1", "msg_cd": "40070000", "msg1": "매매불가 종목입니다"}))

    def test_empty_dict_returns_false(self):
        self.assertFalse(self._helper({}))

    def test_empty_string_returns_false(self):
        self.assertFalse(self._helper(""))

    def test_none_returns_false(self):
        self.assertFalse(self._helper(None))

    def test_success_response_returns_false(self):
        self.assertFalse(self._helper({"rt_cd": "0", "msg1": "정상처리되었습니다"}))


class SellFailureRawResponseCategoryTests(unittest.TestCase):
    """Verify that log_order_event raw_response contains failure_category for no-position failures."""

    def _run_sell_flow_with_result(self, sell_result: dict) -> dict:
        """
        Runs the rt_cd != "0" branch of _run_sell_order_flow indirectly by calling
        log_order_event in the same way main.py does and capturing the written record.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "orders.jsonl"
            from app.core.error_classification import (
                looks_like_no_position_sell_response as _looks_like_no_position_sell_response,
            )

            # Build raw_response the same way main.py does for this branch
            sell_raw_response: dict = {}
            _sell_fail_response: dict = {**sell_raw_response, "order_response": sell_result}
            if _looks_like_no_position_sell_response(sell_result):
                _sell_fail_response["failure_category"] = "no_position_on_sell"

            with (
                patch("app.core.order_log.get_order_log_path", return_value=log_path),
            ):
                from app.core.order_log import log_order_event
                log_order_event(
                    symbol="068270",
                    qty=3,
                    order_type="market_sell",
                    confirm_buy="YES",
                    market_open=True,
                    action="sell_order_failed",
                    result="failed",
                    reason="잔고내역이 없습니다 (40240000)",
                    raw_response=_sell_fail_response,
                    environment="mock",
                    cycle_id="c_test",
                )

            lines = [l for l in log_path.read_text(encoding="utf-8").splitlines() if l.strip()]
            self.assertTrue(lines)
            return json.loads(lines[0])

    def test_no_position_response_has_failure_category(self):
        sell_result = {"rt_cd": "1", "msg_cd": "40240000", "msg1": "잔고내역이 없습니다"}
        record = self._run_sell_flow_with_result(sell_result)
        raw = record.get("raw_response", {})
        self.assertIn("failure_category", raw)
        self.assertEqual(raw["failure_category"], "no_position_on_sell")

    def test_no_position_preserves_order_response(self):
        sell_result = {"rt_cd": "1", "msg_cd": "40240000", "msg1": "잔고내역이 없습니다"}
        record = self._run_sell_flow_with_result(sell_result)
        raw = record.get("raw_response", {})
        self.assertIn("order_response", raw)
        self.assertEqual(raw["order_response"]["msg_cd"], "40240000")

    def test_no_position_result_is_failed(self):
        sell_result = {"rt_cd": "1", "msg_cd": "40240000", "msg1": "잔고내역이 없습니다"}
        record = self._run_sell_flow_with_result(sell_result)
        self.assertEqual(record["result"], "failed")
        self.assertEqual(record["action"], "sell_order_failed")

    def test_unrelated_failure_has_no_failure_category(self):
        sell_result = {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."}
        record = self._run_sell_flow_with_result(sell_result)
        raw = record.get("raw_response", {})
        self.assertNotIn("failure_category", raw)

    def test_unrelated_failure_result_is_failed(self):
        sell_result = {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."}
        record = self._run_sell_flow_with_result(sell_result)
        self.assertEqual(record["result"], "failed")

    def test_잔고내역_in_msg1_has_failure_category(self):
        sell_result = {"rt_cd": "1", "msg_cd": "APBK9999", "msg1": "잔고내역 없음"}
        record = self._run_sell_flow_with_result(sell_result)
        raw = record.get("raw_response", {})
        self.assertIn("failure_category", raw)
        self.assertEqual(raw["failure_category"], "no_position_on_sell")


if __name__ == "__main__":
    unittest.main()
