"""Order submission reserves 2 request slots before its hashkey+POST pair (T4).

docs/todo_20260710.md §A-2: an order is always two requests inside one gateway
second. The old fixed `time.sleep(1.0)` measured from *code time*, not from the
throttle ledger, so a quote issued 0.9s earlier could still share the second and
push the pair over KIS's per-second ceiling → EGW00201 on the order POST.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from app.core import throttle as throttle_module
from app.domestic_stock import order as order_module


class WaitForRequestSlotsTests(unittest.TestCase):
    def setUp(self) -> None:
        throttle_module.reset_throttle_state()

    def tearDown(self) -> None:
        throttle_module.reset_throttle_state()

    def _settings(self, *, limit: int = 4, floor: float = 1.1) -> SimpleNamespace:
        return SimpleNamespace(
            api_soft_max_requests_per_second=limit,
            api_soft_max_quotes_per_tick=2,
            api_min_inter_request_seconds=floor,
        )

    def test_empty_ledger_still_honors_the_global_spacing_floor(self) -> None:
        with (
            mock.patch.object(
                throttle_module, "get_settings", return_value=self._settings()
            ),
            mock.patch.object(throttle_module.time, "sleep") as sleep_mock,
        ):
            waited = throttle_module.wait_for_request_slots(2)

        self.assertEqual(waited, 0.0)
        sleep_mock.assert_not_called()

    def test_full_window_waits_until_two_slots_free(self) -> None:
        now = throttle_module.time.perf_counter()
        # 4 requests inside the current 1s window: zero headroom for a 2-request pair.
        for offset in (0.0, 0.1, 0.2, 0.3):
            throttle_module._REQUEST_TIMESTAMPS.append(now - 0.9 + offset)
        throttle_module._LAST_GLOBAL_TS = now - 5.0

        slept: list[float] = []
        with (
            mock.patch.object(
                throttle_module, "get_settings", return_value=self._settings()
            ),
            mock.patch.object(
                throttle_module.time, "sleep", side_effect=slept.append
            ),
        ):
            waited = throttle_module.wait_for_request_slots(2)

        self.assertGreater(waited, 0.0)
        self.assertTrue(slept)

    def test_reserve_clamps_to_the_limit_instead_of_raising(self) -> None:
        """A per-second limit below the pair size must degrade to sequential
        spacing (the old sleep behavior), never crash the order path."""
        now = throttle_module.time.perf_counter()
        throttle_module._REQUEST_TIMESTAMPS.append(now - 0.2)
        throttle_module._LAST_GLOBAL_TS = now - 0.2

        slept: list[float] = []
        with (
            mock.patch.object(
                throttle_module,
                "get_settings",
                return_value=self._settings(limit=1, floor=1.1),
            ),
            mock.patch.object(
                throttle_module.time, "sleep", side_effect=slept.append
            ),
        ):
            waited = throttle_module.wait_for_request_slots(2)

        self.assertGreater(waited, 0.0)
        self.assertTrue(slept)


class OrderReservesSlotsTests(unittest.TestCase):
    def test_order_market_reserves_two_slots_instead_of_blind_sleep(self) -> None:
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
            ) as reserve_mock,
            mock.patch.object(
                order_module, "request_json", return_value={"rt_cd": "0"}
            ),
        ):
            order_module.buy_market("005930", 1, token="tok")

        reserve_mock.assert_called_once_with(2)

    def test_order_module_no_longer_blind_sleeps(self) -> None:
        # The wall-clock sleep is what let a stray quote share the gateway second.
        self.assertFalse(hasattr(order_module, "time"))


if __name__ == "__main__":
    unittest.main()
