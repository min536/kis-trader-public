from __future__ import annotations

import inspect
import unittest
from datetime import datetime
from unittest import mock

from app import main as main_module
from app.core.time_utils import KOREA_TZ
from app.execution import buy_flow as buy_flow_module
from app.execution import sell_flow as sell_flow_module
from app.runtime.cycle_phases import sell_order_phase


class BuyOrderSubmitBudgetTests(unittest.TestCase):
    def test_execution_budget_positive_wait_notifies_and_sleeps(self) -> None:
        now = datetime(2026, 6, 3, 10, 0, tzinfo=KOREA_TZ)
        notes: list[tuple[str, str]] = []

        with (
            mock.patch.object(main_module, "_api_budget_backoff_active", return_value=False),
            mock.patch.object(
                main_module,
                "_api_budget_transient_backoff_active",
                return_value=False,
            ),
            mock.patch.object(
                main_module,
                "_api_budget_min_wait_for_request_slot",
                return_value=0.25,
            ),
            mock.patch.object(main_module.time, "sleep") as mocked_sleep,
        ):
            wait_ms = main_module._wait_for_execution_request_budget(
                {},
                now=now,
                phase="order_submit",
                request_cost=2,
                request_reserve=0,
                note_callback=lambda level, message: notes.append((level, message)),
            )

        self.assertEqual(wait_ms, 250.0)
        self.assertEqual(
            notes,
            [
                (
                    "INFO",
                    "execution tail request reserve를 확보하기 위해 "
                    "order_submit 전에 250ms 대기합니다.",
                )
            ],
        )
        mocked_sleep.assert_called_once_with(0.25)

    def test_buy_order_submit_accounts_for_hashkey_and_order_post(self) -> None:
        source = inspect.getsource(buy_flow_module.run_buy_order_flow)
        start = source.index('phase="order_submit"')
        end = source.index("order_result = buy_market", start)
        order_submit_block = source[start:end]

        self.assertIn("request_cost=2", order_submit_block)
        self.assertIn("api_budget_register_requests(", order_submit_block)
        self.assertIn("request_count=2", order_submit_block)
        self.assertNotIn("api_budget_register_request(", order_submit_block)

    def test_sell_order_submit_accounts_for_hashkey_and_order_post(self) -> None:
        source = inspect.getsource(sell_flow_module.run_sell_order_flow)
        start = source.index('phase="sell_order_submit"')
        end = source.index("sell_result = sell_market", start)
        sell_order_submit_block = source[start:end]

        self.assertIn("request_cost=2", sell_order_submit_block)
        self.assertIn("api_budget_register_requests(", sell_order_submit_block)
        self.assertIn("request_count=2", sell_order_submit_block)

    def test_sell_order_flow_receives_cycle_api_budget_state(self) -> None:
        # Stage B-3 slice L re-aim: the `consumed_by_sell = sell_order_flow(...)`
        # call moved to run_sell_order_phase (byte-verbatim), so this source pin
        # follows it there; run_cycle still wires the phase.
        source = inspect.getsource(sell_order_phase.run_sell_order_phase)
        self.assertIn(
            "run_sell_order_phase(",
            inspect.getsource(main_module.run_cycle),
        )
        call_start = source.index("consumed_by_sell = sell_order_flow(")
        call_end = source.index(")", call_start)
        sell_flow_call = source[call_start:call_end]

        self.assertIn("api_budget_state=api_budget_state", sell_flow_call)


if __name__ == "__main__":
    unittest.main()
