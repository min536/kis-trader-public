from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from app.auth.token import ApiHttpError
from app.domestic_stock import order as order_module


class DomesticOrderRateLimitTests(unittest.TestCase):
    def test_order_does_not_retry_rate_limit_error(self) -> None:
        rate_limit_error = ApiHttpError(
            "시장가 매수 HTTP 429",
            status_code=429,
            data={"msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."},
        )

        with (
            mock.patch.object(
                order_module,
                "get_settings",
                return_value=SimpleNamespace(
                    base_url="https://example.test",
                    cano="12345678",
                    acnt_prdt_cd="01",
                ),
            ),
            mock.patch.object(order_module, "issue_hashkey", return_value="hash"),
            mock.patch.object(order_module, "build_auth_headers", return_value={}),
            mock.patch.object(
                order_module, "wait_for_request_slots", return_value=0.0
            ) as mocked_reserve,
            mock.patch.object(
                order_module,
                "request_json",
                side_effect=rate_limit_error,
            ) as mocked_request_json,
        ):
            with self.assertRaises(ApiHttpError):
                order_module.buy_market("005930", 1, token="tok")

        mocked_request_json.assert_called_once()
        # The hashkey+order pair is admitted as one 2-slot reservation rather than
        # guarded by a blind 1.0s sleep (docs/todo_20260710.md §A-2 / T4).
        mocked_reserve.assert_called_once_with(2)


if __name__ == "__main__":
    unittest.main()
