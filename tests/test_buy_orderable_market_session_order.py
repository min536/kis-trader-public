from __future__ import annotations

import inspect
import unittest

from app import main as main_module
from app.execution import buy_flow as buy_flow_module
from app.execution import sell_flow as sell_flow_module


class BuyOrderableMarketSessionOrderTests(unittest.TestCase):
    def test_market_closed_buy_block_runs_before_orderable_lookup(self) -> None:
        source = inspect.getsource(buy_flow_module.run_buy_order_flow)
        selected_buy_start = source.index("symbol = selected_candidate.symbol")
        orderable_lookup = source.index("orderable_data = inquire_orderable_cash", selected_buy_start)
        market_closed_block = source.index(
            "현재는 주문 가능 세션이 아니므로 주문가능조회와 주문을 보내지 않습니다.",
            selected_buy_start,
        )

        self.assertLess(market_closed_block, orderable_lookup)

    def test_buy_rechecks_market_session_before_order_submit_log(self) -> None:
        source = inspect.getsource(buy_flow_module.run_buy_order_flow)
        selected_buy_start = source.index("symbol = selected_candidate.symbol")
        risk_guard = source.index(
            "risk_guard_decision = evaluate_buy_risk_guards",
            selected_buy_start,
        )
        order_session_recheck = source.index(
            "order_session_status = get_korean_market_session()",
            risk_guard,
        )
        order_submitted_log = source.index('action="order_submitted"', risk_guard)

        self.assertLess(order_session_recheck, order_submitted_log)

    def test_sell_rechecks_market_session_before_order_submit_log(self) -> None:
        source = inspect.getsource(sell_flow_module.run_sell_order_flow)
        order_session_recheck = source.index(
            "order_session_status = get_korean_market_session()"
        )
        order_submitted_log = source.index('action="sell_order_submitted"')

        self.assertLess(order_session_recheck, order_submitted_log)


if __name__ == "__main__":
    unittest.main()
