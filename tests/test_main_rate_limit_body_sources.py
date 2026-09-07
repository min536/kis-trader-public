from __future__ import annotations

import inspect
import unittest

from app import main as main_module
from app.auth.token import ApiHttpError
from app.execution import buy_flow as buy_flow_module
from app.runtime.cycle_phases import account_snapshot as account_snapshot_module


class MainRateLimitBodySourceTests(unittest.TestCase):
    def test_response_body_rate_limit_source_helper_recognizes_kis_body(self) -> None:
        response = {
            "rt_cd": "1",
            "msg_cd": "EGW00201",
            "msg1": "초당 거래건수 초과",
        }

        self.assertEqual(
            main_module._rate_limit_source_from_response_body(
                response,
                source="orderable",
            ),
            "orderable",
        )

    def test_response_body_rate_limit_source_helper_ignores_normal_failure(self) -> None:
        response = {
            "rt_cd": "1",
            "msg_cd": "APBK0001",
            "msg1": "일반 업무 오류",
        }

        self.assertIsNone(
            main_module._rate_limit_source_from_response_body(
                response,
                source="orderable",
            )
        )

    def test_balance_inquiry_has_narrow_rate_limit_try_except(self) -> None:
        # Slice H: the balance-inquiry block moved verbatim into the
        # account-snapshot phase module; re-aim the getsource at it. The chain is
        # preserved by also asserting run_cycle still calls the phase.
        self.assertIn(
            "run_account_snapshot_phase(",
            inspect.getsource(main_module.run_cycle),
        )
        source = inspect.getsource(
            account_snapshot_module.run_account_snapshot_phase
        )

        balance_call_pos = source.index("balance_data = inquire_balance")

        narrow_try_region = source[max(0, balance_call_pos - 300):balance_call_pos]
        self.assertIn("try:", narrow_try_region)

        after_call = source[balance_call_pos:balance_call_pos + 2500]
        self.assertIn("_looks_like_rate_limit_error(_bal_exc)", after_call)
        self.assertIn('rate_limit_source = "balance"', after_call)
        self.assertIn('action="skipped_balance_rate_limit_backoff"', after_call)
        self.assertIn('action="HOLD_BALANCE_RATE_LIMIT"', after_call)
        self.assertIn("_api_budget_note_rate_limit(", after_call)
        self.assertIn("return", after_call)
        self.assertIn("raise", after_call)

    def test_api_budget_note_rate_limit_records_kis_bottleneck(self) -> None:
        """_api_budget_note_rate_limit internally calls _record_bottleneck(KIS_RATE_LIMIT_BACKOFF)."""
        source = inspect.getsource(main_module._api_budget_note_rate_limit)
        self.assertIn("KIS_RATE_LIMIT_BACKOFF", source)
        self.assertIn("_record_bottleneck", source)

    def test_balance_orderable_and_order_body_failures_set_specific_sources(self) -> None:
        # Slice H: the balance segment moved into the account-snapshot phase
        # module; the orderable/order segments stay in buy_flow.
        balance_source = inspect.getsource(
            account_snapshot_module.run_account_snapshot_phase
        )
        buy_source = inspect.getsource(buy_flow_module.run_buy_order_flow)

        balance_block_start = balance_source.index("balance_data = inquire_balance")
        balance_block_end = balance_source.index("raise RuntimeError(f\"잔고 조회 실패", balance_block_start)
        self.assertIn('source="balance"', balance_source[balance_block_start:balance_block_end])

        orderable_block_start = buy_source.index("orderable_data = inquire_orderable_cash")
        orderable_block_end = buy_source.index("raise RuntimeError(f\"주문가능금액 조회 실패", orderable_block_start)
        self.assertIn('source="orderable"', buy_source[orderable_block_start:orderable_block_end])

        order_block_start = buy_source.index("order_result = buy_market")
        order_block_end = buy_source.index("log_order_event", order_block_start)
        self.assertIn('source="buy_order"', buy_source[order_block_start:order_block_end])

    def test_rate_limit_exception_source_infers_sell_order(self) -> None:
        exc = ApiHttpError(
            "시장가 매도 HTTP 429",
            status_code=429,
            data={"msg_cd": "EGW00201", "msg1": "초당 거래건수 초과"},
        )

        self.assertEqual(
            main_module._rate_limit_source_from_exception(exc),
            "sell_order",
        )

    def test_rate_limit_exception_source_infers_balance(self) -> None:
        exc = RuntimeError("잔고 조회 실패: {'msg_cd': 'EGW00201', 'msg1': '초당 거래건수 초과'}")

        self.assertEqual(
            main_module._rate_limit_source_from_exception(exc),
            "balance",
        )


if __name__ == "__main__":
    unittest.main()
