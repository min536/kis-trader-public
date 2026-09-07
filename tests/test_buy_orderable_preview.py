from __future__ import annotations

import inspect
import unittest
from types import SimpleNamespace

from app import main as main_module
from app.execution import buy_flow as buy_flow_module


class BuyOrderablePreviewTests(unittest.TestCase):
    def test_preview_orderable_output_uses_portfolio_cash_without_api_fields(self) -> None:
        portfolio_snapshot = SimpleNamespace(cash_orderable=1_000_000)

        output = main_module._build_preview_orderable_output_from_portfolio(
            portfolio_snapshot=portfolio_snapshot,
            current_price=80_000,
        )

        self.assertEqual(output["ord_psbl_cash"], "1000000")
        self.assertEqual(output["nrcvb_buy_qty"], "12")

    def test_confirm_buy_off_branch_precedes_orderable_api_lookup(self) -> None:
        source = inspect.getsource(buy_flow_module.run_buy_order_flow)
        selected_buy_start = source.index("symbol = selected_candidate.symbol")
        confirm_off_branch = source.index('if settings.confirm_buy != "YES":', selected_buy_start)
        orderable_lookup = source.index("orderable_data = inquire_orderable_cash", selected_buy_start)

        self.assertLess(confirm_off_branch, orderable_lookup)


if __name__ == "__main__":
    unittest.main()
